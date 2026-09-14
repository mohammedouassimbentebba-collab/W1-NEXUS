"""Persistent append-only SessionStore for the W1-CIP reference runtime.

The normative source of truth is an immutable sequence of ProtocolEnvelope
records stored in SQLite.  Projection tables are rebuildable caches; they are
updated in the same transaction as each accepted envelope.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .validation import ProtocolReplayState, replay_protocol_log

GENESIS_HASH = "0" * 64
STORE_SCHEMA_VERSION = 1

EntityVersionKey = tuple[str, str, int]
EntityKey = tuple[str, str]
ReplayValidator = Callable[[list[dict[str, Any]]], tuple[ProtocolReplayState, list[str]]]


def canonical_json(value: Any) -> str:
    """Return the deterministic JSON representation used by the hash chain."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def chained_event_hash(previous_hash: str, envelope: dict[str, Any]) -> str:
    """Hash one canonical envelope together with the previous chain value."""

    try:
        previous = bytes.fromhex(previous_hash)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ValueError("previous_hash must be lowercase hexadecimal") from exc
    payload = previous + b"\n" + canonical_json(envelope).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SessionStoreError(RuntimeError):
    """Base class for stable SessionStore failures."""

    code = "session_store_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class SessionNotFoundError(SessionStoreError):
    code = "session_not_found"


class SequenceConflictError(SessionStoreError):
    code = "session_sequence_conflict"


class DuplicateMessageConflictError(SessionStoreError):
    code = "session_message_id_conflict"


class StoreIntegrityError(SessionStoreError):
    code = "session_store_integrity_error"

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(dict.fromkeys(errors))
        super().__init__(", ".join(self.errors) or self.code)


class StoreValidationError(SessionStoreError):
    code = "session_event_rejected"

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(dict.fromkeys(errors))
        super().__init__(", ".join(self.errors) or self.code)


@dataclass(frozen=True)
class AppendResult:
    session_id: str
    sequence: int
    message_id: str
    event_hash: str
    envelope: dict[str, Any]
    already_present: bool = False


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    last_sequence: int
    last_recorded_at: str | None
    last_event_hash: str
    event_count: int


@dataclass(frozen=True)
class IntegrityReport:
    session_id: str
    valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    last_sequence: int = 0
    last_event_hash: str = GENESIS_HASH
    replay_state: ProtocolReplayState | None = None


def _default_schema_directory() -> Path | None:
    packaged = Path(__file__).resolve().parent / "schemas" / "0.1"
    if packaged.is_dir():
        return packaged
    candidate = Path(__file__).resolve().parents[2] / "schemas" / "w1-cip" / "0.1"
    return candidate if candidate.is_dir() else None


