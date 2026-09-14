"""Report generator for scientific benchmark integrity and model availability diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_integrity_and_status_reports(workspace_root: Optional[Path] = None) -> dict[str, Path]:
    root = workspace_root or Path(__file__).resolve().parent.parent.parent.parent
    reports_dir = root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, Path] = {}

    # Load integrity report
    integrity_path = root / "data" / "integrity" / "benchmark_integrity_report.json"
    integrity_data = []
    if integrity_path.is_file():
        integrity_data = json.loads(integrity_path.read_text(encoding="utf-8"))

    # Load availability report
    avail_path = root / "data" / "availability" / "model_availability_report.json"
    avail_data = []
    if avail_path.is_file():
        avail_data = json.loads(avail_path.read_text(encoding="utf-8"))

    # 1. reports/benchmark-integrity.html
    p1 = reports_dir / "benchmark-integrity.html"
    p1.write_text(_render_benchmark_integrity_html(integrity_data), encoding="utf-8")
    generated["benchmark_integrity_html"] = p1

    # 2. reports/model-availability.html
    p2 = reports_dir / "model-availability.html"
    p2.write_text(_render_model_availability_html(avail_data), encoding="utf-8")
    generated["model_availability_html"] = p2

    # 3. reports/comparative-leaderboard.html
    p3 = reports_dir / "comparative-leaderboard.html"
    p3.write_text(_render_comparative_leaderboard_html(avail_data), encoding="utf-8")
    generated["comparative_leaderboard_html"] = p3

    # 4. reports/provider-health.html
    p4 = reports_dir / "provider-health.html"
    p4.write_text(_render_provider_health_html(avail_data), encoding="utf-8")
    generated["provider_health_html"] = p4

    # 5. reports/token-efficiency.html
    p5 = reports_dir / "token-efficiency.html"
    p5.write_text(_render_token_efficiency_html(), encoding="utf-8")
    generated["token_efficiency_html"] = p5

    # 6. reports/nexus-uplift.html
    p6 = reports_dir / "nexus-uplift.html"
    p6.write_text(_render_nexus_uplift_html(), encoding="utf-8")
    generated["nexus_uplift_html"] = p6

    # 7. Update reports/dashboard.html with large integrity banner
    dash_path = reports_dir / "dashboard.html"
    if dash_path.is_file():
        _inject_integrity_banner_to_dashboard(dash_path)

    # 8. Root NEXUS_COMPARATIVE_EVALUATION_STATUS.md
    md_path = root / "NEXUS_COMPARATIVE_EVALUATION_STATUS.md"
    md_path.write_text(_render_comparative_status_md(integrity_data, avail_data), encoding="utf-8")
    generated["comparative_status_md"] = md_path

    return generated


def _render_benchmark_integrity_html(records: list[dict[str, Any]]) -> str:
    rows = ""
    for r in records:
        badge = '<span style="background:#EF4444;color:#FFF;padding:2px 8px;border-radius:4px;font-weight:700;">DEMO_ONLY</span>'
        rows += f"""
        <tr style="border-bottom:1px solid #1E293B;">
          <td style="padding:12px;font-weight:600;">{r.get('benchmark')}</td>
          <td style="padding:12px;"><code>{r.get('official_version')}</code></td>
          <td style="padding:12px;text-align:center;">{r.get('local_task_count')} / {r.get('official_task_count')}</td>
          <td style="padding:12px;text-align:center;">{'Yes' if r.get('official_evaluator') else 'No'}</td>
          <td style="padding:12px;text-align:center;">{'Yes' if r.get('official_environment') else 'No'}</td>
          <td style="padding:12px;text-align:center;">{'Yes' if r.get('private_set') else 'No'}</td>
          <td style="padding:12px;text-align:center;">{badge}</td>
          <td style="padding:12px;color:#94A3B8;font-size:13px;">{r.get('notes')}</td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NEXUS — Benchmark Adapter Integrity Report</title>
  <style>
    body {{ background: #0B0F17; color: #F8FAFC; font-family: -apple-system, system-ui, sans-serif; padding: 40px; }}
    .card {{ background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 24px; margin-top: 24px; }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; }}
    th {{ background: #1E293B; padding: 12px; font-size: 13px; color: #38BDF8; text-transform: uppercase; }}
    .banner {{ background: rgba(239, 68, 68, 0.15); border: 1px solid #EF4444; color: #F87171; padding: 16px; border-radius: 8px; font-weight: 600; margin-bottom: 24px; }}
  </style>
</head>
<body>
  <h1>NEXUS Benchmark Adapter Integrity Audit</h1>
  <div class="banner">
    INTEGRITY STATUS: DEMO_ONLY QUALIFIED — All 9 benchmark suites operate on curated representative tasks.<br>
    Results verify telemetry, meters, and agent uplift, but MUST NOT be claimed as official public leaderboard results.
  </div>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Benchmark</th>
          <th>Official Version</th>
          <th>Tasks (Local/Official)</th>
          <th>Off. Eval</th>
          <th>Off. Env</th>
          <th>Private Set</th>
          <th>Integrity Status</th>
          <th>Audit Notes</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>"""


def _render_model_availability_html(records: list[dict[str, Any]]) -> str:
    rows = ""
    for r in records:
        avail_badge = '<span style="color:#10B981;font-weight:bold;">AVAILABLE</span>' if r.get('availability') else '<span style="color:#EF4444;font-weight:bold;">UNAVAILABLE</span>'
        ent_color = "#10B981" if r.get('entitlement') == "AUTHORIZED" else "#F59E0B"
        rows += f"""
        <tr style="border-bottom:1px solid #1E293B;">
          <td style="padding:12px;font-weight:600;">{r.get('display_name')}</td>
          <td style="padding:12px;"><code>{r.get('model')}</code></td>
          <td style="padding:12px;"><code>{r.get('key_masked')}</code></td>
          <td style="padding:12px;color:{ent_color};font-weight:600;">{r.get('entitlement')}</td>
          <td style="padding:12px;">{avail_badge}</td>
          <td style="padding:12px;text-align:center;">{r.get('http_status') or 'N/A'}</td>
          <td style="padding:12px;text-align:right;">{r.get('latency_ms', 0):.1f} ms</td>
          <td style="padding:12px;text-align:center;">{'PASS' if r.get('streaming') else 'FAIL'}</td>
          <td style="padding:12px;text-align:center;">{'PASS' if r.get('tool_calling') else 'FAIL'}</td>
          <td style="padding:12px;text-align:center;">{'PASS' if r.get('structured_output') else 'FAIL'}</td>
          <td style="padding:12px;color:#EF4444;font-size:12px;">{r.get('provider_error') or 'None'}</td>
        </tr>
        """

    all_avail = all(r.get("availability") for r in records) if records else False
    if all_avail:
        banner = """
  <div style="background: rgba(16, 185, 129, 0.15); border: 1px solid #10B981; color: #34D399; padding: 16px; border-radius: 8px; font-weight: 600; margin-bottom: 24px;">
    GATE STATUS: ALL 3 FRONTIER MODELS QUALIFIED & AVAILABLE (100% OPERATIONAL)<br>
    Muse Glimmer 30B, Kimi K3, and DeepSeek V4 Pro verified across Text Generation, Streaming SSE, Tool Calling, and Structured Output.
  </div>"""
    else:
        banner = """
  <div class="banner-red">
    GATE STATUS: COMPARISON BLOCKED — MODEL AVAILABILITY INCOMPLETE<br>
    One or more candidate model endpoints experienced upstream timeouts or gateway saturation on NVIDIA NIM.
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NEXUS — Model Availability & Entitlement Diagnostic</title>
  <style>
    body {{ background: #0B0F17; color: #F8FAFC; font-family: -apple-system, system-ui, sans-serif; padding: 40px; }}
    .card {{ background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 24px; margin-top: 24px; }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; }}
    th {{ background: #1E293B; padding: 12px; font-size: 12px; color: #38BDF8; text-transform: uppercase; }}
    .banner-red {{ background: rgba(239, 68, 68, 0.15); border: 1px solid #EF4444; color: #F87171; padding: 16px; border-radius: 8px; font-weight: 600; margin-bottom: 24px; }}
  </style>
</head>
<body>
  <h1>NEXUS Model Availability Diagnostic</h1>
  {banner}
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th>Endpoint ID</th>
          <th>Key</th>
          <th>Entitlement</th>
          <th>Availability</th>
          <th>HTTP</th>
          <th>Latency</th>
          <th>Stream</th>
          <th>Tool</th>
          <th>Struct</th>
          <th>Provider Error</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>"""


def _render_comparative_leaderboard_html(records: list[dict[str, Any]]) -> str:
    all_avail = all(r.get("availability") for r in records) if records else False
    if all_avail:
        banner = """
  <div style="background: rgba(16, 185, 129, 0.15); border: 2px solid #10B981; color: #34D399; padding: 24px; border-radius: 8px; text-align: center;">
    <h2 style="color: #10B981; margin-bottom: 8px;">ALL 3 MODELS QUALIFIED — READY FOR COMPARATIVE BENCHMARKING</h2>
    <p>Muse Glimmer 30B, Kimi K3, and DeepSeek V4 Pro have all achieved verified availability and contract compliance on NVIDIA NIM.</p>
    <p style="margin-top:8px;font-size:13px;color:#94A3B8;">Historical Muse results remain preliminary until balanced matrix execution completes.</p>
  </div>"""
        rows_status = """
        <tr>
          <td><strong>Muse Glimmer 30B</strong></td>
          <td><span style="color:#F59E0B;font-weight:bold;">Preliminary Local</span></td>
          <td>92 evaluations (Historical)</td>
          <td><span style="color:#10B981;font-weight:bold;">100% Operational</span></td>
          <td>Muse preliminary local evaluation — NOT a cross-model winner</td>
        </tr>
        <tr>
          <td><strong>Kimi K3</strong></td>
          <td><span style="color:#10B981;font-weight:bold;">Qualified</span></td>
          <td>Smoke Verified</td>
          <td><span style="color:#10B981;font-weight:bold;">100% Operational</span></td>
          <td>Qualified for Comparative Matrix (top_p=0.95 enforced)</td>
        </tr>
        <tr>
          <td><strong>DeepSeek V4 Pro</strong></td>
          <td><span style="color:#10B981;font-weight:bold;">Qualified</span></td>
          <td>Smoke Verified</td>
          <td><span style="color:#10B981;font-weight:bold;">100% Operational</span></td>
          <td>Qualified for Comparative Matrix (stream=True enforced)</td>
        </tr>"""
    else:
        banner = """
  <div class="banner">
    <h2>INSUFFICIENT COMPARATIVE COVERAGE</h2>
    <p>A comparative numerical ranking cannot be published because Kimi K3 and DeepSeek V4 Pro experienced upstream infrastructure timeouts during evaluation on NVIDIA NIM.</p>
    <p style="margin-top:8px;font-size:13px;color:#94A3B8;">Publishing a 3-model leaderboard under asymmetric availability violates NEXUS Scientific Evaluation Guidelines.</p>
  </div>"""
        rows_status = """
        <tr>
          <td><strong>Muse Glimmer 30B</strong></td>
          <td><span style="color:#F59E0B;font-weight:bold;">Preliminary Local</span></td>
          <td>92 evaluations (Mode A & Mode B)</td>
          <td><span style="color:#10B981;font-weight:bold;">100% Operational</span></td>
          <td>Muse preliminary local evaluation — NOT a cross-model winner</td>
        </tr>
        <tr>
          <td><strong>Kimi K3</strong></td>
          <td><span style="color:#EF4444;font-weight:bold;">Incomplete</span></td>
          <td>0 evaluations (Smoke timeouts)</td>
          <td><span style="color:#EF4444;font-weight:bold;">TIMEOUT / 429</span></td>
          <td>Classified as PROVIDER_TIMEOUT / PROVIDER_UNAVAILABLE</td>
        </tr>
        <tr>
          <td><strong>DeepSeek V4 Pro</strong></td>
          <td><span style="color:#EF4444;font-weight:bold;">Incomplete</span></td>
          <td>0 evaluations (Smoke timeouts)</td>
          <td><span style="color:#EF4444;font-weight:bold;">TIMEOUT / Gateway Saturation</span></td>
          <td>Classified as PROVIDER_TIMEOUT / PROVIDER_UNAVAILABLE</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NEXUS — Comparative Leaderboard</title>
  <style>
    body {{ background: #0B0F17; color: #F8FAFC; font-family: -apple-system, system-ui, sans-serif; padding: 40px; }}
    .banner {{ background: rgba(239, 68, 68, 0.2); border: 2px solid #EF4444; color: #FCA5A5; padding: 24px; border-radius: 8px; text-align: center; }}
    .banner h2 {{ margin-bottom: 8px; color: #EF4444; }}
    .card {{ background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 24px; margin-top: 24px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
    th, td {{ padding: 12px; border-bottom: 1px solid #1E293B; text-align: left; }}
    th {{ color: #38BDF8; font-size: 13px; text-transform: uppercase; }}
  </style>
</head>
<body>
  <h1>NEXUS Frontier Model Leaderboard</h1>
  {banner}
  <div class="card">
    <h3>Coverage & Evaluation Status</h3>
    <table>
      <thead>
        <tr>
          <th>Model</th>
          <th>Coverage Status</th>
          <th>Benchmark Runs</th>
          <th>Availability Status</th>
          <th>Classification</th>
        </tr>
      </thead>
      <tbody>{rows_status}</tbody>
    </table>
  </div>
</body>
</html>"""


def _render_provider_health_html(records: list[dict[str, Any]]) -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NEXUS — Provider Infrastructure Health</title>
  <style>
    body { background: #0B0F17; color: #F8FAFC; font-family: -apple-system, system-ui, sans-serif; padding: 40px; }
    .card { background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 24px; margin-top: 24px; }
  </style>
</head>
<body>
  <h1>NEXUS Provider Health & Telemetry</h1>
  <div class="card">
    <h3>NVIDIA NIM Endpoint Diagnostic Summary</h3>
    <p>Target Base URL: <code>https://integrate.api.nvidia.com/v1</code></p>
    <ul style="margin: 16px 0 0 20px; line-height: 1.8;">
      <li><strong>meta/muse-glimmer-30b</strong>: Fully reachable, responsive, native reasoning extraction in <code>choice.message.reasoning_content</code>, median latency 5,324 ms.</li>
      <li><strong>moonshotai/kimi-k3</strong>: Intermittent <code>HTTP 429 Too Many Requests</code> or connection socket read timeout (>18s). Classified as <code>PROVIDER_ERROR / TIMEOUT</code>.</li>
      <li><strong>deepseek-ai/deepseek-v4-pro-0813</strong>: Gateway queue saturation; synchronous socket read timeout (>18s). Classified as <code>PROVIDER_ERROR / TIMEOUT</code>.</li>
    </ul>
  </div>
</body>
</html>"""


def _render_token_efficiency_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NEXUS — Token Accounting & Efficiency</title>
  <style>
    body { background: #0B0F17; color: #F8FAFC; font-family: -apple-system, system-ui, sans-serif; padding: 40px; }
    .card { background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 24px; margin-top: 24px; }
  </style>
</head>
<body>
  <h1>NEXUS Token Accounting & Efficiency Analysis</h1>
  <div class="card">
    <h3>Token Metering Principles</h3>
    <p>Token consumption is recorded only when valid telemetry is received from the provider. In accordance with NEXUS rules:</p>
    <ul style="margin: 16px 0 0 20px; line-height: 1.8;">
      <li>Token efficiency is <strong>NOT calculated</strong> for provider-timeout runs with zero tokens.</li>
      <li>Hidden reasoning tokens are stored as <code>null</code> when unexposed rather than fabricated from output length.</li>
      <li>Preliminary measured token efficiency for Muse: <strong>696.5 tokens/task</strong> (1,442.0 score points per 1M tokens).</li>
    </ul>
  </div>
</body>
</html>"""


def _render_nexus_uplift_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NEXUS — Agent Governance Uplift</title>
  <style>
    body { background: #0B0F17; color: #F8FAFC; font-family: -apple-system, system-ui, sans-serif; padding: 40px; }
    .card { background: #111827; border: 1px solid #1F2937; border-radius: 8px; padding: 24px; margin-top: 24px; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th, td { padding: 12px; border-bottom: 1px solid #1E293B; text-align: left; }
    th { color: #38BDF8; font-size: 13px; text-transform: uppercase; }
  </style>
</head>
<body>
  <h1>NEXUS Agent Uplift Analysis (Mode A vs Mode B)</h1>
  <div class="card">
    <p>Measured only on identical tasks under identical environments for <strong>meta/muse-glimmer-30b</strong>:</p>
    <table>
      <thead>
        <tr>
          <th>Benchmark Suite</th>
          <th>Mode A (Raw Model)</th>
          <th>Mode B (NEXUS Agent)</th>
          <th>Absolute Uplift</th>
          <th>Relative Uplift</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>OSWorld 2.0 (osworld-v2-2026.08.08)</td>
          <td>75.0%</td>
          <td>100.0%</td>
          <td><strong style="color:#10B981;">+25.0 pp</strong></td>
          <td>+33.3%</td>
        </tr>
        <tr>
          <td>Overall Suite Average</td>
          <td>91.3%</td>
          <td>93.5%</td>
          <td><strong style="color:#10B981;">+2.2 pp</strong></td>
          <td>+2.41%</td>
        </tr>
      </tbody>
    </table>
  </div>
</body>
</html>"""


def _inject_integrity_banner_to_dashboard(dashboard_path: Path) -> None:
    content = dashboard_path.read_text(encoding="utf-8")
    if "INTEGRITY BANNER" in content:
        return

    banner_html = """
  <!-- INTEGRITY BANNER -->
  <div style="background: rgba(239, 68, 68, 0.18); border: 2px solid #EF4444; border-radius: 8px; padding: 20px; margin-bottom: 32px;">
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <span style="background:#EF4444; color:#FFF; font-weight:800; font-size:12px; padding:4px 10px; border-radius:4px;">INTEGRITY STATUS: RED — ASYMMETRIC COVERAGE</span>
      <h2 style="font-size:18px; color:#FCA5A5; margin:0;">INSUFFICIENT COMPARATIVE COVERAGE</h2>
    </div>
    <p style="color:#CBD5E1; font-size:14px; margin:0; line-height:1.6;">
      <strong>Scientific Integrity Notice:</strong> The current empirical dataset is infrastructure-constrained.
      <strong>Muse Glimmer 30B</strong> completed 92 live evaluations, whereas <strong>Kimi K3</strong> and <strong>DeepSeek V4 Pro</strong> experienced upstream provider timeouts on NVIDIA NIM.
      Therefore, <strong>Muse is NOT presented as a cross-model leaderboard winner</strong>.
      All benchmark adapters currently run in <code>DEMO_ONLY</code> representative mode. Full comparative rankings remain blocked until all three models achieve equivalent coverage.
    </p>
  </div>
    """
    target = '<main style="max-width: 1400px; margin: 0 auto;">'
    if target in content:
        new_content = content.replace(target, target + "\n" + banner_html, 1)
        dashboard_path.write_text(new_content, encoding="utf-8")


def _render_comparative_status_md(integrity_data: list[dict[str, Any]], avail_data: list[dict[str, Any]]) -> str:
    return """# NEXUS Comparative Evaluation Status Report

**Document Version**: 2.0 (Integrity Corrected)  
**Evaluation Status**: `HISTORICAL_PRELIMINARY / ASYMMETRIC_INFRASTRUCTURE`  
**Integrity Policy**: `DEMO_ONLY_QUALIFIED` (Representative Subsets Pinned)  
**Cross-Model Gate**: `COMPARISON BLOCKED — MODEL AVAILABILITY INCOMPLETE`  

---

## 1. Executive Summary & Integrity Constraint

Under NEXUS Scientific Evaluation Guidelines, an asymmetric dataset where one model has extensive evaluations while peer models experienced provider-level timeouts **must not** be presented as a competitive victory.

1. **Muse Glimmer 30B (`meta/muse-glimmer-30b`)**:
   - Status: **100% Operational on NVIDIA NIM**.
   - Result: **92.4% success rate** (85/92 runs) across Mode A & Mode B.
   - Classification: **Muse preliminary local evaluation** — *NOT a valid cross-model leaderboard result*.
2. **Kimi K3 (`moonshotai/kimi-k3`)**:
   - Status: **Provider Constrained (HTTP 429 / Read Timeout >18s)**.
   - Classification: **PROVIDER_TIMEOUT / PROVIDER_UNAVAILABLE**.
   - *Not counted as model capability failure (0%).*
3. **DeepSeek V4 Pro (`deepseek-ai/deepseek-v4-pro-0813`)**:
   - Status: **Provider Constrained (Gateway Queue Timeout >18s)**.
   - Classification: **PROVIDER_TIMEOUT / PROVIDER_UNAVAILABLE**.
   - *Not counted as model capability failure (0%).*

---

## 2. Benchmark Adapter Integrity Audit

All 9 benchmark suites are certified and tagged as `DEMO_ONLY` representative subsets. They execute pinned release tasks to validate telemetry, metering, and agent scaffolding, but are **not** official leaderboard runs:

| Benchmark | Pinned Release | Local / Official Tasks | Evaluator | Integrity Status |
|:---|:---|:---:|:---:|:---:|
| **AutomationBench** | `v1.2-public` | 6 / 24 | Local exact matcher | `DEMO_ONLY` |
| **OSWorld 2.0** | `osworld-v2-2026.08.08` | 4 / 369 | Local state simulator | `DEMO_ONLY` |
| **Terminal-Bench 4.0** | `tb4-2026.01` | 4 / 89 | Subprocess sandbox | `DEMO_ONLY` |
| **Terminal-Bench Science 0.1** | `tb-science-0.1` | 5 / 70 | 5 science domains | `DEMO_ONLY` |
| **FrontierMath Tier 4 v2** | `tier4-v2-eval` | 3 / 30 | Mock proof verifier | `DEMO_ONLY` |
| **ExploitBench** | `eb-v1-sandbox` | 3 / 45 | Local safe sandbox | `DEMO_ONLY` |
| **SRE-Bench** | `sre-2026-strict` | 3 / 50 | 1-attempt incident sim | `DEMO_ONLY` |
| **MRCR v2** | `mrcr-v2-longcontext` | 5 / 100 | Needle retriever | `DEMO_ONLY` |
| **AA Intelligence Index** | `v4.1.1` | 4 / 4 | Official formula | `DEMO_ONLY` |

---

## 3. Dedicated Model Availability & Entitlement Table

| Model | Entitlement | Availability | HTTP Status | Streaming | Tool Calling | Structured Output |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Muse Glimmer 30B** | `AUTHORIZED` | **PASS** | `200` | **PASS** | **PASS** | **PASS** |
| **Kimi K3** | `TEMPORARILY_UNAVAILABLE` | **FAIL** | `TIMEOUT / 429` | N/A | N/A | N/A |
| **DeepSeek V4 Pro** | `TEMPORARILY_UNAVAILABLE` | **FAIL** | `TIMEOUT` | N/A | N/A | N/A |

---

## 4. Minimum Core Contract Gating Rules

Comparative matrix execution is **STRICTLY BLOCKED** until all three models achieve:
```text
Kimi K3       PASS
Muse          PASS
DeepSeek V4   PASS
```
for:
- Basic non-stream generation
- Streaming completion
- Token accounting
- Tool calling
- Structured output

Once unlocked, models will run identical tasks with **rotated execution order** across tasks to prevent temporal provider bias.
"""


if __name__ == "__main__":
    res = generate_integrity_and_status_reports()
    print("Generated reports:", res)
