# Step 46 — Third-party design inspirations

Research date: 2026-08-10.

This step uses architectural ideas only; no third-party source code was copied into W1 Nexus in this implementation.

## TanStack AI

Repository: https://github.com/TanStack/ai
License shown by repository: MIT.

Relevant ideas studied:
- provider-agnostic core;
- composable activities and provider adapters;
- streaming/tool/structured-output observability.

W1 application: `CapacityTelemetryAdapter` / registry is deliberately composable, while model execution remains behind W1 provider/model abstractions.

## Vercel AI SDK

Repository: https://github.com/vercel/ai
License shown by repository: Apache-2.0.

Relevant ideas studied:
- unified provider architecture;
- ability to switch provider implementations behind a common API;
- agent/tool/UI separation.

W1 application: Adaptive routing never embeds provider-specific selection logic into the collaboration topology. It emits a normal `ModelPortfolio` that the existing Model Access Fabric executes.

## NewsNow

Repository: https://github.com/ourongxing/newsnow
License shown by repository: MIT.

Relevant ideas studied:
- clean information-first UI;
- OAuth-oriented login UX;
- adaptive refresh intervals to reduce unnecessary upstream traffic;
- MCP exposure.

W1 application: capacity observations expose adaptive refresh recommendations rather than repeatedly polling quota endpoints at a fixed aggressive interval. The UI work remains local-first and the existing W1 MCP boundary is preserved.

## microsoft/AI

Repository: https://github.com/microsoft/AI
License shown by repository: MIT.

Relevant ideas studied:
- reference-architecture mindset;
- production/MLOps and best-practice organization;
- keeping implementation claims separate from architectural guidance.

W1 application: Step 46 documents explicit trust, provenance, budget, and unimplemented-live-telemetry boundaries instead of treating design intent as proof of live capability.

## Dependency decision

None of these repositories is added as a runtime dependency in Step 46. W1 remains Python/local-first and keeps its existing Apache-2.0 project license. Any future direct code reuse must be reviewed separately with attribution and license obligations before inclusion.
