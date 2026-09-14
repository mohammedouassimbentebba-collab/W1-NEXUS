"""Native executable entrypoint used by desktop packagers."""

from __future__ import annotations

import argparse
from pathlib import Path

from .cli_support import require_workspace
from .desktop_shell import DesktopSettings, launch_desktop
from .native_packaging import ProjectDescriptor, parse_deep_link


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="W1 Nexus")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--project")
    parser.add_argument("--deep-link")
    parser.add_argument("--allow-operations", action="store_true")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).expanduser().resolve()
    if args.project:
        descriptor_path = Path(args.project).expanduser().resolve()
        descriptor = ProjectDescriptor.load(descriptor_path)
        workspace = descriptor.resolve_workspace(descriptor_path)
    if args.deep_link:
        # Parsing is deliberately performed before launch; unsupported routes,
        # shell-like parameters, and traversal are rejected fail-closed.
        parse_deep_link(args.deep_link)

    paths = require_workspace(workspace)
    launch_desktop(paths, DesktopSettings(allow_operations=bool(args.allow_operations)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
