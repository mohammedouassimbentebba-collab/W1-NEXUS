"""One-shot subprocess host for W1 plugins.

This module is intentionally small. The parent process performs manifest,
permission and integrity checks before starting the host. The host captures all
plugin stdout/stderr so the stdout protocol remains a single JSON document.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping

# Allow direct execution from a source checkout as well as an installed wheel.
_PACKAGE_PARENT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from w1cip.sdk import PluginContext  # noqa: E402


def _load(entrypoint: str, plugin_root: Path) -> Any:
    if ":" not in entrypoint:
        raise ValueError("plugin_entrypoint_invalid")
    module_name, symbol_name = entrypoint.split(":", 1)
    sys.path.insert(0, str(plugin_root))
    module = importlib.import_module(module_name)
    symbol = getattr(module, symbol_name, None)
    if not callable(symbol):
        raise ValueError("plugin_entrypoint_not_callable")
    instance = symbol()
    return instance


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(code)
    # Ensure the result is JSON serializable and detached from plugin objects.
    return json.loads(json.dumps(dict(value), ensure_ascii=False))


def run(payload: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(str(payload["plugin_root"])).resolve()
    instance = _load(str(payload["entrypoint"]), root)
    context_payload = payload.get("context", {})
    context = PluginContext(
        plugin_id=str(context_payload.get("plugin_id", "")),
        plugin_version=str(context_payload.get("plugin_version", "")),
        api_version=str(context_payload.get("api_version", "")),
        granted_permissions=tuple(context_payload.get("granted_permissions", ())),
        workspace_id=context_payload.get("workspace_id"),
        metadata=dict(context_payload.get("metadata", {})),
    )
    started: dict[str, Any] = {}
    stopped: dict[str, Any] = {}
    if hasattr(instance, "start"):
        started = _mapping(instance.start(context), "plugin_start_contract_invalid")
    operation = str(payload.get("operation", "health"))
    if operation == "health":
        result = _mapping(instance.health() if hasattr(instance, "health") else {"ok": True}, "plugin_health_contract_invalid")
    elif operation == "provider.invoke":
        method = getattr(instance, "invoke_provider", None)
        if not callable(method):
            method = getattr(instance, "invoke", None)
        if not callable(method):
            raise ValueError("plugin_provider_invoke_missing")
        result = _mapping(method(dict(payload.get("request", {}))), "plugin_provider_response_invalid")
        if not isinstance(result.get("output_type"), str) or not isinstance(result.get("payload"), dict):
            raise ValueError("plugin_provider_response_contract_invalid")
    else:
        raise ValueError("plugin_operation_unsupported")
    health = _mapping(instance.health() if hasattr(instance, "health") else {"ok": True}, "plugin_health_contract_invalid")
    if hasattr(instance, "stop"):
        stopped = _mapping(instance.stop(), "plugin_stop_contract_invalid")
    return {"ok": True, "result": result, "health": health, "start": started, "stop": stopped}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("plugin_host_payload_object_required")
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            response = run(payload)
        response["plugin_stdout"] = captured_out.getvalue()[-4096:]
        response["plugin_stderr"] = captured_err.getvalue()[-4096:]
    except Exception as exc:
        response = {
            "ok": False,
            "error_code": getattr(exc, "code", str(exc) or "plugin_host_error"),
            "error_type": type(exc).__name__,
            "traceback_tail": traceback.format_exc(limit=4)[-4096:],
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
    return 0 if response.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
