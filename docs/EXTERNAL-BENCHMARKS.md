# External Benchmark Policy

W1 Nexus does not convert internal probes into competitor scores. External benchmark results are admitted only when an upstream benchmark is pinned and raw evidence is hashed into an immutable workspace result record.

## Registered benchmark families

| ID | Scope | W1 applicability | Status in dev41 |
|---|---|---|---|
| `mcp-conformance-2026-07-28` | MCP protocol interoperability | Directly applicable to W1 MCP client/server | Adapter/direct run pending |
| `swe-bench-verified` | Real GitHub issue resolution | W1 coding-agent + exact model portfolio | Adapter required |
| `claw-swe-bench-lite` | Agent-harness coding evaluation | Strong harness comparison when model/budget are held fixed | Adapter required |
| `bfcl-v4` | Function/tool calling | Combined model + W1 tool stack | Adapter required |
| `osworld-v2-2026-06-24` | Long-horizon computer use | W1 Computer Use + exact vision/reasoning model | Official environment required |

## Fair-comparison rules

1. Pin upstream repository commit/release and dataset/environment release.
2. Record exact model/provider, model version, W1 adapter, budgets, retries, parallelism, and allowed tools.
3. Keep the underlying model fixed when claiming a harness-level comparison.
4. Never attribute a model's raw function-calling or reasoning score to W1 alone.
5. Preserve failed, timed-out, and safety-blocked tasks in the denominator according to the upstream evaluator.
6. Keep raw logs/results and persist their SHA-256 alongside the reported metrics.
7. Mark unexecuted benchmark families `NOT_RUN`; never infer a score from internal tests.

Use `w1 release catalog` to inspect the machine-readable catalog and `w1 release record` to admit an externally executed result.
