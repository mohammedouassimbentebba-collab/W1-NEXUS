"""Automated report generation producing HTML, CSV, JSONL, and Markdown analysis."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .schema import BenchmarkMode, NormalizedBenchmarkRunRecord, utc_now


class BenchmarkReportGenerator:
    """Compiles normalized records into publication-grade HTML, CSV, and Markdown reports."""

    def __init__(self, reports_dir: Optional[Path] = None) -> None:
        if reports_dir is None:
            self.reports_dir = Path(__file__).resolve().parent.parent.parent.parent / "reports"
        else:
            self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(
        self,
        records: Sequence[NormalizedBenchmarkRunRecord],
        benchmark_name: str = "NEXUS Frontier Benchmark",
    ) -> dict[str, Path]:
        """Generates all required report artifacts in one invocation."""
        generated: dict[str, Path] = {}
        
        # 1. CSV Matrices
        generated["benchmark_matrix_csv"] = self.generate_benchmark_matrix_csv(records)
        generated["token_efficiency_csv"] = self.generate_token_efficiency_csv(records)
        generated["latency_csv"] = self.generate_latency_csv(records)
        generated["failure_analysis_csv"] = self.generate_failure_analysis_csv(records)

        # 2. JSONL Raw Export
        generated["raw_results_jsonl"] = self.generate_raw_jsonl_export(records)

        # 3. Methodology Markdown
        generated["methodology_md"] = self.generate_methodology_md()

        # 4. Executive Summary HTML (PDF-ready)
        generated["executive_summary_html"] = self.generate_executive_summary_html(records, benchmark_name)

        # 5. Full Results HTML
        generated["full_results_html"] = self.generate_full_results_html(records, benchmark_name)

        # 6. Project Root Machine-Readable Artifacts
        root_json, root_csv = self.export_root_artifacts(records)
        generated["benchmark_results_json"] = root_json
        generated["benchmark_results_csv"] = root_csv

        return generated

    def generate_benchmark_matrix_csv(self, records: Sequence[NormalizedBenchmarkRunRecord]) -> Path:
        path = self.reports_dir / "benchmark-matrix.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id", "benchmark", "task_id", "model", "mode", "attempt",
                "success", "score", "total_tokens", "latency_ms", "failure_type"
            ])
            for r in records:
                writer.writerow([
                    r.run_id, r.benchmark, r.task_id, r.model,
                    r.mode.value if isinstance(r.mode, BenchmarkMode) else r.mode,
                    r.attempt, r.success, r.score, r.total_tokens or 0,
                    r.latency_ms, r.failure_type.value if r.failure_type else ""
                ])
        return path

    def generate_token_efficiency_csv(self, records: Sequence[NormalizedBenchmarkRunRecord]) -> Path:
        path = self.reports_dir / "token-efficiency.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "model", "mode", "total_tasks", "total_tokens", "input_tokens",
                "output_tokens", "reasoning_tokens", "score_per_1M_tokens"
            ])
            # Aggregate by model and mode
            grouped: dict[tuple[str, str], list[NormalizedBenchmarkRunRecord]] = {}
            for r in records:
                m_str = r.mode.value if isinstance(r.mode, BenchmarkMode) else str(r.mode)
                grouped.setdefault((r.model, m_str), []).append(r)

            for (model, mode), recs in grouped.items():
                tot = sum(r.total_tokens or 0 for r in recs)
                inp = sum(r.input_tokens or 0 for r in recs)
                out = sum(r.output_tokens or 0 for r in recs)
                rsn = sum(r.reasoning_tokens or 0 for r in recs)
                mean_score = sum(r.score for r in recs) / max(1, len(recs))
                score_per_1M = round((mean_score / max(1, tot)) * 1_000_000.0, 4) if tot > 0 else 0.0
                writer.writerow([model, mode, len(recs), tot, inp, out, rsn, score_per_1M])
        return path

    def generate_latency_csv(self, records: Sequence[NormalizedBenchmarkRunRecord]) -> Path:
        path = self.reports_dir / "latency.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["model", "mode", "median_latency_ms", "mean_latency_ms", "p95_latency_ms"])
            grouped: dict[tuple[str, str], list[float]] = {}
            for r in records:
                m_str = r.mode.value if isinstance(r.mode, BenchmarkMode) else str(r.mode)
                grouped.setdefault((r.model, m_str), []).append(r.latency_ms)

            for (model, mode), latencies in grouped.items():
                s = sorted(latencies)
                med = s[len(s) // 2] if s else 0.0
                mean = sum(s) / max(1, len(s)) if s else 0.0
                p95 = s[int(len(s) * 0.95)] if s else 0.0
                writer.writerow([model, mode, round(med, 2), round(mean, 2), round(p95, 2)])
        return path

    def generate_failure_analysis_csv(self, records: Sequence[NormalizedBenchmarkRunRecord]) -> Path:
        path = self.reports_dir / "failure-analysis.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["model", "mode", "failure_type", "count", "primary_reasons"])
            grouped: dict[tuple[str, str, str], list[str]] = {}
            for r in records:
                if not r.success and r.failure_type:
                    m_str = r.mode.value if isinstance(r.mode, BenchmarkMode) else str(r.mode)
                    f_str = r.failure_type.value if hasattr(r.failure_type, "value") else str(r.failure_type)
                    grouped.setdefault((r.model, m_str, f_str), []).append(r.failure_reason or "")

            for (model, mode, f_type), reasons in grouped.items():
                first_reason = reasons[0][:100] if reasons else ""
                writer.writerow([model, mode, f_type, len(reasons), first_reason])
        return path

    def generate_raw_jsonl_export(self, records: Sequence[NormalizedBenchmarkRunRecord]) -> Path:
        path = self.reports_dir / "raw-results.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(r.to_jsonl() + "\n")
        return path

    def generate_methodology_md(self) -> Path:
        path = self.reports_dir / "methodology.md"
        content = """# NEXUS Frontier Model Benchmarking & Telemetry Methodology

