"""W1-specific MCP tools, resources, prompts, and configuration helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .action_runtime import ActionRequest, ActionRuntime, ActionApprovalRequired
from .mcp import (
    MCPConfigurationError,
    MCPManager,
    MCPServer,
    MCPServerConfig,
    PromptDescriptor,
    ResourceDescriptor,
    ToolDescriptor,
    ToolRegistry,
)
from .session_store import SessionNotFoundError, SessionStore


DEFAULT_MCP_DOCUMENT: dict[str, Any] = {
    "protocol_version": "2025-11-25",
    "servers": [],
}


def load_mcp_configs(path: str | Path) -> list[MCPServerConfig]:
    selected = Path(path)
    if not selected.exists():
        return []
    try:
        document = json.loads(selected.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigurationError(f"invalid MCP configuration JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("servers"), list):
        raise MCPConfigurationError("mcp-servers.json must contain a servers array")
    configs: list[MCPServerConfig] = []
    seen: set[str] = set()
    for index, raw in enumerate(document["servers"]):
        if not isinstance(raw, dict):
            raise MCPConfigurationError(f"MCP server entry {index} must be an object")
        config = MCPServerConfig.from_mapping(raw)
        if config.server_id in seen:
            raise MCPConfigurationError(f"duplicate MCP server id: {config.server_id}")
        seen.add(config.server_id)
        configs.append(config)
    return configs


def write_mcp_configs(path: str | Path, configs: Iterable[MCPServerConfig]) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol_version": "2025-11-25",
        "servers": [config.redacted() for config in sorted(configs, key=lambda item: item.server_id)],
    }
    temporary = selected.with_suffix(selected.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(selected)


def _safe_workspace_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes workspace") from exc
    if any(part == ".w1nexus" for part in candidate.relative_to(root).parts):
        raise ValueError(".w1nexus is not exposed as a general workspace resource")
    if candidate.is_symlink():
        raise ValueError("symbolic links are not exposed")
    return candidate


def register_w1_local_tools(
    registry: ToolRegistry,
    *,
    workspace_root: str | Path,
    state_dir: str | Path,
    session_db: str | Path,
    trusted_recorders: set[tuple[str, str]] | None = None,
) -> None:
    root = Path(workspace_root).resolve()
    state = Path(state_dir).resolve()

    def read_text(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        path = _safe_workspace_path(root, str(arguments["path"]))
        if not path.is_file():
            return {"content": [{"type": "text", "text": "File not found"}], "isError": True, "structuredContent": {"error_code": "file_not_found"}}
        max_bytes = int(arguments.get("max_bytes", 200_000))
        data = path.read_bytes()
        truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace")
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"path": str(path.relative_to(root)), "text": text, "truncated": truncated, "size": len(data)},
            "isError": False,
        }

    def list_files(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        relative = str(arguments.get("path", "."))
        base = _safe_workspace_path(root, relative)
        if not base.exists() or not base.is_dir():
            return {"content": [{"type": "text", "text": "Directory not found"}], "isError": True, "structuredContent": {"error_code": "directory_not_found"}}
        limit = int(arguments.get("limit", 200))
        files: list[str] = []
        for path in sorted(base.rglob("*")):
            if len(files) >= limit:
                break
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if ".w1nexus" in rel.parts or path.is_symlink():
                continue
            files.append(str(rel).replace("\\", "/") + ("/" if path.is_dir() else ""))
        return {"content": [{"type": "text", "text": "\n".join(files)}], "structuredContent": {"files": files, "truncated": len(files) >= limit}, "isError": False}

    def action_plan(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        request = ActionRequest(
            action_id=str(arguments["action_id"]),
            kind=str(arguments["kind"]),
            parameters=dict(arguments.get("parameters", {})),
            requested_by=str(arguments.get("requested_by", "mcp-client")),
            reason=(str(arguments["reason"]) if arguments.get("reason") else None),
        )
        with ActionRuntime(root, state_dir=state) as runtime:
            plan = runtime.plan(request)
        payload = asdict(plan)
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}], "structuredContent": payload, "isError": False}

    def action_execute(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        request = ActionRequest(
            action_id=str(arguments["action_id"]),
            kind=str(arguments["kind"]),
            parameters=dict(arguments.get("parameters", {})),
            requested_by=str(arguments.get("requested_by", "mcp-client")),
            reason=(str(arguments["reason"]) if arguments.get("reason") else None),
        )
        approval = arguments.get("approval")
        with ActionRuntime(root, state_dir=state) as runtime:
            try:
                result = runtime.execute(request, approval=approval if isinstance(approval, dict) else None)
            except ActionApprovalRequired as exc:
                payload = {"error_code": "action_approval_required", "plan": asdict(exc.plan)}
                return {"content": [{"type": "text", "text": "Action approval required"}], "structuredContent": payload, "isError": True}
        payload = asdict(result)
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}], "structuredContent": payload, "isError": result.status != "completed"}

    def verify_session(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        with SessionStore(session_db, trusted_recorders=trusted_recorders or set()) as store:
            try:
                report = store.verify_integrity(str(arguments["session_id"]))
            except SessionNotFoundError:
                return {"content": [{"type": "text", "text": "Session not found"}], "structuredContent": {"error_code": "session_not_found"}, "isError": True}
        payload = {"valid": report.valid, "errors": list(report.errors), "last_sequence": report.last_sequence, "last_event_hash": report.last_event_hash}
        return {"content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}], "structuredContent": payload, "isError": not report.valid}

    local_tools = (
        (
            ToolDescriptor(
                name="w1.workspace.read_text",
                title="Read workspace text",
                description="Read one UTF-8 text file inside the W1 workspace without exposing .w1nexus.",
                input_schema={"type": "object", "properties": {"path": {"type": "string", "minLength": 1}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1000000}}, "required": ["path"], "additionalProperties": False},
                output_schema={"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}, "truncated": {"type": "boolean"}, "size": {"type": "integer"}}, "required": ["path", "text", "truncated", "size"]},
                annotations={"readOnlyHint": True}, risk="read", requires_approval=False,
            ), read_text,
        ),
        (
            ToolDescriptor(
                name="w1.workspace.list_files",
                title="List workspace files",
                description="List files and directories inside the W1 workspace.",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 2000}}, "additionalProperties": False},
                output_schema={"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}, "truncated": {"type": "boolean"}}, "required": ["files", "truncated"]},
                annotations={"readOnlyHint": True}, risk="read", requires_approval=False,
            ), list_files,
        ),
        (
            ToolDescriptor(
                name="w1.action.plan",
                title="Plan governed action",
                description="Evaluate an Action Runtime request without executing it.",
                input_schema={"type": "object", "properties": {"action_id": {"type": "string"}, "kind": {"type": "string"}, "parameters": {"type": "object"}, "requested_by": {"type": "string"}, "reason": {"type": "string"}}, "required": ["action_id", "kind"], "additionalProperties": False},
                annotations={"readOnlyHint": True}, risk="read", requires_approval=False,
            ), action_plan,
        ),
        (
            ToolDescriptor(
                name="w1.action.execute",
                title="Execute governed action",
                description="Execute an Action Runtime request. Sensitive actions still require their Action Runtime approval.",
                input_schema={"type": "object", "properties": {"action_id": {"type": "string"}, "kind": {"type": "string"}, "parameters": {"type": "object"}, "requested_by": {"type": "string"}, "reason": {"type": "string"}, "approval": {"type": "object"}}, "required": ["action_id", "kind"], "additionalProperties": False},
                annotations={"destructiveHint": True}, risk="sensitive", requires_approval=True,
            ), action_execute,
        ),
        (
            ToolDescriptor(
                name="w1.session.verify",
                title="Verify session ledger",
                description="Verify the append-only W1-CIP session hash chain and replay projection.",
                input_schema={"type": "object", "properties": {"session_id": {"type": "string", "minLength": 1}}, "required": ["session_id"], "additionalProperties": False},
                annotations={"readOnlyHint": True}, risk="read", requires_approval=False,
            ), verify_session,
        ),
    )
    for descriptor, handler in local_tools:
        registry.register_local(descriptor, handler)


@dataclass
class W1RegistryBundle:
    registry: ToolRegistry
    manager: MCPManager

    def close(self) -> None:
        self.registry.close()

    def __enter__(self) -> "W1RegistryBundle":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_registry_bundle(
    *,
    workspace_root: str | Path,
    state_dir: str | Path,
    session_db: str | Path,
    mcp_config_path: str | Path,
    trusted_recorders: set[tuple[str, str]] | None = None,
    include_remote: bool = True,
    server_ids: Iterable[str] | None = None,
) -> W1RegistryBundle:
    registry = ToolRegistry(state_dir)
    register_w1_local_tools(
        registry,
        workspace_root=workspace_root,
        state_dir=state_dir,
        session_db=session_db,
        trusted_recorders=trusted_recorders,
    )
    manager = MCPManager(load_mcp_configs(mcp_config_path))
    if include_remote:
        manager.populate_registry(registry, server_ids=server_ids)
    return W1RegistryBundle(registry=registry, manager=manager)


def build_w1_mcp_server(
    *,
    workspace_root: str | Path,
    state_dir: str | Path,
    session_db: str | Path,
    mcp_config_path: str | Path,
    workspace_config_path: str | Path,
    trusted_recorders: set[tuple[str, str]] | None = None,
) -> tuple[MCPServer, W1RegistryBundle]:
    bundle = build_registry_bundle(
        workspace_root=workspace_root,
        state_dir=state_dir,
        session_db=session_db,
        mcp_config_path=mcp_config_path,
        trusted_recorders=trusted_recorders,
        include_remote=False,
    )
    config_path = Path(workspace_config_path)
    capabilities_uri = "w1://capabilities"
    workspace_uri = "w1://workspace/config"
    resources = (
        ResourceDescriptor(uri=capabilities_uri, name="W1 Nexus capabilities", description="Implemented W1 runtime capabilities", mime_type="application/json"),
        ResourceDescriptor(uri=workspace_uri, name="W1 workspace configuration", description="Redaction-safe workspace configuration", mime_type="application/json"),
    )

    def capabilities_resource() -> Mapping[str, Any]:
        payload = {
            "name": "W1 Nexus",
            "protocol": "W1-CIP",
            "mcp_protocol_version": "2025-11-25",
            "capabilities": ["governed-tools", "session-integrity", "action-approval", "multi-model-orchestration"],
        }
        return {"contents": [{"uri": capabilities_uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]}

    def workspace_resource() -> Mapping[str, Any]:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return {"contents": [{"uri": workspace_uri, "mimeType": "application/json", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]}

    prompts = (
        PromptDescriptor(name="w1-plan-goal", title="Plan a governed goal", description="Prepare a GoalContract-oriented planning prompt", arguments=({"name": "goal", "required": True},)),
        PromptDescriptor(name="w1-review-change", title="Review a change", description="Prepare an independent review prompt", arguments=({"name": "change", "required": True},)),
    )
    prompt_getters = {
        "w1-plan-goal": lambda args: {"description": "Governed W1 planning prompt", "messages": [{"role": "user", "content": {"type": "text", "text": f"Create a GoalContract and task plan for: {args.get('goal', '')}"}}]},
        "w1-review-change": lambda args: {"description": "Independent W1 review prompt", "messages": [{"role": "user", "content": {"type": "text", "text": f"Review this change independently, identify evidence gaps and challenges: {args.get('change', '')}"}}]},
    }
    server = MCPServer(
        registry=bundle.registry,
        resources=resources,
        resource_readers={capabilities_uri: capabilities_resource, workspace_uri: workspace_resource},
        prompts=prompts,
        prompt_getters=prompt_getters,
    )
    return server, bundle
