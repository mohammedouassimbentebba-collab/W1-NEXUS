# W1 Nexus Threat Model

Threat-model contract version: `1.0`.

This document describes trust boundaries, implemented mitigations, and explicit residual risk. It is not a claim that the system is immune to compromise.

## Trust assumptions

- The local OS account and host kernel are trusted unless execution is delegated to a stronger sandbox/VM boundary.
- Models, remote providers, plugins, MCP servers, collaboration peers, project files, deep links, and external benchmark inputs are treated as untrusted by default.
- Sensitive actions require explicit W1 policy/approval rather than being authorized solely by model output.

## Threat register

### T01 — Provider/model boundary
**Threat:** Untrusted model output attempts to trigger privileged or destructive actions.
**Mitigations:** Action Runtime policy evaluation; digest-bound one-time approvals; review/evidence gates; bounded tool schemas.
**Residual risk:** A user can still approve a harmful but correctly described action; policy review remains necessary.

### T02 — Credential boundary
**Threat:** API/OAuth secrets leak into logs, databases, prompts, plugins, or command history.
**Mitigations:** OS-native vault adapters; reference-only account/credential identifiers; redacted hash-chained audit; no cookie/session-token import.
**Residual risk:** Native vault security inherits the host account/OS trust model.

### T03 — Plugin boundary
**Threat:** Third-party plugin crashes W1 or abuses W1-granted capabilities.
**Mitigations:** copy-on-install integrity lock; explicit permission grants; subprocess host; timeout/output bounds; secret-like environment scrubbing.
**Residual risk:** The subprocess host is not an OS sandbox for hostile Python; hostile plugins require Secure Execution/VM/OCI isolation.

### T04 — Computer-use boundary
**Threat:** Stale UI state causes actions to land on the wrong element or sensitive text enters durable audit.
**Mitigations:** selector fingerprints; pre-action element verification; ephemeral one-shot input handles; governed screenshots/UI inspection.
**Residual risk:** Accessibility-lite adapters may miss semantic changes that full platform accessibility APIs would expose.

### T05 — Desktop integration boundary
**Threat:** Deep links or project files are used as command-injection/path-traversal vectors.
**Mitigations:** fixed route allowlist; command/argv/token parameter denial; relative project descriptors; path traversal rejection.
**Residual risk:** Future route expansion must preserve fail-closed parsing and avoid shell interpretation.

### T06 — Collaboration boundary
**Threat:** Remote member overwrites state, replays mutations, or tampers with audit history.
**Mitigations:** per-key base revisions; device sequences; mutation idempotency; explicit ConflictRecords; hash-chained audit; TLS required off-loopback.
**Residual risk:** Self-hosted operators remain responsible for host security, backups, certificates, and availability.

### T07 — Update/release boundary
**Threat:** Tampered or substituted desktop update is published or installed.
**Mitigations:** HTTPS-only update metadata; exact size/SHA-256 verification; native-signature boundary; release evidence hashes.
**Residual risk:** Authenticode/notarization must be verified on real target-platform release infrastructure.

### T08 — Memory/context boundary
**Threat:** Cross-project or over-broad context leaks private information between tasks/models.
**Mitigations:** least-privilege ContextGrant; project isolation; memory ACLs; provenance/conflict metadata.
**Residual risk:** Misconfigured grants can still expose data intentionally allowed by the operator.

### T09 — AI connection endpoint boundary
**Threat:** A provider credential is redirected to an attacker-controlled endpoint during model onboarding.
**Mitigations:** built-in provider hostname lock; custom providers separated from built-in identities; HTTPS required for remote custom endpoints; loopback-only plaintext custom/local endpoints; Credential Broker references instead of persisted raw secrets.
**Residual risk:** A user can intentionally configure and trust a custom HTTPS provider; that endpoint receives credentials explicitly assigned to that custom connection.

## Release-security boundary

W1 distinguishes integrity verification from platform-native signature verification. A SHA-256 match is not represented as Authenticode/notarization success. Public artifacts must preserve that distinction.

## External evaluation boundary

Internal executable scorecards measure implementation completeness. External benchmark scores are separate evidence and must be backed by an upstream-pinned run plus raw evidence digest.
