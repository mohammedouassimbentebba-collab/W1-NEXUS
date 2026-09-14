"""MCP client/server and unified governed tool registry for W1 Nexus.

The implementation targets the stable Model Context Protocol revision 2025-11-25.
It supports the required JSON-RPC lifecycle, stdio, a conservative subset of
Streamable HTTP, tools/resources/prompts, deterministic discovery, schema
validation, namespacing, audit logging, and one-time digest-bound approvals.

Remote tool metadata is never treated as a security boundary.  Automatic
approval is controlled only by explicit local W1 configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, ValidationError


MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SERVER_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CALL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class MCPError(RuntimeError):
    code = "mcp_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class MCPConfigurationError(MCPError):
    code = "mcp_configuration_error"


class MCPTransportError(MCPError):
    code = "mcp_transport_error"


class MCPTimeoutError(MCPTransportError):
    code = "mcp_timeout"


class MCPProtocolError(MCPError):
    code = "mcp_protocol_error"

    def __init__(self, message: str, *, rpc_code: int | None = None, data: Any = None) -> None:
        self.rpc_code = rpc_code
        self.data = data
        super().__init__(message)


class MCPToolError(MCPError):
    code = "mcp_tool_error"


class ToolRegistryError(MCPError):
    code = "tool_registry_error"


class ToolNotFound(ToolRegistryError):
    code = "tool_not_found"


class ToolApprovalRequired(ToolRegistryError):
    code = "tool_approval_required"

    def __init__(self, plan: "ToolCallPlan") -> None:
        self.plan = plan
        super().__init__(f"Approval required for {plan.tool_name} ({plan.call_digest})")


class ToolApprovalInvalid(ToolRegistryError):
    code = "tool_approval_invalid"


class ToolCallConflict(ToolRegistryError):
    code = "tool_call_conflict"


@dataclass(frozen=True)
class MCPServerConfig:
    server_id: str
    transport: str
    command: tuple[str, ...] = ()
    url: str | None = None
    enabled: bool = True
    timeout_seconds: int = 30
    environment: Mapping[str, str] = field(default_factory=dict)
    headers_from_env: Mapping[str, str] = field(default_factory=dict)
    bearer_token_env: str | None = None
    trust_mode: str = "untrusted"
    auto_approve_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    workspace_root: str | None = None
    allow_insecure_loopback: bool = False

    def __post_init__(self) -> None:
        if not SERVER_ID_RE.fullmatch(self.server_id):
            raise MCPConfigurationError("server_id must be a canonical lowercase identifier")
        if self.transport not in {"stdio", "streamable_http"}:
            raise MCPConfigurationError("transport must be stdio or streamable_http")
        if self.timeout_seconds < 1 or self.timeout_seconds > 600:
            raise MCPConfigurationError("timeout_seconds must be between 1 and 600")
        if self.trust_mode not in {"untrusted", "trusted"}:
            raise MCPConfigurationError("trust_mode must be untrusted or trusted")
        if self.transport == "stdio" and not self.command:
            raise MCPConfigurationError("stdio transport requires a command array")
        if self.transport == "streamable_http":
            if not self.url:
                raise MCPConfigurationError("streamable_http transport requires a URL")
            parsed = urllib.parse.urlparse(self.url)
            loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme != "https" and not (loopback and self.allow_insecure_loopback):
                raise MCPConfigurationError("remote MCP endpoints require HTTPS")
        for tool_name in (*self.auto_approve_tools, *self.allowed_tools):
            if not TOOL_NAME_RE.fullmatch(tool_name):
                raise MCPConfigurationError(f"invalid tool name in policy: {tool_name}")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MCPServerConfig":
        allowed = {
            "server_id", "transport", "command", "url", "enabled", "timeout_seconds",
            "environment", "headers_from_env", "bearer_token_env", "trust_mode",
            "auto_approve_tools", "allowed_tools", "workspace_root",
            "allow_insecure_loopback", "description",
        }
        unknown = set(value) - allowed
        if unknown:
            raise MCPConfigurationError(f"unknown MCP server fields: {', '.join(sorted(unknown))}")
        return cls(
            server_id=str(value["server_id"]),
            transport=str(value["transport"]),
            command=tuple(str(item) for item in value.get("command", ())),
            url=str(value["url"]) if value.get("url") is not None else None,
            enabled=bool(value.get("enabled", True)),
            timeout_seconds=int(value.get("timeout_seconds", 30)),
            environment={str(k): str(v) for k, v in dict(value.get("environment", {})).items()},
            headers_from_env={str(k): str(v) for k, v in dict(value.get("headers_from_env", {})).items()},
            bearer_token_env=(str(value["bearer_token_env"]) if value.get("bearer_token_env") else None),
            trust_mode=str(value.get("trust_mode", "untrusted")),
            auto_approve_tools=tuple(str(x) for x in value.get("auto_approve_tools", ())),
            allowed_tools=tuple(str(x) for x in value.get("allowed_tools", ())),
            workspace_root=(str(value["workspace_root"]) if value.get("workspace_root") else None),
            allow_insecure_loopback=bool(value.get("allow_insecure_loopback", False)),
        )

    def redacted(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "transport": self.transport,
            "command": list(self.command),
            "url": self.url,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "environment": dict(self.environment),
            "headers_from_env": dict(self.headers_from_env),
            "bearer_token_env": self.bearer_token_env,
            "trust_mode": self.trust_mode,
            "auto_approve_tools": list(self.auto_approve_tools),
            "allowed_tools": list(self.allowed_tools),
            "workspace_root": self.workspace_root,
            "allow_insecure_loopback": self.allow_insecure_loopback,
        }


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    title: str | None
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None = None
    annotations: Mapping[str, Any] = field(default_factory=dict)
    source: str = "local"
    server_id: str | None = None
    original_name: str | None = None
    risk: str = "read"
    requires_approval: bool = False
    trust_mode: str = "trusted"

    def __post_init__(self) -> None:
        if not TOOL_NAME_RE.fullmatch(self.name):
            raise MCPConfigurationError(f"invalid tool name: {self.name}")
        Draft202012Validator.check_schema(dict(self.input_schema))
        if self.output_schema is not None:
            Draft202012Validator.check_schema(dict(self.output_schema))
        if self.risk not in {"read", "write", "sensitive"}:
            raise MCPConfigurationError("tool risk must be read, write, or sensitive")

    def mcp_document(self, *, expose_name: str | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": expose_name or self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }
        if self.title:
            value["title"] = self.title
        if self.output_schema is not None:
            value["outputSchema"] = dict(self.output_schema)
        if self.annotations:
            value["annotations"] = dict(self.annotations)
        return value


@dataclass(frozen=True)
class ResourceDescriptor:
    uri: str
    name: str
    description: str = ""
    mime_type: str | None = None
    source: str = "local"
    server_id: str | None = None

    def mcp_document(self) -> dict[str, Any]:
        result: dict[str, Any] = {"uri": self.uri, "name": self.name}
        if self.description:
            result["description"] = self.description
        if self.mime_type:
            result["mimeType"] = self.mime_type
        return result


@dataclass(frozen=True)
class PromptDescriptor:
    name: str
    description: str = ""
    title: str | None = None
    arguments: tuple[Mapping[str, Any], ...] = ()
    source: str = "local"
    server_id: str | None = None

    def mcp_document(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name}
        if self.title:
            result["title"] = self.title
        if self.description:
            result["description"] = self.description
        if self.arguments:
            result["arguments"] = [dict(item) for item in self.arguments]
        return result


@dataclass(frozen=True)
class ToolCallPlan:
    call_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    call_digest: str
    source: str
    server_id: str | None
    risk: str
    requires_approval: bool
    summary: str


@dataclass(frozen=True)
class ToolCallApproval:
    approval_id: str
    call_digest: str
    issued_by: str
    issued_at: str
    expires_at: str
    one_time: bool
    signature: str


@dataclass(frozen=True)
class ToolCallResult:
    call_id: str
    tool_name: str
    status: str
    started_at: str
    completed_at: str
    content: tuple[Mapping[str, Any], ...] = ()
    structured_content: Mapping[str, Any] | None = None
    is_error: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None


class MCPClientProtocol(Protocol):
    server_id: str

    def initialize(self) -> Mapping[str, Any]: ...
    def list_tools(self) -> list[Mapping[str, Any]]: ...
    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def list_resources(self) -> list[Mapping[str, Any]]: ...
    def read_resource(self, uri: str) -> Mapping[str, Any]: ...
    def list_prompts(self) -> list[Mapping[str, Any]]: ...
    def get_prompt(self, name: str, arguments: Mapping[str, str]) -> Mapping[str, Any]: ...
    def close(self) -> None: ...


class MCPStdioClient:
    """Synchronous MCP client over a managed stdio subprocess."""

    def __init__(self, config: MCPServerConfig) -> None:
        if config.transport != "stdio":
            raise MCPConfigurationError("MCPStdioClient requires stdio config")
        self.config = config
        self.server_id = config.server_id
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._write_lock = threading.Lock()
        self._request_id = 0
        self._initialized = False
        self._stderr: list[str] = []
        self.server_info: Mapping[str, Any] | None = None
        self.capabilities: Mapping[str, Any] = {}

    def _child_environment(self) -> dict[str, str]:
        allowed = {name: os.environ[name] for name in ("PATH", "HOME", "USERPROFILE", "TMP", "TEMP", "LANG", "LC_ALL", "SYSTEMROOT", "PATHEXT") if name in os.environ}
        for child_name, source_name in self.config.environment.items():
            if source_name not in os.environ:
                raise MCPConfigurationError(f"missing environment variable for MCP server: {source_name}")
            allowed[child_name] = os.environ[source_name]
        return allowed

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                list(self.config.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.config.workspace_root or None,
                env=self._child_environment(),
                shell=False,
            )
        except OSError as exc:
            raise MCPTransportError(f"failed to start MCP server {self.server_id}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        try:
            for line in self._process.stdout:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._messages.put(MCPProtocolError(f"non-JSON stdout from MCP server: {line[:200]}"))
                    continue
                if not isinstance(value, dict):
                    self._messages.put(MCPProtocolError("MCP stdio message must be an object"))
                    continue
                self._messages.put(value)
        except BaseException as exc:  # pragma: no cover - I/O failure guard
            self._messages.put(exc)

    def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        for line in self._process.stderr:
            if sum(len(item) for item in self._stderr) < 100_000:
                self._stderr.append(line.rstrip("\r\n"))

    def _write(self, message: Mapping[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise MCPTransportError("MCP process is not running")
        payload = canonical_json(message)
        if "\n" in payload or "\r" in payload:
            raise MCPProtocolError("stdio MCP message contains an embedded newline")
        with self._write_lock:
            try:
                self._process.stdin.write(payload + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPTransportError(f"MCP server pipe closed: {exc}") from exc

    def _reply_to_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        if method == "roots/list":
            roots = []
            if self.config.workspace_root:
                roots.append({"uri": Path(self.config.workspace_root).resolve().as_uri(), "name": "W1 Workspace"})
            self._write({"jsonrpc": "2.0", "id": request_id, "result": {"roots": roots}})
        elif method == "ping":
            self._write({"jsonrpc": "2.0", "id": request_id, "result": {}})
        else:
            self._write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unsupported client capability: {method}"}})

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        self.start()
        self._request_id += 1
        request_id = self._request_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._write(message)
        deadline = time.monotonic() + self.config.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTimeoutError(f"MCP request timed out: {method}")
            try:
                incoming = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise MCPTimeoutError(f"MCP request timed out: {method}") from exc
            if isinstance(incoming, BaseException):
                raise MCPTransportError(str(incoming)) from incoming
            if "method" in incoming and "id" in incoming:
                self._reply_to_server_request(incoming)
                continue
            if incoming.get("id") != request_id:
                # Notifications and unrelated responses are safe to ignore in this synchronous client.
                continue
            if "error" in incoming:
                error = incoming["error"]
                raise MCPProtocolError(
                    str(error.get("message", "MCP request failed")),
                    rpc_code=error.get("code"),
                    data=error.get("data"),
                )
            result = incoming.get("result", {})
            if not isinstance(result, dict):
                raise MCPProtocolError("MCP result must be an object")
            return result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self.start()
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._write(message)

    def initialize(self) -> Mapping[str, Any]:
        if self._initialized:
            return {"serverInfo": self.server_info, "capabilities": self.capabilities, "protocolVersion": MCP_PROTOCOL_VERSION}
        result = self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "w1-nexus", "version": "0.1.0-dev50"},
            },
        )
        version = result.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise MCPProtocolError(f"unsupported MCP protocol version: {version}")
        self.server_info = result.get("serverInfo", {})
        self.capabilities = result.get("capabilities", {})
        self.notify("notifications/initialized")
        self._initialized = True
        return result

    def _paged_list(self, method: str, key: str) -> list[Mapping[str, Any]]:
        self.initialize()
        cursor: str | None = None
        results: list[Mapping[str, Any]] = []
        while True:
            params = {"cursor": cursor} if cursor else {}
            page = self.request(method, params)
            items = page.get(key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise MCPProtocolError(f"invalid {key} list")
            results.extend(items)
            next_cursor = page.get("nextCursor")
            if not next_cursor:
                return results
            cursor = str(next_cursor)

    def list_tools(self) -> list[Mapping[str, Any]]:
        return self._paged_list("tools/list", "tools")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.initialize()
        return self.request("tools/call", {"name": name, "arguments": dict(arguments)})

    def list_resources(self) -> list[Mapping[str, Any]]:
        return self._paged_list("resources/list", "resources")

    def read_resource(self, uri: str) -> Mapping[str, Any]:
        self.initialize()
        return self.request("resources/read", {"uri": uri})

    def list_prompts(self) -> list[Mapping[str, Any]]:
        return self._paged_list("prompts/list", "prompts")

    def get_prompt(self, name: str, arguments: Mapping[str, str]) -> Mapping[str, Any]:
        self.initialize()
        return self.request("prompts/get", {"name": name, "arguments": dict(arguments)})

    @property
    def stderr_text(self) -> str:
        return "\n".join(self._stderr)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def __enter__(self) -> "MCPStdioClient":
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class MCPStreamableHTTPClient:
    """Conservative Streamable HTTP client supporting JSON and finite SSE replies."""

    def __init__(self, config: MCPServerConfig) -> None:
        if config.transport != "streamable_http":
            raise MCPConfigurationError("MCPStreamableHTTPClient requires streamable_http config")
        self.config = config
        self.server_id = config.server_id
        self._request_id = 0
        self._initialized = False
        self._session_id: str | None = None
        self.server_info: Mapping[str, Any] | None = None
        self.capabilities: Mapping[str, Any] = {}

    def _headers(self, method: str) -> dict[str, str]:
        result = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._session_id:
            result["Mcp-Session-Id"] = self._session_id
        if self.config.bearer_token_env:
            token = os.environ.get(self.config.bearer_token_env)
            if not token:
                raise MCPConfigurationError(f"missing bearer token environment variable: {self.config.bearer_token_env}")
            result["Authorization"] = f"Bearer {token}"
        for header, env_name in self.config.headers_from_env.items():
            value = os.environ.get(env_name)
            if value is None:
                raise MCPConfigurationError(f"missing header environment variable: {env_name}")
            result[header] = value
        result["Mcp-Method"] = method
        return result

    @staticmethod
    def _parse_sse(text: str, request_id: int) -> Mapping[str, Any]:
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
            if not data_lines:
                continue
            try:
                message = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message
        raise MCPProtocolError("SSE response did not contain the matching JSON-RPC response")

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        request = urllib.request.Request(
            self.config.url,
            data=canonical_json(message).encode("utf-8"),
            headers=self._headers(method),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session
                content_type = response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise MCPTransportError(f"MCP HTTP {exc.code}: {body[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MCPTransportError(f"MCP HTTP request failed: {exc}") from exc
        if content_type == "text/event-stream":
            incoming = self._parse_sse(body, request_id)
        else:
            try:
                incoming = json.loads(body)
            except json.JSONDecodeError as exc:
                raise MCPProtocolError("MCP HTTP response was not JSON") from exc
        if not isinstance(incoming, dict):
            raise MCPProtocolError("MCP HTTP response must be an object")
        if "error" in incoming:
            error = incoming["error"]
            raise MCPProtocolError(str(error.get("message", "MCP request failed")), rpc_code=error.get("code"), data=error.get("data"))
        result = incoming.get("result", {})
        if not isinstance(result, dict):
            raise MCPProtocolError("MCP result must be an object")
        return result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        request = urllib.request.Request(
            self.config.url,
            data=canonical_json(message).encode("utf-8"),
            headers=self._headers(method),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MCPTransportError(f"MCP HTTP notification failed: {exc}") from exc

    def initialize(self) -> Mapping[str, Any]:
        if self._initialized:
            return {"serverInfo": self.server_info, "capabilities": self.capabilities, "protocolVersion": MCP_PROTOCOL_VERSION}
        result = self.request("initialize", {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "w1-nexus", "version": "0.1.0-dev50"}})
        if result.get("protocolVersion") not in SUPPORTED_PROTOCOL_VERSIONS:
            raise MCPProtocolError(f"unsupported MCP protocol version: {result.get('protocolVersion')}")
        self.server_info = result.get("serverInfo", {})
        self.capabilities = result.get("capabilities", {})
        self.notify("notifications/initialized")
        self._initialized = True
        return result

    def _paged_list(self, method: str, key: str) -> list[Mapping[str, Any]]:
        self.initialize()
        cursor: str | None = None
        result: list[Mapping[str, Any]] = []
        while True:
            page = self.request(method, {"cursor": cursor} if cursor else {})
            values = page.get(key, [])
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise MCPProtocolError(f"invalid {key} list")
            result.extend(values)
            cursor_value = page.get("nextCursor")
            if not cursor_value:
                return result
            cursor = str(cursor_value)

    def list_tools(self) -> list[Mapping[str, Any]]:
        return self._paged_list("tools/list", "tools")

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.initialize()
        return self.request("tools/call", {"name": name, "arguments": dict(arguments)})

    def list_resources(self) -> list[Mapping[str, Any]]:
        return self._paged_list("resources/list", "resources")

    def read_resource(self, uri: str) -> Mapping[str, Any]:
        self.initialize()
        return self.request("resources/read", {"uri": uri})

    def list_prompts(self) -> list[Mapping[str, Any]]:
        return self._paged_list("prompts/list", "prompts")

    def get_prompt(self, name: str, arguments: Mapping[str, str]) -> Mapping[str, Any]:
        self.initialize()
        return self.request("prompts/get", {"name": name, "arguments": dict(arguments)})

    def close(self) -> None:
        return None

    def __enter__(self) -> "MCPStreamableHTTPClient":
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


LocalToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]
RemoteClientFactory = Callable[[], MCPClientProtocol]


class ToolRegistry:
    """Unified local and MCP tool catalog with governance and audit."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "tool-registry.sqlite3"
        self.secret_path = self.state_dir / "tool-registry-secret.key"
        self._secret = self._load_or_create_secret()
        self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_calls (
                call_id TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                call_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS tool_approvals (
                approval_id TEXT PRIMARY KEY,
                call_digest TEXT NOT NULL,
                issued_by TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                one_time INTEGER NOT NULL,
                signature TEXT NOT NULL,
                consumed_by_call_id TEXT
            );
            """
        )
        self._connection.commit()
        self._tools: dict[str, ToolDescriptor] = {}
        self._handlers: dict[str, LocalToolHandler | RemoteClientFactory] = {}
        self._remote_original_names: dict[str, str] = {}
        self._lock = threading.RLock()

    def _load_or_create_secret(self) -> bytes:
        if self.secret_path.exists():
            return self.secret_path.read_bytes()
        value = secrets.token_bytes(32)
        temporary = self.secret_path.with_suffix(".tmp")
        temporary.write_bytes(value)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        temporary.replace(self.secret_path)
        return value

    def register_local(self, descriptor: ToolDescriptor, handler: LocalToolHandler) -> None:
        if descriptor.source != "local":
            raise MCPConfigurationError("local tool descriptor must have source=local")
        self._register(descriptor, handler)

    def register_remote(
        self,
        *,
        config: MCPServerConfig,
        tools: Iterable[Mapping[str, Any]],
        client_factory: RemoteClientFactory,
    ) -> None:
        for raw in tools:
            original = str(raw.get("name", ""))
            if not TOOL_NAME_RE.fullmatch(original):
                raise MCPProtocolError(f"remote server exposed invalid tool name: {original}")
            if config.allowed_tools and original not in config.allowed_tools:
                continue
            name = f"mcp.{config.server_id}.{original}"
            input_schema = raw.get("inputSchema", {"type": "object"})
            output_schema = raw.get("outputSchema")
            if not isinstance(input_schema, dict) or (output_schema is not None and not isinstance(output_schema, dict)):
                raise MCPProtocolError(f"remote tool {original} has invalid schema")
            # Remote annotations are preserved for display but never grant trust.
            requires_approval = original not in config.auto_approve_tools
            descriptor = ToolDescriptor(
                name=name,
                title=(str(raw["title"]) if raw.get("title") else None),
                description=str(raw.get("description", "")),
                input_schema=input_schema,
                output_schema=output_schema,
                annotations=(dict(raw.get("annotations", {})) if isinstance(raw.get("annotations", {}), dict) else {}),
                source="mcp",
                server_id=config.server_id,
                original_name=original,
                risk="read" if not requires_approval else "sensitive",
                requires_approval=requires_approval,
                trust_mode=config.trust_mode,
            )
            self._register(descriptor, client_factory)
            self._remote_original_names[name] = original

    def _register(self, descriptor: ToolDescriptor, handler: LocalToolHandler | RemoteClientFactory) -> None:
        if descriptor.name in self._tools:
            raise MCPConfigurationError(f"duplicate tool name: {descriptor.name}")
        self._tools[descriptor.name] = descriptor
        self._handlers[descriptor.name] = handler

    def list_tools(self) -> list[ToolDescriptor]:
        return [self._tools[name] for name in sorted(self._tools)]

    def get(self, name: str) -> ToolDescriptor:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFound(name) from exc

    def plan(self, *, call_id: str, tool_name: str, arguments: Mapping[str, Any]) -> ToolCallPlan:
        if not CALL_ID_RE.fullmatch(call_id):
            raise ToolRegistryError("call_id must be a canonical lowercase identifier")
        descriptor = self.get(tool_name)
        try:
            Draft202012Validator(dict(descriptor.input_schema)).validate(dict(arguments))
        except ValidationError as exc:
            raise ToolRegistryError(f"tool input validation failed: {exc.message}") from exc
        digest = _digest({"tool_name": tool_name, "arguments": dict(arguments)})
        return ToolCallPlan(
            call_id=call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            call_digest=digest,
            source=descriptor.source,
            server_id=descriptor.server_id,
            risk=descriptor.risk,
            requires_approval=descriptor.requires_approval,
            summary=f"Call {tool_name} from {descriptor.source}",
        )

    def _approval_signature(self, *, approval_id: str, call_digest: str, issued_by: str, issued_at: str, expires_at: str, one_time: bool) -> str:
        message = canonical_json({"approval_id": approval_id, "call_digest": call_digest, "issued_by": issued_by, "issued_at": issued_at, "expires_at": expires_at, "one_time": one_time})
        return hmac.new(self._secret, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_approval(self, plan: ToolCallPlan, *, issued_by: str, ttl_seconds: int = 600) -> ToolCallApproval:
        if ttl_seconds < 1 or ttl_seconds > 86_400:
            raise ToolApprovalInvalid("approval TTL must be between 1 and 86400 seconds")
        issued_dt = datetime.now(timezone.utc)
        issued_at = issued_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        expires_at = (issued_dt + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
        approval_id = "tool-approval-" + secrets.token_hex(8)
        signature = self._approval_signature(approval_id=approval_id, call_digest=plan.call_digest, issued_by=issued_by, issued_at=issued_at, expires_at=expires_at, one_time=True)
        approval = ToolCallApproval(approval_id, plan.call_digest, issued_by, issued_at, expires_at, True, signature)
        self._connection.execute(
            "INSERT INTO tool_approvals VALUES (?, ?, ?, ?, ?, 1, ?, NULL)",
            (approval_id, plan.call_digest, issued_by, issued_at, expires_at, signature),
        )
        self._connection.commit()
        return approval

    @staticmethod
    def _approval_from_value(value: ToolCallApproval | Mapping[str, Any]) -> ToolCallApproval:
        if isinstance(value, ToolCallApproval):
            return value
        try:
            return ToolCallApproval(
                approval_id=str(value["approval_id"]),
                call_digest=str(value["call_digest"]),
                issued_by=str(value["issued_by"]),
                issued_at=str(value["issued_at"]),
                expires_at=str(value["expires_at"]),
                one_time=bool(value["one_time"]),
                signature=str(value["signature"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolApprovalInvalid("invalid tool approval document") from exc

    def _validate_approval(self, plan: ToolCallPlan, value: ToolCallApproval | Mapping[str, Any]) -> ToolCallApproval:
        approval = self._approval_from_value(value)
        expected = self._approval_signature(
            approval_id=approval.approval_id,
            call_digest=approval.call_digest,
            issued_by=approval.issued_by,
            issued_at=approval.issued_at,
            expires_at=approval.expires_at,
            one_time=approval.one_time,
        )
        if not hmac.compare_digest(expected, approval.signature):
            raise ToolApprovalInvalid("approval signature invalid")
        if approval.call_digest != plan.call_digest:
            raise ToolApprovalInvalid("approval is bound to another tool call")
        try:
            expires = datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolApprovalInvalid("invalid approval expiry") from exc
        if datetime.now(timezone.utc) >= expires:
            raise ToolApprovalInvalid("approval expired")
        row = self._connection.execute("SELECT * FROM tool_approvals WHERE approval_id = ?", (approval.approval_id,)).fetchone()
        if row is None or row["signature"] != approval.signature:
            raise ToolApprovalInvalid("approval not issued by this workspace")
        if row["consumed_by_call_id"] is not None:
            raise ToolApprovalInvalid("approval already consumed")
        return approval

    def _existing(self, plan: ToolCallPlan) -> ToolCallResult | None:
        row = self._connection.execute("SELECT * FROM tool_calls WHERE call_id = ?", (plan.call_id,)).fetchone()
        if row is None:
            return None
        if row["call_digest"] != plan.call_digest:
            raise ToolCallConflict("call_id was already used for different arguments")
        if not row["result_json"]:
            raise ToolCallConflict("tool call is already in progress")
        payload = json.loads(row["result_json"])
        return ToolCallResult(
            call_id=payload["call_id"], tool_name=payload["tool_name"], status=payload["status"],
            started_at=payload["started_at"], completed_at=payload["completed_at"],
            content=tuple(payload.get("content", [])), structured_content=payload.get("structured_content"),
            is_error=bool(payload.get("is_error", False)), metadata=payload.get("metadata", {}),
            error_code=payload.get("error_code"),
        )

    def call(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        approval: ToolCallApproval | Mapping[str, Any] | None = None,
    ) -> ToolCallResult:
        plan = self.plan(call_id=call_id, tool_name=tool_name, arguments=arguments)
        with self._lock:
            existing = self._existing(plan)
            if existing is not None:
                return existing
            validated_approval: ToolCallApproval | None = None
            if plan.requires_approval:
                if approval is None:
                    raise ToolApprovalRequired(plan)
                validated_approval = self._validate_approval(plan, approval)
            started_at = utc_now()
            self._connection.execute(
                "INSERT INTO tool_calls(call_id, tool_name, call_digest, status, started_at, request_json) VALUES (?, ?, ?, 'running', ?, ?)",
                (call_id, tool_name, plan.call_digest, started_at, canonical_json({"tool_name": tool_name, "arguments": dict(arguments)})),
            )
            if validated_approval is not None:
                self._connection.execute("UPDATE tool_approvals SET consumed_by_call_id = ? WHERE approval_id = ?", (call_id, validated_approval.approval_id))
            self._connection.commit()
        descriptor = self.get(tool_name)
        try:
            if descriptor.source == "local":
                handler = self._handlers[tool_name]
                raw = handler(dict(arguments))  # type: ignore[misc]
            else:
                factory = self._handlers[tool_name]
                client = factory()  # type: ignore[operator]
                try:
                    raw = client.call_tool(self._remote_original_names[tool_name], dict(arguments))
                finally:
                    client.close()
            if not isinstance(raw, Mapping):
                raise MCPToolError("tool handler returned a non-object")
            content = raw.get("content", [])
            if not isinstance(content, list) or any(not isinstance(item, dict) for item in content):
                raise MCPToolError("tool result content must be an array of objects")
            structured = raw.get("structuredContent")
            if structured is not None and not isinstance(structured, dict):
                raise MCPToolError("structuredContent must be an object")
            if descriptor.output_schema is not None:
                if structured is None:
                    raise MCPToolError("tool declared outputSchema but returned no structuredContent")
                Draft202012Validator(dict(descriptor.output_schema)).validate(structured)
            result = ToolCallResult(
                call_id=call_id,
                tool_name=tool_name,
                status="completed",
                started_at=started_at,
                completed_at=utc_now(),
                content=tuple(content),
                structured_content=structured,
                is_error=bool(raw.get("isError", False)),
                metadata={"source": descriptor.source, "server_id": descriptor.server_id},
                error_code=("mcp_tool_execution_error" if raw.get("isError") else None),
            )
        except Exception as exc:
            result = ToolCallResult(
                call_id=call_id, tool_name=tool_name, status="failed", started_at=started_at,
                completed_at=utc_now(), content=({"type": "text", "text": str(exc)},),
                structured_content=None, is_error=True,
                metadata={"source": descriptor.source, "server_id": descriptor.server_id},
                error_code=getattr(exc, "code", "tool_execution_failed"),
            )
        payload = asdict(result)
        with self._lock:
            self._connection.execute(
                "UPDATE tool_calls SET status = ?, completed_at = ?, result_json = ?, error_code = ? WHERE call_id = ?",
                (result.status, result.completed_at, canonical_json(payload), result.error_code, call_id),
            )
            self._connection.commit()
        return result

    def list_calls(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM tool_calls ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ToolRegistry":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class MCPManager:
    def __init__(self, configs: Iterable[MCPServerConfig]) -> None:
        self.configs = {config.server_id: config for config in configs if config.enabled}

    def config(self, server_id: str) -> MCPServerConfig:
        try:
            return self.configs[server_id]
        except KeyError as exc:
            raise MCPConfigurationError(f"unknown or disabled MCP server: {server_id}") from exc

    def client(self, server_id: str) -> MCPClientProtocol:
        config = self.config(server_id)
        if config.transport == "stdio":
            return MCPStdioClient(config)
        return MCPStreamableHTTPClient(config)

    def probe(self, server_id: str) -> dict[str, Any]:
        client = self.client(server_id)
        try:
            initialized = client.initialize()
            tools = client.list_tools() if "tools" in initialized.get("capabilities", {}) else []
            resources = client.list_resources() if "resources" in initialized.get("capabilities", {}) else []
            prompts = client.list_prompts() if "prompts" in initialized.get("capabilities", {}) else []
            return {
                "server_id": server_id,
                "protocol_version": initialized.get("protocolVersion"),
                "server_info": initialized.get("serverInfo", {}),
                "capabilities": initialized.get("capabilities", {}),
                "tool_count": len(tools),
                "resource_count": len(resources),
                "prompt_count": len(prompts),
                "tools": tools,
                "resources": resources,
                "prompts": prompts,
            }
        finally:
            client.close()

    def populate_registry(self, registry: ToolRegistry, *, server_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        selected = list(server_ids) if server_ids is not None else sorted(self.configs)
        reports: list[dict[str, Any]] = []
        for server_id in selected:
            config = self.config(server_id)
            client = self.client(server_id)
            try:
                initialized = client.initialize()
                tools = client.list_tools() if "tools" in initialized.get("capabilities", {}) else []
            finally:
                client.close()
            registry.register_remote(config=config, tools=tools, client_factory=lambda sid=server_id: self.client(sid))
            reports.append({"server_id": server_id, "tool_count": len(tools)})
        return reports


class MCPServer:
    """In-process MCP request dispatcher used by stdio and tests."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        server_name: str = "w1-nexus",
        server_version: str = "0.1.0-dev50",
        resources: Iterable[ResourceDescriptor] = (),
        resource_readers: Mapping[str, Callable[[], Mapping[str, Any]]] | None = None,
        prompts: Iterable[PromptDescriptor] = (),
        prompt_getters: Mapping[str, Callable[[Mapping[str, str]], Mapping[str, Any]]] | None = None,
        page_size: int = 100,
    ) -> None:
        self.registry = registry
        self.server_name = server_name
        self.server_version = server_version
        self.resources = {item.uri: item for item in resources}
        self.resource_readers = dict(resource_readers or {})
        self.prompts = {item.name: item for item in prompts}
        self.prompt_getters = dict(prompt_getters or {})
        self.page_size = page_size
        self.initialized = False

    @staticmethod
    def _page(values: Sequence[Mapping[str, Any]], cursor: str | None, page_size: int) -> tuple[list[Mapping[str, Any]], str | None]:
        try:
            start = int(cursor or 0)
        except ValueError as exc:
            raise MCPProtocolError("invalid cursor", rpc_code=-32602) from exc
        if start < 0:
            raise MCPProtocolError("invalid cursor", rpc_code=-32602)
        page = list(values[start:start + page_size])
        next_cursor = str(start + page_size) if start + page_size < len(values) else None
        return page, next_cursor

    def _result(self, request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}

    def _error(self, request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def handle(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return self._error(message.get("id"), -32600, "Invalid Request")
        request_id = message.get("id")
        method = message["method"]
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested not in SUPPORTED_PROTOCOL_VERSIONS:
                return self._error(request_id, -32602, f"Unsupported protocol version: {requested}")
            self.initialized = True
            capabilities: dict[str, Any] = {"tools": {"listChanged": False}}
            if self.resources:
                capabilities["resources"] = {"subscribe": False, "listChanged": False}
            if self.prompts:
                capabilities["prompts"] = {"listChanged": False}
            return self._result(request_id, {
                "protocolVersion": requested,
                "capabilities": capabilities,
                "serverInfo": {"name": self.server_name, "version": self.server_version, "description": "Governed W1 Nexus MCP server"},
            })
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method == "ping":
            return self._result(request_id, {})
        if not self.initialized:
            return self._error(request_id, -32002, "Server not initialized")
        try:
            if method == "tools/list":
                documents = [item.mcp_document(expose_name=item.original_name or item.name) for item in self.registry.list_tools() if item.source == "local"]
                page, next_cursor = self._page(documents, params.get("cursor"), self.page_size)
                result: dict[str, Any] = {"tools": page}
                if next_cursor:
                    result["nextCursor"] = next_cursor
                return self._result(request_id, result)
            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return self._error(request_id, -32602, "Invalid tool call")
                call_id = "mcp-call-" + secrets.token_hex(8)
                plan = self.registry.plan(call_id=call_id, tool_name=name, arguments=arguments)
                if plan.requires_approval:
                    return self._result(request_id, {"content": [{"type": "text", "text": "W1 approval is required before this tool can run."}], "isError": True, "structuredContent": {"error_code": "tool_approval_required", "call_digest": plan.call_digest}})
                result = self.registry.call(call_id=call_id, tool_name=name, arguments=arguments)
                payload: dict[str, Any] = {"content": [dict(item) for item in result.content], "isError": result.is_error}
                if result.structured_content is not None:
                    payload["structuredContent"] = dict(result.structured_content)
                return self._result(request_id, payload)
            if method == "resources/list":
                documents = [self.resources[key].mcp_document() for key in sorted(self.resources)]
                page, next_cursor = self._page(documents, params.get("cursor"), self.page_size)
                result = {"resources": page}
                if next_cursor:
                    result["nextCursor"] = next_cursor
                return self._result(request_id, result)
            if method == "resources/read":
                uri = params.get("uri")
                if not isinstance(uri, str) or uri not in self.resource_readers:
                    return self._error(request_id, -32602, f"Unknown resource: {uri}")
                return self._result(request_id, self.resource_readers[uri]())
            if method == "prompts/list":
                documents = [self.prompts[key].mcp_document() for key in sorted(self.prompts)]
                page, next_cursor = self._page(documents, params.get("cursor"), self.page_size)
                result = {"prompts": page}
                if next_cursor:
                    result["nextCursor"] = next_cursor
                return self._result(request_id, result)
            if method == "prompts/get":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict) or name not in self.prompt_getters:
                    return self._error(request_id, -32602, f"Unknown prompt: {name}")
                return self._result(request_id, self.prompt_getters[name]({str(k): str(v) for k, v in arguments.items()}))
            return self._error(request_id, -32601, f"Method not found: {method}")
        except ToolNotFound as exc:
            return self._error(request_id, -32602, str(exc))
        except (ToolRegistryError, MCPError, ValidationError) as exc:
            return self._error(request_id, -32602, str(exc), {"error_code": getattr(exc, "code", "invalid_params")})
        except Exception as exc:  # pragma: no cover - final protocol guard
            return self._error(request_id, -32603, "Internal error", {"error_code": getattr(exc, "code", "internal_error")})


class MCPStdioServer:
    def __init__(self, server: MCPServer) -> None:
        self.server = server

    def serve_forever(self, input_stream: Any = None, output_stream: Any = None) -> None:
        import sys
        source = input_stream or sys.stdin
        target = output_stream or sys.stdout
        for line in source:
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("message must be an object")
                response = self.server.handle(message)
            except Exception as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error", "data": str(exc)}}
            if response is not None:
                target.write(canonical_json(response) + "\n")
                target.flush()


def tool_documents(descriptors: Iterable[ToolDescriptor]) -> list[dict[str, Any]]:
    return [item.mcp_document() for item in descriptors]
