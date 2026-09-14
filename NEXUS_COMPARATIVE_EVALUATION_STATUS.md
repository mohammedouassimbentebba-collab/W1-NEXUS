# NEXUS Comparative Evaluation Status Report

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
