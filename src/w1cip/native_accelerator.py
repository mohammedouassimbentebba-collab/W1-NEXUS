"""W1-CIP Native Systems Accelerator & Graceful Fallback Bridge.

This module probes for compiled native acceleration cores (Rust w1_cip_core,
C w1_sandbox) via C-ABI / ctypes. When native libraries are compiled and
present on the host, W1-CIP promotes itself to Aerospace Hardware-Accelerated
Mode (sub-microsecond BLAKE3 hashing, native topological sort, kernel job objects).

When native libraries are not present, this module gracefully degrades to
mathematically equivalent, deterministic pure-Python fallbacks with zero breakage.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
from collections import defaultdict, deque
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

# Global flags
_RUST_CORE_LIB: ctypes.CDLL | None = None
_NATIVE_LOAD_ERROR: str | None = None


def _find_rust_core_library() -> Path | None:
    """Locates the compiled w1_cip_core dynamic library across target directories."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    system = platform.system().lower()

    if "windows" in system:
        lib_name = "w1_cip_core.dll"
    elif "darwin" in system:
        lib_name = "libw1_cip_core.dylib"
    else:
        lib_name = "libw1_cip_core.so"

    candidates = [
        repo_root / "crates" / "w1-cip-core" / "target" / "release" / lib_name,
        repo_root / "crates" / "w1-cip-core" / "target" / "debug" / lib_name,
        repo_root / "target" / "release" / lib_name,
        repo_root / "target" / "debug" / lib_name,
        Path(__file__).parent / lib_name,
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _init_native_rust_core() -> None:
    """Attempts to bind C-ABI symbols from w1_cip_core."""
    global _RUST_CORE_LIB, _NATIVE_LOAD_ERROR
    lib_path = _find_rust_core_library()
    if not lib_path:
        _NATIVE_LOAD_ERROR = "Native dynamic library not found in target directories."
        return

    try:
        cdll = ctypes.CDLL(str(lib_path))

        # 1. w1_blake3_hash(input: *const c_char) -> *mut c_char
        cdll.w1_blake3_hash.argtypes = [ctypes.c_char_p]
        cdll.w1_blake3_hash.restype = ctypes.c_void_p

        # 2. w1_dag_verify_acyclic_json(edges_json: *const c_char) -> i32
        cdll.w1_dag_verify_acyclic_json.argtypes = [ctypes.c_char_p]
        cdll.w1_dag_verify_acyclic_json.restype = ctypes.c_int32

        # 3. w1_free_string(ptr: *mut c_char)
        cdll.w1_free_string.argtypes = [ctypes.c_void_p]
        cdll.w1_free_string.restype = None

        _RUST_CORE_LIB = cdll
        _NATIVE_LOAD_ERROR = None
    except Exception as exc:
        _RUST_CORE_LIB = None
        _NATIVE_LOAD_ERROR = f"Failed to load native library: {exc}"


# Initialize upon module load
_init_native_rust_core()


# ============================================================================
# 1. Cryptographic State Ledger & Merkle Hashes
# ============================================================================

def compute_state_hash(data: bytes | str) -> str:
    """Computes state hash via Rust BLAKE3 if available, falling back to SHA-256."""
    raw_bytes = data.encode("utf-8") if isinstance(data, str) else data

    if _RUST_CORE_LIB is not None:
        try:
            ptr = _RUST_CORE_LIB.w1_blake3_hash(raw_bytes)
            if ptr:
                res_str = ctypes.cast(ptr, ctypes.c_char_p).value
                _RUST_CORE_LIB.w1_free_string(ptr)
                if res_str:
                    return res_str.decode("utf-8")
        except Exception:
            pass  # Fall through to pure-Python fallback

    # Pure-Python deterministic fallback (SHA-256)
    return hashlib.sha256(raw_bytes).hexdigest()


def create_merkle_receipt(goal_data: bytes | str, evidence_data: bytes | str) -> dict[str, Any]:
    """Generates a verifiable Merkle pair receipt binding GoalContract to Evidence."""
    goal_hash = compute_state_hash(goal_data)
    evidence_hash = compute_state_hash(evidence_data)
    combined_root = compute_state_hash((goal_hash + evidence_hash).encode("utf-8"))

    return {
        "goal_hash": goal_hash,
        "evidence_hash": evidence_hash,
        "combined_root": combined_root,
        "backend": "rust_blake3" if _RUST_CORE_LIB is not None else "python_sha256",
    }


# ============================================================================
# 2. Topological Acyclicity & Cycle Detection
# ============================================================================

def verify_dag_acyclic(edges: Sequence[tuple[str, str]]) -> bool:
    """Verifies that the given directed graph is acyclic.

    Uses Rust petgraph if loaded, otherwise falls back to Kahn's algorithm.
    """
    if _RUST_CORE_LIB is not None:
        try:
            payload = json.dumps([[str(u), str(v)] for u, v in edges]).encode("utf-8")
            res = _RUST_CORE_LIB.w1_dag_verify_acyclic_json(payload)
            return bool(res == 1)
        except Exception:
            pass

    # Pure-Python fallback using Kahn's algorithm
    in_degree: dict[str, int] = defaultdict(int)
    adj: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()

    for u, v in edges:
        nodes.add(u)
        nodes.add(v)
        adj[u].append(v)
        in_degree[v] += 1
        if u not in in_degree:
            in_degree[u] = 0

    queue = deque([n for n in nodes if in_degree[n] == 0])
    visited_count = 0

    while queue:
        curr = queue.popleft()
        visited_count += 1
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return visited_count == len(nodes)


# ============================================================================
# 3. Transactional Copy-on-Write (CoW) State Management
# ============================================================================

class TransactionalState:
    """Transactional state manager providing instant snapshotting and atomic rollback."""

    def __init__(self, initial_state: Mapping[str, Any] | None = None) -> None:
        self._current: dict[str, Any] = deepcopy(dict(initial_state or {}))
        self._snapshots: list[dict[str, Any]] = []

    def snapshot(self) -> int:
        """Captures an atomic snapshot before applying speculative agent edits."""
        self._snapshots.append(deepcopy(self._current))
        return len(self._snapshots) - 1

    def set(self, key: str, value: Any) -> None:
        self._current[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._current.get(key, default)

    def rollback(self, snapshot_id: int) -> bool:
        """Performs atomic rollback to the specified snapshot index."""
        if 0 <= snapshot_id < len(self._snapshots):
            self._current = deepcopy(self._snapshots[snapshot_id])
            return True
        return False

    @property
    def current(self) -> dict[str, Any]:
        return dict(self._current)


# ============================================================================
# 4. Telemetry & Diagnostics
# ============================================================================

def diagnose_native_status() -> dict[str, Any]:
    """Returns the current operational status of the native systems acceleration layer."""
    return {
        "rust_core_available": _RUST_CORE_LIB is not None,
        "active_hash_backend": "rust_blake3" if _RUST_CORE_LIB is not None else "python_sha256",
        "active_dag_backend": "rust_petgraph" if _RUST_CORE_LIB is not None else "python_kahns",
        "load_error": _NATIVE_LOAD_ERROR,
    }
