from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from w1cip.mcp import MCPServerConfig, MCPStdioClient, ToolRegistry


def main() -> int:
    source_root = Path(__file__).resolve().parents[2] / "src"
    os.environ.setdefault("PYTHONPATH", str(source_root))
    config = MCPServerConfig(
        server_id="demo",
        transport="stdio",
        command=(sys.executable, "-m", "w1cip.mcp_demo_server"),
        environment={"PYTHONPATH": "PYTHONPATH"},
        auto_approve_tools=("echo",),
    )
    with tempfile.TemporaryDirectory() as temp:
        with MCPStdioClient(config) as client:
            tools = client.list_tools()
            result = client.call_tool("echo", {"text": "W1 MCP is connected"})
        with ToolRegistry(Path(temp)) as registry:
            registry.register_remote(config=config, tools=tools, client_factory=lambda: MCPStdioClient(config))
            unified = [item.name for item in registry.list_tools()]
            call = registry.call(
                call_id="demo-echo-one",
                tool_name="mcp.demo.echo",
                arguments={"text": "governed through W1"},
            )
        print({"discovered": unified, "direct": result, "governed": call.structured_content})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
