from __future__ import annotations

import json
import tempfile
from pathlib import Path

from w1cip.scientific import ScientificLab, ScientificPrincipal

ROOT = Path(__file__).resolve().parent


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        actor = ScientificPrincipal("human", "researcher-one")
        reviewer = ScientificPrincipal("human", "reviewer-one")
        with ScientificLab(workspace / ".w1nexus" / "science.sqlite3", workspace_root=workspace) as lab:
            plan = json.loads((ROOT / "pump-current-study.json").read_text(encoding="utf-8"))
            rows = json.loads((ROOT / "observations.json").read_text(encoding="utf-8"))
            lab.create_study(plan, actor=actor)
            lab.preregister(plan["study_id"], actor=actor)
            lab.add_observations(plan["study_id"], rows, actor=actor)
            lab.run_analysis(plan["study_id"], "current-difference", actor=ScientificPrincipal("agent", "statistician-one"))
            evaluation = lab.evaluate(plan["study_id"], actor=ScientificPrincipal("agent", "statistician-one"))
            lab.review(plan["study_id"], reviewer=reviewer, outcome="approved", rationale="Independent fixture review.")
            package = lab.build_reproducibility_package(plan["study_id"], workspace / "study.zip", actor=reviewer)
            print(json.dumps({"evaluation": evaluation, "package": package, "integrity": lab.verify()}, indent=2))


if __name__ == "__main__":
    main()
