"""CLI Integration tests for Step 52 AID-CF (Discovery and Gateway extensions)."""

import json
from pathlib import Path
import pytest

from w1cip.cli import main


def _run_cli(*args: str) -> tuple[int, str]:
    import sys
    from io import StringIO
    from w1cip.cli import main

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()
    try:
        exit_code = main(list(args))
        output = sys.stdout.getvalue() + sys.stderr.getvalue()
        return exit_code, output
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def test_discovery_scan_and_list_cli(tmp_path: Path) -> None:
    """Test w1 discovery scan and w1 discovery list CLI commands."""
    ws = str(tmp_path)
    _run_cli("--workspace", ws, "init")
    code, out = _run_cli("--workspace", ws, "--json", "discovery", "scan")
    assert code == 0
    data = json.loads(out)
    assert data["discovered_count"] > 0
    assert data["identities_resolved"] > 0
    assert data["routes_projected"] > 0

    code2, out2 = _run_cli("--workspace", ws, "--json", "discovery", "list")
    assert code2 == 0
    data2 = json.loads(out2)
    assert data2["count"] > 0


def test_gateway_doctor_cli(tmp_path: Path) -> None:
    """Test w1 gateway doctor CLI command."""
    ws = str(tmp_path)
    _run_cli("--workspace", ws, "init")
    code, out = _run_cli("--workspace", ws, "--json", "gateway", "doctor")
    assert code == 0
    data = json.loads(out)
    assert "health_status" in data
    assert "providers_count" in data
    assert "routes_count" in data
    assert "identities_count" in data


def test_gateway_certify_and_revoke_cli(tmp_path: Path) -> None:
    """Test w1 gateway certify and w1 gateway revoke CLI commands."""
    ws = str(tmp_path)
    _run_cli("--workspace", ws, "init")
    # First populate store
    _run_cli("--workspace", ws, "gateway", "discover")

    # Certify
    code, out = _run_cli("--workspace", ws, "--json", "gateway", "certify", "gemini-2.5-flash@google_ai_studio:default", "--ttl-days", "7")
    assert code == 0
    data = json.loads(out)
    assert data["status"] == "certified"
    assert data["route_id"] == "gemini-2.5-flash@google_ai_studio:default"

    # Inspect to verify cert presence
    code_insp, out_insp = _run_cli("--workspace", ws, "--json", "gateway", "inspect", "gemini-2.5-flash@google_ai_studio:default")
    assert code_insp == 0
    insp_data = json.loads(out_insp)
    assert insp_data["certification"] is not None
    assert insp_data["certification"]["status"] == "certified"

    # Revoke
    code_rev, out_rev = _run_cli("--workspace", ws, "--json", "gateway", "revoke", "gemini-2.5-flash@google_ai_studio:default", "--reason", "token_revoked")
    assert code_rev == 0
    data_rev = json.loads(out_rev)
    assert data_rev["status"] == "revoked"



def test_gateway_identities_and_routes_cli(tmp_path: Path) -> None:
    """Test w1 gateway identities and w1 gateway routes with filtering."""
    ws = str(tmp_path)
    _run_cli("--workspace", ws, "init")
    _run_cli("--workspace", ws, "gateway", "discover")

    code_id, out_id = _run_cli("--workspace", ws, "--json", "gateway", "identities", "--vendor", "google")
    assert code_id == 0
    data_id = json.loads(out_id)
    assert all(i["vendor"] == "google" for i in data_id["identities"])

    code_rt, out_rt = _run_cli("--workspace", ws, "--json", "gateway", "routes", "--free-only")
    assert code_rt == 0
    data_rt = json.loads(out_rt)
    assert len(data_rt["routes"]) > 0


def test_gateway_explain_cli(tmp_path: Path) -> None:
    """Test w1 gateway explain CLI command."""
    ws = str(tmp_path)
    _run_cli("--workspace", ws, "init")
    _run_cli("--workspace", ws, "gateway", "discover")

    code, out = _run_cli("--workspace", ws, "--json", "gateway", "explain", "--task", "coding", "--free-only")
    assert code == 0
    data = json.loads(out)
    assert data["task_type"] == "coding"
    assert data["selected_route_id"] is not None
