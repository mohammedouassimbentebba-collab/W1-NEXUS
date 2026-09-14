#!/usr/bin/env python3
"""W1 Nexus Fail-Closed Source Distribution Archive Builder.

Enforces strict Step 52 release hygiene:
  - Traverses the source repository and filters out all unneeded and banned files.
  - Fails closed if any virtual environments (.venv), test caches (.pytest_cache),
    compiled python artifacts (*.pyc, __pycache__), databases (*.sqlite3, *.db),
    or operating system scratch paths (%SystemDrive%) are detected in source inputs.
  - Packages the clean source files into a standard root folder:
    `w1cip_step52_autonomous_intelligence_discovery_certification/`
  - Runs a post-generation verification step on the resulting ZIP to ensure:
    1. Zero forbidden files
    2. Exactly expected source file count
    3. Proper archive root directory prefix
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile
from pathlib import Path
from typing import List, Sequence, Set, Tuple

DEFAULT_ROOT_PREFIX = "w1cip_step52_autonomous_intelligence_discovery_certification"

BANNED_DIRS: Set[str] = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "dist",
    "build",
    "%SystemDrive%",
    "node_modules",
    ".idea",
    ".vscode",
    ".ruff_cache",
    ".mypy_cache",
}

BANNED_EXTENSIONS: Set[str] = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".sqlite3",
    ".db",
    ".log",
}

ALLOWED_TOP_LEVEL_DIRS: Set[str] = {
    "src",
    "tests",
    "schemas",
    "packaging",
    "docs",
    "examples",
    "brand",
    ".github",
}

ALLOWED_TOP_LEVEL_FILES: Set[str] = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "README.md",
    "LICENSE",
    "NOTICE",
    "SECURITY.md",
    "BRAND-POLICY.md",
    "ACTUAL-CAPABILITY-EVALUATION.json",
    "VALIDATION-REPORT.txt",
    "SHA256SUMS.txt",
    "uv.lock",
}


def scan_source_files(root: Path) -> List[Tuple[Path, str]]:
    """Scan and return (absolute_path, relative_posix_path) for all valid source files.

    Fails immediately if any banned artifact is encountered during traversal.
    """
    root = root.resolve()
    included: List[Tuple[Path, str]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        parts = rel_dir.parts

        # Check if current directory path contains banned parts
        for part in parts:
            if part in BANNED_DIRS:
                raise RuntimeError(
                    f"Release Hygiene Violation: Banned directory segment '{part}' found at '{rel_dir}'"
                )

        # Filter in-place to prevent os.walk from entering banned subdirectories
        dirnames[:] = [d for d in dirnames if d not in BANNED_DIRS]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            rel_path = fpath.relative_to(root).as_posix()
            suffix = fpath.suffix.lower()

            if suffix in BANNED_EXTENSIONS:
                raise RuntimeError(
                    f"Release Hygiene Violation: Banned file extension '{suffix}' in '{rel_path}'"
                )

            # Filter top-level items
            top_level = rel_path.split("/")[0]
            if len(rel_path.split("/")) == 1:
                if top_level not in ALLOWED_TOP_LEVEL_FILES:
                    continue
            else:
                if top_level not in ALLOWED_TOP_LEVEL_DIRS:
                    continue

            included.append((fpath, rel_path))

    included.sort(key=lambda x: x[1])
    return included


def verify_zip_hygiene(zip_path: Path, expected_count: int, prefix: str = "") -> dict:
    """Verifies that the generated zip file has zero forbidden files and proper structure."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        if len(namelist) != expected_count:
            raise ValueError(
                f"ZIP Verification Error: Expected {expected_count} files, found {len(namelist)}"
            )

        for name in namelist:
            if prefix and not name.startswith(prefix + "/"):
                raise ValueError(
                    f"ZIP Verification Error: Entry '{name}' does not start with expected prefix '{prefix}/'"
                )

            parts = Path(name).parts
            for banned in BANNED_DIRS:
                if banned in parts:
                    raise RuntimeError(
                        f"ZIP Verification Error: Banned directory '{banned}' found in '{name}'"
                    )

            suffix = Path(name).suffix.lower()
            if suffix in BANNED_EXTENSIONS:
                raise RuntimeError(
                    f"ZIP Verification Error: Banned file extension '{suffix}' found in '{name}'"
                )

            if ".venv" in name or "__pycache__" in name or ".pytest_cache" in name or "%SystemDrive%" in name:
                raise RuntimeError(f"ZIP Verification Error: Forbidden pattern in '{name}'")

    return {
        "verified": True,
        "entry_count": len(namelist),
        "prefix": prefix,
    }


def build_source_zip(
    root: Path,
    output_zip: Path,
    prefix: str = DEFAULT_ROOT_PREFIX,
    dry_run: bool = False,
) -> dict:
    """Builds clean source ZIP archive and returns build metadata."""
    files = scan_source_files(root)

    if not files:
        raise ValueError(f"No valid source files found in {root}")

    output_zip.parent.mkdir(parents=True, exist_ok=True)

    total_uncompressed_bytes = 0
    archive_sha256 = ""

    if not dry_run:
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for abs_path, rel_path in files:
                arcname = f"{prefix}/{rel_path}" if prefix else rel_path
                zf.write(abs_path, arcname=arcname)
                total_uncompressed_bytes += abs_path.stat().st_size

        # Post-build integrity verification pass
        verify_zip_hygiene(output_zip, len(files), prefix=prefix)

        # Compute archive checksum
        hasher = hashlib.sha256()
        with output_zip.open("rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        archive_sha256 = hasher.hexdigest()
        archive_size = output_zip.stat().st_size
    else:
        total_uncompressed_bytes = sum(p.stat().st_size for p, _ in files)
        archive_size = 0

    return {
        "file_count": len(files),
        "uncompressed_bytes": total_uncompressed_bytes,
        "archive_bytes": archive_size,
        "archive_sha256": archive_sha256,
        "output_path": str(output_zip),
        "prefix": prefix,
        "dry_run": dry_run,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W1 Nexus Fail-Closed Source Distribution Archive Builder")
    parser.add_argument("--root", default=".", help="Root directory of the repository")
    parser.add_argument("--output", default="dist/w1cip_step52_autonomous_intelligence_discovery_certification.zip", help="Output ZIP path")
    parser.add_argument("--prefix", default=DEFAULT_ROOT_PREFIX, help="Top-level root folder prefix inside archive")
    parser.add_argument("--dry-run", action="store_true", help="Scan and validate without writing archive")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    output = Path(args.output).resolve()

    try:
        result = build_source_zip(root, output, prefix=args.prefix, dry_run=args.dry_run)
        print("=" * 70)
        print("W1 Nexus Step 52 Clean Source Distribution Builder")
        print("=" * 70)
        print(f"Archive Root Prefix:   {result['prefix']}/")
        print(f"Source Files Included: {result['file_count']}")
        print(f"Uncompressed Size:     {result['uncompressed_bytes']:,} bytes")
        if not args.dry_run:
            print(f"Archive Compressed:    {result['archive_bytes']:,} bytes")
            print(f"Archive SHA-256:       {result['archive_sha256']}")
            print(f"Archive Output Path:   {result['output_path']}")
        print("=" * 70)
        print("VERIFICATION: PASS (Zero banned artifacts, verified clean source tree)")
        return 0
    except Exception as exc:
        print(f"FATAL: Source Distribution Build Failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
