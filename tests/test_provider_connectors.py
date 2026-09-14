from __future__ import annotations

import json
import sys
import unittest
from collections import deque
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.orchestrator import (  # noqa: E402
    CompiledTask,
    OutputContractError,
    ProviderOutcomeUncertain,
    ProviderPermanentError,
    ProviderQuotaExhausted,
    ProviderRequest,
    ProviderTransientError,
)
from w1cip.provider_connectors import (  # noqa: E402
    AnthropicMessagesConnector,
    ConnectorConfig,
    EnvironmentSecretResolver,
    GeminiGenerateContentConnector,
    GeminiOAuthGenerateContentConnector,
    GeminiInteractionsConnector,
    HTTPRequest,
    HTTPResponse,
    OpenAICompatibleConnector,
    OpenAIResponsesConnector,
    XAIResponsesConnector,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderPayloadTooLarge,
    ProviderRateLimited,
    ProviderRegistry,
    ProviderSafetyBlocked,
    StaticSecretResolver,
    TransportConnectionError,
    TransportOutcomeUncertain,
    build_provider_prompt,
    estimate_tokens,
    parse_provider_contract,
)


class QueueTransport:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("transport_outcomes_exhausted")
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(status: int, body: dict, headers: dict[str, str] | None = None) -> HTTPResponse:
    return HTTPResponse(
        status=status,
        headers={key.lower(): value for key, value in (headers or {}).items()},
        body=json.dumps(body).encode("utf-8"),
    )


def request_fixture(*, output_type: str = "contribution", resource_id: str = "resource-a") -> ProviderRequest:
    return ProviderRequest(
        run_id="run-provider-001",
        session_id="session-provider-001",
        task=CompiledTask(
            task_id="execute-provider-task",
            title="Execute provider task",
            phase="execution",
            role="executor",
            expected_output_type=output_type,
            required_context_fields=("supply-voltage",),
            estimated_units=1,
            domains=("electronics",),
        ),
        resource_id=resource_id,
        idempotency_key="idem-provider-001",
        context={"supply-voltage": 12},
        prior_outputs={"prior": {"status": "ready"}},
        attempt=1,
    )


def contract_json(output_type: str = "contribution") -> str:
    return json.dumps(
        {
            "output_type": output_type,
            "payload": {"candidate": "candidate-b"},
            "context_fields_used": ["supply-voltage"],
            "protocol_envelopes": [],
        }
    )


