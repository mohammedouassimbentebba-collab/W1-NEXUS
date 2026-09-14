"""Tests for Step 52: Autonomous Intelligence Discovery, Identity & Certification Framework (AID-CF)."""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from w1cip.intelligence_discovery import (
    ALLOWED_OPERATIONS_BY_TRUST_CLASS,
    AutonomousDiscoveryEngine,
    CertificationStatus,
    DiscoveredModel,
    DiversityPolicyEngine,
    DiversityPolicyLevel,
    EntitlementState,
    LocalRuntimeDiscoveryAdapter,
    ModelIdentityResolver,
    ModelProvenance,
    PublicCatalogDiscoveryAdapter,
    RouteCertificationRecord,
    TaskFitnessEngine,
    TrustClass,
    assert_loopback_host,
)
from w1cip.w1_gateway import (
    GatewayStore,
    GatewayTeamBuildError,
    ModelIdentity,
    ModelRoute,
    RouteCost,
    RouteQuota,
    TeamBuilder,
    TeamPolicy,
    W1RouteSelector,
)


def test_fail_closed_loopback_host_assertion() -> None:
    """Test 1: assert_loopback_host fails closed for non-loopback hosts."""
    # Allowed loopback hosts
    assert_loopback_host("localhost")
    assert_loopback_host("127.0.0.1")
    assert_loopback_host("[::1]")
    assert_loopback_host("127.0.0.100")
    assert_loopback_host("http://127.0.0.1:11434")
    assert_loopback_host("http://localhost:8080/v1")

    # Forbidden remote hosts - must fail closed
    with pytest.raises(ValueError):
        assert_loopback_host("192.168.1.50")

    with pytest.raises(ValueError):
        assert_loopback_host("10.0.0.1")

    with pytest.raises(ValueError):
        assert_loopback_host("api.external.com")

    with pytest.raises(ValueError):
        assert_loopback_host("https://llama.corporate.lan:8000")


def test_trust_classes_and_allowed_operations_matrix() -> None:
    """Test 2: Trust classes and allowed operations matrix enforcement."""
    # Local loopback permits metadata, probes, auto-certification
    local_ops = ALLOWED_OPERATIONS_BY_TRUST_CLASS[TrustClass.LOCAL_LOOPBACK]
    assert "discover_metadata" in local_ops
    assert "inference_probe" in local_ops
    assert "auto_certify" in local_ops
    assert "network_outbound" not in local_ops

    # Trusted Account API permits metadata, probes, auto-certification, network outbound
    account_ops = ALLOWED_OPERATIONS_BY_TRUST_CLASS[TrustClass.TRUSTED_ACCOUNT_API]
    assert "discover_metadata" in account_ops
    assert "inference_probe" in account_ops
    assert "auto_certify" in account_ops
    assert "network_outbound" in account_ops

    # Public Community Catalog strictly prohibits auto_certify and inference_probe
    community_ops = ALLOWED_OPERATIONS_BY_TRUST_CLASS[TrustClass.PUBLIC_COMMUNITY_CATALOG]
    assert "discover_metadata" in community_ops
    assert "inference_probe" not in community_ops
    assert "auto_certify" not in community_ops


def test_model_identity_resolver_resolves_providers_to_canonical_identity() -> None:
    """Test 3: ModelIdentityResolver maps OpenRouter, Groq, Ollama Llama 3.3 to single canonical identity."""
    resolver = ModelIdentityResolver()

    # OpenRouter
    m1 = DiscoveredModel(
        provider_id="openrouter",
        raw_model_id="meta-llama/llama-3.3-70b-instruct:free",
        display_name="Llama 3.3 70B Instruct (free)",
        context_window=131072,
        is_free=True,
    )
    # Groq
    m2 = DiscoveredModel(
        provider_id="groq",
        raw_model_id="llama-3.3-70b-versatile",
        display_name="Llama 3.3 70B Versatile",
        context_window=131072,
        is_free=True,
    )
    # Ollama
    m3 = DiscoveredModel(
        provider_id="ollama",
        raw_model_id="llama3.3:70b",
        display_name="Ollama Llama 3.3 70b",
        context_window=131072,
        is_free=True,
    )

    r1 = resolver.resolve(m1)
    r2 = resolver.resolve(m2)
    r3 = resolver.resolve(m3)

    assert r1.canonical_id == "llama-3.3-70b"
    assert r2.canonical_id == "llama-3.3-70b"
    assert r3.canonical_id == "llama-3.3-70b"

    assert r1.vendor == "meta"
    assert r2.vendor == "meta"
    assert r3.vendor == "meta"

    assert r1.family == "llama-3.3"
    assert r2.family == "llama-3.3"
    assert r3.family == "llama-3.3"


