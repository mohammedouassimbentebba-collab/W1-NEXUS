#!/usr/bin/env sh
set -eu
WORKSPACE="${1:-/tmp/w1-nexus-reference-demo}"
python -m w1cip --workspace "$WORKSPACE" init
python -m w1cip --workspace "$WORKSPACE" demo --reset
python -m w1cip --workspace "$WORKSPACE" benchmark
python -m w1cip --workspace "$WORKSPACE" sessions verify session-w1-reference-demo-001
