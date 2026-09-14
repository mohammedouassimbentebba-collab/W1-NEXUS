# NEXUS Benchmark Adapter Integrity Report

> **Integrity Constraint Notice**: Under NEXUS Scientific Evaluation Guidelines, any benchmark adapter
> utilizing synthetic or representative subsets rather than the full official evaluation corpus and harness
> is classified strictly as `DEMO_ONLY`. Such tasks may be used for telemetry smoke testing and
> scaffolding verification, but **MUST NOT** be counted as official benchmark leaderboard results.

## Summary Table

| Benchmark | Pinned Version | Official Tasks | Local Tasks | Official Eval | Official Env | Private Set | Integrity Status |
|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| **AutomationBench** | `v1.2-public` | 24 | 7 | No | Yes | No | **`DEMO_ONLY`** |
| **OSWorld 2.0** | `osworld-v2-2026.08.08` | 369 | 4 | No | No | No | **`DEMO_ONLY`** |
| **Terminal-Bench 4.0** | `tb4-2026.01` | 89 | 4 | No | Yes | No | **`DEMO_ONLY`** |
| **Terminal-Bench Science** | `tb-science-0.1` | 70 | 5 | No | Yes | No | **`DEMO_ONLY`** |
| **FrontierMath Tier 4** | `tier4-v2-eval` | 30 | 3 | No | Yes | Yes | **`DEMO_ONLY`** |
| **ExploitBench** | `eb-v1-sandbox` | 45 | 3 | No | Yes | No | **`DEMO_ONLY`** |
| **SRE-Bench** | `sre-2026-strict` | 50 | 3 | No | Yes | No | **`DEMO_ONLY`** |
| **MRCR v2** | `mrcr-v2-longcontext` | 100 | 4 | Yes | Yes | No | **`DEMO_ONLY`** |
| **AA Intelligence Index** | `v4.1.1` | 4 | 4 | Yes | Yes | No | **`DEMO_ONLY`** |

## Detailed Adapter Audits

### AutomationBench
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `v1.2-public`
- **Task Coverage**: 7 local tasks vs 24 official tasks (29.2%)
- **Official Evaluator Active**: False
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Curated public subset across 6 business domains (Sales, Marketing, Ops, Support, Finance, HR); not official held-out split.

### OSWorld 2.0
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `osworld-v2-2026.08.08`
- **Task Coverage**: 4 local tasks vs 369 official tasks (1.1%)
- **Official Evaluator Active**: False
- **Official Environment Active**: False
- **Private / Held-out Set**: False
- **Audit Notes**: Pinned to official release osworld-v2-2026.08.08; runs local representative GUI/web tasks without live VM cluster.

### Terminal-Bench 4.0
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `tb4-2026.01`
- **Task Coverage**: 4 local tasks vs 89 official tasks (4.5%)
- **Official Evaluator Active**: False
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Curated representative bash/process tasks; not the full official Docker execution harness.

### Terminal-Bench Science
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `tb-science-0.1`
- **Task Coverage**: 5 local tasks vs 70 official tasks (7.1%)
- **Official Evaluator Active**: False
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Curated representative tasks across 5 science domains (Life, Physical, Earth, Math, Engineering).

### FrontierMath Tier 4
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `tier4-v2-eval`
- **Task Coverage**: 3 local tasks vs 30 official tasks (10.0%)
- **Official Evaluator Active**: False
- **Official Environment Active**: True
- **Private / Held-out Set**: True
- **Audit Notes**: Official Tier 4 questions are private/held-out research assets. Uses mock verification tasks to prevent leakage.

### ExploitBench
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `eb-v1-sandbox`
- **Task Coverage**: 3 local tasks vs 45 official tasks (6.7%)
- **Official Evaluator Active**: False
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Isolated synthetic memory safety and privilege escalation challenges; zero execution against real external targets.

### SRE-Bench
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `sre-2026-strict`
- **Task Coverage**: 3 local tasks vs 50 official tasks (6.0%)
- **Official Evaluator Active**: False
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Enforces strict attempts=1 protocol. Evaluates incident remediation on representative scenarios.

### MRCR v2
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `mrcr-v2-longcontext`
- **Task Coverage**: 4 local tasks vs 100 official tasks (4.0%)
- **Official Evaluator Active**: True
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Tests multi-needle retrieval scaling across 512K, 768K, and 1M synthetic contexts.

### AA Intelligence Index
- **Status**: `DEMO_ONLY`
- **Official Version Pinned**: `v4.1.1`
- **Task Coverage**: 4 local tasks vs 4 official tasks (100.0%)
- **Official Evaluator Active**: True
- **Official Environment Active**: True
- **Private / Held-out Set**: False
- **Audit Notes**: Computes composite index using official weighting formula across 4 representative capability pillars.
