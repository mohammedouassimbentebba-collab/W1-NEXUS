"""Small deterministic MCP server used by W1 tests and offline examples."""

from __future__ import annotations

import tempfile
from pathlib import Path

from .mcp import MCPServer, MCPStdioServer, PromptDescriptor, ResourceDescriptor, ToolDescriptor, ToolRegistry


def build_server() -> tuple[MCPServer, ToolRegistry, tempfile.TemporaryDirectory[str]]:
    temp = tempfile.TemporaryDirectory()
    registry = ToolRegistry(Path(temp.name))
    registry.register_local(
        ToolDescriptor(
            name="echo",
            title="Echo",
            description="Return the supplied text.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
            output_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False},
            annotations={"readOnlyHint": True},
        ),
        lambda args: {"content": [{"type": "text", "text": str(args["text"])}], "structuredContent": {"text": str(args["text"])}, "isError": False},
    )
    registry.register_local(
        ToolDescriptor(
            name="sum",
            title="Sum",
            description="Add two numbers.",
            input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"], "additionalProperties": False},
            output_schema={"type": "object", "properties": {"value": {"type": "number"}}, "required": ["value"], "additionalProperties": False},
            annotations={"readOnlyHint": True},
        ),
        lambda args: {"content": [{"type": "text", "text": str(args["a"] + args["b"])}], "structuredContent": {"value": args["a"] + args["b"]}, "isError": False},
    )
    resource = ResourceDescriptor(uri="w1-demo://status", name="Demo status", description="Deterministic test resource", mime_type="application/json")
    prompt = PromptDescriptor(name="review", title="Review", description="Create a review prompt", arguments=({"name": "subject", "required": True},))
    server = MCPServer(
        registry=registry,
        server_name="w1-mcp-demo",
        resources=(resource,),
        resource_readers={"w1-demo://status": lambda: {"contents": [{"uri": "w1-demo://status", "mimeType": "application/json", "text": '{"ok":true}'}]}},
        prompts=(prompt,),
        prompt_getters={"review": lambda args: {"description": "Review prompt", "messages": [{"role": "user", "content": {"type": "text", "text": f"Review {args.get('subject', '')}"}}]}},
    )
    return server, registry, temp


def main() -> int:
    server, registry, temp = build_server()
    try:
        MCPStdioServer(server).serve_forever()
    finally:
        registry.close()
        temp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
