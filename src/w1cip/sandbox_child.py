"""Internal POSIX limit launcher used by SecureExecutionFabric.

This module is invoked as a separate process, applies resource limits, then
replaces itself with the requested command.  Keeping limit setup outside
``preexec_fn`` makes the parent safe to use from threaded multi-agent runtimes.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

try:
    import resource  # type: ignore
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore


def apply_limits(limits: dict[str, Any]) -> None:
    if resource is None:
        return
    cpu = int(limits["cpu_seconds"])
    # RLIMIT_CPU is an absolute process-CPU boundary, not a countdown from
    # the moment setrlimit is called.  This launcher must first import Python
    # modules and parse the limits file; on slower hosts that startup cost can
    # consume a material part of a small experiment budget.  Add the CPU time
    # already spent by the launcher so the executed command receives the full
    # preregistered allowance.
    usage = resource.getrusage(resource.RUSAGE_SELF)
    consumed = float(usage.ru_utime) + float(usage.ru_stime)
    soft_cpu = max(1, int(math.ceil(consumed + cpu)))
    resource.setrlimit(resource.RLIMIT_CPU, (soft_cpu, soft_cpu + 1))
    memory = int(limits["memory_mb"]) * 1024 * 1024
    if hasattr(resource, "RLIMIT_AS"):
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    open_files = int(limits["open_files"])
    resource.setrlimit(resource.RLIMIT_NOFILE, (open_files, open_files))
    if hasattr(resource, "RLIMIT_NPROC"):
        pids = int(limits["pids"])
        resource.setrlimit(resource.RLIMIT_NPROC, (pids, pids))
    size = int(limits["file_size_mb"]) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    os.umask(0o077)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[0] != "--limits-file" or "--" not in args:
        print("invalid sandbox child invocation", file=sys.stderr)
        return 64
    separator = args.index("--")
    limits_path = Path(args[1])
    command = args[separator + 1 :]
    if not command:
        print("sandbox command missing", file=sys.stderr)
        return 64
    limits = json.loads(limits_path.read_text(encoding="utf-8"))
    apply_limits(limits)
    os.execvpe(command[0], command, os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
