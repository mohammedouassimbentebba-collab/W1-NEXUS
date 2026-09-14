# W1 Nexus Release Policy

Policy version: `1.0`.

## Development release gates

A dev release requires:

- isolated test-module regression with no assertion failures;
- deterministic hardening benchmark PASS;
- static schema/JSON/Python/JavaScript/document-link validation;
- wheel build and install outside the source tree;
- executable benchmarks and `w1 evaluate` from the installed wheel;
- SHA-256 hashes for delivered artifacts;
- explicit limitations for platform capabilities not live-tested on the build host.

## Public release gate

The source-release identity now intentionally uses **Apache License 2.0**, with `NOTICE` and `BRAND-POLICY.md`. `w1 release gate --public` validates that source-release identity and hardening state.

Publishing native Windows/macOS/Linux installers remains a separate stronger boundary: production icon assets, a real target-platform build, and platform-native signing/notarization evidence are still required before those native artifacts are represented as public signed releases.

## Reproducibility

`w1 release manifest` hashes the package-relevant source tree. Deterministic source manifests are required; byte-identical wheels across heterogeneous builders are a later reproducible-build target and must not be claimed unless independently reproduced.

## External benchmark claims

External benchmark scores are separate from the internal completeness scorecard. Claims require an immutable W1 result record plus SHA-256 of the raw upstream evidence.
