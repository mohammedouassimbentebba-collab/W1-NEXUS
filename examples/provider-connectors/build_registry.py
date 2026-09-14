"""Build configured provider adapters without sending a live request."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from w1cip.provider_connectors import ConnectorConfig, ProviderRegistry  # noqa: E402


def main() -> None:
    config_path = Path(__file__).with_name("providers.example.json")
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    registry = ProviderRegistry()
    providers = {}
    for item in raw["providers"]:
        provider_type = item.pop("type")
        connector = registry.build(provider_type, ConnectorConfig(**item))
        providers[connector.resource_id] = connector
    print("Built connectors:")
    for resource_id, connector in providers.items():
        print(f"- {resource_id}: {connector.__class__.__name__}")
    print("No API request was sent and no secret was read.")


if __name__ == "__main__":
    main()