def test_multi_account_routes_produce_distinct_route_ids() -> None:
    """Test 4: 3 accounts on same provider produce 3 distinct routes."""
    m_acc1 = DiscoveredModel(
        provider_id="openrouter",
        raw_model_id="google/gemini-2.5-flash",
        account_id="acc_team_a",
        is_free=False,
    )
    m_acc2 = DiscoveredModel(
        provider_id="openrouter",
        raw_model_id="google/gemini-2.5-flash",
        account_id="acc_team_b",
        is_free=False,
    )
    m_acc3 = DiscoveredModel(
        provider_id="openrouter",
        raw_model_id="google/gemini-2.5-flash",
        account_id="acc_backup",
        is_free=False,
    )

    resolver = ModelIdentityResolver()
    r1 = resolver.resolve_route(m_acc1)
    r2 = resolver.resolve_route(m_acc2)
    r3 = resolver.resolve_route(m_acc3)

    assert r1["route_id"] == "gemini-2.5-flash@openrouter:acc_team_a"
    assert r2["route_id"] == "gemini-2.5-flash@openrouter:acc_team_b"
    assert r3["route_id"] == "gemini-2.5-flash@openrouter:acc_backup"
    assert len({r1["route_id"], r2["route_id"], r3["route_id"]}) == 3


def test_task_fitness_engine_produces_task_specific_fitness() -> None:
    """Test 5: TaskFitnessEngine produces specialized fitness for coding vs reasoning vs vision."""
    coding_caps = {"code": 1.0, "reasoning": 0.8}
    reasoning_caps = {"reasoning": 1.0, "code": 0.5}
    vision_caps = {"vision": 1.0, "multimodal": 1.0}

    f_code_for_coding = TaskFitnessEngine.calculate_fitness("coding", coding_caps, 128000)
    f_reason_for_coding = TaskFitnessEngine.calculate_fitness("coding", reasoning_caps, 128000)
    assert f_code_for_coding > f_reason_for_coding

    f_reason_for_reasoning = TaskFitnessEngine.calculate_fitness("reasoning", reasoning_caps, 128000)
    f_vision_for_reasoning = TaskFitnessEngine.calculate_fitness("reasoning", vision_caps, 128000)
    assert f_reason_for_reasoning > f_vision_for_reasoning

    f_vision_for_vision = TaskFitnessEngine.calculate_fitness("vision", vision_caps, 128000)
    f_code_for_vision = TaskFitnessEngine.calculate_fitness("vision", coding_caps, 128000)
    assert f_vision_for_vision > f_code_for_vision


def test_diversity_policy_engine_enforces_strict_balanced_relaxed() -> None:
    """Test 6: DiversityPolicyEngine behaves properly for strict vs balanced vs relaxed."""
    # 1. Strict: Rejects same vendor
    d_strict = DiversityPolicyEngine.evaluate(
        DiversityPolicyLevel.STRICT,
        producer_vendor="google",
        producer_family="gemini-2.5",
        producer_arch="dense_transformer",
        producer_provider="gemini_api",
        candidate_vendor="google",
        candidate_family="gemini-1.5",
        candidate_arch="dense_transformer",
        candidate_provider="openrouter",
        role="reviewer",
    )
    assert not d_strict.permitted
    assert any("strict" in r.lower() or "vendor" in r.lower() for r in d_strict.reasons)

    # Strict: Accepts different vendor
    d_strict_ok = DiversityPolicyEngine.evaluate(
        DiversityPolicyLevel.STRICT,
        producer_vendor="google",
        producer_family="gemini-2.5",
        producer_arch="dense_transformer",
        producer_provider="gemini_api",
        candidate_vendor="anthropic",
        candidate_family="claude-3.5",
        candidate_arch="dense_transformer",
        candidate_provider="openrouter",
        role="reviewer",
    )
    assert d_strict_ok.permitted

    # 2. Balanced: Permits same vendor if family/tier differs
    d_balanced = DiversityPolicyEngine.evaluate(
        DiversityPolicyLevel.BALANCED,
        producer_vendor="google",
        producer_family="gemini-2.5-flash",
        producer_arch="dense_transformer",
        producer_provider="gemini_api",
        candidate_vendor="google",
        candidate_family="gemini-2.5-pro",
        candidate_arch="dense_transformer",
        candidate_provider="openrouter",
        role="reviewer",
    )
    assert d_balanced.permitted

    # 3. Relaxed: Permits same family across different routes
    d_relaxed = DiversityPolicyEngine.evaluate(
        DiversityPolicyLevel.RELAXED,
        producer_vendor="meta",
        producer_family="llama-3.3",
        producer_arch="dense_transformer",
        producer_provider="groq",
        candidate_vendor="meta",
        candidate_family="llama-3.3",
        candidate_arch="dense_transformer",
        candidate_provider="cerebras",
        role="reviewer",
    )
    assert d_relaxed.permitted


