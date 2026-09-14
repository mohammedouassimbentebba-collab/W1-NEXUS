from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from w1cip.mcp import (
    MCPConfigurationError,
    MCPManager,
    MCPServer,
    MCPServerConfig,
    MCPStdioClient,
    MCPStreamableHTTPClient,
    ToolApprovalInvalid,
    ToolApprovalRequired,
    ToolDescriptor,
    ToolRegistry,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeRemoteClient:
    def __init__(self) -> None:
        self.server_id = "remote"
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def initialize(self) -> Mapping[str, Any]:
        return {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}

    def list_tools(self):
        return []

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"ok": True}, "isError": False}

    def list_resources(self):
        return []

    def read_resource(self, uri):
        return {}

    def list_prompts(self):
        return []

    def get_prompt(self, name, arguments):
        return {}

    def close(self):
        return None


class MCPToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_local_tool_schema_validation_idempotency_and_conflict(self) -> None:
        with ToolRegistry(self.state) as registry:
            registry.register_local(
                ToolDescriptor(
                    name="local.echo",
                    title="Echo",
                    description="Echo text",
                    input_schema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                    output_schema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                ),
                lambda args: {
                    "content": [{"type": "text", "text": args["text"]}],
                    "structuredContent": {"text": args["text"]},
                    "isError": False,
                },
            )
            result = registry.call(call_id="echo-one", tool_name="local.echo", arguments={"text": "hello"})
            repeated = registry.call(call_id="echo-one", tool_name="local.echo", arguments={"text": "hello"})
            self.assertEqual(asdict(result), asdict(repeated))
            self.assertEqual({"text": "hello"}, result.structured_content)
            with self.assertRaises(Exception) as invalid:
                registry.plan(call_id="echo-two", tool_name="local.echo", arguments={"text": 1})
            self.assertIn("validation", str(invalid.exception))
            with self.assertRaises(Exception):
                registry.call(call_id="echo-one", tool_name="local.echo", arguments={"text": "different"})

    def test_remote_metadata_does_not_bypass_explicit_local_approval(self) -> None:
        client = FakeRemoteClient()
        config = MCPServerConfig(server_id="remote", transport="stdio", command=(sys.executable, "-c", "pass"))
        remote_tool = {
            "name": "read_everything",
            "description": "Claims to be read only",
            "inputSchema": {"type": "object", "additionalProperties": False},
            "outputSchema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        }
        with ToolRegistry(self.state) as registry:
            registry.register_remote(config=config, tools=[remote_tool], client_factory=lambda: client)
            plan = registry.plan(call_id="remote-one", tool_name="mcp.remote.read_everything", arguments={})
            self.assertTrue(plan.requires_approval)
            with self.assertRaises(ToolApprovalRequired):
                registry.call(call_id="remote-one", tool_name=plan.tool_name, arguments={})
            approval = registry.issue_approval(plan, issued_by="human-owner", ttl_seconds=60)
            result = registry.call(call_id="remote-one", tool_name=plan.tool_name, arguments={}, approval=approval)
            self.assertFalse(result.is_error)
            self.assertEqual([("read_everything", {})], client.calls)
            with self.assertRaises(ToolApprovalInvalid):
                registry.call(call_id="remote-two", tool_name=plan.tool_name, arguments={}, approval=approval)

    def test_explicit_auto_approve_policy_is_local_and_tool_specific(self) -> None:
        client = FakeRemoteClient()
        config = MCPServerConfig(
            server_id="remote",
            transport="stdio",
            command=(sys.executable, "-c", "pass"),
            auto_approve_tools=("safe_read",),
        )
        tools = [
            {"name": "safe_read", "inputSchema": {"type": "object"}},
            {"name": "other", "inputSchema": {"type": "object"}},
        ]
        with ToolRegistry(self.state) as registry:
            registry.register_remote(config=config, tools=tools, client_factory=lambda: client)
            safe = registry.plan(call_id="safe-one", tool_name="mcp.remote.safe_read", arguments={})
            other = registry.plan(call_id="other-one", tool_name="mcp.remote.other", arguments={})
            self.assertFalse(safe.requires_approval)
            self.assertTrue(other.requires_approval)

    def test_stdio_lifecycle_tools_resources_and_prompts(self) -> None:
        old_pythonpath = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(ROOT / "src")
        config = MCPServerConfig(
            server_id="demo",
            transport="stdio",
            command=(sys.executable, "-m", "w1cip.mcp_demo_server"),
            environment={"PYTHONPATH": "PYTHONPATH"},
        )
        try:
            with MCPStdioClient(config) as client:
                initialized = client.initialize()
                self.assertEqual("2025-11-25", initialized["protocolVersion"])
                tools = client.list_tools()
                self.assertEqual(["echo", "sum"], [item["name"] for item in tools])
                called = client.call_tool("sum", {"a": 2, "b": 3})
                self.assertEqual(5, called["structuredContent"]["value"])
                resources = client.list_resources()
                self.assertEqual("w1-demo://status", resources[0]["uri"])
                self.assertIn("ok", client.read_resource("w1-demo://status")["contents"][0]["text"])
                prompts = client.list_prompts()
                self.assertEqual("review", prompts[0]["name"])
                prompt = client.get_prompt("review", {"subject": "code"})
                self.assertIn("Review code", prompt["messages"][0]["content"]["text"])
        finally:
            if old_pythonpath is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old_pythonpath

    def test_server_requires_initialization_and_preserves_sensitive_approval_gate(self) -> None:
        with ToolRegistry(self.state) as registry:
            registry.register_local(
                ToolDescriptor(
                    name="danger",
                    title=None,
                    description="Sensitive tool",
                    input_schema={"type": "object"},
                    risk="sensitive",
                    requires_approval=True,
                ),
                lambda args: {"content": [{"type": "text", "text": "ran"}], "isError": False},
            )
            server = MCPServer(registry=registry)
            before = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
            self.assertEqual(-32002, before["error"]["code"])
            initialized = server.handle({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            })
            self.assertEqual("2025-11-25", initialized["result"]["protocolVersion"])
            result = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "danger", "arguments": {}}})
            self.assertTrue(result["result"]["isError"])
            self.assertEqual("tool_approval_required", result["result"]["structuredContent"]["error_code"])

    def test_streamable_http_json_round_trip(self) -> None:
        registry = ToolRegistry(self.state)
        registry.register_local(
            ToolDescriptor(
                name="echo",
                title=None,
                description="echo",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            ),
            lambda args: {"content": [{"type": "text", "text": args["text"]}], "isError": False},
        )
        server = MCPServer(registry=registry)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                message = json.loads(self.rfile.read(length))
                response = server.handle(message)
                if response is None:
                    self.send_response(202)
                    self.end_headers()
                    return
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                return None

        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        config = MCPServerConfig(
            server_id="http-demo",
            transport="streamable_http",
            url=f"http://127.0.0.1:{http.server_port}/mcp",
            allow_insecure_loopback=True,
        )
        try:
            with MCPStreamableHTTPClient(config) as client:
                self.assertEqual("2025-11-25", client.initialize()["protocolVersion"])
                self.assertEqual("echo", client.list_tools()[0]["name"])
                self.assertEqual("hello", client.call_tool("echo", {"text": "hello"})["content"][0]["text"])
        finally:
            http.shutdown()
            http.server_close()
            registry.close()

    def test_remote_http_requires_https_except_explicit_loopback(self) -> None:
        with self.assertRaises(MCPConfigurationError):
            MCPServerConfig(server_id="remote", transport="streamable_http", url="http://example.com/mcp")
        config = MCPServerConfig(
            server_id="local",
            transport="streamable_http",
            url="http://127.0.0.1:1234/mcp",
            allow_insecure_loopback=True,
        )
        self.assertEqual("local", config.server_id)

    def test_config_redaction_contains_secret_references_not_values(self) -> None:
        os.environ["TEST_MCP_TOKEN"] = "do-not-store-this-value"
        config = MCPServerConfig(
            server_id="secure",
            transport="streamable_http",
            url="https://example.com/mcp",
            bearer_token_env="TEST_MCP_TOKEN",
        )
        rendered = json.dumps(config.redacted())
        self.assertIn("TEST_MCP_TOKEN", rendered)
        self.assertNotIn("do-not-store-this-value", rendered)


if __name__ == "__main__":
    unittest.main()
