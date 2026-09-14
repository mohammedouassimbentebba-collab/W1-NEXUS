from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from w1cip.collaboration import (
    AccessTokenInvalid,
    CollaborationClient,
    CollaborationConflict,
    CollaborationDenied,
    CollaborationIntegrityError,
    CollaborationServerSettings,
    CollaborationService,
    CollaborationStore,
    CollaborationTransportError,
    InvitationInvalid,
    SyncMutation,
    create_collaboration_server,
    run_collaboration_benchmark,
)


class CollaborationFabricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "collaboration.sqlite3"
        self.store = CollaborationStore(self.db)
        self.team = self.store.create_team("W1 Team", owner_principal_id="alice", team_id="team-1")
        invitation, self.invite_token = self.store.create_invitation(
            self.team.team_id, role="member", created_by="alice", expires_in_hours=1
        )
        self.invitation_id = invitation.invitation_id
        self.store.accept_invitation(self.invite_token, principal_id="bob")
        self.project = self.store.create_project(self.team.team_id, "Pump", created_by="alice", project_id="project-1")
        self.store.grant_project_access(self.project.project_id, "bob", "editor", granted_by="alice")
        self.alice_device = self.store.register_replica(self.team.team_id, principal_id="alice", device_id="alice-device")
        self.bob_device = self.store.register_replica(self.team.team_id, principal_id="bob", device_id="bob-device")

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_invitation_is_one_time_and_plaintext_is_not_stored(self) -> None:
        self.assertNotIn(self.invite_token.encode(), self.db.read_bytes())
        with self.assertRaises(InvitationInvalid):
            self.store.accept_invitation(self.invite_token, principal_id="charlie")

    def test_access_token_is_hash_stored_and_revocable(self) -> None:
        record, token = self.store.issue_access_token(self.team.team_id, principal_id="bob", label="laptop")
        self.assertNotIn(token.encode(), self.db.read_bytes())
        self.assertEqual(self.store.authenticate_access_token(token).principal_id, "bob")
        self.store.revoke_access_token(record.token_id, actor="bob")
        with self.assertRaises(AccessTokenInvalid):
            self.store.authenticate_access_token(token)

    def test_access_token_expiry_is_enforced(self) -> None:
        record, token = self.store.issue_access_token(self.team.team_id, principal_id="bob", expires_in_hours=1)
        with self.store.connection:
            self.store.connection.execute("UPDATE access_tokens SET expires_at=? WHERE token_id=?", ("2000-01-01T00:00:00Z", record.token_id))
        with self.assertRaises(AccessTokenInvalid):
            self.store.authenticate_access_token(token)

    def test_acl_denies_unshared_member(self) -> None:
        invite, token = self.store.create_invitation(self.team.team_id, role="viewer", created_by="alice", expires_in_hours=1)
        self.store.accept_invitation(token, principal_id="eve")
        with self.assertRaises(CollaborationDenied):
            self.store.get_state(self.project.project_id, principal_id="eve")

    def test_sync_is_hash_chained_idempotent_and_conflict_explicit(self) -> None:
        first = SyncMutation(
            mutation_id="m1", project_id=self.project.project_id, device_id=self.bob_device.device_id,
            sequence=1, base_revision=0, key="design/head", operation="set", value=12,
            previous_event_hash=None, created_at="2026-08-08T00:00:00Z",
        ).normalized()
        accepted = self.store.apply_mutation(first, principal_id="bob")
        self.assertEqual(accepted["resulting_revision"], 1)
        self.assertTrue(self.store.apply_mutation(first, principal_id="bob")["idempotent"])

        stale = SyncMutation(
            mutation_id="m2", project_id=self.project.project_id, device_id=self.alice_device.device_id,
            sequence=1, base_revision=0, key="design/head", operation="set", value=14,
            previous_event_hash=None, created_at="2026-08-08T00:00:01Z",
        )
        with self.assertRaises(CollaborationConflict) as captured:
            self.store.apply_mutation(stale, principal_id="alice")
        conflict_id = captured.exception.conflict_id
        self.assertIsNotNone(conflict_id)
        recorded = self.store.list_conflicts(self.project.project_id, principal_id="alice")[0]
        self.assertEqual(recorded.client_value, 14)
        self.assertEqual(recorded.client_operation, "set")
        self.assertTrue(recorded.client_event_hash)
        self.assertEqual(self.store.get_replica(self.team.team_id, self.alice_device.device_id).last_sequence, 0)

        resolution = SyncMutation(
            mutation_id="m3", project_id=self.project.project_id, device_id=self.alice_device.device_id,
            sequence=1, base_revision=1, key="design/head", operation="set", value=13,
            previous_event_hash=None, created_at="2026-08-08T00:00:02Z",
        )
        result = self.store.resolve_conflict(conflict_id, resolution, principal_id="alice")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(self.store.get_state(self.project.project_id, principal_id="bob")["values"]["design/head"], 13)

    def test_sequence_and_previous_hash_fail_closed(self) -> None:
        wrong_sequence = SyncMutation(
            mutation_id="x1", project_id=self.project.project_id, device_id=self.bob_device.device_id,
            sequence=2, base_revision=0, key="a", operation="set", value=1,
        )
        with self.assertRaises(CollaborationIntegrityError):
            self.store.apply_mutation(wrong_sequence, principal_id="bob")

    def test_audit_chain_detects_tampering(self) -> None:
        valid = self.store.verify_audit(self.team.team_id)
        self.assertTrue(valid["valid"])
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE audit_events SET details_json='{}' WHERE team_id=? AND ordinal=(SELECT MIN(ordinal) FROM audit_events WHERE team_id=?)",
                (self.team.team_id, self.team.team_id),
            )
        self.assertFalse(self.store.verify_audit(self.team.team_id)["valid"])

    def test_non_loopback_requires_tls(self) -> None:
        with self.assertRaises(CollaborationTransportError):
            CollaborationServerSettings(host="0.0.0.0", port=8780).validate()
        with self.assertRaises(CollaborationTransportError):
            CollaborationClient("http://example.invalid:8780", "secret")

    def test_loopback_http_api_auth_and_sync(self) -> None:
        _record, token = self.store.issue_access_token(self.team.team_id, principal_id="bob")
        server = create_collaboration_server(
            CollaborationServerSettings(host="127.0.0.1", port=0), CollaborationService(self.store)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = CollaborationClient(f"http://127.0.0.1:{server.server_address[1]}", token)
            self.assertEqual(len(client.projects()), 1)
            mutation = SyncMutation(
                mutation_id="api1", project_id=self.project.project_id, device_id=self.bob_device.device_id,
                sequence=1, base_revision=0, key="api/value", operation="set", value={"ok": True},
                previous_event_hash=None, created_at="2026-08-08T00:00:00Z",
            )
            self.assertTrue(client.push(mutation)["accepted"])
            self.assertTrue(client.state(self.project.project_id)["values"]["api/value"]["ok"])
            self.assertEqual(len(client.pull(self.project.project_id)["events"]), 1)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


    def test_admin_cannot_demote_owner(self) -> None:
        invite, token = self.store.create_invitation(self.team.team_id, role="admin", created_by="alice", expires_in_hours=1)
        self.store.accept_invitation(token, principal_id="admin")
        with self.assertRaises(CollaborationDenied):
            self.store.set_member_role(self.team.team_id, "alice", "member", actor="admin")

    def test_member_removal_revokes_tokens_replicas_and_shares(self) -> None:
        record, token = self.store.issue_access_token(self.team.team_id, principal_id="bob")
        result = self.store.remove_member(self.team.team_id, "bob", actor="alice")
        self.assertTrue(result["removed"])
        self.assertEqual(result["revoked_tokens"], 1)
        with self.assertRaises(AccessTokenInvalid):
            self.store.authenticate_access_token(token)
        with self.assertRaises(CollaborationDenied):
            self.store.get_state(self.project.project_id, principal_id="bob")

    def test_sync_value_size_is_bounded(self) -> None:
        oversized = SyncMutation(
            mutation_id="large", project_id=self.project.project_id, device_id=self.bob_device.device_id,
            sequence=1, base_revision=0, key="blob", operation="set", value="x" * 1_000_100,
        )
        with self.assertRaises(CollaborationIntegrityError):
            oversized.normalized()

    def test_collaboration_client_is_on_stable_sdk_surface(self) -> None:
        from w1cip.sdk import CollaborationClient as SDKClient, SyncMutation as SDKMutation
        self.assertIs(SDKClient, CollaborationClient)
        self.assertIs(SDKMutation, SyncMutation)

    def test_benchmark(self) -> None:
        result = run_collaboration_benchmark()
        self.assertTrue(result["passed"])
        self.assertEqual(result["metrics"]["w1_owned_cloud_calls"], 0)


if __name__ == "__main__":
    unittest.main()