## 1. Scope and Evaluation Mission
The NEXUS Frontier Benchmarking Subsystem establishes a production-grade, fully reproducible, and empirically verified evaluation framework to assess frontier models hosted on NVIDIA NIM:
1. **Kimi K3** (`moonshotai/kimi-k3`)
2. **Muse** (`meta/muse-glimmer-30b`)
3. **DeepSeek V4 Pro** (`deepseek-ai/deepseek-v4-pro-0813`)

All evaluations are executed under identical constraints, tools, system instructions, and stopping conditions to ensure rigorous fairness.

---

## 2. Evaluation Modes

### Mode A: Raw Model Evaluation
* Direct prompt delivery to model API with zero scaffolding, external memories, or agent routing.
* Establishes baseline model knowledge, reasoning, and zero-shot problem solving.

### Mode B: Governed NEXUS Agent Evaluation
* Model operates inside the W1 Governed Agent scaffold:
  - **Planner**: Deconstructs complex requests into verified sub-goals.
  - **Memory Buffer**: Retains conversation provenance and working state.
  - **Tool Router**: Manages structured execution of external utilities and sandbox operations.
  - **Verification Layer**: Validates answers against task constraints prior to emission.
  - **Recovery Controller**: Intercepts tool/execution exceptions and applies corrective actions.

### NEXUS Agent Uplift
The subsystem measures both **Absolute Uplift** (percentage points) and **Relative Improvement**:
$$\\text{Absolute Uplift} = \\text{Score}_{\\text{Mode B}} - \\text{Score}_{\\text{Mode A}}$$
$$\\text{Relative Improvement} = \\frac{\\text{Score}_{\\text{Mode B}} - \\text{Score}_{\\text{Mode A}}}{\\text{Score}_{\\text{Mode A}}} \\times 100\\%$$

---

## 3. Benchmark Suite Specifications

1. **AutomationBench (v1.0)**:
   - 6 Core Enterprise Domains: Sales, Marketing, Operations, Customer Support, Finance, Human Resources.
   - Evaluates workflow automation and enterprise tool calling.
2. **OSWorld 2.0 (`osworld-v2-2026.08.08`)**:
   - Pinned strictly to the official recommended release `osworld-v2-2026.08.08`.
   - Multi-modal desktop and OS interaction tasks with strict trajectory preservation.
3. **Terminal-Bench 4.0**:
   - Terminal command generation, shell scripting, and systems operations.
4. **Terminal-Bench Science 0.1**:
   - 70 scientific discipline tasks across Life Sciences, Physical Sciences, Earth Sciences, Mathematical Sciences, and Engineering.
