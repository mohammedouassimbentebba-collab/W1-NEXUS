from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from w1cip.secure_execution import (
    NetworkPolicy,
    SandboxLimits,
    SandboxProfile,
    SandboxRequest,
    SecureExecutionFabric,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="w1-secure-demo-") as td:
        root = Path(td).resolve()
        fabric = SecureExecutionFabric(root)
        try:
            profile = SandboxProfile(
                profile_id="demo-local",
                backend="local",
                root_read_only=False,
                require_hard_isolation=False,
                network=NetworkPolicy(mode="inherit"),
                limits=SandboxLimits(
                    wall_seconds=10,
                    cpu_seconds=5,
                    memory_mb=1024,
                    pids=128,
                ),
                allowed_commands=(Path(sys.executable).name,),
                artifact_globs=("artifacts/**",),
            )
            fabric.save_profile(profile)
            request = SandboxRequest(
                execution_id="demo-secure-execution",
                profile_id=profile.profile_id,
                argv=(
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('artifacts').mkdir(); Path('artifacts/report.txt').write_text('verified', encoding='utf-8'); print('completed')",
                ),
            )
            result = fabric.execute(request)
            assert result.status == "completed", result.stderr
            assert result.artifacts and result.artifacts[0].sha256
            assert fabric.verify_attestation(result.attestation)
            repeated = fabric.execute(request)
            assert repeated.attestation.attestation_id == result.attestation.attestation_id
            print(
                {
                    "status": result.status,
                    "backend": result.backend,
                    "artifact_count": len(result.artifacts),
                    "attestation_valid": True,
                    "idempotent": True,
                }
            )
            return 0
        finally:
            fabric.close()


if __name__ == "__main__":
    raise SystemExit(main())
