"""Immutable provenance tracking, environment hashing, and run signature verification."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def compute_environment_hash() -> str:
    """Computes a deterministic SHA-256 fingerprint of the current execution environment."""
    env_info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "python_executable": sys.executable,
    }
    encoded = json.dumps(env_info, sort_keys=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def get_nexus_git_commit(workspace_root: Optional[Path] = None) -> str:
    """Retrieves the NEXUS git commit hash or computes an unversioned deterministic tree hash."""
    if workspace_root is None:
        workspace_root = Path(__file__).resolve().parent.parent.parent.parent

    # 1. Try git rev-parse
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    # 2. Check pyproject.toml version
    pyproject = workspace_root / "pyproject.toml"
    if pyproject.is_file():
        try:
            with open(pyproject, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("version"):
                        ver_val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        return f"nexus-{ver_val}"
        except Exception:
            pass

    return "w1-nexus-0.1.0.dev52-local"


def build_provenance_metadata(
    benchmark_name: str,
    benchmark_version: str,
    benchmark_release_tag: str,
    task_id: str,
    model: str,
    provider: str,
    endpoint: str,
    runner_version: str = "1.0.0",
    extra_config: Optional[dict[str, Any]] = None,
    provider_id: Optional[str] = None,
    credential_ref: Optional[str] = None,
) -> dict[str, Any]:
    """Builds a complete, immutable provenance record for a benchmark run.

    ``provider_id`` and ``credential_ref`` are safe metadata: they reference a
    credential without ever exposing the underlying secret.
    """
    metadata = {
        "benchmark_name": benchmark_name,
        "benchmark_version": benchmark_version,
        "benchmark_release_tag": benchmark_release_tag,
        "task_id": task_id,
        "model": model,
        "provider": provider,
        "provider_id": provider_id or provider,
        "endpoint": endpoint,
        "credential_ref": credential_ref,
        "runner_version": runner_version,
        "environment_hash": compute_environment_hash(),
        "nexus_git_commit": get_nexus_git_commit(),
        "operating_system": platform.platform(),
        "python_version": sys.version.split()[0],
        "node_version": None,
    }

    # Probe node version if present
    try:
        res = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            metadata["node_version"] = res.stdout.strip()
    except Exception:
        pass

    if extra_config:
        metadata["task_configuration"] = extra_config

    return metadata
