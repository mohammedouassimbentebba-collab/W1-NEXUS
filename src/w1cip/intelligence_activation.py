"""Verified activation bridge for Intelligence Search candidates.

Step 50 closes the gap between *discovery* and *usable capacity* without adding a
W1-owned server. Search candidates remain inert until local evidence proves that
an existing user-controlled connection can actually see the candidate model.

The bridge is deliberately conservative:
- GitHub repositories are never activated as model capacity.
- Hugging Face repositories remain local deployment candidates only.
- OpenRouter free candidates may be activated only when an enabled OpenRouter
  connection already exists and its persisted ``discovered_models`` contains the
  exact catalog model id.
- Activation writes no raw credential values and routes the model as
  ``third_party_free`` with unknown quota. The default Capacity Router policy
  still excludes third-party-free capacity unless the user explicitly opts in.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .ai_connections import AIConnectionRecord, AIConnectionService, AIConnectionStore
from .capacity_router import CapacityObservation, CapacityStore
from .intelligence_search import IntelligenceSearchStore, SearchCandidate, utc_now
from .model_access import ModelAccessStore
from .session_store import canonical_json

INTELLIGENCE_ACTIVATION_VERSION = "1.0"
REVIEW_DECISIONS = {"approved", "rejected", "deferred"}
ASSESSMENT_STATES = {
    "activation_ready",
    "connection_required",
    "model_discovery_required",
    "manual_review_required",
    "local_runtime_required",
    "candidate_not_eligible",
}


@dataclass(frozen=True)
class CandidateAssessment:
    candidate_id: str
    source_id: str
    state: str
    activation_allowed: bool
    reason: str
    connection_id: str | None = None
    matching_connections: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    assessed_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.state not in ASSESSMENT_STATES:
            raise ValueError("candidate_assessment_state_invalid")
        if self.activation_allowed and self.state != "activation_ready":
            raise ValueError("candidate_assessment_activation_state_invalid")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateActivationRecord:
    candidate_id: str
    source_id: str
    status: str
    decision: str | None = None
    connection_id: str | None = None
    profile_id: str | None = None
    note: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in {"reviewed", "rejected", "deferred", "activated"}:
            raise ValueError("candidate_activation_status_invalid")
        if self.decision is not None and self.decision not in REVIEW_DECISIONS:
            raise ValueError("candidate_review_decision_invalid")
        if self.status == "activated" and (not self.connection_id or not self.profile_id):
            raise ValueError("candidate_activation_identity_required")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class IntelligenceActivationStore:
    """Workspace-local activation state plus an append-only hash-chained audit."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS intelligence_activation_state (
                    candidate_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intelligence_activation_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intelligence_activation_audit_candidate
                    ON intelligence_activation_audit(candidate_id, sequence);
                """
            )

    def put(self, record: CandidateActivationRecord, *, event_type: str) -> None:
        payload = canonical_json(record.as_dict())
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO intelligence_activation_state(candidate_id,payload_json,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(candidate_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                    (record.candidate_id, payload, record.updated_at),
                )
                row = connection.execute(
                    "SELECT event_hash FROM intelligence_activation_audit ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                previous = str(row["event_hash"]) if row else None
                details = {
                    "status": record.status,
                    "decision": record.decision,
                    "connection_id": record.connection_id,
                    "profile_id": record.profile_id,
                    "note_digest": hashlib.sha256(record.note.encode("utf-8")).hexdigest() if record.note else None,
                    "evidence": dict(record.evidence),
                }
                body = {
                    "event_type": event_type,
                    "candidate_id": record.candidate_id,
                    "details": details,
                    "previous_hash": previous,
                    "recorded_at": record.updated_at,
                }
                digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO intelligence_activation_audit(event_type,candidate_id,details_json,previous_hash,event_hash,recorded_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (event_type, record.candidate_id, canonical_json(details), previous, digest, record.updated_at),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get(self, candidate_id: str) -> CandidateActivationRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM intelligence_activation_state WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            return None
        return CandidateActivationRecord(**json.loads(row["payload_json"]))

    def list(self, *, limit: int = 100) -> list[CandidateActivationRecord]:
        cap = max(1, min(int(limit), 1000))
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM intelligence_activation_state ORDER BY updated_at DESC LIMIT ?", (cap,)
            ).fetchall()
        return [CandidateActivationRecord(**json.loads(row["payload_json"])) for row in rows]

    def verify_audit(self) -> bool:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT event_type,candidate_id,details_json,previous_hash,event_hash,recorded_at "
                "FROM intelligence_activation_audit ORDER BY sequence"
            ).fetchall()
        previous: str | None = None
        for row in rows:
            if row["previous_hash"] != previous:
                return False
            details = json.loads(row["details_json"])
            body = {
                "event_type": row["event_type"],
                "candidate_id": row["candidate_id"],
                "details": details,
                "previous_hash": previous,
                "recorded_at": row["recorded_at"],
            }
            expected = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
            if expected != row["event_hash"]:
                return False
            previous = row["event_hash"]
        return True


class IntelligenceActivationService:
    """Assess and activate inert search candidates using local evidence only."""

    def __init__(
        self,
        *,
        search_store: IntelligenceSearchStore,
        activation_store: IntelligenceActivationStore,
        connection_store: AIConnectionStore,
        model_store: ModelAccessStore,
        capacity_store: CapacityStore,
        connection_service: AIConnectionService | None = None,
    ) -> None:
        self.search_store = search_store
        self.activation_store = activation_store
        self.connection_store = connection_store
        self.model_store = model_store
        self.capacity_store = capacity_store
        self.connection_service = connection_service or AIConnectionService(connection_store, model_store)

    def _candidate(self, candidate_id: str) -> SearchCandidate:
        candidate = self.search_store.get(candidate_id)
        if candidate is None:
            raise ValueError("intelligence_candidate_not_found")
        return candidate

    def _openrouter_connections(self) -> list[AIConnectionRecord]:
        return [
            item for item in self.connection_store.list_connections()
            if item.provider_id == "openrouter" and item.enabled and item.status == "connected"
        ]

    def assess(self, candidate_id: str, *, connection_id: str | None = None) -> CandidateAssessment:
        candidate = self._candidate(candidate_id)
        if candidate.source_id == "github":
            return CandidateAssessment(
                candidate_id=candidate.candidate_id,
                source_id=candidate.source_id,
                state="manual_review_required",
                activation_allowed=False,
                reason="github_repository_is_not_model_capacity",
                evidence={"terms_status": candidate.terms_status, "review_status": candidate.review_status},
            )
        if candidate.source_id == "huggingface":
            return CandidateAssessment(
                candidate_id=candidate.candidate_id,
                source_id=candidate.source_id,
                state="local_runtime_required",
                activation_allowed=False,
                reason="model_repository_does_not_prove_local_install_or_hosted_entitlement",
                evidence={"model_id": candidate.model_id, "free_status": candidate.free_status},
            )
        if (
            candidate.source_id != "openrouter"
            or candidate.free_status != "verified_free"
            or candidate.review_status != "connectable"
            or not candidate.model_id
            or candidate.terms_status == "unknown"
        ):
            return CandidateAssessment(
                candidate_id=candidate.candidate_id,
                source_id=candidate.source_id,
                state="candidate_not_eligible",
                activation_allowed=False,
                reason="candidate_has_insufficient_activation_evidence",
                evidence={
                    "free_status": candidate.free_status,
                    "review_status": candidate.review_status,
                    "terms_status": candidate.terms_status,
                },
            )

        connections = self._openrouter_connections()
        if connection_id is not None:
            connections = [item for item in connections if item.connection_id == connection_id]
        if not connections:
            return CandidateAssessment(
                candidate_id=candidate.candidate_id,
                source_id=candidate.source_id,
                state="connection_required",
                activation_allowed=False,
                reason="enabled_openrouter_connection_required",
                matching_connections=(),
                evidence={"model_id": candidate.model_id, "raw_credentials_stored": False},
            )
        matching = tuple(sorted(
            item.connection_id for item in connections if candidate.model_id in set(item.discovered_models)
        ))
        if not matching:
            return CandidateAssessment(
                candidate_id=candidate.candidate_id,
                source_id=candidate.source_id,
                state="model_discovery_required",
                activation_allowed=False,
                reason="candidate_model_not_present_in_persisted_provider_discovery",
                matching_connections=tuple(sorted(item.connection_id for item in connections)),
                evidence={
                    "model_id": candidate.model_id,
                    "connections_checked": len(connections),
                    "live_network_call_performed": False,
                },
            )
        selected = connection_id if connection_id in matching else matching[0]
        return CandidateAssessment(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            state="activation_ready",
            activation_allowed=True,
            reason="catalog_free_marker_and_provider_model_discovery_match",
            connection_id=selected,
            matching_connections=matching,
            evidence={
                "model_id": candidate.model_id,
                "free_status": candidate.free_status,
                "terms_status": candidate.terms_status,
                "provider_discovery_match": True,
                "live_network_call_performed": False,
                "raw_credentials_stored": False,
            },
        )

    def review(self, candidate_id: str, *, decision: str, note: str = "") -> CandidateActivationRecord:
        if decision not in REVIEW_DECISIONS:
            raise ValueError("candidate_review_decision_invalid")
        candidate = self._candidate(candidate_id)
        status = {"approved": "reviewed", "rejected": "rejected", "deferred": "deferred"}[decision]
        record = CandidateActivationRecord(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            status=status,
            decision=decision,
            note=note[:2000],
            evidence={
                "manual_review_only": candidate.source_id in {"github", "huggingface"},
                "review_does_not_create_entitlement": True,
            },
        )
        self.activation_store.put(record, event_type="candidate.reviewed")
        return record

    @staticmethod
    def _profile_id(candidate: SearchCandidate) -> str:
        digest = hashlib.sha256(str(candidate.model_id).encode("utf-8")).hexdigest()[:16]
        return f"openrouter-free-{digest}"

    def activate(
        self,
        candidate_id: str,
        *,
        connection_id: str | None = None,
        profile_id: str | None = None,
    ) -> CandidateActivationRecord:
        candidate = self._candidate(candidate_id)
        assessment = self.assess(candidate_id, connection_id=connection_id)
        if not assessment.activation_allowed or not assessment.connection_id:
            raise ValueError(f"intelligence_candidate_not_activation_ready:{assessment.state}")
        model_profile_id = profile_id or self._profile_id(candidate)
        existing = {item.model_id for item in self.model_store.list_profiles()}
        if model_profile_id not in existing:
            self.connection_service.add_model(
                connection_id=assessment.connection_id,
                model_id=model_profile_id,
                model_name=str(candidate.model_id),
                display_name=candidate.title,
                roles=("producer",),
                domains=("general",),
                capabilities={"general": 0.65},
                metadata={
                    "capacity_source": "third_party_free",
                    "terms_status": candidate.terms_status,
                    "input_cost_per_million": 0.0,
                    "output_cost_per_million": 0.0,
                    "api_entitlement_verified": False,
                    "quota_state": "unknown",
                    "intelligence_candidate_id": candidate.candidate_id,
                    "intelligence_source": candidate.source_id,
                    "quality_unbenchmarked": True,
                    "activated_by": "intelligence_activation_v1",
                },
            )
        self.capacity_store.put(CapacityObservation(
            model_id=model_profile_id,
            source="third_party_free",
            quota_state="unknown",
            input_cost_per_million=0.0,
            output_cost_per_million=0.0,
            terms_status=candidate.terms_status,
            entitlement_verified=False,
            metadata={
                "intelligence_candidate_id": candidate.candidate_id,
                "provider_discovery_match": True,
                "default_router_opt_in_required": True,
                "raw_credentials_stored": False,
            },
        ))
        record = CandidateActivationRecord(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            status="activated",
            decision="approved",
            connection_id=assessment.connection_id,
            profile_id=model_profile_id,
            evidence={
                **dict(assessment.evidence),
                "capacity_source": "third_party_free",
                "quota_state": "unknown",
                "entitlement_verified": False,
                "default_router_opt_in_required": True,
            },
        )
        self.activation_store.put(record, event_type="candidate.activated")
        return record


def run_intelligence_activation_benchmark() -> dict[str, Any]:
    """Deterministic no-network probes for the Step 50 activation gate."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="w1-intelligence-activation-") as directory:
        root = Path(directory)
        search_store = IntelligenceSearchStore(root / "search.sqlite3")
        activation_store = IntelligenceActivationStore(root / "search.sqlite3")
        connection_store = AIConnectionStore(root / "connections.sqlite3")
        model_store = ModelAccessStore(root / "models.sqlite3")
        capacity_store = CapacityStore(root / "capacity.sqlite3")
        free = SearchCandidate(
            candidate_id="or-demo-free",
            source_id="openrouter",
            source_kind="hosted_model",
            title="Demo Free",
            url="https://openrouter.ai/demo/model:free",
            provider="OpenRouter",
            model_id="demo/model:free",
            free_status="verified_free",
            terms_status="verified_third_party",
            review_status="connectable",
            requires_auth=True,
            score=0.96,
            metadata={"entitlement_inferred": False},
        )
        github = SearchCandidate(
            candidate_id="gh-demo",
            source_id="github",
            source_kind="repository",
            title="Demo Gateway",
            url="https://github.com/demo/gateway",
            free_status="free_marker",
            terms_status="unknown",
            review_status="manual_review",
            score=0.7,
        )
        huggingface = SearchCandidate(
            candidate_id="hf-demo",
            source_id="huggingface",
            source_kind="open_model",
            title="Demo Weights",
            url="https://huggingface.co/demo/model",
            model_id="demo/model",
            free_status="open_weights_candidate",
            terms_status="verified_third_party",
            review_status="local_candidate",
            score=0.72,
        )
        search_store.put_many((free, github, huggingface))
        connection_store.put_connection(AIConnectionRecord(
            connection_id="openrouter-demo",
            provider_id="openrouter",
            display_name="OpenRouter Demo",
            auth_mode="api_key",
            credential_reference="w1-credential:benchmark-redacted",
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            status="connected",
            discovered_models=("demo/model:free",),
        ))
        service = IntelligenceActivationService(
            search_store=search_store,
            activation_store=activation_store,
            connection_store=connection_store,
            model_store=model_store,
            capacity_store=capacity_store,
        )
        ready = service.assess(free.candidate_id)
        gh = service.assess(github.candidate_id)
        hf = service.assess(huggingface.candidate_id)
        activated = service.activate(free.candidate_id)
        observation = capacity_store.get(str(activated.profile_id))
        profile = model_store.get_profile(str(activated.profile_id))
        probes = {
            "openrouter_requires_provider_discovery_match": ready.activation_allowed and ready.state == "activation_ready",
            "github_not_direct_capacity": gh.state == "manual_review_required" and not gh.activation_allowed,
            "huggingface_requires_local_runtime": hf.state == "local_runtime_required" and not hf.activation_allowed,
            "activation_creates_model_profile": profile.model_name == "demo/model:free",
            "activation_marks_third_party_free": bool(observation and observation.source == "third_party_free"),
            "quota_not_invented": bool(observation and observation.quota_state == "unknown"),
            "entitlement_not_invented": bool(observation and observation.entitlement_verified is False),
            "default_router_opt_in_required": bool(observation and observation.metadata.get("default_router_opt_in_required") is True),
            "raw_credential_not_persisted_in_activation_evidence": "benchmark-redacted" not in canonical_json(activated.as_dict()),
            "audit_chain_valid": activation_store.verify_audit(),
        }
        return {
            "passed": all(bool(value) for value in probes.values()),
            "probes": probes,
            "profile_id": activated.profile_id,
            "local_first": True,
            "w1_owned_server_required": False,
            "live_network_calls": 0,
        }