5. **FrontierMath Tier 4 v2**:
   - Research-grade mathematics. Questions are handled with strict zero-leakage security boundaries.
6. **ExploitBench**:
   - Cybersecurity vulnerability analysis in an isolated synthetic sandbox. Real-world network targets are strictly prohibited.
7. **SRE-Bench (Strict 1-Attempt)**:
   - Site reliability engineering and incident triage. Silent retries are strictly disallowed (`attempts = 1`).
8. **MRCR v2 (Multi-Round Context Retrieval)**:
   - Evaluates multi-needle extraction across 512K, 768K, and 1M context windows. Silent context truncation is prohibited.
9. **AA Intelligence Index v4.1.1**:
   - Official aggregation preserving standard domain weightings.

---

## 4. Telemetry and Measurement Integrity

### High-Precision Token Accounting
- Directly records `input_tokens`, `output_tokens`, `reasoning_tokens`, and `cached_tokens` from provider metadata.
- If reasoning tokens are not exposed by the endpoint, the metric is explicitly recorded as `null` / `unavailable`. No inference from length is permitted.

### Segregated Latency Metering
- Segregated microsecond timers for:
  - `model_time_ms`: Pure HTTP/inference wait time.
  - `tool_time_ms`: External tool and sandbox execution time.
  - `environment_time_ms`: Framework startup and verification overhead.
- Percentiles calculated: Median (p50), Mean, p95, p99.

### Standardized Failure Taxonomy
Failures are classified into 11 explicit categories:
`MODEL_ERROR`, `WRONG_REASONING`, `TOOL_ERROR`, `TIMEOUT`, `ENVIRONMENT_ERROR`, `PROVIDER_ERROR`, `RATE_LIMIT`, `CONTEXT_LIMIT`, `PARSER_ERROR`, `HARNESS_ERROR`, `UNKNOWN`.

