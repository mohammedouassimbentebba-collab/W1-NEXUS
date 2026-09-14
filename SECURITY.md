# Security Policy

W1 Nexus is a local-first, provider-neutral orchestration runtime. Security reports should include the affected version, component, reproducible steps, impact, and whether the issue requires an already-authorized local user.

## Supported development line

The current development line is `0.1.0.dev41`. Development builds are not a promise of production hardening or support lifetime.

## Security boundaries

- Model/provider output is untrusted and does not itself authorize sensitive actions.
- OAuth/API secrets belong in the OS credential vault and are referenced indirectly.
- Managed plugins are integrity-locked and subprocess-hosted, but the subprocess boundary is **not** an OS sandbox for hostile Python.
- Non-loopback Collaboration serving requires TLS.
- Deep links and `.w1nexus` descriptors are data routes, never shell commands.
- Update SHA-256 verification and native code-signature verification are distinct gates.

## Reporting

Do not place real credentials, private project data, or access tokens in a public report. Provide a minimal synthetic reproduction where possible. A future public repository should publish a dedicated private security-reporting channel before accepting production users.

## Non-claims

The project does not claim formal verification, universal sandbox escape resistance, live certification on every supported OS, or that internal completeness percentages are competitive intelligence benchmarks.

## Step 49 — Intelligence Search outbound boundary

The W1 Intelligence Search Engine is local-first and does not use a W1-owned backend. It performs explicit, on-demand HTTPS GET requests from the user's local W1 process to a hard-coded allowlist of public catalog/search hosts (`openrouter.ai`, `api.github.com`, `huggingface.co`). Results are cached as public metadata in `.w1nexus/intelligence-search.sqlite3`.

Search discovery never imports browser cookies, never stores provider/API credentials in the search cache, never treats GitHub popularity as trust, and never auto-routes third-party candidates. A candidate must still pass the normal W1 connection, entitlement, terms/provenance, capacity, and quality gates before it can participate in a team.

## Step 50 — Verified intelligence activation boundary

- Intelligence Search candidates are inert metadata until an explicit local activation action.
- OpenRouter activation requires an enabled OpenRouter connection and an exact match against that connection's persisted `discovered_models`; the activation step itself performs no hidden network call.
- GitHub repositories are never treated as executable model capacity, and Hugging Face repositories are never treated as installed local capacity or hosted entitlement.
- Activation records contain references and evidence only; raw provider credentials are never written to the intelligence-search/activation database.
- Activated OpenRouter free candidates are recorded as `third_party_free`, `quota_state=unknown`, and `entitlement_verified=false`; the default Adaptive Capacity Router policy continues to reject third-party-free routing unless the user opts in.
- Review and activation events are appended to a local SHA-256 hash-chained audit ledger.