def test_route_certification_lifecycle(tmp_path) -> None:
    """Test 7: Route certification record, expiration verification, and revocation."""
    db_path = tmp_path / "gateway_cert.db"
    store = GatewayStore(db_path)

    # Insert test identity and route
    store.put_identity(
        ModelIdentity(
            canonical_id="gemini-2.5-flash",
            family="gemini-2.5",
            vendor="google",
            architecture="dense_transformer",
            quality_tier="frontier",
            context_window=1000000,
            capabilities=frozenset({"code", "reasoning"}),
        )
    )
    store.put_route(
        ModelRoute(
            route_id="gemini-2.5-flash@google:default",
            model_identity_id="gemini-2.5-flash",
            provider_id="google",
            account_id="default",
            source_type="cloud_free",
            entitlement_state="verified",
            cost=RouteCost(0.0, 0.0),
            quota=RouteQuota(state="available"),
            auth_mode="api_key",
        )
    )

    # 1. Certify route for 14 days
    cert = store.certify_route("gemini-2.5-flash@google:default", ttl_days=14)
    assert cert.status == CertificationStatus.CERTIFIED
    assert cert.is_active()
    assert cert.evidence_hash
    assert cert.model_identity_hash

    fetched = store.get_certification("gemini-2.5-flash@google:default")
    assert fetched is not None
    assert fetched.status == CertificationStatus.CERTIFIED
    assert fetched.is_active()

    # 2. Revoke route
    revoked = store.revoke_route("gemini-2.5-flash@google:default", reason="upstream_auth_failure")
    assert revoked.status == CertificationStatus.REVOKED
    assert not revoked.is_active()
    assert revoked.revocation_reason == "upstream_auth_failure"

    fetched_rev = store.get_certification("gemini-2.5-flash@google:default")
    assert fetched_rev is not None
    assert not fetched_rev.is_active()

    # 3. Check gateway doctor reflection
    doc = store.gateway_doctor()
    assert doc["revoked_count"] == 1
    assert doc["certified_count"] == 0


def test_uncertified_and_expired_routes_rejected_when_require_certified(tmp_path) -> None:
    """Test 8: Uncertified, expired, or revoked routes are rejected when require_certified=True."""
    db_path = tmp_path / "gateway_filter.db"
    store = GatewayStore(db_path)

    store.put_identity(
        ModelIdentity(
            canonical_id="deepseek-r1",
            family="deepseek-r1",
            vendor="deepseek",
            architecture="moe_reasoning",
            quality_tier="frontier",
            context_window=131072,
            capabilities=frozenset({"reasoning", "code"}),
        )
    )
    store.put_route(
        ModelRoute(
            route_id="deepseek-r1@openrouter:free",
            model_identity_id="deepseek-r1",
            provider_id="openrouter",
            account_id="free",
            source_type="cloud_free",
            cost=RouteCost(0.0, 0.0),
            quota=RouteQuota(state="available"),
            auth_mode="api_key",
        )
    )

    # Without certification: accessible when require_certified=False
    selector = W1RouteSelector.from_store(store)
    res_uncert = selector.find_routes(require_certified=False)
    assert len(res_uncert) == 1

    # Inaccessible when require_certified=True
    res_cert = selector.find_routes(require_certified=True)
    assert len(res_cert) == 0

    # Explanation shows explicit rejection reason
    expl = selector.explain_route("reasoning", require_certified=True)
    assert expl.selected_route_id is None
    rejections = [r["reason"] for r in expl.rejected_candidates]
    assert "route_uncertified" in rejections


def test_loopback_rejects_wildcard_bind_address() -> None:
    """Test 9: 0.0.0.0 is a wildcard bind address, not a loopback host, and must be rejected fail-closed."""
    from w1cip.intelligence_discovery import assert_loopback_host, is_safe_loopback_host

    assert not is_safe_loopback_host("0.0.0.0")
    assert not is_safe_loopback_host("http://0.0.0.0:8000")
    assert is_safe_loopback_host("127.0.0.1")
    assert is_safe_loopback_host("localhost")
    assert is_safe_loopback_host("::1")

    with pytest.raises(ValueError, match="Security Policy Violation"):
        assert_loopback_host("0.0.0.0")


