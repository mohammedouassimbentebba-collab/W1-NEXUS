"""Archive management module to preserve historical preliminary benchmark runs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional


def archive_historical_evaluation(
    workspace_root: Optional[Path] = None,
    archive_id: str = "evaluation_2026-09-06",
) -> dict[str, Any]:
    """Preserves all historical preliminary results into an immutable archive folder."""
    root = workspace_root or Path(__file__).resolve().parent.parent.parent.parent
    archive_dir = root / "data" / "archive" / archive_id
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1. Snapshot raw data
    raw_dir = root / "data" / "raw"
    archive_raw = archive_dir / "raw"
    archive_raw.mkdir(parents=True, exist_ok=True)
    raw_files_copied = 0
    if raw_dir.is_dir():
        for f in raw_dir.glob("*.jsonl"):
            dest = archive_raw / f.name
            shutil.copy2(f, dest)
            raw_files_copied += 1

    # 2. Snapshot reports
    reports_dir = root / "reports"
    archive_reports = archive_dir / "reports"
    archive_reports.mkdir(parents=True, exist_ok=True)
    report_files_copied = 0
    if reports_dir.is_dir():
        for f in reports_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, archive_reports / f.name)
                report_files_copied += 1

    # 3. Snapshot summary files
    for fname in ["benchmark_results.json", "benchmark_results.csv", "NEXUS_BENCHMARK_FINAL_REPORT.md"]:
        src = root / fname
        if src.is_file():
            shutil.copy2(src, archive_dir / fname)

    # 4. Ensure required data directories exist
    for sub in ["availability", "integrity", "normalized", "derived", "artifacts"]:
        (root / "data" / sub).mkdir(parents=True, exist_ok=True)

    # 5. Write immutable manifest
    manifest = {
        "archive_id": archive_id,
        "evaluation_status": "HISTORICAL_PRELIMINARY",
        "archive_timestamp": "2026-09-06T22:25:00Z",
        "raw_files_archived": raw_files_copied,
        "report_files_archived": report_files_copied,
        "reason": "Asymmetric model coverage due to upstream provider-level NIM gateway timeouts on Kimi K3 and DeepSeek V4 Pro",
        "muse_evaluation_status": (
            "Muse preliminary local evaluation — NOT a valid cross-model leaderboard result "
            "until Kimi K3 and DeepSeek V4 Pro complete equivalent evaluation coverage."
        ),
        "kimi_evaluation_status": "PROVIDER_TIMEOUT / PROVIDER_UNAVAILABLE",
        "deepseek_evaluation_status": "PROVIDER_TIMEOUT / PROVIDER_UNAVAILABLE",
    }
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return manifest


if __name__ == "__main__":
    res = archive_historical_evaluation()
    print("Archive created successfully:", res)
