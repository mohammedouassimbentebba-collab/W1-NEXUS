from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from w1cip.cli import main as cli_main
from w1cip.cli_support import initialize_workspace
from w1cip.evaluation import evaluate_w1_nexus, run_model_access_benchmark
from w1cip.model_access import (
    EmbeddedW1Runtime,
    ExternalApplicationConnector,
    LocalControlServer,
    LocalControlSettings,
    ModelAccessFabric,
    ModelAccessStore,
    ModelPortfolio,
    ModelProfile,
    PortfolioMember,
    PortfolioProviderAdapter,
    PythonPluginAdapterFactory,
    W1LocalClient,
)
from w1cip.orchestrator import CompiledTask, ProviderPermanentError, ProviderRequest, ProviderResponse


class Adapter:
    supports_idempotency = True

    def __init__(self, resource_id: str, marker: str, *, fail: bool = False) -> None:
        self.resource_id = resource_id
        self.marker = marker
        self.fail = fail
        self.calls = []

    def invoke(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.fail:
            raise ProviderPermanentError("test_failure")
        return ProviderResponse(
            output_type=request.task.expected_output_type,
            payload={"marker": self.marker, "prior_count": len(request.prior_outputs)},
            context_fields_used=tuple(request.context),
        )


def profile(model_id: str, *, role: str = "executor", mode: str = "local_endpoint", **kwargs) -> ModelProfile:
    defaults = dict(
        model_id=model_id,
        display_name=model_id,
        provider_id="test",
        connector_type="openai_compatible",
        connector_resource_id=f"resource-{model_id}",
        model_name=model_id,
        access_mode=mode,
        privacy_mode="local" if mode == "local_endpoint" else "provider_cloud",
        capabilities={role: 0.9, "execution": 0.8, "coding": 0.8},
        roles=(role,),
        domains=("coding",),
        endpoint="http://127.0.0.1:11434/v1/chat/completions" if mode == "local_endpoint" else None,
    )
    defaults.update(kwargs)
    return ModelProfile(**defaults)


def request(key: str = "test") -> ProviderRequest:
    return ProviderRequest(
        run_id="run-model-access",
        session_id="session-model-access",
        task=CompiledTask(
            task_id="task-model-access",
            title="Test preferred model team",
            phase="execution",
            role="executor",
            expected_output_type="contribution",
            required_context_fields=("input",),
            domains=("coding",),
        ),
        resource_id="portfolio",
        idempotency_key=key,
        context={"input": "safe"},
        prior_outputs={},
        attempt=1,
    )


class ModelAccessFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ModelAccessStore(self.root / "model-access.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_profiles_and_portfolios_are_persistent_and_redacted(self) -> None:
        item = profile("local-a")
        self.store.put_profile(item)
        portfolio = ModelPortfolio(
            portfolio_id="preferred",
            display_name="Preferred models",
            strategy="best_fit",
            members=(PortfolioMember("local-a"),),
        )
        self.store.put_portfolio(portfolio)
        reopened = ModelAccessStore(self.root / "model-access.sqlite3")
        self.assertEqual("local-a", reopened.get_profile("local-a").model_id)
        self.assertEqual("preferred", reopened.get_portfolio("preferred").portfolio_id)
        self.assertNotIn("secret_value", item.redacted_dict())

    def test_parallel_collect_returns_all_outputs_without_count_vote(self) -> None:
        for model_id in ("a", "b"):
            self.store.put_profile(profile(model_id))
        self.store.put_portfolio(ModelPortfolio(
            portfolio_id="parallel",
            display_name="Parallel",
            strategy="parallel_collect",
            members=(PortfolioMember("a"), PortfolioMember("b")),
            minimum_successful_producers=2,
        ))
        fabric = ModelAccessFabric(self.store, adapter_overrides={
            "a": Adapter("resource-a", "a"),
            "b": Adapter("resource-b", "b"),
        })
        result = fabric.invoke_portfolio("parallel", request())
        self.assertTrue(result.successful)
        self.assertEqual(2, len(result.invocations))
        self.assertIsNone(result.selected_model_id)
        self.assertIn("parallel_outputs_require_governed_review", result.warnings)

    def test_fallback_moves_to_next_user_preferred_model(self) -> None:
        for model_id in ("primary", "secondary"):
            self.store.put_profile(profile(model_id))
        self.store.put_portfolio(ModelPortfolio(
            portfolio_id="fallback",
            display_name="Fallback",
            strategy="fallback_chain",
            members=(
                PortfolioMember("primary", priority=1),
                PortfolioMember("secondary", priority=2),
            ),
        ))
        fabric = ModelAccessFabric(self.store, adapter_overrides={
            "primary": Adapter("resource-primary", "primary", fail=True),
            "secondary": Adapter("resource-secondary", "secondary"),
        })
        result = fabric.invoke_portfolio("fallback", request())
        self.assertEqual("secondary", result.selected_model_id)
        self.assertEqual(("fallback_model_used",), result.warnings)

    def test_verified_synthesis_uses_independent_verifier(self) -> None:
        self.store.put_profile(profile("producer-a"))
        self.store.put_profile(profile("producer-b"))
        self.store.put_profile(profile("verifier", role="verifier"))
        self.store.put_portfolio(ModelPortfolio(
            portfolio_id="verified",
            display_name="Verified team",
            strategy="verified_synthesis",
            members=(
                PortfolioMember("producer-a", role="producer"),
                PortfolioMember("producer-b", role="specialist"),
                PortfolioMember("verifier", role="verifier"),
            ),
            minimum_successful_producers=2,
            require_independent_verifier=True,
        ))
        verifier = Adapter("resource-verifier", "verified")
        fabric = ModelAccessFabric(self.store, adapter_overrides={
            "producer-a": Adapter("resource-producer-a", "a"),
            "producer-b": Adapter("resource-producer-b", "b"),
            "verifier": verifier,
        })
        result = fabric.invoke_portfolio("verified", request())
        self.assertEqual("verifier", result.selected_model_id)
        self.assertEqual(2, result.verified_response.payload["prior_count"])
        self.assertEqual(1, len(verifier.calls))

    def test_portfolio_adapter_integrates_with_orchestrator_contract(self) -> None:
        self.store.put_profile(profile("a"))
        self.store.put_profile(profile("b"))
        self.store.put_portfolio(ModelPortfolio(
            portfolio_id="orchestrated",
            display_name="Orchestrated portfolio",
            strategy="parallel_collect",
            members=(PortfolioMember("a"), PortfolioMember("b")),
            minimum_successful_producers=2,
        ))
        fabric = ModelAccessFabric(self.store, adapter_overrides={
            "a": Adapter("resource-a", "a"),
            "b": Adapter("resource-b", "b"),
        })
        adapter = PortfolioProviderAdapter(fabric, "orchestrated")
        response = adapter.invoke(request("orchestrator"))
        self.assertTrue(response.payload["selection_required"])
        self.assertEqual(2, len(response.payload["_w1_multi_model_candidates"]))
        self.assertIn("w1_model_portfolio", response.metadata)

    def test_external_stdio_application_can_supply_a_model(self) -> None:
        script = self.root / "external_model.py"
        script.write_text(
            "import json,sys\n"
            "doc=json.loads(sys.stdin.readline())\n"
            "print(json.dumps({'output_type':'contribution','payload':{'external': True},"
            "'context_fields_used':list(doc['request']['context'])}))\n",
            encoding="utf-8",
        )
        item = profile(
            "external-app",
            mode="external_application",
            privacy_mode="local",
            endpoint=None,
            external_command=(sys.executable, str(script)),
        )
        response = ExternalApplicationConnector(item).invoke(request())
        self.assertTrue(response.payload["external"])
        self.assertEqual(("input",), response.context_fields_used)

    def test_custom_plugin_factory_is_loadable(self) -> None:
        module = self.root / "user_plugin.py"
        module.write_text(
            "from w1cip.orchestrator import ProviderResponse\n"
            "class A:\n"
            " supports_idempotency=True\n"
            " def __init__(self,r): self.resource_id=r\n"
            " def invoke(self,request): return ProviderResponse(output_type=request.task.expected_output_type,payload={'plugin':True})\n"
            "def build(profile): return A(profile.connector_resource_id)\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(self.root))
        self.addCleanup(sys.path.remove, str(self.root))
        item = profile(
            "plugin",
            mode="custom_plugin",
            endpoint=None,
            privacy_mode="private_network",
            plugin_factory="user_plugin:build",
        )
        adapter = PythonPluginAdapterFactory().build(item)
        self.assertTrue(adapter.invoke(request()).payload["plugin"])

    def test_local_control_api_requires_token_and_is_embeddable(self) -> None:
        self.store.put_profile(profile("a"))
        runtime = EmbeddedW1Runtime(ModelAccessFabric(
            self.store,
            adapter_overrides={"a": Adapter("resource-a", "a")},
        ))
        server = LocalControlServer(LocalControlSettings(port=0, token="token"), runtime)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.close_server)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/v1/models", timeout=5)
        self.assertEqual(401, caught.exception.code)
        client = W1LocalClient(base, "token")
        self.assertEqual("a", client.list_models()[0]["model_id"])

    def test_executable_benchmark_and_scorecard(self) -> None:
        benchmark = run_model_access_benchmark()
        self.assertTrue(benchmark["passed"])
        self.assertGreater(benchmark["metrics"]["observed_parallel_speedup"], 1.3)
        report = evaluate_w1_nexus(Path(__file__).parents[1])
        self.assertTrue(report["model_access_benchmark"]["passed"])
        self.assertGreater(report["verified_backend_capability_percent"], 90)
        self.assertGreater(report["full_product_completeness_percent"], 75)
        self.assertGreaterEqual(report["full_product_completeness_percent"], 94.1)
        self.assertLess(report["full_product_completeness_percent"], 95)
        self.assertTrue(report["credential_broker_benchmark"]["passed"])
        self.assertTrue(report["collaboration_benchmark"]["passed"])

    def test_cli_lists_models_runs_benchmark_and_evaluates(self) -> None:
        workspace = self.root / "workspace"
        initialize_workspace(workspace)

        def run(*args: str):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli_main(["--workspace", str(workspace), "--json", *args])
            self.assertEqual(0, code, stderr.getvalue())
            return json.loads(stdout.getvalue())

        listed = run("models", "list")
        self.assertFalse(listed["requires_w1_owned_server"])
        self.assertGreaterEqual(len(listed["models"]), 2)
        benchmark = run("models", "benchmark")
        self.assertTrue(benchmark["passed"])
        report = run("evaluate")
        self.assertIn("full_product_completeness_percent", report)


if __name__ == "__main__":
    unittest.main()