def test_offline_certification_must_not_claim_latency(tmp_path) -> None:
    """Test 10: offline_contract certification must never claim observed latency."""
    db_path = tmp_path / "cert_latency.db"
    store = GatewayStore(db_path)

    store.put_identity(
        ModelIdentity(
            canonical_id="test-model",
            family="test-family",
            vendor="test-vendor",
            architecture="dense_transformer",
            quality_tier="strong",
            context_window=32768,
            capabilities=frozenset({"coding"}),
        )
    )
    store.put_route(
        ModelRoute(
            route_id="test-model@local:default",
            model_identity_id="test-model",
            provider_id="local",
            account_id="default",
            source_type="local",
            cost=RouteCost(0.0, 0.0),
            quota=RouteQuota(state="available"),
            auth_mode="none",
        )
    )

    # 1. Standard offline certification produces null latency and measurement_source='none'
    cert = store.certify_route("test-model@local:default", probe_profile="offline_contract")
    assert cert.latency_p50_ms is None
    assert cert.latency_p95_ms is None
    assert cert.measurement_source == "none"

    # Serialized JSON representation must have null latency
    d = cert.as_dict()
    assert d["latency_p50_ms"] is None
    assert d["latency_p95_ms"] is None
    assert d["measurement_source"] == "none"

    # 2. Attempting to claim latency under offline_contract raises ValueError fail-closed
    with pytest.raises(ValueError, match="offline_contract probe profile must not claim observed latency"):
        store.certify_route(
            "test-model@local:default",
            probe_profile="offline_contract",
            latency_p50_ms=45.0,
        )

    # 3. Attempting to pass non-none measurement_source under offline_contract raises ValueError
    with pytest.raises(ValueError, match="offline_contract probe profile must have measurement_source='none'"):
        store.certify_route(
            "test-model@local:default",
            probe_profile="offline_contract",
            measurement_source="synthetic_estimate",
        )


def test_live_probe_requires_real_measurement_source(tmp_path) -> None:
    """Test 11: live_probe contract strictly enforces both measured latencies and measurement_source='live_probe'."""
    db_path = tmp_path / "live_probe_cert.db"
    store = GatewayStore(db_path)

    store.put_identity(
        ModelIdentity(
            canonical_id="test-probe-model",
            family="test-family",
            vendor="test-vendor",
            architecture="dense_transformer",
            quality_tier="strong",
            context_window=32768,
            capabilities=frozenset({"coding"}),
        )
    )
    store.put_route(
        ModelRoute(
            route_id="test-probe-model@local:default",
            model_identity_id="test-probe-model",
            provider_id="local",
            account_id="default",
            source_type="local",
            cost=RouteCost(0.0, 0.0),
            quota=RouteQuota(state="available"),
            auth_mode="none",
        )
    )

    # Case 1: Rejection of live_probe without measurements (p50 or p95 is None)
    with pytest.raises(ValueError, match="requires actual measured latencies"):
        store.certify_route(
            "test-probe-model@local:default",
            probe_profile="live_probe",
            measurement_source="live_probe",
            latency_p50_ms=None,
            latency_p95_ms=None,
        )

    with pytest.raises(ValueError, match="requires actual measured latencies"):
        store.certify_route(
            "test-probe-model@local:default",
            probe_profile="live_probe",
            measurement_source="live_probe",
            latency_p50_ms=50.0,
            latency_p95_ms=None,
        )

    # Case 2: Rejection of live_probe with measurements but measurement_source != 'live_probe'
    with pytest.raises(ValueError, match="requires measurement_source='live_probe'"):
        store.certify_route(
            "test-probe-model@local:default",
            probe_profile="live_probe",
            measurement_source="none",
            latency_p50_ms=45.0,
            latency_p95_ms=90.0,
        )

    with pytest.raises(ValueError, match="requires measurement_source='live_probe'"):
        store.certify_route(
            "test-probe-model@local:default",
            probe_profile="live_probe",
            measurement_source="synthetic_estimate",
            latency_p50_ms=45.0,
            latency_p95_ms=90.0,
        )

    # Case 3: Rejection of measurement_source='live_probe' on non-live_probe profile
    with pytest.raises(ValueError, match="must have measurement_source='none'"):
        store.certify_route(
            "test-probe-model@local:default",
            probe_profile="offline_contract",
            measurement_source="live_probe",
        )

    with pytest.raises(ValueError, match="measurement_source='live_probe' must be paired with probe_profile='live_probe'"):
        store.certify_route(
            "test-probe-model@local:default",
            probe_profile="custom_profile",
            measurement_source="live_probe",
        )

    # Case 4: Valid live_probe succeeds with explicit measurements and measurement_source='live_probe'
    cert = store.certify_route(
        "test-probe-model@local:default",
        probe_profile="live_probe",
        measurement_source="live_probe",
        latency_p50_ms=42.5,
        latency_p95_ms=88.0,
    )
    assert cert.probe_profile == "live_probe"
    assert cert.measurement_source == "live_probe"
    assert cert.latency_p50_ms == 42.5
    assert cert.latency_p95_ms == 88.0

    # Case 5: Valid synthetic_estimate succeeds when explicitly declared
    synth_cert = store.certify_route(
        "test-probe-model@local:default",
        probe_profile="synthetic_estimate",
        measurement_source="synthetic_estimate",
        latency_p50_ms=50.0,
        latency_p95_ms=100.0,
    )
    assert synth_cert.probe_profile == "synthetic_estimate"
    assert synth_cert.measurement_source == "synthetic_estimate"


