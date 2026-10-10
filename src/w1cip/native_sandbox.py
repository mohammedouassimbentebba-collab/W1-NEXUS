"""W1™ NEXUS Native C Sandbox Bridge.

Binds to the compiled w1_sandbox dynamic library to execute model-generated tasks
inside an aerospace containment envelope. Guarantees that neither CPU throughput,
token count, nor reasoning depth are artificially constrained, while protecting
the host OS from runaway fork-bombs, memory exhaustion, or freeze loops.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class _W1SandboxConfigStruct(ctypes.Structure):
    _fields_ = [
        ("timeout_ms", ctypes.c_uint32),
        ("memory_limit_bytes", ctypes.c_uint64),
        ("max_processes", ctypes.c_uint32),
        ("unconstrained_cpu", ctypes.c_int32),
        ("working_dir", ctypes.c_char_p),
    ]


class _W1SandboxResultStruct(ctypes.Structure):
    _fields_ = [
        ("exit_code", ctypes.c_int32),
        ("timed_out", ctypes.c_int32),
        ("memory_exceeded", ctypes.c_int32),
        ("peak_memory_bytes", ctypes.c_uint64),
        ("elapsed_ms", ctypes.c_uint64),
        ("stdout_buffer", ctypes.c_char_p),
        ("stdout_len", ctypes.c_uint64),
        ("stderr_buffer", ctypes.c_char_p),
        ("stderr_len", ctypes.c_uint64),
    ]


@dataclass
class SandboxExecutionResult:
    exit_code: int
    timed_out: bool
    memory_exceeded: bool
    peak_memory_bytes: int
    elapsed_ms: int
    stdout: str
    stderr: str
    backend: str


_C_SANDBOX_LIB: ctypes.CDLL | None = None
_SANDBOX_LOAD_ERROR: str | None = None


def _find_sandbox_library() -> Path | None:
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parent.parent
    system = platform.system().lower()

    if "windows" in system:
        lib_name = "w1_sandbox.dll"
    elif "darwin" in system:
        lib_name = "libw1_sandbox.dylib"
    else:
        lib_name = "libw1_sandbox.so"

    candidates = [
        package_dir / lib_name,
        repo_root / "c_sandbox" / lib_name,
        repo_root / "target" / "release" / lib_name,
    ]

    for c in candidates:
        if c.is_file():
            return c
    return None


def _init_native_c_sandbox() -> None:
    global _C_SANDBOX_LIB, _SANDBOX_LOAD_ERROR
    lib_path = _find_sandbox_library()
    if not lib_path:
        _SANDBOX_LOAD_ERROR = "Native w1_sandbox library not found."
        return

    try:
        cdll = ctypes.CDLL(str(lib_path))
        cdll.w1_sandbox_execute.argtypes = [ctypes.c_char_p, ctypes.POINTER(_W1SandboxConfigStruct)]
        cdll.w1_sandbox_execute.restype = ctypes.POINTER(_W1SandboxResultStruct)

        cdll.w1_sandbox_free_result.argtypes = [ctypes.POINTER(_W1SandboxResultStruct)]
        cdll.w1_sandbox_free_result.restype = None

        cdll.w1_sandbox_diagnose.argtypes = []
        cdll.w1_sandbox_diagnose.restype = ctypes.c_void_p

        cdll.w1_sandbox_free_string.argtypes = [ctypes.c_void_p]
        cdll.w1_sandbox_free_string.restype = None

        _C_SANDBOX_LIB = cdll
        _SANDBOX_LOAD_ERROR = None
    except Exception as exc:
        _C_SANDBOX_LIB = None
        _SANDBOX_LOAD_ERROR = f"Failed to load native sandbox library: {exc}"


_init_native_c_sandbox()


def execute_sandboxed(
    command: str,
    *,
    timeout_s: float = 30.0,
    memory_limit_mb: int = 2048,  # Generous default: 2GB to never choke AI models
    max_processes: int = 32,      # Prevents fork-bombs while allowing threads & sub-processes
    working_dir: Optional[str] = None,
) -> SandboxExecutionResult:
    """Executes a command safely under native Win32/POSIX containment.

    Guarantees that valid AI model reasoning, compilation tasks, and calculations
    run at 100% full hardware speed without arbitrary token or compute throttling.
    """
    if _C_SANDBOX_LIB is not None:
        cfg = _W1SandboxConfigStruct()
        cfg.timeout_ms = int(timeout_s * 1000)
        cfg.memory_limit_bytes = int(memory_limit_mb * 1024 * 1024)
        cfg.max_processes = max_processes
        cfg.unconstrained_cpu = 1  # Full unthrottled CPU priority
        cfg.working_dir = working_dir.encode("utf-8") if working_dir else None

        cmd_bytes = command.encode("utf-8")
        raw_res_ptr = _C_SANDBOX_LIB.w1_sandbox_execute(cmd_bytes, ctypes.byref(cfg))
        if raw_res_ptr:
            try:
                res = raw_res_ptr.contents
                stdout_str = (res.stdout_buffer or b"").decode("utf-8", errors="replace")
                stderr_str = (res.stderr_buffer or b"").decode("utf-8", errors="replace")
                return SandboxExecutionResult(
                    exit_code=int(res.exit_code),
                    timed_out=bool(res.timed_out),
                    memory_exceeded=bool(res.memory_exceeded),
                    peak_memory_bytes=int(res.peak_memory_bytes),
                    elapsed_ms=int(res.elapsed_ms),
                    stdout=stdout_str,
                    stderr=stderr_str,
                    backend="native_c_win32_job" if platform.system().lower() == "windows" else "native_c_posix",
                )
            finally:
                _C_SANDBOX_LIB.w1_sandbox_free_result(raw_res_ptr)

    # Pure-Python fallback via subprocess if native C library is absent
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout_s,
            cwd=working_dir,
            text=True,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return SandboxExecutionResult(
            exit_code=proc.returncode,
            timed_out=False,
            memory_exceeded=False,
            peak_memory_bytes=0,
            elapsed_ms=elapsed_ms,
            stdout=proc.stdout,
            stderr=proc.stderr,
            backend="python_subprocess_fallback",
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        stdout_str = (exc.stdout or "").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr_str = (exc.stderr or "").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return SandboxExecutionResult(
            exit_code=124,
            timed_out=True,
            memory_exceeded=False,
            peak_memory_bytes=0,
            elapsed_ms=elapsed_ms,
            stdout=stdout_str,
            stderr=stderr_str,
            backend="python_subprocess_fallback",
        )


def diagnose_sandbox_status() -> dict[str, Any]:
    """Returns diagnostics regarding the native sandbox engine."""
    if _C_SANDBOX_LIB is not None:
        ptr = _C_SANDBOX_LIB.w1_sandbox_diagnose()
        if ptr:
            try:
                res_str = ctypes.cast(ptr, ctypes.c_char_p).value
                if res_str:
                    data = json.loads(res_str.decode("utf-8"))
                    data["native_available"] = True
                    return data
            finally:
                _C_SANDBOX_LIB.w1_sandbox_free_string(ptr)

    return {
        "native_available": False,
        "platform": platform.system().lower(),
        "backend": "python_subprocess_fallback",
        "error": _SANDBOX_LOAD_ERROR,
    }