class ProviderConnectorTests(unittest.TestCase):
    def test_prompt_contains_only_minimized_context_and_contract(self) -> None:
        prompt = json.loads(build_provider_prompt(request_fixture()))
        self.assertEqual({"supply-voltage": 12}, prompt["allowed_context"])
        self.assertEqual("contribution", prompt["response_contract"]["output_type"])
        self.assertNotIn("api_key", json.dumps(prompt).lower())

    def test_contract_parser_is_strict(self) -> None:
        parsed = parse_provider_contract(contract_json(), "contribution")
        self.assertEqual("candidate-b", parsed["payload"]["candidate"])
        with self.assertRaises(OutputContractError):
            parse_provider_contract(contract_json("evidence"), "contribution")
        unknown = json.loads(contract_json())
        unknown["surprise"] = True
        with self.assertRaises(OutputContractError):
            parse_provider_contract(json.dumps(unknown), "contribution")

    def test_contract_parser_accepts_governed_action_requests(self) -> None:
        payload = json.loads(contract_json())
        payload["action_requests"] = [
            {
                "action_id": "write-provider-output",
                "kind": "file.write",
                "parameters": {"path": "provider.txt", "content": "hello"},
                "reason": "Materialize the reviewed output."
            }
        ]
        parsed = parse_provider_contract(json.dumps(payload), "contribution")
        self.assertEqual("write-provider-output", parsed["action_requests"][0]["action_id"])
        invalid = json.loads(json.dumps(payload))
        invalid["action_requests"][0]["shell"] = "rm -rf /"
        with self.assertRaises(OutputContractError):
            parse_provider_contract(json.dumps(invalid), "contribution")

    def test_openai_responses_connector_parses_usage_and_rate_headers(self) -> None:
        transport = QueueTransport(
            [
                response(
                    200,
                    {
                        "id": "resp-001",
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": contract_json()}],
                            }
                        ],
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 40,
                            "total_tokens": 160,
                            "input_tokens_details": {"cached_tokens": 20},
                            "output_tokens_details": {"reasoning_tokens": 5},
                        },
                    },
                    {
                        "x-request-id": "req-openai-001",
                        "x-ratelimit-limit-requests": "500",
                        "x-ratelimit-remaining-requests": "499",
                        "x-ratelimit-limit-tokens": "100000",
                        "x-ratelimit-remaining-tokens": "99840",
                        "x-ratelimit-reset-requests": "1s",
                    },
                )
            ]
        )
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gpt-test",
                api_key_reference="OPENAI_API_KEY",
                accounting_meter="tokens",
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"OPENAI_API_KEY": "secret-openai"}),
        )
        result = connector.invoke(request_fixture())
        self.assertEqual(160, result.units_consumed)
        self.assertEqual(160, result.metadata["usage"]["total_tokens"])
        self.assertEqual(499, result.metadata["quota_observation"]["request_remaining"])
        self.assertEqual("req-openai-001", result.metadata["provider_request_id"])
        sent = transport.requests[0]
        self.assertEqual("Bearer secret-openai", sent.headers["authorization"])
        self.assertEqual("idem-provider-001", sent.headers["x-client-request-id"])
        self.assertNotIn("secret-openai", json.dumps(result.metadata))
        sent_body = json.loads(sent.body)
        self.assertFalse(sent_body["store"])


    def test_xai_responses_connector_uses_xai_endpoint_and_provider_name(self) -> None:
        transport = QueueTransport([response(200, {
            "id": "xai-resp-1",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": contract_json()}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        })])
        connector = XAIResponsesConnector(
            ConnectorConfig(resource_id="resource-a", model="grok-test", api_key_reference="XAI_API_KEY"),
            transport=transport,
            secret_resolver=StaticSecretResolver({"XAI_API_KEY": "secret-xai"}),
        )
        result = connector.invoke(request_fixture())
        self.assertEqual("contribution", result.output_type)
        self.assertEqual("api.x.ai", __import__("urllib.parse").parse.urlparse(transport.requests[0].url).hostname)
        self.assertEqual("Bearer secret-xai", transport.requests[0].headers["authorization"])

    def test_anthropic_connector_counts_cache_usage_and_headers(self) -> None:
        transport = QueueTransport(
            [
                response(
                    200,
                    {
                        "id": "msg-001",
                        "content": [{"type": "text", "text": contract_json()}],
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": 50,
                            "cache_creation_input_tokens": 30,
                            "cache_read_input_tokens": 100,
                            "output_tokens": 20,
                        },
                    },
                    {
                        "request-id": "req-anthropic-001",
                        "anthropic-ratelimit-requests-limit": "1000",
                        "anthropic-ratelimit-requests-remaining": "999",
                        "anthropic-ratelimit-input-tokens-limit": "2000000",
                        "anthropic-ratelimit-input-tokens-remaining": "1999920",
                        "anthropic-ratelimit-output-tokens-limit": "400000",
                        "anthropic-ratelimit-output-tokens-remaining": "399980",
                    },
                )
            ]
        )
        connector = AnthropicMessagesConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="claude-test",
                api_key_reference="ANTHROPIC_API_KEY",
                accounting_meter="tokens",
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"ANTHROPIC_API_KEY": "secret-anthropic"}),
        )
        result = connector.invoke(request_fixture())
        self.assertEqual(200, result.units_consumed)
        self.assertEqual(180, result.metadata["usage"]["input_tokens"])
        self.assertEqual(100, result.metadata["usage"]["cached_input_tokens"])
        self.assertEqual(999, result.metadata["quota_observation"]["request_remaining"])
        sent = transport.requests[0]
        self.assertEqual("secret-anthropic", sent.headers["x-api-key"])
        self.assertEqual("2023-06-01", sent.headers["anthropic-version"])


    def test_gemini_interactions_connector_uses_current_interaction_shape(self) -> None:
        transport = QueueTransport(
            [
                response(
                    200,
                    {
                        "id": "interaction-001",
                        "model": "gemini-test",
                        "status": "completed",
                        "steps": [
                            {
                                "type": "model_output",
                                "content": [{"type": "text", "text": contract_json()}],
                            }
                        ],
                        "usage": {
                            "total_input_tokens": 70,
                            "total_output_tokens": 20,
                            "total_thought_tokens": 10,
                            "total_cached_tokens": 5,
                            "total_tokens": 100,
                        },
                    },
                )
            ]
        )
        connector = GeminiInteractionsConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gemini-test",
                api_key_reference="GEMINI_API_KEY",
                accounting_meter="tokens",
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"GEMINI_API_KEY": "secret-gemini"}),
        )
        result = connector.invoke(request_fixture())
        self.assertEqual(100, result.units_consumed)
        self.assertEqual(10, result.metadata["usage"]["reasoning_tokens"])
        sent = transport.requests[0]
        self.assertTrue(sent.url.endswith("/v1beta/interactions"))
        body = json.loads(sent.body)
        self.assertEqual("application/json", body["response_format"]["mime_type"])
        self.assertEqual("object", body["response_format"]["schema"]["type"])
        self.assertFalse(body["store"])

    def test_gemini_connector_uses_json_schema_and_usage_metadata(self) -> None:
        transport = QueueTransport(
            [
                response(
                    200,
                    {
                        "responseId": "gemini-response-001",
                        "modelVersion": "gemini-test-001",
                        "candidates": [
                            {
                                "finishReason": "STOP",
                                "content": {"parts": [{"text": contract_json()}]},
                            }
                        ],
                        "usageMetadata": {
                            "promptTokenCount": 80,
                            "candidatesTokenCount": 25,
                            "thoughtsTokenCount": 10,
                            "totalTokenCount": 115,
                            "cachedContentTokenCount": 5,
                        },
                    },
                )
            ]
        )
        connector = GeminiGenerateContentConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gemini-test",
                api_key_reference="GEMINI_API_KEY",
                accounting_meter="tokens",
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"GEMINI_API_KEY": "secret-gemini"}),
        )
        result = connector.invoke(request_fixture())
        self.assertEqual(115, result.units_consumed)
        self.assertEqual(10, result.metadata["usage"]["reasoning_tokens"])
        sent = transport.requests[0]
        self.assertTrue(sent.url.endswith("/gemini-test:generateContent"))
        body = json.loads(sent.body)
        config = body["generationConfig"]
        self.assertEqual("application/json", config["responseMimeType"])
        self.assertEqual("object", config["responseJsonSchema"]["type"])
        self.assertEqual("secret-gemini", sent.headers["x-goog-api-key"])

    def test_gemini_oauth_connector_uses_bearer_not_api_key_header(self) -> None:
        transport = QueueTransport([response(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": contract_json()}]}}]})])
        connector = GeminiOAuthGenerateContentConnector(
            ConnectorConfig(resource_id="resource-a", model="gemini-test", api_key_reference="w1-account:test"),
            transport=transport,
            secret_resolver=StaticSecretResolver({"w1-account:test": "oauth-access-token"}),
        )
        connector.invoke(request_fixture())
        sent = transport.requests[0]
        self.assertEqual("Bearer oauth-access-token", sent.headers["authorization"])
        self.assertNotIn("x-goog-api-key", sent.headers)

    def test_gemini_oauth_connector_uses_bearer_not_api_key_header(self) -> None:
        transport = QueueTransport([response(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": contract_json()}]}}]})])
        connector = GeminiOAuthGenerateContentConnector(
            ConnectorConfig(resource_id="resource-a", model="gemini-test", api_key_reference="w1-account:test"),
            transport=transport,
            secret_resolver=StaticSecretResolver({"w1-account:test": "oauth-access-token"}),
        )
        connector.invoke(request_fixture())
        sent = transport.requests[0]
        self.assertEqual("Bearer oauth-access-token", sent.headers["authorization"])
        self.assertNotIn("x-goog-api-key", sent.headers)

    def test_gemini_safety_block_is_not_output_contract_failure(self) -> None:
        transport = QueueTransport(
            [response(200, {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []})]
        )
        connector = GeminiGenerateContentConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gemini-test",
                api_key_reference="GEMINI_API_KEY",
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"GEMINI_API_KEY": "secret"}),
        )
        with self.assertRaises(ProviderSafetyBlocked):
            connector.invoke(request_fixture())

    def test_openai_compatible_allows_only_explicit_loopback_http(self) -> None:
        with self.assertRaises(ProviderConfigurationError):
            OpenAICompatibleConnector(
                ConnectorConfig(resource_id="resource-a", model="local-test")
            )
        transport = QueueTransport(
            [
                response(
                    200,
                    {
                        "id": "chatcmpl-local",
                        "choices": [
                            {
                                "message": {"content": contract_json()},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    },
                )
            ]
        )
        connector = OpenAICompatibleConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="qwen-local",
                allow_insecure_loopback=True,
            ),
            transport=transport,
        )
        result = connector.invoke(request_fixture())
        self.assertEqual("candidate-b", result.payload["candidate"])
        self.assertNotIn("authorization", transport.requests[0].headers)

    def test_remote_plain_http_is_rejected_even_when_loopback_flag_is_set(self) -> None:
        with self.assertRaises(ProviderConfigurationError):
            OpenAICompatibleConnector(
                ConnectorConfig(
                    resource_id="resource-a",
                    model="remote-model",
                    endpoint="http://example.com/v1/chat/completions",
                    allow_insecure_loopback=True,
                )
            )

    def test_temporary_rate_limit_is_distinct_from_hard_quota(self) -> None:
        temporary = QueueTransport(
            [
                response(
                    429,
                    {"error": {"type": "rate_limit_exceeded", "message": "requests per minute"}},
                    {"retry-after": "2"},
                )
            ]
        )
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gpt-test",
                api_key_reference="KEY",
            ),
            transport=temporary,
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderRateLimited) as caught:
            connector.invoke(request_fixture())
        self.assertEqual(2.0, caught.exception.retry_after_seconds)

        hard = QueueTransport(
            [response(429, {"error": {"type": "insufficient_quota", "message": "quota"}})]
        )
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gpt-test",
                api_key_reference="KEY",
            ),
            transport=hard,
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderQuotaExhausted):
            connector.invoke(request_fixture())

    def test_gemini_per_minute_vs_per_day_quota_mapping(self) -> None:
        per_minute = QueueTransport(
            [
                response(
                    429,
                    {
                        "error": {
                            "status": "RESOURCE_EXHAUSTED",
                            "details": [{"quotaId": "GenerateRequestsPerMinutePerProject"}],
                        }
                    },
                )
            ]
        )
        connector = GeminiGenerateContentConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gemini-test", api_key_reference="KEY"
            ),
            transport=per_minute,
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderRateLimited):
            connector.invoke(request_fixture())

        per_day = QueueTransport(
            [
                response(
                    429,
                    {
                        "error": {
                            "status": "RESOURCE_EXHAUSTED",
                            "details": [{"quotaId": "GenerateRequestsPerDayPerProject"}],
                        }
                    },
                )
            ]
        )
        connector = GeminiGenerateContentConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gemini-test", api_key_reference="KEY"
            ),
            transport=per_day,
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderQuotaExhausted):
            connector.invoke(request_fixture())

    def test_authentication_and_http_error_mapping(self) -> None:
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gpt-test", api_key_reference="KEY"
            ),
            transport=QueueTransport([response(401, {"error": {"message": "bad key"}})]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderAuthenticationError):
            connector.invoke(request_fixture())

        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gpt-test", api_key_reference="KEY"
            ),
            transport=QueueTransport([response(503, {"error": {"message": "capacity"}})]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderTransientError):
            connector.invoke(request_fixture())

        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gpt-test", api_key_reference="KEY"
            ),
            transport=QueueTransport([response(400, {"error": {"message": "bad request"}})]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderPermanentError):
            connector.invoke(request_fixture())

    def test_transport_failure_preserves_safe_retry_boundary(self) -> None:
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gpt-test", api_key_reference="KEY"
            ),
            transport=QueueTransport([TransportConnectionError()]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderTransientError):
            connector.invoke(request_fixture())

        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gpt-test", api_key_reference="KEY"
            ),
            transport=QueueTransport([TransportOutcomeUncertain()]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderOutcomeUncertain):
            connector.invoke(request_fixture())

    def test_payload_limit_and_resource_mismatch_are_rejected_before_network(self) -> None:
        transport = QueueTransport([])
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gpt-test",
                api_key_reference="KEY",
                max_input_bytes=10,
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderPayloadTooLarge):
            connector.invoke(request_fixture())
        self.assertEqual([], transport.requests)

        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-b", model="gpt-test", api_key_reference="KEY"
            ),
            transport=transport,
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        with self.assertRaises(ProviderConfigurationError):
            connector.invoke(request_fixture(resource_id="resource-a"))

    def test_missing_environment_secret_is_stable_and_does_not_echo_name_value(self) -> None:
        resolver = EnvironmentSecretResolver()
        with self.assertRaises(ProviderConfigurationError) as caught:
            resolver.resolve("W1_CIP_TEST_SECRET_THAT_DOES_NOT_EXIST")
        self.assertEqual("provider_secret_missing", str(caught.exception))

    def test_registry_builds_each_supported_connector(self) -> None:
        registry = ProviderRegistry(
            transport=QueueTransport([]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        self.assertIsInstance(
            registry.build(
                "openai_responses",
                ConnectorConfig(
                    resource_id="openai", model="gpt-test", api_key_reference="KEY"
                ),
            ),
            OpenAIResponsesConnector,
    XAIResponsesConnector,
        )
        self.assertIsInstance(
            registry.build(
                "anthropic_messages",
                ConnectorConfig(
                    resource_id="anthropic", model="claude-test", api_key_reference="KEY"
                ),
            ),
            AnthropicMessagesConnector,
        )
        self.assertIsInstance(
            registry.build(
                "gemini_interactions",
                ConnectorConfig(
                    resource_id="gemini-current", model="gemini-test", api_key_reference="KEY"
                ),
            ),
            GeminiInteractionsConnector,
        )
        self.assertIsInstance(
            registry.build(
                "gemini_generate_content",
                ConnectorConfig(
                    resource_id="gemini", model="gemini-test", api_key_reference="KEY"
                ),
            ),
            GeminiGenerateContentConnector,
    GeminiOAuthGenerateContentConnector,
        )
        self.assertIsInstance(
            registry.build(
                "openai_compatible",
                ConnectorConfig(
                    resource_id="local",
                    model="qwen",
                    allow_insecure_loopback=True,
                ),
            ),
            OpenAICompatibleConnector,
        )
        with self.assertRaises(ProviderConfigurationError):
            registry.build(
                "unknown",
                ConnectorConfig(resource_id="x", model="x", endpoint="https://example.com"),
            )

    def test_estimate_is_deterministic_and_marked_as_estimate_when_usage_missing(self) -> None:
        self.assertEqual(estimate_tokens("abcd"), estimate_tokens("abcd"))
        transport = QueueTransport(
            [
                response(
                    200,
                    {
                        "choices": [
                            {"message": {"content": contract_json()}, "finish_reason": "stop"}
                        ]
                    },
                )
            ]
        )
        connector = OpenAICompatibleConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="local",
                allow_insecure_loopback=True,
                accounting_meter="tokens",
            ),
            transport=transport,
        )
        result = connector.invoke(request_fixture())
        self.assertTrue(result.metadata["usage"]["estimated"])
        self.assertGreater(result.units_consumed, 1)

    def test_connector_default_does_not_claim_provider_idempotency(self) -> None:
        connector = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a", model="gpt-test", api_key_reference="KEY"
            ),
            transport=QueueTransport([]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        self.assertFalse(connector.supports_idempotency)
        trusted_gateway = OpenAIResponsesConnector(
            ConnectorConfig(
                resource_id="resource-a",
                model="gpt-test",
                api_key_reference="KEY",
                supports_idempotency=True,
            ),
            transport=QueueTransport([]),
            secret_resolver=StaticSecretResolver({"KEY": "secret"}),
        )
        self.assertTrue(trusted_gateway.supports_idempotency)


if __name__ == "__main__":
    unittest.main()