def test_dynamic_ingestion_does_not_invent_capabilities(tmp_path) -> None:
    """Test 11: Dynamic ingestion of unknown model defaults to unknown, frozenset(), and 0 context."""
    from w1cip.w1_gateway import ingest_discovered_models

    db_path = tmp_path / "ingest_unknown.db"
    store = GatewayStore(db_path)

    res = ingest_discovered_models(
        store,
        provider_id="custom_provider",
        models=[
            {"id": "unknown-raw-model-xyz"},
        ],
    )
    assert res["identities_ingested"] == 1
    assert res["routes_ingested"] == 1

    identities = store.list_identities()
    assert len(identities) == 1
    ident = identities[0]
    assert ident["quality_tier"] == "unknown"
    assert ident["architecture"] == "unknown"
    assert ident["capabilities"] == []
    assert ident["context_window"] == 0


def test_unknown_context_is_not_treated_as_zero_capacity() -> None:
    """Test 12: context_window=0 is semantically UNKNOWN, not real zero capacity."""
    # Model with UNKNOWN context window (0)
    identity_unknown_ctx = ModelIdentity(
        canonical_id="unknown-ctx-model",
        family="unknown",
        vendor="custom",
        architecture="unknown",
        quality_tier="unknown",
        context_window=0,
        capabilities=frozenset(),
    )

    # Model with explicitly known small context window (8192)
    identity_known_small_ctx = ModelIdentity(
        canonical_id="small-ctx-model",
        family="small",
        vendor="custom",
        architecture="dense_transformer",
        quality_tier="fast",
        context_window=8192,
        capabilities=frozenset(),
    )

    # When min_context=32000 is required:
    # 1. Known small context model (8192 < 32000) is definitely rejected
    assert not identity_known_small_ctx.matches_requirements(min_context=32000)

    # 2. Unknown context model (0) is NOT rejected as having zero capacity; it remains a candidate
    assert identity_unknown_ctx.matches_requirements(min_context=32000)


def test_source_distribution_archive_hygiene(tmp_path: Path) -> None:
    """Test 13: Source distribution ZIP archive must fail-closed and contain zero banned artifacts."""
    import importlib.util
    import zipfile

    repo_root = Path(__file__).resolve().parent.parent
    builder_path = repo_root / "packaging" / "build_source_zip.py"

    spec = importlib.util.spec_from_file_location("build_source_zip_module", builder_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    build_source_zip = mod.build_source_zip
    BANNED_DIRS = mod.BANNED_DIRS
    BANNED_EXTENSIONS = mod.BANNED_EXTENSIONS
    zip_output = tmp_path / "w1-nexus-clean-test.zip"

    meta = build_source_zip(repo_root, zip_output)
    assert meta["file_count"] > 100
    assert meta["archive_sha256"]

    with zipfile.ZipFile(zip_output, "r") as zf:
        namelist = zf.namelist()
        for name in namelist:
            parts = Path(name).parts
            # 1. Zero banned directory components
            for banned in BANNED_DIRS:
                assert banned not in parts, f"Banned directory '{banned}' found in archive entry: {name}"

            # 2. Zero banned file extensions
            suffix = Path(name).suffix.lower()
            assert suffix not in BANNED_EXTENSIONS, f"Banned file extension '{suffix}' in archive entry: {name}"

            # 3. Explicit check for known offenders
            assert ".venv" not in name
            assert "__pycache__" not in name
            assert ".pytest_cache" not in name
            assert "%SystemDrive%" not in name
            assert not name.endswith(".pyc")
            assert not name.endswith(".sqlite3")
            assert not name.endswith(".db")


