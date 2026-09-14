# NEXUS Frontier Model Benchmarking & Telemetry Methodology

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
$$\text{Absolute Uplift} = \text{Score}_{\text{Mode B}} - \text{Score}_{\text{Mode A}}$$
$$\text{Relative Improvement} = \frac{\text{Score}_{\text{Mode B}} - \text{Score}_{\text{Mode A}}}{\text{Score}_{\text{Mode A}}} \times 100\%$$

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
