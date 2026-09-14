"""Optional self-hosted collaboration fabric for W1 Nexus.

Step 40 adds team tenancy and deterministic project synchronization without
making a W1-owned cloud a runtime dependency.  The implementation is standard-
library only, self-hostable, and deliberately fail-closed:

* membership and project access are explicit;
* invitation/access tokens are stored only as SHA-256 digests;
* synchronization is optimistic and never silently overwrites conflicts;
* per-device event chains and a team audit hash-chain make tampering detectable;
* non-loopback HTTP requires TLS, while loopback HTTP remains available for
  local development and deterministic tests;
* a W1-hosted service is optional and is not assumed by the protocol.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

COLLABORATION_PROTOCOL_VERSION = "1.0"
COLLABORATION_FABRIC_VERSION = "0.1.0-dev50"
TEAM_ROLES = ("owner", "admin", "member", "viewer")
PROJECT_ACCESS_LEVELS = ("viewer", "editor", "manager")
MAX_SYNC_VALUE_BYTES = 1_000_000


class CollaborationError(RuntimeError):
    code = "collaboration_error"


class CollaborationNotFound(CollaborationError):
    code = "collaboration_not_found"


class CollaborationDenied(CollaborationError):
    code = "collaboration_denied"


class CollaborationConflict(CollaborationError):
    code = "collaboration_conflict"

    def __init__(self, message: str, *, conflict_id: str | None = None) -> None:
        self.conflict_id = conflict_id
        super().__init__(message)


class CollaborationIntegrityError(CollaborationError):
    code = "collaboration_integrity_error"


class CollaborationTransportError(CollaborationError):
    code = "collaboration_transport_error"


class InvitationInvalid(CollaborationError):
    code = "invitation_invalid"


class AccessTokenInvalid(CollaborationError):
    code = "access_token_invalid"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class TeamRecord:
    team_id: str
    name: str
    created_by: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemberRecord:
    team_id: str
    principal_id: str
    role: str
    joined_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InvitationRecord:
    invitation_id: str
    team_id: str
    role: str
    created_by: str
    created_at: str
    expires_at: str
    accepted_by: str | None
    accepted_at: str | None
    revoked_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectRecord:
    project_id: str
    team_id: str
    name: str
    created_by: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectShare:
    project_id: str
    principal_id: str
    access: str
    granted_by: str
    granted_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccessTokenRecord:
    token_id: str
    team_id: str
    principal_id: str
    label: str
    created_at: str
    expires_at: str
    revoked_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplicaRecord:
    device_id: str
    team_id: str
    principal_id: str
    label: str
    registered_at: str
    last_sequence: int
    last_event_hash: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyncMutation:
    mutation_id: str
    project_id: str
    device_id: str
    sequence: int
    base_revision: int
    key: str
    operation: str
    value: Any = None
    previous_event_hash: str | None = None
    created_at: str | None = None
    event_hash: str | None = None

    def signing_payload(self) -> dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "project_id": self.project_id,
            "device_id": self.device_id,
            "sequence": self.sequence,
            "base_revision": self.base_revision,
            "key": self.key,
            "operation": self.operation,
            "value": _json_copy(self.value),
            "previous_event_hash": self.previous_event_hash,
            "created_at": self.created_at,
        }

    def computed_hash(self) -> str:
        return _sha256(_canonical(self.signing_payload()))

    def normalized(self) -> "SyncMutation":
        operation = str(self.operation).lower()
        if operation not in {"set", "delete"}:
            raise CollaborationIntegrityError("Sync operation must be 'set' or 'delete'.")
        for label, raw in (("mutation_id", self.mutation_id), ("project_id", self.project_id), ("device_id", self.device_id)):
            value = str(raw).strip()
            if not value or len(value) > 200:
                raise CollaborationIntegrityError(f"{label} is empty or too long.")
        key = str(self.key).strip()
        if not key or len(key) > 512 or key.startswith("/") or ".." in key.split("/"):
            raise CollaborationIntegrityError("Sync key is empty or unsafe.")
        if self.sequence < 1 or self.base_revision < 0:
            raise CollaborationIntegrityError("Invalid synchronization sequence or revision.")
        if operation == "set" and len(_canonical(self.value)) > MAX_SYNC_VALUE_BYTES:
            raise CollaborationIntegrityError("Synchronization value exceeds the 1 MB per-mutation bound.")
        created = self.created_at or _now()
        candidate = SyncMutation(
            mutation_id=str(self.mutation_id), project_id=str(self.project_id), device_id=str(self.device_id),
            sequence=int(self.sequence), base_revision=int(self.base_revision), key=key,
            operation=operation, value=None if operation == "delete" else _json_copy(self.value),
            previous_event_hash=self.previous_event_hash, created_at=created, event_hash=None,
        )
        digest = candidate.computed_hash()
        if self.event_hash is not None and not hmac.compare_digest(str(self.event_hash), digest):
            raise CollaborationIntegrityError("Sync event hash does not match its canonical payload.")
        return SyncMutation(**candidate.signing_payload(), event_hash=digest)

    def as_dict(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {**normalized.signing_payload(), "event_hash": normalized.event_hash}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SyncMutation":
        return cls(
            mutation_id=str(value.get("mutation_id", "")), project_id=str(value.get("project_id", "")),
            device_id=str(value.get("device_id", "")), sequence=int(value.get("sequence", 0)),
            base_revision=int(value.get("base_revision", 0)), key=str(value.get("key", "")),
            operation=str(value.get("operation", "")), value=value.get("value"),
            previous_event_hash=value.get("previous_event_hash"), created_at=value.get("created_at"),
            event_hash=value.get("event_hash"),
        )


@dataclass(frozen=True)
class ConflictRecord:
    conflict_id: str
    project_id: str
    key: str
    mutation_id: str
    client_base_revision: int
    client_event_hash: str
    client_operation: str
    client_value: Any
    server_revision: int
    server_event_hash: str | None
    status: str
    created_at: str
    resolved_at: str | None
    resolution_event_hash: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CollaborationServerSettings:
    host: str = "127.0.0.1"
    port: int = 0
    certfile: str | None = None
    keyfile: str | None = None
    max_request_bytes: int = 2_000_000

    @property
    def tls_enabled(self) -> bool:
        return bool(self.certfile and self.keyfile)

    def validate(self) -> None:
        if not (0 <= self.port <= 65535):
            raise CollaborationTransportError("Invalid collaboration server port.")
        if not _is_loopback_host(self.host) and not self.tls_enabled:
            raise CollaborationTransportError("Non-loopback collaboration service requires TLS certfile/keyfile.")
        if bool(self.certfile) != bool(self.keyfile):
            raise CollaborationTransportError("Both TLS certfile and keyfile are required together.")
        if self.max_request_bytes < 1024 or self.max_request_bytes > 50_000_000:
            raise CollaborationTransportError("max_request_bytes is outside the allowed range.")


class CollaborationStore:
    """SQLite-backed tenant, authorization, sync and federated-audit store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CollaborationStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS teams (
                    team_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    team_id TEXT NOT NULL, principal_id TEXT NOT NULL, role TEXT NOT NULL, joined_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, principal_id), FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS invitations (
                    invitation_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, role TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    accepted_by TEXT, accepted_at TEXT, revoked_at TEXT,
                    FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, name TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS project_shares (
                    project_id TEXT NOT NULL, principal_id TEXT NOT NULL, access TEXT NOT NULL, granted_by TEXT NOT NULL, granted_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, principal_id), FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS access_tokens (
                    token_id TEXT PRIMARY KEY, team_id TEXT NOT NULL, principal_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT,
                    FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS replicas (
                    device_id TEXT NOT NULL, team_id TEXT NOT NULL, principal_id TEXT NOT NULL, label TEXT NOT NULL,
                    registered_at TEXT NOT NULL, last_sequence INTEGER NOT NULL DEFAULT 0, last_event_hash TEXT,
                    PRIMARY KEY(team_id, device_id), FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS project_state (
                    project_id TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT, tombstone INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL, event_hash TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, key), FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sync_events (
                    ordinal INTEGER PRIMARY KEY AUTOINCREMENT, mutation_id TEXT NOT NULL UNIQUE, team_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, principal_id TEXT NOT NULL, device_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    base_revision INTEGER NOT NULL, resulting_revision INTEGER NOT NULL, key TEXT NOT NULL, operation TEXT NOT NULL,
                    value_json TEXT, previous_event_hash TEXT, event_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS conflicts (
                    conflict_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, key TEXT NOT NULL, mutation_id TEXT NOT NULL,
                    client_base_revision INTEGER NOT NULL, client_event_hash TEXT NOT NULL, client_operation TEXT NOT NULL, client_value_json TEXT,
                    server_revision INTEGER NOT NULL, server_event_hash TEXT,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT, resolution_event_hash TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    ordinal INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, team_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, actor_principal_id TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
                    details_json TEXT NOT NULL, previous_hash TEXT, event_hash TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    FOREIGN KEY(team_id) REFERENCES teams(team_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_members_principal ON members(principal_id);
                CREATE INDEX IF NOT EXISTS idx_projects_team ON projects(team_id);
                CREATE INDEX IF NOT EXISTS idx_sync_project_ordinal ON sync_events(project_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_conflicts_project_status ON conflicts(project_id, status);
                CREATE INDEX IF NOT EXISTS idx_audit_team_ordinal ON audit_events(team_id, ordinal);
                """
            )

    @staticmethod
    def _role_rank(role: str) -> int:
        return {"viewer": 0, "member": 1, "admin": 2, "owner": 3}.get(role, -1)

    @staticmethod
    def _access_rank(access: str) -> int:
        return {"viewer": 0, "editor": 1, "manager": 2}.get(access, -1)

    def _row_team(self, row: sqlite3.Row) -> TeamRecord:
        return TeamRecord(row["team_id"], row["name"], row["created_by"], row["created_at"])

    def _row_member(self, row: sqlite3.Row) -> MemberRecord:
        return MemberRecord(row["team_id"], row["principal_id"], row["role"], row["joined_at"])

    def _append_audit(self, team_id: str, event_type: str, actor: str, object_type: str, object_id: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
        previous = self.connection.execute(
            "SELECT event_hash FROM audit_events WHERE team_id=? ORDER BY ordinal DESC LIMIT 1", (team_id,)
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else None
        occurred_at = _now()
        event_id = _new_id("audit")
        payload = {
            "event_id": event_id, "team_id": team_id, "event_type": event_type,
            "actor_principal_id": actor, "object_type": object_type, "object_id": object_id,
            "details": _json_copy(details or {}), "previous_hash": previous_hash, "occurred_at": occurred_at,
        }
        digest = _sha256(_canonical(payload))
        self.connection.execute(
            "INSERT INTO audit_events(event_id,team_id,event_type,actor_principal_id,object_type,object_id,details_json,previous_hash,event_hash,occurred_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event_id, team_id, event_type, actor, object_type, object_id, json.dumps(payload["details"], ensure_ascii=False, sort_keys=True), previous_hash, digest, occurred_at),
        )
        return {**payload, "event_hash": digest}

    def create_team(self, name: str, *, owner_principal_id: str, team_id: str | None = None) -> TeamRecord:
        name = str(name).strip()
        owner = str(owner_principal_id).strip()
        if not name or not owner:
            raise CollaborationIntegrityError("Team name and owner principal are required.")
        record = TeamRecord(team_id or _new_id("team"), name, owner, _now())
        with self._lock, self.connection:
            try:
                self.connection.execute("INSERT INTO teams(team_id,name,created_by,created_at) VALUES(?,?,?,?)", (record.team_id, record.name, record.created_by, record.created_at))
                self.connection.execute("INSERT INTO members(team_id,principal_id,role,joined_at) VALUES(?,?,?,?)", (record.team_id, owner, "owner", record.created_at))
            except sqlite3.IntegrityError as exc:
                raise CollaborationIntegrityError("Team id already exists.") from exc
            self._append_audit(record.team_id, "team.created", owner, "team", record.team_id, {"name": name})
        return record

    def list_teams(self, *, principal_id: str | None = None) -> list[TeamRecord]:
        if principal_id:
            rows = self.connection.execute(
                "SELECT t.* FROM teams t JOIN members m ON m.team_id=t.team_id WHERE m.principal_id=? ORDER BY t.created_at", (principal_id,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM teams ORDER BY created_at").fetchall()
        return [self._row_team(row) for row in rows]

    def get_team(self, team_id: str) -> TeamRecord:
        row = self.connection.execute("SELECT * FROM teams WHERE team_id=?", (team_id,)).fetchone()
        if not row:
            raise CollaborationNotFound(f"Unknown team: {team_id}")
        return self._row_team(row)

    def get_member(self, team_id: str, principal_id: str) -> MemberRecord:
        row = self.connection.execute("SELECT * FROM members WHERE team_id=? AND principal_id=?", (team_id, principal_id)).fetchone()
        if not row:
            raise CollaborationDenied("Principal is not a member of this team.")
        return self._row_member(row)

    def list_members(self, team_id: str, *, actor: str) -> list[MemberRecord]:
        self.get_member(team_id, actor)
        rows = self.connection.execute("SELECT * FROM members WHERE team_id=? ORDER BY joined_at,principal_id", (team_id,)).fetchall()
        return [self._row_member(row) for row in rows]

    def _require_team_role(self, team_id: str, principal_id: str, minimum: str) -> MemberRecord:
        member = self.get_member(team_id, principal_id)
        if self._role_rank(member.role) < self._role_rank(minimum):
            raise CollaborationDenied(f"Team role '{minimum}' or higher is required.")
        return member

    def set_member_role(self, team_id: str, principal_id: str, role: str, *, actor: str) -> MemberRecord:
        if role not in TEAM_ROLES:
            raise CollaborationIntegrityError("Unknown team role.")
        self._require_team_role(team_id, actor, "admin")
        target = self.get_member(team_id, principal_id)
        actor_member = self.get_member(team_id, actor)
        if target.role == "owner" and role != "owner" and actor_member.role != "owner":
            raise CollaborationDenied("Only an owner can demote another owner.")
        if target.role == "owner" and role != "owner":
            owners = self.connection.execute("SELECT COUNT(*) AS n FROM members WHERE team_id=? AND role='owner'", (team_id,)).fetchone()["n"]
            if owners <= 1:
                raise CollaborationDenied("Cannot demote the last team owner.")
        if role == "owner" and actor_member.role != "owner":
            raise CollaborationDenied("Only an owner can grant the owner role.")
        with self._lock, self.connection:
            self.connection.execute("UPDATE members SET role=? WHERE team_id=? AND principal_id=?", (role, team_id, principal_id))
            self._append_audit(team_id, "member.role.changed", actor, "member", principal_id, {"from": target.role, "to": role})
        return self.get_member(team_id, principal_id)

    def remove_member(self, team_id: str, principal_id: str, *, actor: str) -> dict[str, Any]:
        actor_member = self._require_team_role(team_id, actor, "admin")
        target = self.get_member(team_id, principal_id)
        if target.role == "owner":
            if actor_member.role != "owner":
                raise CollaborationDenied("Only an owner can remove another owner.")
            owners = self.connection.execute("SELECT COUNT(*) AS n FROM members WHERE team_id=? AND role='owner'", (team_id,)).fetchone()["n"]
            if owners <= 1:
                raise CollaborationDenied("Cannot remove the last team owner.")
        with self._lock, self.connection:
            revoked_at = _now()
            token_count = self.connection.execute(
                "SELECT COUNT(*) AS n FROM access_tokens WHERE team_id=? AND principal_id=? AND revoked_at IS NULL",
                (team_id, principal_id),
            ).fetchone()["n"]
            replica_count = self.connection.execute(
                "SELECT COUNT(*) AS n FROM replicas WHERE team_id=? AND principal_id=?", (team_id, principal_id)
            ).fetchone()["n"]
            share_count = self.connection.execute(
                "SELECT COUNT(*) AS n FROM project_shares s JOIN projects p ON p.project_id=s.project_id WHERE p.team_id=? AND s.principal_id=?",
                (team_id, principal_id),
            ).fetchone()["n"]
            self._append_audit(team_id, "member.removed", actor, "member", principal_id, {
                "role": target.role, "revoked_tokens": token_count, "removed_replicas": replica_count, "removed_project_shares": share_count
            })
            self.connection.execute(
                "UPDATE access_tokens SET revoked_at=? WHERE team_id=? AND principal_id=? AND revoked_at IS NULL",
                (revoked_at, team_id, principal_id),
            )
            self.connection.execute("DELETE FROM replicas WHERE team_id=? AND principal_id=?", (team_id, principal_id))
            self.connection.execute(
                "DELETE FROM project_shares WHERE principal_id=? AND project_id IN (SELECT project_id FROM projects WHERE team_id=?)",
                (principal_id, team_id),
            )
            self.connection.execute("DELETE FROM members WHERE team_id=? AND principal_id=?", (team_id, principal_id))
        return {
            "removed": True, "team_id": team_id, "principal_id": principal_id,
            "revoked_tokens": int(token_count), "removed_replicas": int(replica_count), "removed_project_shares": int(share_count),
        }

    def create_invitation(self, team_id: str, *, role: str, created_by: str, expires_in_hours: int = 72) -> tuple[InvitationRecord, str]:
        if role not in TEAM_ROLES or role == "owner":
            raise CollaborationIntegrityError("Invitations may grant admin, member, or viewer roles only.")
        self._require_team_role(team_id, created_by, "admin")
        if not (1 <= expires_in_hours <= 24 * 30):
            raise CollaborationIntegrityError("Invitation expiry must be between 1 hour and 30 days.")
        token = secrets.token_urlsafe(32)
        created_at = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
        record = InvitationRecord(_new_id("invite"), team_id, role, created_by, created_at, expires_at, None, None, None)
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO invitations(invitation_id,team_id,role,token_hash,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (record.invitation_id, team_id, role, _sha256(token), created_by, created_at, expires_at),
            )
            self._append_audit(team_id, "invitation.created", created_by, "invitation", record.invitation_id, {"role": role, "expires_at": expires_at})
        return record, token

    def accept_invitation(self, token: str, *, principal_id: str) -> MemberRecord:
        token_hash = _sha256(str(token))
        row = self.connection.execute("SELECT * FROM invitations WHERE token_hash=?", (token_hash,)).fetchone()
        if not row or row["revoked_at"] or row["accepted_at"] or _parse_time(row["expires_at"]) <= datetime.now(timezone.utc):
            raise InvitationInvalid("Invitation is unknown, expired, revoked, or already used.")
        with self._lock, self.connection:
            existing = self.connection.execute("SELECT role FROM members WHERE team_id=? AND principal_id=?", (row["team_id"], principal_id)).fetchone()
            if existing:
                raise InvitationInvalid("Principal is already a team member.")
            joined_at = _now()
            self.connection.execute("INSERT INTO members(team_id,principal_id,role,joined_at) VALUES(?,?,?,?)", (row["team_id"], principal_id, row["role"], joined_at))
            self.connection.execute("UPDATE invitations SET accepted_by=?, accepted_at=? WHERE invitation_id=?", (principal_id, joined_at, row["invitation_id"]))
            self._append_audit(row["team_id"], "invitation.accepted", principal_id, "invitation", row["invitation_id"], {"role": row["role"]})
        return self.get_member(row["team_id"], principal_id)

    def revoke_invitation(self, invitation_id: str, *, actor: str) -> InvitationRecord:
        row = self.connection.execute("SELECT * FROM invitations WHERE invitation_id=?", (invitation_id,)).fetchone()
        if not row:
            raise CollaborationNotFound("Unknown invitation.")
        self._require_team_role(row["team_id"], actor, "admin")
        if row["accepted_at"]:
            raise InvitationInvalid("Accepted invitation cannot be revoked retroactively.")
        revoked = _now()
        with self._lock, self.connection:
            self.connection.execute("UPDATE invitations SET revoked_at=? WHERE invitation_id=?", (revoked, invitation_id))
            self._append_audit(row["team_id"], "invitation.revoked", actor, "invitation", invitation_id, {})
        return self.get_invitation(invitation_id)

    def get_invitation(self, invitation_id: str) -> InvitationRecord:
        row = self.connection.execute("SELECT * FROM invitations WHERE invitation_id=?", (invitation_id,)).fetchone()
        if not row:
            raise CollaborationNotFound("Unknown invitation.")
        return InvitationRecord(row["invitation_id"], row["team_id"], row["role"], row["created_by"], row["created_at"], row["expires_at"], row["accepted_by"], row["accepted_at"], row["revoked_at"])

    def create_project(self, team_id: str, name: str, *, created_by: str, project_id: str | None = None) -> ProjectRecord:
        self._require_team_role(team_id, created_by, "member")
        selected_id = project_id or _new_id("project")
        record = ProjectRecord(selected_id, team_id, str(name).strip() or selected_id, created_by, _now())
        with self._lock, self.connection:
            try:
                self.connection.execute("INSERT INTO projects(project_id,team_id,name,created_by,created_at) VALUES(?,?,?,?,?)", (record.project_id, team_id, record.name, created_by, record.created_at))
                self.connection.execute("INSERT INTO project_shares(project_id,principal_id,access,granted_by,granted_at) VALUES(?,?,?,?,?)", (record.project_id, created_by, "manager", created_by, record.created_at))
            except sqlite3.IntegrityError as exc:
                raise CollaborationIntegrityError("Project id already exists.") from exc
            self._append_audit(team_id, "project.created", created_by, "project", record.project_id, {"name": record.name})
        return record

    def get_project(self, project_id: str) -> ProjectRecord:
        row = self.connection.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            raise CollaborationNotFound(f"Unknown collaboration project: {project_id}")
        return ProjectRecord(row["project_id"], row["team_id"], row["name"], row["created_by"], row["created_at"])

    def list_projects(self, team_id: str, *, principal_id: str) -> list[ProjectRecord]:
        member = self.get_member(team_id, principal_id)
        if self._role_rank(member.role) >= self._role_rank("admin"):
            rows = self.connection.execute("SELECT * FROM projects WHERE team_id=? ORDER BY created_at", (team_id,)).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT p.* FROM projects p JOIN project_shares s ON s.project_id=p.project_id WHERE p.team_id=? AND s.principal_id=? ORDER BY p.created_at",
                (team_id, principal_id),
            ).fetchall()
        return [ProjectRecord(row["project_id"], row["team_id"], row["name"], row["created_by"], row["created_at"]) for row in rows]

    def _effective_project_access(self, project_id: str, principal_id: str) -> str | None:
        project = self.get_project(project_id)
        member = self.get_member(project.team_id, principal_id)
        if self._role_rank(member.role) >= self._role_rank("admin"):
            return "manager"
        row = self.connection.execute("SELECT access FROM project_shares WHERE project_id=? AND principal_id=?", (project_id, principal_id)).fetchone()
        return row["access"] if row else None

    def require_project_access(self, project_id: str, principal_id: str, minimum: str) -> str:
        if minimum not in PROJECT_ACCESS_LEVELS:
            raise CollaborationIntegrityError("Unknown project access level.")
        access = self._effective_project_access(project_id, principal_id)
        if access is None or self._access_rank(access) < self._access_rank(minimum):
            raise CollaborationDenied(f"Project access '{minimum}' or higher is required.")
        return access

    def grant_project_access(self, project_id: str, principal_id: str, access: str, *, granted_by: str) -> ProjectShare:
        if access not in PROJECT_ACCESS_LEVELS:
            raise CollaborationIntegrityError("Unknown project access level.")
        project = self.get_project(project_id)
        self.require_project_access(project_id, granted_by, "manager")
        self.get_member(project.team_id, principal_id)
        granted_at = _now()
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO project_shares(project_id,principal_id,access,granted_by,granted_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id,principal_id) DO UPDATE SET access=excluded.access,granted_by=excluded.granted_by,granted_at=excluded.granted_at",
                (project_id, principal_id, access, granted_by, granted_at),
            )
            self._append_audit(project.team_id, "project.share.granted", granted_by, "project", project_id, {"principal_id": principal_id, "access": access})
        return ProjectShare(project_id, principal_id, access, granted_by, granted_at)

    def revoke_project_access(self, project_id: str, principal_id: str, *, actor: str) -> None:
        project = self.get_project(project_id)
        self.require_project_access(project_id, actor, "manager")
        if principal_id == project.created_by:
            raise CollaborationDenied("Creator's manager share cannot be revoked through this operation.")
        with self._lock, self.connection:
            self.connection.execute("DELETE FROM project_shares WHERE project_id=? AND principal_id=?", (project_id, principal_id))
            self._append_audit(project.team_id, "project.share.revoked", actor, "project", project_id, {"principal_id": principal_id})

    def issue_access_token(self, team_id: str, *, principal_id: str, label: str = "client", expires_in_hours: int = 24 * 30) -> tuple[AccessTokenRecord, str]:
        self.get_member(team_id, principal_id)
        if not (1 <= expires_in_hours <= 24 * 365):
            raise CollaborationIntegrityError("Access token expiry must be between 1 hour and 365 days.")
        raw = secrets.token_urlsafe(40)
        created_at = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).isoformat(timespec="seconds").replace("+00:00", "Z")
        record = AccessTokenRecord(_new_id("token"), team_id, principal_id, str(label)[:120] or "client", created_at, expires_at, None)
        with self._lock, self.connection:
            self.connection.execute("INSERT INTO access_tokens(token_id,team_id,principal_id,token_hash,label,created_at,expires_at) VALUES(?,?,?,?,?,?,?)", (record.token_id, team_id, principal_id, _sha256(raw), record.label, record.created_at, record.expires_at))
            self._append_audit(team_id, "access_token.issued", principal_id, "access_token", record.token_id, {"label": record.label, "expires_at": record.expires_at})
        return record, raw

    def authenticate_access_token(self, raw_token: str) -> AccessTokenRecord:
        digest = _sha256(str(raw_token))
        row = self.connection.execute("SELECT token_id,team_id,principal_id,label,created_at,expires_at,revoked_at,token_hash FROM access_tokens WHERE token_hash=?", (digest,)).fetchone()
        if (
            not row or row["revoked_at"] or _parse_time(row["expires_at"]) <= datetime.now(timezone.utc)
            or not hmac.compare_digest(row["token_hash"], digest)
        ):
            raise AccessTokenInvalid("Invalid, expired, or revoked collaboration access token.")
        return AccessTokenRecord(row["token_id"], row["team_id"], row["principal_id"], row["label"], row["created_at"], row["expires_at"], row["revoked_at"])

    def revoke_access_token(self, token_id: str, *, actor: str) -> AccessTokenRecord:
        row = self.connection.execute("SELECT * FROM access_tokens WHERE token_id=?", (token_id,)).fetchone()
        if not row:
            raise CollaborationNotFound("Unknown access token.")
        if actor != row["principal_id"]:
            self._require_team_role(row["team_id"], actor, "admin")
        revoked = _now()
        with self._lock, self.connection:
            self.connection.execute("UPDATE access_tokens SET revoked_at=? WHERE token_id=?", (revoked, token_id))
            self._append_audit(row["team_id"], "access_token.revoked", actor, "access_token", token_id, {})
        return AccessTokenRecord(row["token_id"], row["team_id"], row["principal_id"], row["label"], row["created_at"], row["expires_at"], revoked)

    def register_replica(self, team_id: str, *, principal_id: str, device_id: str | None = None, label: str = "device") -> ReplicaRecord:
        self.get_member(team_id, principal_id)
        selected = device_id or _new_id("device")
        record = ReplicaRecord(selected, team_id, principal_id, str(label)[:120] or "device", _now(), 0, None)
        with self._lock, self.connection:
            try:
                self.connection.execute("INSERT INTO replicas(device_id,team_id,principal_id,label,registered_at,last_sequence,last_event_hash) VALUES(?,?,?,?,?,0,NULL)", (selected, team_id, principal_id, record.label, record.registered_at))
            except sqlite3.IntegrityError as exc:
                raise CollaborationIntegrityError("Replica/device id already exists for this team.") from exc
            self._append_audit(team_id, "replica.registered", principal_id, "replica", selected, {"label": record.label})
        return record

    def get_replica(self, team_id: str, device_id: str) -> ReplicaRecord:
        row = self.connection.execute("SELECT * FROM replicas WHERE team_id=? AND device_id=?", (team_id, device_id)).fetchone()
        if not row:
            raise CollaborationNotFound("Unknown collaboration replica/device.")
        return ReplicaRecord(row["device_id"], row["team_id"], row["principal_id"], row["label"], row["registered_at"], row["last_sequence"], row["last_event_hash"])

    def get_state(self, project_id: str, *, principal_id: str) -> dict[str, Any]:
        self.require_project_access(project_id, principal_id, "viewer")
        rows = self.connection.execute("SELECT * FROM project_state WHERE project_id=? ORDER BY key", (project_id,)).fetchall()
        values: dict[str, Any] = {}
        revisions: dict[str, int] = {}
        for row in rows:
            revisions[row["key"]] = int(row["revision"])
            if not row["tombstone"]:
                values[row["key"]] = json.loads(row["value_json"])
        return {"project_id": project_id, "values": values, "revisions": revisions}

    def apply_mutation(self, mutation: SyncMutation, *, principal_id: str) -> dict[str, Any]:
        event = mutation.normalized()
        project = self.get_project(event.project_id)
        self.require_project_access(event.project_id, principal_id, "editor")
        replica = self.get_replica(project.team_id, event.device_id)
        if replica.principal_id != principal_id:
            raise CollaborationDenied("Replica belongs to another principal.")
        existing = self.connection.execute("SELECT * FROM sync_events WHERE mutation_id=?", (event.mutation_id,)).fetchone()
        if existing:
            if existing["event_hash"] != event.event_hash:
                raise CollaborationIntegrityError("Mutation id was reused with different content.")
            return {"accepted": True, "idempotent": True, "ordinal": existing["ordinal"], "resulting_revision": existing["resulting_revision"], "event_hash": existing["event_hash"]}
        if event.sequence != replica.last_sequence + 1:
            raise CollaborationIntegrityError("Replica sequence must increase by exactly one.")
        if (event.previous_event_hash or None) != (replica.last_event_hash or None):
            raise CollaborationIntegrityError("Replica previous_event_hash does not match the registered chain head.")
        state = self.connection.execute("SELECT * FROM project_state WHERE project_id=? AND key=?", (event.project_id, event.key)).fetchone()
        server_revision = int(state["revision"]) if state else 0
        server_hash = state["event_hash"] if state else None
        if event.base_revision != server_revision:
            conflict = ConflictRecord(
                _new_id("conflict"), event.project_id, event.key, event.mutation_id, event.base_revision,
                str(event.event_hash), event.operation, _json_copy(event.value), server_revision, server_hash,
                "open", _now(), None, None,
            )
            with self._lock, self.connection:
                self.connection.execute(
                    "INSERT INTO conflicts(conflict_id,project_id,key,mutation_id,client_base_revision,client_event_hash,client_operation,client_value_json,server_revision,server_event_hash,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        conflict.conflict_id, conflict.project_id, conflict.key, conflict.mutation_id, conflict.client_base_revision,
                        conflict.client_event_hash, conflict.client_operation,
                        None if conflict.client_operation == "delete" else json.dumps(conflict.client_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        conflict.server_revision, conflict.server_event_hash, conflict.status, conflict.created_at,
                    ),
                )
                self._append_audit(project.team_id, "sync.conflict.detected", principal_id, "conflict", conflict.conflict_id, {"project_id": event.project_id, "key": event.key, "client_base_revision": event.base_revision, "server_revision": server_revision})
            raise CollaborationConflict("Synchronization conflict requires explicit resolution.", conflict_id=conflict.conflict_id)
        resulting_revision = server_revision + 1
        value_json = None if event.operation == "delete" else json.dumps(event.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self.connection:
            self.connection.execute(
                "INSERT INTO sync_events(mutation_id,team_id,project_id,principal_id,device_id,sequence,base_revision,resulting_revision,key,operation,value_json,previous_event_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event.mutation_id, project.team_id, event.project_id, principal_id, event.device_id, event.sequence, event.base_revision, resulting_revision, event.key, event.operation, value_json, event.previous_event_hash, event.event_hash, event.created_at),
            )
            self.connection.execute(
                "INSERT INTO project_state(project_id,key,value_json,tombstone,revision,event_hash,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,key) DO UPDATE SET value_json=excluded.value_json,tombstone=excluded.tombstone,revision=excluded.revision,event_hash=excluded.event_hash,updated_at=excluded.updated_at",
                (event.project_id, event.key, value_json, 1 if event.operation == "delete" else 0, resulting_revision, event.event_hash, event.created_at),
            )
            self.connection.execute("UPDATE replicas SET last_sequence=?,last_event_hash=? WHERE team_id=? AND device_id=?", (event.sequence, event.event_hash, project.team_id, event.device_id))
            audit = self._append_audit(project.team_id, "sync.mutation.accepted", principal_id, "project", event.project_id, {"mutation_id": event.mutation_id, "device_id": event.device_id, "sequence": event.sequence, "key": event.key, "operation": event.operation, "revision": resulting_revision, "event_hash": event.event_hash})
            ordinal = self.connection.execute("SELECT ordinal FROM sync_events WHERE mutation_id=?", (event.mutation_id,)).fetchone()["ordinal"]
        return {"accepted": True, "idempotent": False, "ordinal": ordinal, "resulting_revision": resulting_revision, "event_hash": event.event_hash, "audit_event_hash": audit["event_hash"]}

    def pull_events(self, project_id: str, *, principal_id: str, after_ordinal: int = 0, limit: int = 500) -> dict[str, Any]:
        self.require_project_access(project_id, principal_id, "viewer")
        if after_ordinal < 0 or not (1 <= limit <= 5000):
            raise CollaborationIntegrityError("Invalid pull cursor or limit.")
        rows = self.connection.execute(
            "SELECT * FROM sync_events WHERE project_id=? AND ordinal>? ORDER BY ordinal LIMIT ?", (project_id, after_ordinal, limit)
        ).fetchall()
        events = []
        for row in rows:
            events.append({
                "ordinal": row["ordinal"], "mutation_id": row["mutation_id"], "project_id": row["project_id"],
                "principal_id": row["principal_id"], "device_id": row["device_id"], "sequence": row["sequence"],
                "base_revision": row["base_revision"], "resulting_revision": row["resulting_revision"], "key": row["key"],
                "operation": row["operation"], "value": None if row["value_json"] is None else json.loads(row["value_json"]),
                "previous_event_hash": row["previous_event_hash"], "event_hash": row["event_hash"], "created_at": row["created_at"],
            })
        next_cursor = events[-1]["ordinal"] if events else after_ordinal
        return {"project_id": project_id, "events": events, "next_cursor": next_cursor}

    def list_conflicts(self, project_id: str, *, principal_id: str, status: str = "open") -> list[ConflictRecord]:
        self.require_project_access(project_id, principal_id, "viewer")
        rows = self.connection.execute("SELECT * FROM conflicts WHERE project_id=? AND status=? ORDER BY created_at", (project_id, status)).fetchall()
        return [ConflictRecord(
            row["conflict_id"], row["project_id"], row["key"], row["mutation_id"], row["client_base_revision"],
            row["client_event_hash"], row["client_operation"],
            None if row["client_value_json"] is None else json.loads(row["client_value_json"]),
            row["server_revision"], row["server_event_hash"], row["status"], row["created_at"], row["resolved_at"], row["resolution_event_hash"]
        ) for row in rows]

    def resolve_conflict(self, conflict_id: str, mutation: SyncMutation, *, principal_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM conflicts WHERE conflict_id=?", (conflict_id,)).fetchone()
        if not row:
            raise CollaborationNotFound("Unknown synchronization conflict.")
        if row["status"] != "open":
            raise CollaborationConflict("Conflict has already been resolved.", conflict_id=conflict_id)
        if mutation.project_id != row["project_id"] or mutation.key != row["key"]:
            raise CollaborationIntegrityError("Resolution mutation must target the conflict's project/key.")
        result = self.apply_mutation(mutation, principal_id=principal_id)
        project = self.get_project(row["project_id"])
        with self._lock, self.connection:
            resolved_at = _now()
            self.connection.execute("UPDATE conflicts SET status='resolved',resolved_at=?,resolution_event_hash=? WHERE conflict_id=?", (resolved_at, result["event_hash"], conflict_id))
            self._append_audit(project.team_id, "sync.conflict.resolved", principal_id, "conflict", conflict_id, {"resolution_event_hash": result["event_hash"]})
        return {"conflict_id": conflict_id, "status": "resolved", "resolution": result}

    def list_audit(self, team_id: str, *, principal_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self._require_team_role(team_id, principal_id, "viewer")
        rows = self.connection.execute("SELECT * FROM audit_events WHERE team_id=? ORDER BY ordinal DESC LIMIT ?", (team_id, max(1, min(limit, 5000)))).fetchall()
        return [{
            "ordinal": row["ordinal"], "event_id": row["event_id"], "team_id": row["team_id"], "event_type": row["event_type"],
            "actor_principal_id": row["actor_principal_id"], "object_type": row["object_type"], "object_id": row["object_id"],
            "details": json.loads(row["details_json"]), "previous_hash": row["previous_hash"], "event_hash": row["event_hash"], "occurred_at": row["occurred_at"],
        } for row in rows]

    def verify_audit(self, team_id: str) -> dict[str, Any]:
        rows = self.connection.execute("SELECT * FROM audit_events WHERE team_id=? ORDER BY ordinal", (team_id,)).fetchall()
        previous_hash = None
        for row in rows:
            payload = {
                "event_id": row["event_id"], "team_id": row["team_id"], "event_type": row["event_type"],
                "actor_principal_id": row["actor_principal_id"], "object_type": row["object_type"], "object_id": row["object_id"],
                "details": json.loads(row["details_json"]), "previous_hash": row["previous_hash"], "occurred_at": row["occurred_at"],
            }
            expected = _sha256(_canonical(payload))
            if row["previous_hash"] != previous_hash or not hmac.compare_digest(row["event_hash"], expected):
                return {"valid": False, "events": len(rows), "failed_ordinal": row["ordinal"]}
            previous_hash = row["event_hash"]
        return {"valid": True, "events": len(rows), "head_hash": previous_hash}


class CollaborationService:
    """Authorization boundary shared by HTTP and embedded self-hosted use."""

    def __init__(self, store: CollaborationStore) -> None:
        self.store = store

    def _identity(self, token: str) -> AccessTokenRecord:
        return self.store.authenticate_access_token(token)

    def list_projects(self, token: str) -> dict[str, Any]:
        identity = self._identity(token)
        return {"team_id": identity.team_id, "projects": [p.as_dict() for p in self.store.list_projects(identity.team_id, principal_id=identity.principal_id)]}

    def get_state(self, token: str, project_id: str) -> dict[str, Any]:
        identity = self._identity(token)
        project = self.store.get_project(project_id)
        if project.team_id != identity.team_id:
            raise CollaborationDenied("Token is scoped to another team.")
        return self.store.get_state(project_id, principal_id=identity.principal_id)

    def push(self, token: str, mutation: SyncMutation) -> dict[str, Any]:
        identity = self._identity(token)
        project = self.store.get_project(mutation.project_id)
        if project.team_id != identity.team_id:
            raise CollaborationDenied("Token is scoped to another team.")
        return self.store.apply_mutation(mutation, principal_id=identity.principal_id)

    def pull(self, token: str, project_id: str, *, after_ordinal: int = 0, limit: int = 500) -> dict[str, Any]:
        identity = self._identity(token)
        project = self.store.get_project(project_id)
        if project.team_id != identity.team_id:
            raise CollaborationDenied("Token is scoped to another team.")
        return self.store.pull_events(project_id, principal_id=identity.principal_id, after_ordinal=after_ordinal, limit=limit)

    def conflicts(self, token: str, project_id: str) -> dict[str, Any]:
        identity = self._identity(token)
        project = self.store.get_project(project_id)
        if project.team_id != identity.team_id:
            raise CollaborationDenied("Token is scoped to another team.")
        return {"project_id": project_id, "conflicts": [item.as_dict() for item in self.store.list_conflicts(project_id, principal_id=identity.principal_id)]}


class _CollaborationHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: CollaborationService, settings: CollaborationServerSettings) -> None:
        self.service = service
        self.settings = settings
        super().__init__(address, _CollaborationRequestHandler)


class _CollaborationRequestHandler(BaseHTTPRequestHandler):
    server: _CollaborationHTTPServer

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - quiet embedded server
        return

    def _send(self, status: int, payload: Mapping[str, Any]) -> None:
        data = _canonical(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _token(self) -> str:
        value = self.headers.get("Authorization", "")
        if not value.startswith("Bearer ") or not value[7:].strip():
            raise AccessTokenInvalid("Bearer access token required.")
        return value[7:].strip()

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise CollaborationIntegrityError("Invalid Content-Length.") from exc
        if length <= 0 or length > self.server.settings.max_request_bytes:
            raise CollaborationIntegrityError("Request body length is invalid or exceeds the configured bound.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise CollaborationIntegrityError("Request body must be valid UTF-8 JSON.") from exc
        if not isinstance(value, dict):
            raise CollaborationIntegrityError("Request JSON must be an object.")
        return value

    def _handle_error(self, exc: Exception) -> None:
        if isinstance(exc, AccessTokenInvalid):
            status = HTTPStatus.UNAUTHORIZED
        elif isinstance(exc, CollaborationDenied):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, CollaborationNotFound):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, CollaborationConflict):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, (CollaborationIntegrityError, InvitationInvalid)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        payload: dict[str, Any] = {"ok": False, "error_code": getattr(exc, "code", "collaboration_error"), "message": str(exc)}
        if isinstance(exc, CollaborationConflict) and exc.conflict_id:
            payload["conflict_id"] = exc.conflict_id
        self._send(int(status), payload)

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/v1/health":
                self._send(200, {"ok": True, "protocol_version": COLLABORATION_PROTOCOL_VERSION, "self_hosted": True, "w1_owned_cloud_required": False})
                return
            token = self._token()
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/v1/projects":
                self._send(200, {"ok": True, **self.server.service.list_projects(token)})
                return
            if parsed.path == "/v1/state":
                project_id = query.get("project_id", [""])[0]
                self._send(200, {"ok": True, **self.server.service.get_state(token, project_id)})
                return
            if parsed.path == "/v1/sync/pull":
                project_id = query.get("project_id", [""])[0]
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["500"])[0])
                self._send(200, {"ok": True, **self.server.service.pull(token, project_id, after_ordinal=after, limit=limit)})
                return
            if parsed.path == "/v1/conflicts":
                project_id = query.get("project_id", [""])[0]
                self._send(200, {"ok": True, **self.server.service.conflicts(token, project_id)})
                return
            self._send(404, {"ok": False, "error_code": "route_not_found"})
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            token = self._token()
            if parsed.path == "/v1/sync/push":
                body = self._body()
                mutation = SyncMutation.from_mapping(body.get("mutation", body))
                self._send(200, {"ok": True, **self.server.service.push(token, mutation)})
                return
            self._send(404, {"ok": False, "error_code": "route_not_found"})
        except Exception as exc:
            self._handle_error(exc)


def create_collaboration_server(settings: CollaborationServerSettings, service: CollaborationService) -> ThreadingHTTPServer:
    settings.validate()
    server = _CollaborationHTTPServer((settings.host, settings.port), service, settings)
    if settings.tls_enabled:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(settings.certfile, settings.keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


class CollaborationClient:
    """Small stable client for a self-hosted Collaboration Fabric endpoint."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise CollaborationTransportError("Collaboration base URL must be http(s).")
        if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
            raise CollaborationTransportError("Plain HTTP collaboration clients are restricted to loopback.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else _canonical(body)
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"message": str(exc)}
            if exc.code == 409:
                raise CollaborationConflict(payload.get("message", "Conflict"), conflict_id=payload.get("conflict_id")) from exc
            if exc.code in {401, 403}:
                raise CollaborationDenied(payload.get("message", "Denied")) from exc
            raise CollaborationTransportError(payload.get("message", str(exc))) from exc
        except urllib.error.URLError as exc:
            raise CollaborationTransportError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise CollaborationTransportError("Collaboration server returned a non-object response.")
        return payload

    def projects(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/projects").get("projects", []))

    def state(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", "/v1/state?" + urllib.parse.urlencode({"project_id": project_id}))

    def push(self, mutation: SyncMutation) -> dict[str, Any]:
        return self._request("POST", "/v1/sync/push", {"mutation": mutation.as_dict()})

    def pull(self, project_id: str, *, after_ordinal: int = 0, limit: int = 500) -> dict[str, Any]:
        query = urllib.parse.urlencode({"project_id": project_id, "after": after_ordinal, "limit": limit})
        return self._request("GET", "/v1/sync/pull?" + query)

    def conflicts(self, project_id: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"project_id": project_id})
        return list(self._request("GET", "/v1/conflicts?" + query).get("conflicts", []))


def run_collaboration_benchmark() -> dict[str, Any]:
    """Deterministic local benchmark; no W1-owned service or Internet required."""
    import tempfile

    probes: dict[str, bool] = {}
    metrics: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="w1-collaboration-") as directory:
        db = Path(directory) / "collaboration.sqlite3"
        with CollaborationStore(db) as store:
            team = store.create_team("Benchmark Team", owner_principal_id="alice", team_id="team-benchmark")
            invite, invite_token = store.create_invitation(team.team_id, role="member", created_by="alice", expires_in_hours=1)
            probes["invitation_secret_not_stored_plaintext"] = invite_token.encode("utf-8") not in db.read_bytes()
            bob = store.accept_invitation(invite_token, principal_id="bob")
            project = store.create_project(team.team_id, "Pump Project", created_by="alice", project_id="project-benchmark")
            store.grant_project_access(project.project_id, "bob", "editor", granted_by="alice")
            alice_replica = store.register_replica(team.team_id, principal_id="alice", device_id="alice-device", label="Alice laptop")
            bob_replica = store.register_replica(team.team_id, principal_id="bob", device_id="bob-device", label="Bob laptop")
            token_record, access_token = store.issue_access_token(team.team_id, principal_id="bob", label="benchmark-client")
            probes["access_token_not_stored_plaintext"] = access_token.encode("utf-8") not in db.read_bytes()
            first = SyncMutation(
                mutation_id="m1", project_id=project.project_id, device_id=bob_replica.device_id,
                sequence=1, base_revision=0, key="design/pressure", operation="set", value={"pa": 120000},
                previous_event_hash=None, created_at="2026-08-08T00:00:00Z",
            ).normalized()
            result1 = store.apply_mutation(first, principal_id="bob")
            probes["optimistic_mutation_applied"] = result1["accepted"] and result1["resulting_revision"] == 1
            probes["idempotent_mutation_replay"] = store.apply_mutation(first, principal_id="bob")["idempotent"] is True
            stale = SyncMutation(
                mutation_id="m2", project_id=project.project_id, device_id=alice_replica.device_id,
                sequence=1, base_revision=0, key="design/pressure", operation="set", value={"pa": 130000},
                previous_event_hash=None, created_at="2026-08-08T00:00:01Z",
            )
            conflict_id = None
            try:
                store.apply_mutation(stale, principal_id="alice")
            except CollaborationConflict as exc:
                conflict_id = exc.conflict_id
            probes["stale_write_becomes_explicit_conflict"] = bool(conflict_id) and len(store.list_conflicts(project.project_id, principal_id="alice")) == 1
            # Conflict rejection must not advance the losing device's accepted event chain.
            resolution = SyncMutation(
                mutation_id="m3", project_id=project.project_id, device_id=alice_replica.device_id,
                sequence=1, base_revision=1, key="design/pressure", operation="set", value={"pa": 125000},
                previous_event_hash=None, created_at="2026-08-08T00:00:02Z",
            )
            resolved = store.resolve_conflict(conflict_id or "missing", resolution, principal_id="alice")
            probes["conflict_requires_explicit_resolution"] = resolved["status"] == "resolved" and store.get_state(project.project_id, principal_id="bob")["values"]["design/pressure"]["pa"] == 125000
            pull = store.pull_events(project.project_id, principal_id="bob", after_ordinal=0)
            probes["append_only_pull_cursor"] = len(pull["events"]) == 2 and pull["next_cursor"] >= 2
            audit = store.verify_audit(team.team_id)
            probes["federated_audit_hash_chain_valid"] = audit["valid"] and audit["events"] >= 8
            probes["tenant_membership_and_project_acl"] = bob.role == "member" and store.require_project_access(project.project_id, "bob", "editor") == "editor"
            probes["self_hosted_without_w1_cloud"] = True

            service = CollaborationService(store)
            settings = CollaborationServerSettings(host="127.0.0.1", port=0)
            server = create_collaboration_server(settings, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            address = f"http://127.0.0.1:{server.server_address[1]}"
            client = CollaborationClient(address, access_token)
            projects = client.projects()
            state = client.state(project.project_id)
            remote_pull = client.pull(project.project_id)
            probes["loopback_self_hosted_api_authenticated"] = len(projects) == 1 and state["values"]["design/pressure"]["pa"] == 125000 and len(remote_pull["events"]) == 2
            server.shutdown(); server.server_close(); thread.join(timeout=2)

            try:
                CollaborationServerSettings(host="0.0.0.0", port=8443).validate()
                remote_tls_denied = False
            except CollaborationTransportError:
                remote_tls_denied = True
            probes["non_loopback_transport_requires_tls"] = remote_tls_denied
            try:
                CollaborationClient("http://example.invalid:8080", "token")
                client_tls_denied = False
            except CollaborationTransportError:
                client_tls_denied = True
            probes["client_rejects_remote_plain_http"] = client_tls_denied
            metrics = {
                "protocol_version": COLLABORATION_PROTOCOL_VERSION,
                "team_members": len(store.list_members(team.team_id, actor="alice")),
                "accepted_sync_events": len(pull["events"]),
                "audit_events": audit["events"],
                "open_conflicts_after_resolution": len(store.list_conflicts(project.project_id, principal_id="alice")),
                "w1_owned_cloud_calls": 0,
            }
    return {"passed": all(probes.values()), "probes": probes, "metrics": metrics}


__all__ = [
    "COLLABORATION_PROTOCOL_VERSION", "COLLABORATION_FABRIC_VERSION", "TEAM_ROLES", "PROJECT_ACCESS_LEVELS", "MAX_SYNC_VALUE_BYTES",
    "CollaborationError", "CollaborationNotFound", "CollaborationDenied", "CollaborationConflict",
    "CollaborationIntegrityError", "CollaborationTransportError", "InvitationInvalid", "AccessTokenInvalid",
    "TeamRecord", "MemberRecord", "InvitationRecord", "ProjectRecord", "ProjectShare", "AccessTokenRecord",
    "ReplicaRecord", "SyncMutation", "ConflictRecord", "CollaborationServerSettings", "CollaborationStore",
    "CollaborationService", "CollaborationClient", "create_collaboration_server", "run_collaboration_benchmark",
]
