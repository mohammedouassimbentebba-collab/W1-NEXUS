"""Adaptive capacity and cost routing for cooperative W1 Nexus AI teams.

The router does not replace multi-model collaboration.  It selects eligible
models for each collaboration role while preserving quality floors, provider
provenance, quota state, privacy, and user budget.  It deliberately refuses to
assume that a consumer app subscription grants API entitlement.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .model_access import ModelPortfolio, ModelProfile, PortfolioMember
from .orchestrator import CompiledTask
from .session_store import canonical_json

CAPACITY_ROUTER_VERSION = "1.0"
CAPACITY_SOURCES = {
    "official_free",
    "subscription_entitlement",
    "promotional",
    "third_party_free",
    "paid",
    "local",
}
QUOTA_STATES = {"unknown", "available", "low", "exhausted", "unlimited"}
TERMS_STATUSES = {"official", "verified_third_party", "unknown", "blocked"}
ROUTING_MODES = {"maximum_quality", "balanced", "maximum_free", "local_only", "custom_budget"}
TEAM_MODES = {"solo", "fallback", "parallel", "verify", "challenge"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CapacityRoutingError(RuntimeError):
    code = "capacity_routing_error"


class CapacityRoutingConfigurationError(CapacityRoutingError):
    code = "capacity_routing_configuration_invalid"


class CapacityRoutingUnavailable(CapacityRoutingError):
    code = "capacity_routing_unavailable"


@dataclass(frozen=True)
class CapacityObservation:
    model_id: str
    source: str
    quota_state: str = "unknown"
    remaining_tokens: int | None = None
    reset_at: str | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    terms_status: str = "official"
    entitlement_verified: bool = False
    observed_at: str = field(default_factory=utc_now)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id:
            raise CapacityRoutingConfigurationError("capacity_model_id_required")
        if self.source not in CAPACITY_SOURCES:
            raise CapacityRoutingConfigurationError("capacity_source_invalid")
        if self.quota_state not in QUOTA_STATES:
            raise CapacityRoutingConfigurationError("capacity_quota_state_invalid")
        if self.terms_status not in TERMS_STATUSES:
            raise CapacityRoutingConfigurationError("capacity_terms_status_invalid")
        if self.remaining_tokens is not None and self.remaining_tokens < 0:
            raise CapacityRoutingConfigurationError("capacity_remaining_tokens_invalid")
        if self.input_cost_per_million < 0 or self.output_cost_per_million < 0:
            raise CapacityRoutingConfigurationError("capacity_cost_invalid")

    @property
    def free_like(self) -> bool:
        return self.source in {"official_free", "subscription_entitlement", "promotional", "third_party_free", "local"} and self.input_cost_per_million == 0 and self.output_cost_per_million == 0

    def estimated_cost(self, *, input_tokens: int, output_tokens: int) -> float:
        return (
            (max(0, input_tokens) / 1_000_000) * self.input_cost_per_million
            + (max(0, output_tokens) / 1_000_000) * self.output_cost_per_million
        )

    def recommended_refresh_seconds(self) -> int:
        """Adaptive telemetry refresh interval that avoids wasteful provider polling."""
        if self.source == "local" or self.quota_state == "unlimited":
            return 1800
        if self.quota_state == "low":
            return 60
        if self.quota_state == "exhausted":
            return 300
        if self.quota_state == "available":
            return 900
        return 300

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoutingPolicy:
    mode: str = "balanced"
    quality_floor: float = 0.65
    max_task_cost_usd: float | None = None
    estimated_input_tokens: int = 2500
    estimated_output_tokens: int = 1500
    producer_count: int = 2
    fallback_per_role: int = 1
    prefer_provider_diversity: bool = True
    allow_paid: bool = True
    allow_local: bool = True
    allow_promotional: bool = True
    allow_subscription_entitlement: bool = True
    allow_third_party_free: bool = False
    require_known_terms: bool = True
    reserve_tokens: int = 512

    def __post_init__(self) -> None:
        if self.mode not in ROUTING_MODES:
            raise CapacityRoutingConfigurationError("routing_mode_invalid")
        if not 0 <= self.quality_floor <= 1:
            raise CapacityRoutingConfigurationError("routing_quality_floor_invalid")
        if self.max_task_cost_usd is not None and self.max_task_cost_usd < 0:
            raise CapacityRoutingConfigurationError("routing_budget_invalid")
        if self.estimated_input_tokens < 0 or self.estimated_output_tokens < 0:
            raise CapacityRoutingConfigurationError("routing_token_estimate_invalid")
        if self.producer_count < 1 or self.fallback_per_role < 0:
            raise CapacityRoutingConfigurationError("routing_role_limits_invalid")


@dataclass(frozen=True)
class RoutedCandidate:
    model_id: str
    provider_id: str
    role: str
    quality: float
    route_score: float
    estimated_cost_usd: float
    capacity_source: str
    quota_state: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoleRoute:
    role: str
    selected: tuple[RoutedCandidate, ...]
    rejected: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "selected": [item.as_dict() for item in self.selected],
            "rejected": [{"model_id": model_id, "reason": reason} for model_id, reason in self.rejected],
        }


@dataclass(frozen=True)
class AdaptiveCapacityPlan:
    plan_id: str
    team_mode: str
    policy: RoutingPolicy
    roles: tuple[RoleRoute, ...]
    portfolio: ModelPortfolio
    estimated_cost_usd: float
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "team_mode": self.team_mode,
            "policy": asdict(self.policy),
            "roles": [route.as_dict() for route in self.roles],
            "portfolio": asdict(self.portfolio),
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "warnings": list(self.warnings),
        }


class CapacityTelemetryAdapter(Protocol):
    """Provider-specific quota telemetry adapter; implementations may be local or remote."""

    adapter_id: str

    def observe(self, profile: ModelProfile) -> CapacityObservation | None:
        ...


class CapacityTelemetryRegistry:
    """Composable telemetry adapters with no default network calls."""

    def __init__(self, adapters: Iterable[CapacityTelemetryAdapter] = ()) -> None:
        self.adapters = tuple(adapters)

    def collect(self, profiles: Iterable[ModelProfile], store: "CapacityStore") -> dict[str, Any]:
        observed = 0
        errors: list[dict[str, str]] = []
        for profile in profiles:
            for adapter in self.adapters:
                try:
                    observation = adapter.observe(profile)
                except Exception as exc:  # adapter boundary: telemetry must not break orchestration
                    errors.append({"adapter_id": getattr(adapter, "adapter_id", "unknown"), "model_id": profile.model_id, "error": type(exc).__name__})
                    continue
                if observation is not None:
                    store.put(observation)
                    observed += 1
                    break
        return {"observed": observed, "errors": errors, "adapter_count": len(self.adapters)}


class CapacityStore:
    """Persistent redaction-safe quota/cost observations; never stores credentials."""

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
                CREATE TABLE IF NOT EXISTS capacity_observations (
                    model_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def put(self, observation: CapacityObservation) -> None:
        payload = canonical_json(observation.as_dict())
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO capacity_observations(model_id,payload_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(model_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                (observation.model_id, payload, utc_now()),
            )

    def get(self, model_id: str) -> CapacityObservation | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capacity_observations WHERE model_id=?", (model_id,)
            ).fetchone()
        return CapacityObservation(**json.loads(row["payload_json"])) if row else None

    def list(self) -> list[CapacityObservation]:
        with self._connection() as connection:
            rows = connection.execute("SELECT payload_json FROM capacity_observations ORDER BY model_id").fetchall()
        return [CapacityObservation(**json.loads(row["payload_json"])) for row in rows]


class AdaptiveCapacityRouter:
    """Select role-specific models without removing the collaboration topology."""

    def __init__(self, observations: Mapping[str, CapacityObservation] | None = None) -> None:
        self.observations = dict(observations or {})

    @classmethod
    def from_store(cls, store: CapacityStore) -> "AdaptiveCapacityRouter":
        return cls({item.model_id: item for item in store.list()})

    def observation_for(self, profile: ModelProfile) -> CapacityObservation:
        explicit = self.observations.get(profile.model_id)
        if explicit is not None:
            return explicit
        meta = dict(profile.metadata)
        source = str(meta.get("capacity_source") or ("local" if profile.access_mode == "local_endpoint" else "paid"))
        if source not in CAPACITY_SOURCES:
            source = "paid"
        default_quota = "unlimited" if source == "local" else "unknown"
        return CapacityObservation(
            model_id=profile.model_id,
            source=source,
            quota_state=str(meta.get("quota_state") or default_quota),
            remaining_tokens=(int(meta["remaining_tokens"]) if meta.get("remaining_tokens") is not None else None),
            reset_at=str(meta.get("quota_reset_at") or "") or None,
            input_cost_per_million=float(meta.get("input_cost_per_million", 0.0)),
            output_cost_per_million=float(meta.get("output_cost_per_million", 0.0)),
            terms_status=str(meta.get("terms_status") or ("official" if source != "third_party_free" else "unknown")),
            entitlement_verified=bool(meta.get("api_entitlement_verified", False)),
            metadata={"derived_from_profile": True},
        )

    @staticmethod
    def _quality(profile: ModelProfile, task: CompiledTask, role: str) -> float:
        keys = set(task.domains) | {task.phase, task.role, "general"}
        values = [float(profile.capabilities.get(key, 0.0)) for key in keys]
        capability_quality = max(values) if values else 0.0
        role_capability = float(profile.capabilities.get(role, 0.0))
        role_bonus = 0.12 if role in profile.roles else 0.0
        domain_bonus = min(0.08, 0.04 * len(set(task.domains) & set(profile.domains)))
        quality = max(capability_quality, role_capability) + role_bonus + domain_bonus
        return round(min(1.0, quality), 6)

    def _eligible(
        self,
        profile: ModelProfile,
        observation: CapacityObservation,
        *,
        role: str,
        quality: float,
        policy: RoutingPolicy,
    ) -> tuple[bool, str | None, float]:
        if observation.terms_status == "blocked":
            return False, "terms_blocked", 0.0
        if policy.require_known_terms and observation.terms_status == "unknown":
            return False, "terms_unknown", 0.0
        if observation.source == "third_party_free" and not policy.allow_third_party_free:
            return False, "third_party_free_not_allowed", 0.0
        if observation.source == "promotional" and not policy.allow_promotional:
            return False, "promotional_not_allowed", 0.0
        if observation.source == "subscription_entitlement":
            if not policy.allow_subscription_entitlement:
                return False, "subscription_entitlement_not_allowed", 0.0
            if not observation.entitlement_verified:
                return False, "subscription_does_not_prove_api_entitlement", 0.0
        if observation.source == "paid" and not policy.allow_paid:
            return False, "paid_not_allowed", 0.0
        if observation.source == "local" and not policy.allow_local:
            return False, "local_not_allowed", 0.0
        if policy.mode == "local_only" and observation.source != "local":
            return False, "local_only_policy", 0.0
        if observation.quota_state == "exhausted":
            return False, "quota_exhausted", 0.0
        required_tokens = policy.estimated_input_tokens + policy.estimated_output_tokens + policy.reserve_tokens
        if observation.remaining_tokens is not None and observation.remaining_tokens < required_tokens:
            return False, "quota_insufficient", 0.0
        role_floor = policy.quality_floor + (0.03 if role in {"verifier", "challenger", "synthesizer"} else 0.0)
        if quality < min(1.0, role_floor):
            return False, "quality_floor_not_met", 0.0
        estimated_cost = observation.estimated_cost(
            input_tokens=policy.estimated_input_tokens,
            output_tokens=policy.estimated_output_tokens,
        )
        if policy.max_task_cost_usd is not None and estimated_cost > policy.max_task_cost_usd:
            return False, "task_budget_exceeded", estimated_cost
        return True, None, estimated_cost

    @staticmethod
    def _source_bonus(source: str, mode: str) -> float:
        if mode == "maximum_quality":
            return {"official_free": 2, "subscription_entitlement": 2, "promotional": 1, "third_party_free": -2, "paid": 0, "local": 1}[source]
        if mode == "maximum_free":
            return {"official_free": 38, "subscription_entitlement": 32, "promotional": 25, "third_party_free": 15, "paid": -32, "local": 35}[source]
        if mode == "local_only":
            return 40 if source == "local" else -100
        return {"official_free": 16, "subscription_entitlement": 13, "promotional": 10, "third_party_free": 4, "paid": 0, "local": 12}[source]

    def _rank_role(
        self,
        task: CompiledTask,
        role: str,
        profiles: Iterable[ModelProfile],
        policy: RoutingPolicy,
        selected_providers: set[str],
        used_models: set[str],
    ) -> tuple[list[RoutedCandidate], list[tuple[str, str]]]:
        candidates: list[RoutedCandidate] = []
        rejected: list[tuple[str, str]] = []
        for profile in profiles:
            if not profile.enabled or profile.model_id in used_models:
                continue
            observation = self.observation_for(profile)
            quality = self._quality(profile, task, role)
            eligible, reason, estimated_cost = self._eligible(
                profile, observation, role=role, quality=quality, policy=policy
            )
            if not eligible:
                rejected.append((profile.model_id, str(reason)))
                continue
            score = quality * 100 + self._source_bonus(observation.source, policy.mode)
            reasons = [f"quality={quality:.3f}", f"source={observation.source}"]
            if observation.free_like:
                score += 3
                reasons.append("zero_direct_cost")
            if observation.quota_state in {"available", "unlimited"}:
                score += 2
                reasons.append("quota_available")
            elif observation.quota_state == "low":
                score -= 6
                reasons.append("quota_low")
            if observation.terms_status == "official":
                score += 3
                reasons.append("official_terms")
            elif observation.terms_status == "verified_third_party":
                score += 1
                reasons.append("verified_third_party_terms")
            if policy.prefer_provider_diversity and role in {"reviewer", "verifier", "challenger"}:
                if selected_providers and profile.provider_id not in selected_providers:
                    score += 9
                    reasons.append("provider_diversity")
                elif selected_providers:
                    score -= 5
                    reasons.append("same_provider_penalty")
            score -= min(20.0, estimated_cost * 100.0)
            candidates.append(RoutedCandidate(
                model_id=profile.model_id,
                provider_id=profile.provider_id,
                role=role,
                quality=quality,
                route_score=round(score, 6),
                estimated_cost_usd=round(estimated_cost, 8),
                capacity_source=observation.source,
                quota_state=observation.quota_state,
                reasons=tuple(reasons),
            ))
        candidates.sort(key=lambda item: (-item.route_score, item.estimated_cost_usd, item.model_id))
        return candidates, rejected

    @staticmethod
    def _role_layout(team_mode: str, producer_count: int) -> list[tuple[str, int, bool]]:
        if team_mode == "solo":
            return [("producer", 1, False)]
        if team_mode == "fallback":
            return [("producer", 1, True)]
        if team_mode == "parallel":
            return [("producer", producer_count, True)]
        if team_mode == "verify":
            return [("producer", producer_count, True), ("verifier", 1, True)]
        return [("producer", producer_count, True), ("challenger", 1, True), ("synthesizer", 1, True)]

    def plan(
        self,
        *,
        plan_id: str,
        display_name: str,
        team_mode: str,
        task: CompiledTask,
        profiles: Iterable[ModelProfile],
        policy: RoutingPolicy | None = None,
    ) -> AdaptiveCapacityPlan:
        if team_mode not in TEAM_MODES:
            raise CapacityRoutingConfigurationError("capacity_team_mode_invalid")
        policy = policy or RoutingPolicy()
        profiles = tuple(profiles)
        if not profiles:
            raise CapacityRoutingUnavailable("no_models_registered")
        selected_providers: set[str] = set()
        used_models: set[str] = set()
        role_routes: list[RoleRoute] = []
        members: list[PortfolioMember] = []
        warnings: list[str] = []
        total_cost = 0.0

        for role, count, fallback_role in self._role_layout(team_mode, policy.producer_count):
            ranked, rejected = self._rank_role(task, role, profiles, policy, selected_providers, used_models)
            primary: list[RoutedCandidate] = []
            remaining: list[RoutedCandidate] = []
            for candidate in ranked:
                if len(primary) < count:
                    projected = total_cost + candidate.estimated_cost_usd
                    if policy.max_task_cost_usd is not None and projected > policy.max_task_cost_usd:
                        rejected.append((candidate.model_id, "cumulative_task_budget_exceeded"))
                        continue
                    primary.append(candidate)
                    total_cost = projected
                else:
                    remaining.append(candidate)
            if len(primary) < count:
                raise CapacityRoutingUnavailable(f"no_eligible_model_for_role:{role}")
            fallbacks = remaining[: policy.fallback_per_role] if fallback_role else []
            selected = primary + fallbacks
            role_routes.append(RoleRoute(role=role, selected=tuple(selected), rejected=tuple(rejected)))
            for index, candidate in enumerate(selected):
                priority = 100 + index
                members.append(PortfolioMember(candidate.model_id, role=role, priority=priority, required=index < count))
                used_models.add(candidate.model_id)
                selected_providers.add(candidate.provider_id)
            if fallback_role and fallbacks:
                warnings.append(f"{role}_fallback_chain_ready")

        strategy_map = {
            "solo": "best_fit",
            "fallback": "fallback_chain",
            "parallel": "parallel_collect",
            "verify": "verified_synthesis",
            "challenge": "challenge_synthesis",
        }
        portfolio = ModelPortfolio(
            portfolio_id=plan_id,
            display_name=display_name,
            strategy=strategy_map[team_mode],
            members=tuple(members),
            max_parallel=max(1, min(policy.producer_count, len(members))),
            minimum_successful_producers=1,
            require_independent_verifier=team_mode == "verify",
            metadata={
                "adaptive_capacity_router": CAPACITY_ROUTER_VERSION,
                "routing_mode": policy.mode,
                "quality_floor": policy.quality_floor,
                "estimated_cost_usd": round(total_cost, 8),
                "provider_diversity_preferred": policy.prefer_provider_diversity,
                "adaptive_primary_counts": {role: count for role, count, _ in self._role_layout(team_mode, policy.producer_count)},
                "consumer_subscription_api_access_assumed": False,
            },
        )
        if policy.max_task_cost_usd is not None and total_cost > policy.max_task_cost_usd:
            raise CapacityRoutingUnavailable("cooperative_plan_budget_exceeded")
        if team_mode in {"verify", "challenge"} and len(selected_providers) < 2:
            warnings.append("cross_provider_diversity_unavailable")
        return AdaptiveCapacityPlan(
            plan_id=plan_id,
            team_mode=team_mode,
            policy=policy,
            roles=tuple(role_routes),
            portfolio=portfolio,
            estimated_cost_usd=round(total_cost, 8),
            warnings=tuple(warnings),
        )


def run_capacity_router_benchmark() -> dict[str, Any]:
    """Deterministic routing probes with zero provider/network calls."""
    task = CompiledTask(
        task_id="capacity-benchmark",
        title="Cooperative coding task",
        phase="execution",
        role="executor",
        expected_output_type="contribution",
        domains=("coding",),
    )
    profiles = (
        ModelProfile(
            model_id="free-google", display_name="Free Google", provider_id="google",
            connector_type="openai_compatible", connector_resource_id="free-google", model_name="free-google",
            access_mode="byok_api", privacy_mode="provider_cloud",
            capabilities={"coding": 0.88, "general": 0.82, "challenger": 0.78},
            roles=("producer", "challenger"), domains=("coding",), metadata={"capacity_source": "official_free"},
        ),
        ModelProfile(
            model_id="free-other", display_name="Free Other", provider_id="other",
            connector_type="openai_compatible", connector_resource_id="free-other", model_name="free-other",
            access_mode="byok_api", privacy_mode="provider_cloud",
            capabilities={"coding": 0.84, "general": 0.80, "challenger": 0.90},
            roles=("producer", "challenger"), domains=("coding",), metadata={"capacity_source": "official_free"},
        ),
        ModelProfile(
            model_id="paid-synth", display_name="Paid Synth", provider_id="premium",
            connector_type="openai_compatible", connector_resource_id="paid-synth", model_name="paid-synth",
            access_mode="byok_api", privacy_mode="provider_cloud",
            capabilities={"coding": 0.96, "general": 0.95, "synthesizer": 0.98},
            roles=("synthesizer",), domains=("coding",),
        ),
        ModelProfile(
            model_id="weak-free", display_name="Weak Free", provider_id="weak",
            connector_type="openai_compatible", connector_resource_id="weak-free", model_name="weak-free",
            access_mode="byok_api", privacy_mode="provider_cloud",
            capabilities={"coding": 0.30, "general": 0.35}, metadata={"capacity_source": "official_free"},
        ),
        ModelProfile(
            model_id="third-party", display_name="Third Party", provider_id="aggregator",
            connector_type="openai_compatible", connector_resource_id="third-party", model_name="third-party",
            access_mode="byok_api", privacy_mode="provider_cloud",
            capabilities={"coding": 0.92, "general": 0.92}, metadata={"capacity_source": "third_party_free", "terms_status": "unknown"},
        ),
        ModelProfile(
            model_id="subscription-unverified", display_name="App Subscription", provider_id="consumer",
            connector_type="openai_compatible", connector_resource_id="consumer", model_name="consumer",
            access_mode="oauth_broker", privacy_mode="provider_cloud",
            capabilities={"coding": 0.99, "general": 0.99}, metadata={"capacity_source": "subscription_entitlement"},
        ),
    )
    observations = {
        "free-google": CapacityObservation("free-google", "official_free", "available", remaining_tokens=100_000),
        "free-other": CapacityObservation("free-other", "official_free", "available", remaining_tokens=100_000),
        "paid-synth": CapacityObservation("paid-synth", "paid", "available", remaining_tokens=100_000, input_cost_per_million=2.0, output_cost_per_million=8.0),
        "weak-free": CapacityObservation("weak-free", "official_free", "available", remaining_tokens=1_000_000),
        "third-party": CapacityObservation("third-party", "third_party_free", "available", remaining_tokens=1_000_000, terms_status="unknown"),
        "subscription-unverified": CapacityObservation("subscription-unverified", "subscription_entitlement", "available", remaining_tokens=1_000_000, entitlement_verified=False),
    }
    router = AdaptiveCapacityRouter(observations)
    plan = router.plan(
        plan_id="dreamer-challenge",
        display_name="Dreamer Challenge",
        team_mode="challenge",
        task=task,
        profiles=profiles,
        policy=RoutingPolicy(mode="maximum_free", quality_floor=0.70, producer_count=1, fallback_per_role=0, max_task_cost_usd=0.10),
    )
    members = {member.role: member.model_id for member in plan.portfolio.members}
    rejected = {item[0]: item[1] for route in plan.roles for item in route.rejected}
    probes = {
        "collaboration_preserved": plan.portfolio.strategy == "challenge_synthesis" and set(members) == {"producer", "challenger", "synthesizer"},
        "free_models_used_when_adequate": members["producer"].startswith("free-") and members["challenger"].startswith("free-"),
        "premium_model_allowed_for_quality_floor": members["synthesizer"] == "paid-synth",
        "provider_diversity_present": len({candidate.provider_id for route in plan.roles for candidate in route.selected[:1]}) >= 2,
        "weak_free_rejected": rejected.get("weak-free") == "quality_floor_not_met",
        "unknown_third_party_not_auto_routed": rejected.get("third-party") in {"terms_unknown", "third_party_free_not_allowed"},
        "consumer_subscription_not_assumed_api": rejected.get("subscription-unverified") == "subscription_does_not_prove_api_entitlement",
        "budget_respected": plan.estimated_cost_usd <= 0.10,
        "adaptive_quota_refresh": observations["free-google"].recommended_refresh_seconds() == 900 and observations["weak-free"].recommended_refresh_seconds() == 900,
    }
    return {
        "passed": all(probes.values()),
        "probes": probes,
        "plan": plan.as_dict(),
        "metrics": {
            "models_considered": len(profiles),
            "roles_routed": len(plan.roles),
            "provider_network_calls": 0,
            "consumer_subscription_entitlements_assumed": 0,
        },
    }