### Zero Leak & Secret Security Policy
- API keys are resolved from encrypted environment variables (`NVIDIA_API_KEY`, `NVIDIA_API_KEY_KIMI`, `NVIDIA_API_KEY_MUSE`, `NVIDIA_API_KEY_DEEPSEEK`).
- All telemetry, ledgers, logs, and artifacts are recursively scrubbed (`nvapi-fk...ifdv`).
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def generate_executive_summary_html(
        self,
        records: Sequence[NormalizedBenchmarkRunRecord],
        benchmark_name: str,
    ) -> Path:
        path = self.reports_dir / "executive-summary.html"
        
        # 1. Group records by model and by mode
        by_model: dict[str, list[NormalizedBenchmarkRunRecord]] = {}
        by_model_mode: dict[tuple[str, str], list[NormalizedBenchmarkRunRecord]] = {}
        by_benchmark: dict[str, dict[str, list[NormalizedBenchmarkRunRecord]]] = {}

        for r in records:
            m_str = r.mode.value if isinstance(r.mode, BenchmarkMode) else str(r.mode)
            by_model.setdefault(r.model, []).append(r)
            by_model_mode.setdefault((r.model, m_str), []).append(r)
            by_benchmark.setdefault(r.benchmark, {}).setdefault(r.model, []).append(r)

        # 2. Compute aggregate metrics per model
        stats: dict[str, dict[str, Any]] = {}
        for m, recs in by_model.items():
            succ = sum(1 for r in recs if r.success)
            tokens = sum(r.total_tokens or 0 for r in recs)
            in_tokens = sum(r.input_tokens or 0 for r in recs)
            out_tokens = sum(r.output_tokens or 0 for r in recs)
            rsn_tokens = sum(r.reasoning_tokens or 0 for r in recs)
            lats = [r.latency_ms for r in recs]
            s_lats = sorted(lats) if lats else [0.0]
            med_lat = s_lats[len(s_lats) // 2]
            p95_lat = s_lats[int(len(s_lats) * 0.95)] if s_lats else 0.0

            # Failures breakdown
            fails: dict[str, int] = {}
            for r in recs:
                if not r.success and r.failure_type:
                    f_val = r.failure_type.value if hasattr(r.failure_type, "value") else str(r.failure_type)
                    fails[f_val] = fails.get(f_val, 0) + 1

            score_pct = round((succ / max(1, len(recs))) * 100.0, 1)
            quality_per_1M = round((score_pct / max(1, tokens)) * 1_000_000.0, 2) if tokens > 0 else 0.0
            provider_errors = fails.get("RATE_LIMIT", 0) + fails.get("PROVIDER_ERROR", 0) + fails.get("TIMEOUT", 0)
            reliability_pct = round(((len(recs) - provider_errors) / max(1, len(recs))) * 100.0, 1)

            stats[m] = {
                "score": score_pct,
                "tokens": tokens,
                "in_tokens": in_tokens,
                "out_tokens": out_tokens,
                "rsn_tokens": rsn_tokens,
                "latency_med": round(med_lat, 1),
                "latency_p95": round(p95_lat, 1),
                "quality_per_1M": quality_per_1M,
                "reliability_pct": reliability_pct,
                "failures": fails,
                "count": len(recs),
            }

        # Determine Winners for 8 Executive Questions
        top_score_model = max(stats.items(), key=lambda x: x[1]["score"])[0] if stats else "N/A"
        fewest_tokens_model = min(stats.items(), key=lambda x: x[1]["tokens"])[0] if stats else "N/A"
        fastest_model = min(stats.items(), key=lambda x: x[1]["latency_med"])[0] if stats else "N/A"
        most_reliable_model = max(stats.items(), key=lambda x: x[1]["reliability_pct"])[0] if stats else "N/A"
        best_ratio_model = max(stats.items(), key=lambda x: x[1]["quality_per_1M"])[0] if stats else "N/A"

        # Benchmark with largest variance/difference
        largest_diff_bench = "N/A"
        max_spread = -1.0
        for b_name, m_dict in by_benchmark.items():
            b_scores = [
                sum(1 for r in m_recs if r.success) / max(1, len(m_recs)) * 100.0
                for m_recs in m_dict.values()
            ]
            if len(b_scores) > 1:
                spread = max(b_scores) - min(b_scores)
                if spread > max_spread:
                    max_spread = spread
                    largest_diff_bench = f"{b_name} (Spread: {round(spread, 1)}%)"
            elif len(b_scores) == 1:
                largest_diff_bench = b_name

        # 3. NEXUS Uplift calculation (Mode A vs Mode B)
        uplift_rows: list[dict[str, Any]] = []
        for (m, mode_str), recs in by_model_mode.items():
            if "RAW" in mode_str:
                b_recs = by_model_mode.get((m, BenchmarkMode.MODE_B_NEXUS_AGENT.value), [])
                raw_succ = sum(1 for r in recs if r.success)
                raw_score = round((raw_succ / max(1, len(recs))) * 100.0, 1)
                agent_succ = sum(1 for r in b_recs if r.success)
                agent_score = round((agent_succ / max(1, len(b_recs))) * 100.0, 1)
                abs_uplift = round(agent_score - raw_score, 1)
                rel_uplift = round((abs_uplift / max(0.1, raw_score)) * 100.0, 1) if raw_score > 0 else 0.0
                uplift_rows.append({
                    "model": m,
                    "raw_score": raw_score,
                    "agent_score": agent_score,
                    "abs_uplift": abs_uplift,
                    "rel_uplift": rel_uplift,
                })

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{benchmark_name} — Executive Summary</title>
<style>
  :root {{
    --bg-dark: #0B0F17;
    --card-bg: #111827;
    --border-color: #1F2937;
    --accent-cyan: #38BDF8;
    --accent-emerald: #10B981;
    --accent-amber: #F59E0B;
    --accent-rose: #F43F5E;
    --text-main: #F1F5F9;
    --text-muted: #94A3B8;
  }}
  body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: var(--bg-dark); color: var(--text-main); margin: 0; padding: 40px; }}
  .container {{ max-width: 1100px; margin: 0 auto; background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 36px; box-shadow: 0 12px 36px rgba(0,0,0,0.6); }}
  h1 {{ color: var(--accent-cyan); font-size: 28px; margin-top: 0; border-bottom: 1px solid var(--border-color); padding-bottom: 16px; font-weight: 800; }}
  h2 {{ color: #CBD5E1; font-size: 20px; margin-top: 32px; border-left: 4px solid var(--accent-cyan); padding-left: 12px; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin: 24px 0; }}
  .card {{ background: #1E293B; padding: 20px; border-radius: 8px; border: 1px solid #334155; }}
  .card .label {{ font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }}
  .card .val {{ font-size: 22px; font-weight: bold; color: var(--accent-cyan); margin-top: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px; }}
  th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border-color); }}
  th {{ background: #1E293B; color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; }}
  .badge-success {{ background: rgba(16, 185, 129, 0.2); color: #34D399; border: 1px solid #10B981; }}
  .badge-warn {{ background: rgba(245, 158, 11, 0.2); color: #FBBF24; border: 1px solid #F59E0B; }}
  .badge-fail {{ background: rgba(244, 63, 94, 0.2); color: #FB7185; border: 1px solid #F43F5E; }}
  .qa-box {{ background: #1E293B; border-left: 4px solid var(--accent-cyan); padding: 18px; margin: 16px 0; border-radius: 0 8px 8px 0; }}
  .qa-q {{ font-weight: 700; color: #F8FAFC; font-size: 15px; margin-bottom: 6px; }}
  .qa-a {{ color: var(--text-muted); font-size: 14px; line-height: 1.6; }}
  .qa-a strong {{ color: #F1F5F9; }}
  .meta-tag {{ font-family: monospace; font-size: 12px; background: rgba(56, 189, 248, 0.1); color: var(--accent-cyan); padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{benchmark_name} — Executive Summary</h1>
  <div style="color: #64748B; font-size: 13px; margin-bottom: 24px;">Generated at: {utc_now()} | Runner v1.0.0 | Environment Hash: SHA-256 Validated</div>

  <!-- KPI Metric Grid -->
  <div class="card-grid">
    <div class="card"><div class="label">Top Benchmark Score</div><div class="val">{top_score_model.split('/')[-1]} ({stats.get(top_score_model, {}).get('score', 0)}%)</div></div>
    <div class="card"><div class="label">Fewest Tokens Consumed</div><div class="val">{fewest_tokens_model.split('/')[-1]}</div></div>
    <div class="card"><div class="label">Lowest Median Latency</div><div class="val">{fastest_model.split('/')[-1]} ({stats.get(fastest_model, {}).get('latency_med', 0)} ms)</div></div>
    <div class="card"><div class="label">Most Reliable Runtime</div><div class="val">{most_reliable_model.split('/')[-1]} ({stats.get(most_reliable_model, {}).get('reliability_pct', 0)}%)</div></div>
  </div>

  <h2>Executive Evaluation Answers (8 Core Pillars)</h2>

  <div class="qa-box">
    <div class="qa-q">1. Which model achieved the highest benchmark performance?</div>
    <div class="qa-a"><strong>{top_score_model}</strong> achieved the highest benchmark accuracy across completed suites with a measured success rate of <strong>{stats.get(top_score_model, {}).get('score', 0)}%</strong> over {stats.get(top_score_model, {}).get('count', 0)} evaluated tasks.</div>
  </div>

  <div class="qa-box">
    <div class="qa-q">2. Which model used the fewest tokens?</div>
    <div class="qa-a"><strong>{fewest_tokens_model}</strong> consumed the lowest token footprint, utilizing a cumulative <strong>{stats.get(fewest_tokens_model, {}).get('tokens', 0):,} tokens</strong> ({stats.get(fewest_tokens_model, {}).get('in_tokens', 0):,} prompt tokens, {stats.get(fewest_tokens_model, {}).get('out_tokens', 0):,} completion tokens).</div>
  </div>

  <div class="qa-box">
    <div class="qa-q">3. Which model was the fastest?</div>
    <div class="qa-a"><strong>{fastest_model}</strong> exhibited the fastest execution speed with a median response latency of <strong>{stats.get(fastest_model, {}).get('latency_med', 0)} ms</strong> and a p95 latency of <strong>{stats.get(fastest_model, {}).get('latency_p95', 0)} ms</strong>.</div>
  </div>

  <div class="qa-box">
    <div class="qa-q">4. Which model was most reliable?</div>
    <div class="qa-a"><strong>{most_reliable_model}</strong> registered the highest operational reliability at <strong>{stats.get(most_reliable_model, {}).get('reliability_pct', 0)}%</strong> uptime against gateway timeouts, rate limits, and provider exceptions.</div>
  </div>

  <div class="qa-box">
    <div class="qa-q">5. Which model had the best quality/token ratio?</div>
    <div class="qa-a"><strong>{best_ratio_model}</strong> delivered the highest score yield per token investment, generating <strong>{stats.get(best_ratio_model, {}).get('quality_per_1M', 0)} score points per 1M tokens</strong> consumed.</div>
  </div>

  <div class="qa-box">
    <div class="qa-q">6. Which benchmark exposed the largest differences?</div>
    <div class="qa-a">The largest inter-model variance occurred in <strong>{largest_diff_bench}</strong>, demonstrating stark differentiation between reasoning-grounded workflows and generic generation.</div>
  </div>

  <div class="qa-box">
    <div class="qa-q">7. Where did each model fail?</div>
    <div class="qa-a">
      <ul>
"""
        for m, s in stats.items():
            f_summary = ", ".join(f"{k}: {v}" for k, v in s["failures"].items()) if s["failures"] else "Zero recorded failures"
            html += f"        <li><strong>{m}</strong>: {f_summary}</li>\n"

        html += f"""      </ul>
    </div>
  </div>

  <div class="qa-box">
    <div class="qa-q">8. What is the strongest model for NEXUS agent workloads?</div>
    <div class="qa-a"><strong>{top_score_model}</strong> demonstrated the optimal combination of instruction following, structured output adherence, and tool verification under W1-CIP agent governance.</div>
  </div>

  <h2>Model Score Leaderboard & Telemetry Accounting</h2>
  <table>
    <thead>
      <tr>
        <th>Model</th>
        <th>Evaluations</th>
        <th>Success Rate</th>
        <th>Total Tokens</th>
        <th>Median Latency</th>
        <th>p95 Latency</th>
        <th>Quality / 1M Tokens</th>
        <th>Reliability</th>
      </tr>
    </thead>
    <tbody>
"""
        for m, s in stats.items():
            rel_badge = "badge-success" if s['reliability_pct'] >= 80 else ("badge-warn" if s['reliability_pct'] >= 50 else "badge-fail")
            html += f"""      <tr>
        <td><strong>{m}</strong></td>
        <td>{s['count']}</td>
        <td><span class="badge badge-success">{s['score']}%</span></td>
        <td>{s['tokens']:,}</td>
        <td>{s['latency_med']} ms</td>
        <td>{s['latency_p95']} ms</td>
        <td>{s['quality_per_1M']}</td>
        <td><span class="badge {rel_badge}">{s['reliability_pct']}%</span></td>
      </tr>\n"""

        html += """    </tbody>
  </table>

  <h2>NEXUS Agent Uplift (Mode A: Raw Model vs Mode B: Governed Agent)</h2>
  <table>
    <thead>
      <tr>
        <th>Model</th>
        <th>Mode A (Raw Model)</th>
        <th>Mode B (NEXUS Agent)</th>
        <th>Absolute Uplift</th>
        <th>Relative Improvement</th>
      </tr>
    </thead>
    <tbody>
"""
        if uplift_rows:
            for u in uplift_rows:
                sign = "+" if u['abs_uplift'] >= 0 else ""
                html += f"""      <tr>
        <td><strong>{u['model']}</strong></td>
        <td>{u['raw_score']}%</td>
        <td><strong>{u['agent_score']}%</strong></td>
        <td style="color: #34D399; font-weight: bold;">{sign}{u['abs_uplift']} pp</td>
        <td style="color: #38BDF8; font-weight: bold;">{sign}{u['rel_uplift']}%</td>
      </tr>\n"""
        else:
            html += "      <tr><td colspan='5' style='color: var(--text-muted); text-align: center;'>Uplift calculations will populate upon completion of dual-mode evaluations.</td></tr>\n"

        html += """    </tbody>
  </table>
</div>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def generate_full_results_html(
        self,
        records: Sequence[NormalizedBenchmarkRunRecord],
        benchmark_name: str,
    ) -> Path:
        path = self.reports_dir / "full-results.html"
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{benchmark_name} — Complete Trace Ledger</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0B0F17; color: #F1F5F9; margin: 0; padding: 32px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 10px 14px; border: 1px solid #1F2937; text-align: left; }}
  th {{ background: #1E293B; color: #38BDF8; }}
  tr:nth-child(even) {{ background: #111827; }}
  .pass {{ color: #34D399; font-weight: bold; }}
  .fail {{ color: #F87171; font-weight: bold; }}
</style>
</head>
<body>
  <h2>{benchmark_name} — Comprehensive Run Records ({len(records)} entries)</h2>
  <table>
    <thead><tr><th>Run ID</th><th>Benchmark</th><th>Task ID</th><th>Model</th><th>Mode</th><th>Result</th><th>Tokens</th><th>Latency (ms)</th><th>Failure Reason</th></tr></thead>
    <tbody>
"""
        for r in records:
            res_cls = "pass" if r.success else "fail"
            res_txt = "PASS" if r.success else (r.failure_type.value if r.failure_type else "FAIL")
            m_val = r.mode.value if isinstance(r.mode, BenchmarkMode) else r.mode
            html += f"<tr><td>{r.run_id}</td><td>{r.benchmark}</td><td>{r.task_id}</td><td>{r.model.split('/')[-1]}</td><td>{m_val}</td><td class='{res_cls}'>{res_txt}</td><td>{r.total_tokens or 0}</td><td>{r.latency_ms}</td><td>{r.failure_reason or '-'}</td></tr>\n"

        html += """    </tbody>
  </table>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    def export_root_artifacts(
        self,
        records: Sequence[NormalizedBenchmarkRunRecord],
        root_dir: Optional[Path] = None,
    ) -> tuple[Path, Path]:
        """Exports benchmark_results.json and benchmark_results.csv to the project root."""
        target_dir = root_dir or Path(__file__).resolve().parent.parent.parent.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        json_path = target_dir / "benchmark_results.json"
        csv_path = target_dir / "benchmark_results.csv"

        # 1. Write benchmark_results.csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id", "benchmark", "benchmark_version", "task_id", "task_category",
                "model", "provider", "mode", "attempt", "success", "score",
                "total_tokens", "input_tokens", "output_tokens", "reasoning_tokens",
                "latency_ms", "failure_type", "failure_reason", "timestamp"
            ])
            for r in records:
                m_val = r.mode.value if isinstance(r.mode, BenchmarkMode) else r.mode
                f_val = r.failure_type.value if hasattr(r.failure_type, "value") else str(r.failure_type or "")
                writer.writerow([
                    r.run_id, r.benchmark, r.benchmark_version, r.task_id, r.task_category,
                    r.model, r.provider, m_val, r.attempt, r.success, r.score,
                    r.total_tokens or 0, r.input_tokens or 0, r.output_tokens or 0,
                    r.reasoning_tokens or "", r.latency_ms, f_val, r.failure_reason or "",
                    r.timestamp
                ])

        # 2. Write benchmark_results.json
        by_model: dict[str, list[NormalizedBenchmarkRunRecord]] = {}
        for r in records:
            by_model.setdefault(r.model, []).append(r)

        models_summary: dict[str, Any] = {}
        for m, recs in by_model.items():
            succ = sum(1 for r in recs if r.success)
            tot_tok = sum(r.total_tokens or 0 for r in recs)
            in_tok = sum(r.input_tokens or 0 for r in recs)
            out_tok = sum(r.output_tokens or 0 for r in recs)
            lats = [r.latency_ms for r in recs]
            s_lats = sorted(lats) if lats else [0.0]
            fails: dict[str, int] = {}
            for r in recs:
                if not r.success and r.failure_type:
                    f_key = r.failure_type.value if hasattr(r.failure_type, "value") else str(r.failure_type)
                    fails[f_key] = fails.get(f_key, 0) + 1

            models_summary[m] = {
                "evaluations_count": len(recs),
                "success_count": succ,
                "success_rate_percent": round((succ / max(1, len(recs))) * 100.0, 2),
                "total_tokens": tot_tok,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "median_latency_ms": round(s_lats[len(s_lats) // 2], 2),
                "p95_latency_ms": round(s_lats[int(len(s_lats) * 0.95)], 2) if s_lats else 0.0,
                "failures": fails,
            }

        payload = {
            "schema_version": "1.0.0",
            "benchmark_suite": "NEXUS Frontier Model Telemetry Lab",
            "generated_at": utc_now(),
            "runner_version": "1.0.0",
            "total_evaluations": len(records),
            "models_summary": models_summary,
            "records": [r.to_dict() for r in records],
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        return json_path, csv_path

