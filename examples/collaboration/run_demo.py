from __future__ import annotations

import json
import tempfile
from pathlib import Path

from w1cip.collaboration import CollaborationConflict, CollaborationStore, SyncMutation

with tempfile.TemporaryDirectory(prefix="w1-collab-example-") as directory:
    with CollaborationStore(Path(directory) / "collaboration.sqlite3") as store:
        team = store.create_team("Example Team", owner_principal_id="alice")
        _invite, invite_token = store.create_invitation(team.team_id, role="member", created_by="alice", expires_in_hours=1)
        store.accept_invitation(invite_token, principal_id="bob")
        project = store.create_project(team.team_id, "W1 Pump", created_by="alice")
        store.grant_project_access(project.project_id, "bob", "editor", granted_by="alice")
        alice = store.register_replica(team.team_id, principal_id="alice", device_id="alice-device")
        bob = store.register_replica(team.team_id, principal_id="bob", device_id="bob-device")

        first = SyncMutation("m1", project.project_id, bob.device_id, 1, 0, "pump/target_head_m", "set", 12)
        store.apply_mutation(first, principal_id="bob")

        stale = SyncMutation("m2", project.project_id, alice.device_id, 1, 0, "pump/target_head_m", "set", 14)
        try:
            store.apply_mutation(stale, principal_id="alice")
        except CollaborationConflict as exc:
            conflict_id = exc.conflict_id
        else:
            raise RuntimeError("Expected a conflict")

        resolution = SyncMutation("m3", project.project_id, alice.device_id, 1, 1, "pump/target_head_m", "set", 13)
        store.resolve_conflict(conflict_id, resolution, principal_id="alice")
        print(json.dumps({
            "state": store.get_state(project.project_id, principal_id="bob"),
            "audit": store.verify_audit(team.team_id),
        }, ensure_ascii=False, indent=2))