def _build_envelope_validator(schema_dir: Path | None) -> Draft202012Validator | None:
    if schema_dir is None:
        return None
    envelope_path = schema_dir / "protocol-envelope.schema.json"
    if not envelope_path.is_file():
        return None

    registry = Registry()
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(schema_dir.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[path.name] = schema
        schema_id = schema.get("$id")
        if isinstance(schema_id, str):
            registry = registry.with_resource(schema_id, Resource.from_contents(schema))
    envelope_schema = schemas[envelope_path.name]
    return Draft202012Validator(
        envelope_schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


class SessionStore:
    """SQLite-backed append-only store with deterministic W1-CIP replay.

    SQLite provides process-safe write serialization through ``BEGIN IMMEDIATE``.
    The Python re-entrant lock additionally protects one shared connection from
    concurrent threads.  Every append performs the following in one transaction:

    1. reserve/check the next sequence;
    2. structurally validate the envelope;
    3. replay the existing log plus the candidate;
    4. append the immutable event and chain hash;
    5. replace rebuildable entity/effect projections;
    6. advance the session head.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        schema_dir: str | Path | None = None,
        replay_validator: ReplayValidator = replay_protocol_log,
        trusted_recorders: Iterable[tuple[str, str]] | None = None,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = Path(database_path)
        if str(database_path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._requested_trusted_recorders = frozenset(trusted_recorders or ())
        self._base_replay_validator = replay_validator
        selected_schema_dir = (
            Path(schema_dir) if schema_dir is not None else _default_schema_directory()
        )
        self._envelope_validator = _build_envelope_validator(selected_schema_dir)
        self._connection = sqlite3.connect(
            str(database_path),
            isolation_level=None,
            timeout=max(busy_timeout_ms / 1000, 0.001),
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._initialize_schema()
        self._trusted_recorders = self._load_and_persist_trusted_recorders()
        if replay_validator is replay_protocol_log:
            self._replay_validator = lambda envelopes: replay_protocol_log(
                envelopes, trusted_recorders=self._trusted_recorders
            )
        else:
            self._replay_validator = replay_validator

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for read-only diagnostics and controlled tests."""

        return self._connection

    def _initialize_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS store_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trusted_recorders (
            principal_type TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            PRIMARY KEY (principal_type, principal_id)
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            first_message_id TEXT NOT NULL,
            last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
            last_recorded_at TEXT,
            last_event_hash TEXT NOT NULL CHECK (length(last_event_hash) = 64)
        );

        CREATE TABLE IF NOT EXISTS events (
            session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 1),
            message_id TEXT NOT NULL,
            recorded_at TEXT,
            event_class TEXT,
            message_type TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            entity_version INTEGER,
            envelope_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL CHECK (length(previous_hash) = 64),
            event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
            PRIMARY KEY (session_id, sequence),
            UNIQUE (session_id, message_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS events_entity_idx
        ON events(session_id, entity_type, entity_id, entity_version);

        CREATE TABLE IF NOT EXISTS entity_versions (
            session_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_version INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            message_id TEXT NOT NULL,
            record_json TEXT NOT NULL,
            PRIMARY KEY (session_id, entity_type, entity_id, entity_version),
            FOREIGN KEY (session_id, sequence) REFERENCES events(session_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS latest_entities (
            session_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_version INTEGER NOT NULL,
            PRIMARY KEY (session_id, entity_type, entity_id),
            FOREIGN KEY (
                session_id, entity_type, entity_id, entity_version
            ) REFERENCES entity_versions(
                session_id, entity_type, entity_id, entity_version
            )
        );

        CREATE TABLE IF NOT EXISTS protocol_effects (
            session_id TEXT NOT NULL,
            event_entity_id TEXT NOT NULL,
            event_entity_version INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            subject_type TEXT,
            subject_id TEXT,
            subject_version INTEGER,
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            record_json TEXT NOT NULL,
            PRIMARY KEY (session_id, event_entity_id, event_entity_version)
        );

        CREATE INDEX IF NOT EXISTS protocol_effects_subject_idx
        ON protocol_effects(session_id, subject_type, subject_id, active);

        CREATE TRIGGER IF NOT EXISTS events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'append_only_events');
        END;

        CREATE TRIGGER IF NOT EXISTS events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'append_only_events');
        END;
        """
        with self._lock:
            self._connection.executescript(schema)
            row = self._connection.execute(
                "SELECT value FROM store_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO store_meta(key, value) VALUES('schema_version', ?)",
                    (str(STORE_SCHEMA_VERSION),),
                )
            elif int(row["value"]) != STORE_SCHEMA_VERSION:
                raise StoreIntegrityError(["session_store_schema_version_unsupported"])


    def _load_and_persist_trusted_recorders(self) -> frozenset[tuple[str, str]]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for principal_type, principal_id in sorted(self._requested_trusted_recorders):
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO trusted_recorders(principal_type, principal_id)
                        VALUES (?, ?)
                        """,
                        (principal_type, principal_id),
                    )
                rows = self._connection.execute(
                    "SELECT principal_type, principal_id FROM trusted_recorders"
                ).fetchall()
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return frozenset((row["principal_type"], row["principal_id"]) for row in rows)

    @property
    def trusted_recorders(self) -> frozenset[tuple[str, str]]:
        return self._trusted_recorders

    def _structural_errors(self, envelope: dict[str, Any]) -> list[str]:
        if self._envelope_validator is None:
            return []
        errors: list[str] = []
        for error in sorted(self._envelope_validator.iter_errors(envelope), key=lambda e: list(e.path)):
            location = ".".join(str(item) for item in error.absolute_path) or "$"
            errors.append(f"protocol_envelope_schema_invalid:{location}")
        return errors

    def _load_envelopes_locked(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT envelope_json FROM events WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        ).fetchall()
        return [json.loads(row["envelope_json"]) for row in rows]

    def _existing_message_locked(
        self, session_id: str, message_id: str
    ) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT sequence, envelope_json, event_hash
            FROM events
            WHERE session_id = ? AND message_id = ?
            """,
            (session_id, message_id),
        ).fetchone()

    @staticmethod
    def _event_entity(envelope: dict[str, Any]) -> tuple[str | None, str | None, int | None]:
        entity = envelope.get("entity")
        if not isinstance(entity, dict):
            return None, None, None
        entity_type = entity.get("entity_type")
        entity_id = entity.get("entity_id")
        entity_version = entity.get("entity_version")
        return (
            entity_type if isinstance(entity_type, str) else None,
            entity_id if isinstance(entity_id, str) else None,
            entity_version if isinstance(entity_version, int) else None,
        )

    def append(
        self,
        envelope: dict[str, Any],
        *,
        expected_last_sequence: int | None = None,
        assign_sequence: bool = True,
    ) -> AppendResult:
        """Atomically append one event and advance all rebuildable projections.

        ``expected_last_sequence`` is an optimistic concurrency precondition.
        When ``assign_sequence`` is true, a missing sequence is assigned inside
        the write transaction.  A supplied sequence must still equal the next
        sequence, preventing callers from creating gaps or branches.
        """

        candidate = deepcopy(envelope)
        session_id = candidate.get("session_id")
        message_id = candidate.get("message_id")
        if not isinstance(session_id, str) or not session_id:
            raise StoreValidationError(["session_id_required"])
        if not isinstance(message_id, str) or not message_id:
            raise StoreValidationError(["message_id_required"])
        if candidate.get("kind") != "event":
            raise StoreValidationError(["session_store_accepts_events_only"])
        if not self._trusted_recorders:
            raise StoreValidationError(["session_store_trusted_recorder_not_configured"])

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                session = self._connection.execute(
                    "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                last_sequence = int(session["last_sequence"]) if session else 0
                last_hash = str(session["last_event_hash"]) if session else GENESIS_HASH

                existing = self._existing_message_locked(session_id, message_id)
                if existing is not None:
                    persisted = json.loads(existing["envelope_json"])
                    if "sequence" not in candidate and assign_sequence:
                        candidate["sequence"] = persisted.get("sequence")
                    if canonical_json(candidate) != canonical_json(persisted):
                        raise DuplicateMessageConflictError()
                    self._connection.execute("ROLLBACK")
                    return AppendResult(
                        session_id=session_id,
                        sequence=int(existing["sequence"]),
                        message_id=message_id,
                        event_hash=str(existing["event_hash"]),
                        envelope=persisted,
                        already_present=True,
                    )

                if expected_last_sequence is not None and last_sequence != expected_last_sequence:
                    raise SequenceConflictError(
                        f"expected {expected_last_sequence}, current {last_sequence}"
                    )

                next_sequence = last_sequence + 1
                supplied_sequence = candidate.get("sequence")
                if supplied_sequence is None and assign_sequence:
                    candidate["sequence"] = next_sequence
                elif supplied_sequence != next_sequence:
                    raise SequenceConflictError(
                        f"expected next sequence {next_sequence}, got {supplied_sequence}"
                    )

                structural_errors = self._structural_errors(candidate)
                if structural_errors:
                    raise StoreValidationError(structural_errors)

                prior = self._load_envelopes_locked(session_id)
                projection, replay_errors = self._replay_validator(prior + [candidate])
                if replay_errors:
                    raise StoreValidationError(replay_errors)

                event_hash = chained_event_hash(last_hash, candidate)
                recorded_at = candidate.get("recorded_at")
                created_at = recorded_at if isinstance(recorded_at, str) else ""
                if session is None:
                    self._connection.execute(
                        """
                        INSERT INTO sessions(
                            session_id, created_at, first_message_id,
                            last_sequence, last_recorded_at, last_event_hash
                        ) VALUES (?, ?, ?, 0, NULL, ?)
                        """,
                        (session_id, created_at, message_id, GENESIS_HASH),
                    )

                entity_type, entity_id, entity_version = self._event_entity(candidate)
                self._connection.execute(
                    """
                    INSERT INTO events(
                        session_id, sequence, message_id, recorded_at,
                        event_class, message_type, entity_type, entity_id,
                        entity_version, envelope_json, previous_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        next_sequence,
                        message_id,
                        recorded_at if isinstance(recorded_at, str) else None,
                        candidate.get("event_class"),
                        candidate.get("type"),
                        entity_type,
                        entity_id,
                        entity_version,
                        canonical_json(candidate),
                        last_hash,
                        event_hash,
                    ),
                )
                self._replace_projection_locked(session_id, projection)
                self._connection.execute(
                    """
                    UPDATE sessions
                    SET last_sequence = ?, last_recorded_at = ?, last_event_hash = ?
                    WHERE session_id = ?
                    """,
                    (
                        next_sequence,
                        recorded_at if isinstance(recorded_at, str) else None,
                        event_hash,
                        session_id,
                    ),
                )
                self._connection.execute("COMMIT")
                return AppendResult(
                    session_id=session_id,
                    sequence=next_sequence,
                    message_id=message_id,
                    event_hash=event_hash,
                    envelope=candidate,
                )
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def append_many(
        self,
        envelopes: Iterable[dict[str, Any]],
        *,
        expected_last_sequence: int | None = None,
    ) -> list[AppendResult]:
        """Append a batch serially using optimistic continuity.

        Each event is its own durable transaction.  This intentionally does not
        claim all-or-nothing semantics for an arbitrary multi-event batch; the
        individual envelope remains the protocol transaction boundary in v0.1.
        """

        results: list[AppendResult] = []
        expected = expected_last_sequence
        for envelope in envelopes:
            result = self.append(envelope, expected_last_sequence=expected)
            results.append(result)
            expected = result.sequence
        return results

    def _replace_projection_locked(
        self, session_id: str, projection: ProtocolReplayState
    ) -> None:
        self._connection.execute(
            "DELETE FROM latest_entities WHERE session_id = ?", (session_id,)
        )
        self._connection.execute(
            "DELETE FROM entity_versions WHERE session_id = ?", (session_id,)
        )
        self._connection.execute(
            "DELETE FROM protocol_effects WHERE session_id = ?", (session_id,)
        )

        sequence_by_message = {
            row["message_id"]: int(row["sequence"])
            for row in self._connection.execute(
                "SELECT message_id, sequence FROM events WHERE session_id = ?",
                (session_id,),
            )
        }
        for key, record in sorted(projection.records.items()):
            entity_type, entity_id, version = key
            message_id = record.get("created_by_event_id")
            sequence = sequence_by_message.get(message_id)
            if sequence is None:
                raise StoreIntegrityError(["projection_creator_event_missing"])
            self._connection.execute(
                """
                INSERT INTO entity_versions(
                    session_id, entity_type, entity_id, entity_version,
                    sequence, message_id, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    entity_type,
                    entity_id,
                    version,
                    sequence,
                    message_id,
                    canonical_json(record),
                ),
            )
        for (entity_type, entity_id), version in sorted(projection.latest_versions.items()):
            self._connection.execute(
                """
                INSERT INTO latest_entities(
                    session_id, entity_type, entity_id, entity_version
                ) VALUES (?, ?, ?, ?)
                """,
                (session_id, entity_type, entity_id, version),
            )

        all_protocol_events = {
            key: record
            for key, record in projection.records.items()
            if key[0] == "protocol_event"
        }
        for key, record in sorted(all_protocol_events.items()):
            _, event_id, event_version = key
            payload = record.get("legal_payload", {})
            subject = payload.get("subject", {})
            active = int(key in projection.active_protocol_events)
            self._connection.execute(
                """
                INSERT INTO protocol_effects(
                    session_id, event_entity_id, event_entity_version,
                    event_type, subject_type, subject_id, subject_version,
                    active, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event_id,
                    event_version,
                    payload.get("event_type"),
                    subject.get("entity_type"),
                    subject.get("entity_id"),
                    subject.get("entity_version"),
                    active,
                    canonical_json(record),
                ),
            )

    def get_events(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT envelope_json FROM events
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence
        """
        parameters: list[Any] = [session_id, after_sequence]
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must be non-negative")
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [json.loads(row["envelope_json"]) for row in rows]

    def get_event(self, session_id: str, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT envelope_json FROM events WHERE session_id = ? AND message_id = ?",
                (session_id, message_id),
            ).fetchone()
        return json.loads(row["envelope_json"]) if row is not None else None

    def get_entity(
        self,
        session_id: str,
        entity_type: str,
        entity_id: str,
        entity_version: int | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if entity_version is None:
                row = self._connection.execute(
                    """
                    SELECT v.record_json
                    FROM latest_entities l
                    JOIN entity_versions v
                      ON v.session_id = l.session_id
                     AND v.entity_type = l.entity_type
                     AND v.entity_id = l.entity_id
                     AND v.entity_version = l.entity_version
                    WHERE l.session_id = ? AND l.entity_type = ? AND l.entity_id = ?
                    """,
                    (session_id, entity_type, entity_id),
                ).fetchone()
            else:
                row = self._connection.execute(
                    """
                    SELECT record_json FROM entity_versions
                    WHERE session_id = ? AND entity_type = ?
                      AND entity_id = ? AND entity_version = ?
                    """,
                    (session_id, entity_type, entity_id, entity_version),
                ).fetchone()
        return json.loads(row["record_json"]) if row is not None else None

    def get_active_protocol_effects(
        self,
        session_id: str,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT record_json FROM protocol_effects
            WHERE session_id = ? AND active = 1
        """
        parameters: list[Any] = [session_id]
        if subject_type is not None:
            sql += " AND subject_type = ?"
            parameters.append(subject_type)
        if subject_id is not None:
            sql += " AND subject_id = ?"
            parameters.append(subject_id)
        sql += " ORDER BY event_entity_id, event_entity_version"
        with self._lock:
            rows = self._connection.execute(sql, parameters).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def get_summary(self, session_id: str) -> SessionSummary:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT session_id, last_sequence, last_recorded_at, last_event_hash
                FROM sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise SessionNotFoundError()
        return SessionSummary(
            session_id=row["session_id"],
            last_sequence=int(row["last_sequence"]),
            last_recorded_at=row["last_recorded_at"],
            last_event_hash=row["last_event_hash"],
            event_count=int(row["last_sequence"]),
        )

    def list_sessions(self) -> list[SessionSummary]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT session_id, last_sequence, last_recorded_at, last_event_hash
                FROM sessions ORDER BY session_id
                """
            ).fetchall()
        return [
            SessionSummary(
                session_id=row["session_id"],
                last_sequence=int(row["last_sequence"]),
                last_recorded_at=row["last_recorded_at"],
                last_event_hash=row["last_event_hash"],
                event_count=int(row["last_sequence"]),
            )
            for row in rows
        ]

    def replay(self, session_id: str) -> ProtocolReplayState:
        envelopes = self.get_events(session_id)
        if not envelopes and not any(s.session_id == session_id for s in self.list_sessions()):
            raise SessionNotFoundError()
        state, errors = self._replay_validator(envelopes)
        if errors:
            raise StoreIntegrityError(errors)
        return state

    def verify_integrity(self, session_id: str) -> IntegrityReport:
        """Verify chain hashes, session head, deterministic replay and projections."""

        errors: list[str] = []
        with self._lock:
            session = self._connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise SessionNotFoundError()
            rows = self._connection.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()

            previous_hash = GENESIS_HASH
            envelopes: list[dict[str, Any]] = []
            for expected, row in enumerate(rows, start=1):
                if int(row["sequence"]) != expected:
                    errors.append("session_store_sequence_gap")
                if row["previous_hash"] != previous_hash:
                    errors.append("session_store_previous_hash_mismatch")
                try:
                    envelope = json.loads(row["envelope_json"])
                except json.JSONDecodeError:
                    errors.append("session_store_invalid_envelope_json")
                    continue
                if envelope.get("sequence") != expected:
                    errors.append("session_store_envelope_sequence_mismatch")
                if envelope.get("message_id") != row["message_id"]:
                    errors.append("session_store_message_id_mismatch")
                computed = chained_event_hash(previous_hash, envelope)
                if computed != row["event_hash"]:
                    errors.append("session_store_event_hash_mismatch")
                previous_hash = str(row["event_hash"])
                envelopes.append(envelope)

            if int(session["last_sequence"]) != len(rows):
                errors.append("session_store_head_sequence_mismatch")
            if str(session["last_event_hash"]) != previous_hash:
                errors.append("session_store_head_hash_mismatch")

            projection, replay_errors = self._replay_validator(envelopes)
            errors.extend(replay_errors)
            if not replay_errors:
                db_latest = {
                    (row["entity_type"], row["entity_id"]): int(row["entity_version"])
                    for row in self._connection.execute(
                        """
                        SELECT entity_type, entity_id, entity_version
                        FROM latest_entities WHERE session_id = ?
                        """,
                        (session_id,),
                    )
                }
                if db_latest != projection.latest_versions:
                    errors.append("session_store_latest_projection_mismatch")
                db_records = {
                    (row["entity_type"], row["entity_id"], int(row["entity_version"])):
                    json.loads(row["record_json"])
                    for row in self._connection.execute(
                        """
                        SELECT entity_type, entity_id, entity_version, record_json
                        FROM entity_versions WHERE session_id = ?
                        """,
                        (session_id,),
                    )
                }
                if db_records != projection.records:
                    errors.append("session_store_entity_projection_mismatch")
                active_keys = {
                    ("protocol_event", row["event_entity_id"], int(row["event_entity_version"]))
                    for row in self._connection.execute(
                        """
                        SELECT event_entity_id, event_entity_version
                        FROM protocol_effects
                        WHERE session_id = ? AND active = 1
                        """,
                        (session_id,),
                    )
                }
                if active_keys != set(projection.active_protocol_events):
                    errors.append("session_store_effect_projection_mismatch")

        unique_errors = tuple(dict.fromkeys(errors))
        return IntegrityReport(
            session_id=session_id,
            valid=not unique_errors,
            errors=unique_errors,
            last_sequence=len(rows),
            last_event_hash=previous_hash,
            replay_state=projection if not replay_errors else None,
        )

    def rebuild_projections(self, session_id: str) -> ProtocolReplayState:
        """Rebuild mutable caches from the immutable event ledger."""

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                session = self._connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                if session is None:
                    raise SessionNotFoundError()
                envelopes = self._load_envelopes_locked(session_id)
                projection, errors = self._replay_validator(envelopes)
                if errors:
                    raise StoreIntegrityError(errors)
                self._replace_projection_locked(session_id, projection)
                self._connection.execute("COMMIT")
                return projection
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def checkpoint(self, *, truncate: bool = False) -> tuple[int, int, int]:
        """Run a WAL checkpoint and return SQLite's checkpoint tuple."""

        mode = "TRUNCATE" if truncate else "PASSIVE"
        with self._lock:
            row = self._connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        return tuple(int(value) for value in row)
