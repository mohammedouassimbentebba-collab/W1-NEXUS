"""Offline demonstration of the OpenAI Responses connector contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.orchestrator import CompiledTask, ProviderRequest  # noqa: E402
from w1cip.provider_connectors import (  # noqa: E402
    ConnectorConfig,
    HTTPResponse,
    OpenAIResponsesConnector,
    StaticSecretResolver,
)


class DemoTransport:
    def send(self, request):
        contract = {
            "output_type": "contribution",
            "payload": {"candidate": "candidate-b"},
            "context_fields_used": ["supply-voltage"],
            "protocol_envelopes": [],
        }
        body = {
            "id": "resp-demo",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(contract)}],
                }
            ],
            "usage": {"input_tokens": 40, "output_tokens": 15, "total_tokens": 55},
        }
        return HTTPResponse(
            200,
            {
                "x-request-id": "req-demo",
                "x-ratelimit-remaining-requests": "99",
            },
            json.dumps(body).encode(),
        )


def main() -> None:
    connector = OpenAIResponsesConnector(
        ConnectorConfig(
            resource_id="demo-resource",
            model="demo-model",
            api_key_reference="DEMO_KEY",
            accounting_meter="tokens",
        ),
        transport=DemoTransport(),
        secret_resolver=StaticSecretResolver({"DEMO_KEY": "not-a-real-secret"}),
    )
    result = connector.invoke(
        ProviderRequest(
            run_id="demo-run",
            session_id="demo-session",
            task=CompiledTask(
                task_id="select-driver",
                title="Select driver",
                phase="execution",
                role="executor",
                expected_output_type="contribution",
                required_context_fields=("supply-voltage",),
            ),
            resource_id="demo-resource",
            idempotency_key="demo-idempotency-key",
            context={"supply-voltage": 12},
            prior_outputs={},
            attempt=1,
        )
    )
    print(json.dumps({
        "payload": result.payload,
        "units_consumed": result.units_consumed,
        "metadata": result.metadata,
    }, indent=2))


if __name__ == "__main__":
    main()
