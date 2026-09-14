from __future__ import annotations

import argparse
from pathlib import Path

from w1cip.cli_support import initialize_workspace
from w1cip.reference_demo import run_reference_demo
from w1cip.workspace_console import ConsoleSettings, serve_console


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the W1 Nexus Workspace reference console")
    parser.add_argument("workspace", nargs="?", default="./w1-workspace-demo")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--skip-demo", action="store_true")
    args = parser.parse_args()
    paths = initialize_workspace(Path(args.workspace))
    if not args.skip_demo:
        run_reference_demo(paths.root, reset=True)
    print(f"W1 Nexus Workspace: http://127.0.0.1:{args.port}/")
    serve_console(paths, ConsoleSettings(port=args.port))


if __name__ == "__main__":
    main()
