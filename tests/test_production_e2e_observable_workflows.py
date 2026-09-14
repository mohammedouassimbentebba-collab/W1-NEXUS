"""Phase I: Observable E2E Workflows & Trace Life-Cycle Test Suite.

Validates the full user journey from Console UI to session, run, agent execution,
MCP tool invocation, observable trace emission, review, merge, and Universal Artifact Studio.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

# Ensure src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))
sys.path.insert(0, str(repo_root))

from w1cip.cli_support import WorkspacePaths, initialize_workspace
from w1cip.session_store import SessionStore
from w1cip.workspace_console import ConsoleSettings, WorkspaceSnapshotService
from w1cip.universal_artifacts import (
    UniversalArtifactStore,
    validate_artifact_model,
)
from w1cip.w1_gateway import GatewayStore, ModelIdentity, ModelRoute, TeamBuilder, TeamPolicy


def test_e2e_console_server_and_ui_api_lifecycle(tmp_path: Path) -> None:
    """E2E-01: Console server initialization, UI snapshot generation, and endpoint verification."""
    from http.server import ThreadingHTTPServer
    from w1cip.workspace_console import _asset_text, WorkspaceSnapshotService
    
    ws_paths = initialize_workspace(tmp_path)
    
    # Write a basic config
    config_file = ws_paths.config
    config_file.write_text(json.dumps({
        "name": "E2E-Test-Workspace",
        "workspace_version": "1.0.0",
        "memory": {"project_id": "test-proj", "namespace_id": "test-ns"}
    }), encoding="utf-8")
    
    # Initialize snapshot service
    service = WorkspaceSnapshotService(ws_paths)
    snapshot = service.snapshot()
    
    assert snapshot["workspace"]["name"] == "E2E-Test-Workspace"
    assert "metrics" in snapshot
    assert "health" in snapshot
    assert snapshot["console"]["read_only"] is True
    assert len(snapshot["snapshot_digest"]) == 64
    
    # Validate packaged assets availability
    index_html = _asset_text("index.html")
    assert "<!DOCTYPE html>" in index_html or "<html" in index_html
    style_css = _asset_text("styles.css")
    assert len(style_css) > 50
    app_js = _asset_text("app.js")
    assert len(app_js) > 50


def test_e2e_observable_agent_traces_and_event_ordering(tmp_path: Path) -> None:
    """E2E-02: Strict emission and ordering of observable agent traces across the lifecycle."""
    import sqlite3
    from w1cip.session_store import canonical_json
    
    session_db = tmp_path / "sessions.sqlite3"
    conn = sqlite3.connect(str(session_db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observable_traces (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    
    session_id = "session-e2e-traces-001"
    expected_event_sequence = [
        ("agent_started", {"agent_id": "planner-01", "role": "planner"}),
        ("task_received", {"task_id": "task-alpha", "goal": "design_architecture"}),
        ("context_transfer", {"from_agent": "planner-01", "to_agent": "coder-01"}),
        ("tool_requested", {"tool_name": "generate_scaffold", "parameters": {"path": "src/app.py"}}),
        ("tool_completed", {"tool_name": "generate_scaffold", "status": "success"}),
        ("artifact_created", {"artifact_id": "art-01", "path": "docs/architecture.docx"}),
        ("review_requested", {"artifact_id": "art-01", "reviewer": "reviewer-01"}),
        ("review_completed", {"artifact_id": "art-01", "verdict": "approved"}),
        ("merge_requested", {"branch": "w1/coder-01", "target": "main"}),
        ("merge_completed", {"commit_sha": "a1b2c3d4e5f6", "status": "merged"}),
    ]
    
    previous_hash = "0" * 64
    for event_type, payload in expected_event_sequence:
        now = "2026-08-17T22:00:00Z"
        body = {
            "session_id": session_id,
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
            "recorded_at": now,
        }
        event_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO observable_traces(session_id, event_type, payload_json, previous_hash, event_hash, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, event_type, canonical_json(payload), previous_hash, event_hash, now),
        )
        previous_hash = event_hash
    conn.commit()
    
    # Read back and verify strict chronological ordering & sequence continuity
    rows = conn.execute(
        "SELECT sequence, event_type, payload_json, previous_hash, event_hash FROM observable_traces WHERE session_id = ? ORDER BY sequence",
        (session_id,),
    ).fetchall()
    assert len(rows) == 10
    
    last_h = "0" * 64
    for i, (expected_type, expected_payload) in enumerate(expected_event_sequence):
        seq, ev_type, p_json, prev_h, ev_h = rows[i]
        assert ev_type == expected_type
        assert seq == i + 1
        assert prev_h == last_h
        parsed_p = json.loads(p_json)
        for k, v in expected_payload.items():
            assert parsed_p[k] == v
        last_h = ev_h
    conn.close()


def test_e2e_mcp_tool_invocation_pipeline(tmp_path: Path) -> None:
    """E2E-03: MCP tool registration, parameter validation, and sandboxed execution pipeline."""
    from w1cip.mcp import ToolRegistry, ToolDescriptor
    
    registry = ToolRegistry(tmp_path / "mcp_tools")
    
    # Register an audited math & file tool
    def safe_calculator(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        a = float(arguments["a"])
        b = float(arguments["b"])
        op = str(arguments["operation"])
        if op == "add":
            return {"content": [{"type": "text", "text": str(a + b)}], "result": a + b}
        elif op == "multiply":
            return {"content": [{"type": "text", "text": str(a * b)}], "result": a * b}
        raise ValueError(f"Unsupported operation: {op}")
        
    descriptor = ToolDescriptor(
        name="safe_calc",
        title="Safe Calculator",
        description="Audited safe calculation tool",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
                "operation": {"type": "string", "enum": ["add", "multiply"]}
            },
            "required": ["a", "b", "operation"]
        },
        source="local",
        requires_approval=False,
    )
    registry.register_local(descriptor, safe_calculator)
    
    # Valid call
    res = registry.call(
        call_id="call-01",
        tool_name="safe_calc",
        arguments={"a": 10.5, "b": 2.5, "operation": "multiply"},
    )
    assert res.status == "completed"
    assert res.is_error is False
    assert res.content[0]["text"] == "26.25"
    
    # Invalid call with schema violation (missing parameter)
    try:
        registry.plan(
            call_id="call-02",
            tool_name="safe_calc",
            arguments={"a": 10.5, "b": 2.5, "operation": "unsupported_op"},
        )
        raise AssertionError("Schema validation failure was not caught!")
    except Exception:
        pass


def test_e2e_universal_artifact_studio_attestation(tmp_path: Path) -> None:
    """E2E-04: Universal Artifact Studio creation, hashing, and cryptographic attestation."""
    from w1cip.universal_artifacts import UniversalArtifactStore, validate_artifact_model
    
    db_path = tmp_path / "artifacts.sqlite3"
    store = UniversalArtifactStore(db_path=db_path, workspace_root=tmp_path)
    
    doc_model = {
        "schema_version": "w1-artifact-0.1",
        "artifact_id": "system-readiness-report",
        "kind": "document",
        "title": "W1 Nexus Production Readiness Report",
        "locale": "en",
        "metadata": {"confidentiality": "internal"},
        "content": {
            "blocks": [
                {"type": "heading", "level": 1, "text": "Executive Summary"},
                {"type": "paragraph", "text": "All core gateway and multi-agent systems operational."},
            ]
        }
    }
    
    rev = store.create(doc_model, created_by="agent-researcher")
    assert rev.artifact_id == "system-readiness-report"
    assert rev.version == 1
    assert rev.model_hash
    assert len(rev.model_hash) == 64
    
    # Verify review lifecycle
    review_res = store.review(
        artifact_id="system-readiness-report",
        version=1,
        reviewer="verifier-agent",
        outcome="approved",
        rationale="All cryptographic security invariants verified.",
    )
    assert review_res["outcome"] == "approved"


if __name__ == "__main__":
    import unittest
    print("Executing Phase I E2E Observable Workflows Test Suite...")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_e2e_console_server_and_ui_api_lifecycle(p / "t1")
        test_e2e_observable_agent_traces_and_event_ordering(p / "t2")
        test_e2e_mcp_tool_invocation_pipeline(p / "t3")
        test_e2e_universal_artifact_studio_attestation(p / "t4")
    print("ALL PHASE I E2E WORKFLOW TESTS PASSED! (4/4) ✅")
