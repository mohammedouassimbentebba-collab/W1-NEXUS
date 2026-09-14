from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_protocol_event_schema import ProtocolEventSchemaTests  # noqa: E402
from w1cip.session_store import (  # noqa: E402
    DuplicateMessageConflictError,
    SequenceConflictError,
    SessionStore,
    StoreValidationError,
)


def fixture_log() -> list[dict]:
    return deepcopy(ProtocolEventSchemaTests.minimal_replay_log())


def without_sequence(envelope: dict) -> dict:
    value = deepcopy(envelope)
    value.pop("sequence", None)
    return value


TRUSTED_RECORDERS = {("runtime", "runtime-local-001")}


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "w1cip.sqlite3"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_append_assigns_sequence_persists_and_reopens(self) -> None:
        first, second, *_ = fixture_log()
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            one = store.append(without_sequence(first), expected_last_sequence=0)
            two = store.append(without_sequence(second), expected_last_sequence=1)
            self.assertEqual((1, 2), (one.sequence, two.sequence))
            self.assertEqual(2, store.get_summary(first["session_id"]).last_sequence)
            latest = store.get_entity(first["session_id"], "role_assignment", "owner")
            self.assertIsNotNone(latest)
            self.assertEqual(2, latest["entity"]["entity_version"])
            self.assertTrue(store.verify_integrity(first["session_id"]).valid)

        with SessionStore(self.database) as reopened:
            self.assertEqual(2, len(reopened.get_events(first["session_id"])))
            self.assertTrue(reopened.verify_integrity(first["session_id"]).valid)

    def test_invalid_event_rolls_back_without_consuming_sequence(self) -> None:
        first, second, third, *_ = fixture_log()
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            store.append(without_sequence(first))
            invalid = without_sequence(second)
            invalid["causation_id"] = "evt-future"
            with self.assertRaises(StoreValidationError) as caught:
                store.append(invalid)
            self.assertIn("protocol_log_causation_not_prior", caught.exception.errors)
            self.assertEqual(1, store.get_summary(first["session_id"]).last_sequence)
            accepted = store.append(without_sequence(second))
            self.assertEqual(2, accepted.sequence)
            accepted_third = store.append(without_sequence(third))
            self.assertEqual(3, accepted_third.sequence)

    def test_idempotent_retry_and_conflicting_duplicate(self) -> None:
        first, *_ = fixture_log()
        candidate = without_sequence(first)
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            accepted = store.append(candidate)
            retry = store.append(candidate)
            self.assertTrue(retry.already_present)
            self.assertEqual(accepted.event_hash, retry.event_hash)
            self.assertEqual(1, store.get_summary(first["session_id"]).last_sequence)

            conflict = deepcopy(candidate)
            conflict["correlation_id"] = "different-correlation"
            with self.assertRaises(DuplicateMessageConflictError):
                store.append(conflict)

    def test_optimistic_sequence_precondition(self) -> None:
        first, second, *_ = fixture_log()
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            store.append(without_sequence(first), expected_last_sequence=0)
            with self.assertRaises(SequenceConflictError):
                store.append(without_sequence(second), expected_last_sequence=0)
            self.assertEqual(1, store.get_summary(first["session_id"]).last_sequence)

    def test_two_store_instances_serialize_concurrent_writers(self) -> None:
        first, second, third, fourth, *_ = fixture_log()
        session_id = first["session_id"]
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as seed:
            seed.append(without_sequence(first))
            seed.append(without_sequence(second))

        left = without_sequence(third)
        right = without_sequence(fourth)
        # Equal timestamps make either serialization order legal.
        for candidate in (left, right):
            candidate["occurred_at"] = "2026-08-05T15:00:03Z"
            candidate["received_at"] = "2026-08-05T15:00:03Z"
            candidate["recorded_at"] = "2026-08-05T15:00:03Z"

        barrier = threading.Barrier(2)
        results: list[int] = []
        failures: list[BaseException] = []

        def writer(candidate: dict) -> None:
            try:
                with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS, busy_timeout_ms=10_000) as store:
                    barrier.wait(timeout=5)
                    results.append(store.append(candidate).sequence)
            except BaseException as exc:  # test captures worker failures
                failures.append(exc)

        threads = [
            threading.Thread(target=writer, args=(left,)),
            threading.Thread(target=writer, args=(right,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual([], failures)
        self.assertEqual([3, 4], sorted(results))
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            self.assertEqual(4, store.get_summary(session_id).last_sequence)
            self.assertTrue(store.verify_integrity(session_id).valid)

    def test_compensation_projection_and_projection_recovery(self) -> None:
        log = fixture_log()
        session_id = log[0]["session_id"]
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            store.append_many(without_sequence(item) for item in log)
            self.assertEqual([], store.get_active_protocol_effects(session_id))
            self.assertTrue(store.verify_integrity(session_id).valid)

            # Projection tables are caches and may be reconstructed from events.
            store.connection.execute("DELETE FROM latest_entities WHERE session_id = ?", (session_id,))
            broken = store.verify_integrity(session_id)
            self.assertFalse(broken.valid)
            self.assertIn("session_store_latest_projection_mismatch", broken.errors)

            rebuilt = store.rebuild_projections(session_id)
            self.assertEqual(6, rebuilt.last_sequence)
            self.assertTrue(store.verify_integrity(session_id).valid)
            self.assertIsNotNone(store.get_entity(session_id, "artifact", "subject"))

    def test_event_rows_are_append_only(self) -> None:
        first, *_ = fixture_log()
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            store.append(without_sequence(first))
            with self.assertRaises(sqlite3.IntegrityError):
                store.connection.execute(
                    "DELETE FROM events WHERE session_id = ?", (first["session_id"],)
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.connection.execute(
                    "UPDATE events SET message_type = 'changed' WHERE session_id = ?",
                    (first["session_id"],),
                )
            self.assertEqual(1, store.get_summary(first["session_id"]).last_sequence)

    def test_hash_chain_detects_low_level_tampering(self) -> None:
        first, *_ = fixture_log()
        session_id = first["session_id"]
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            store.append(without_sequence(first))
            store.connection.execute("DROP TRIGGER events_no_update")
            store.connection.execute(
                "UPDATE events SET event_hash = ? WHERE session_id = ? AND sequence = 1",
                ("f" * 64, session_id),
            )
            report = store.verify_integrity(session_id)
            self.assertFalse(report.valid)
            self.assertIn("session_store_event_hash_mismatch", report.errors)
            self.assertIn("session_store_head_hash_mismatch", report.errors)

    def test_new_store_requires_explicit_trusted_recorder_root(self) -> None:
        first, *_ = fixture_log()
        with SessionStore(self.database) as store:
            with self.assertRaises(StoreValidationError) as caught:
                store.append(without_sequence(first))
            self.assertIn(
                "session_store_trusted_recorder_not_configured",
                caught.exception.errors,
            )

    def test_structural_validation_rejects_unknown_property(self) -> None:
        first, *_ = fixture_log()
        invalid = without_sequence(first)
        invalid["unexpected"] = True
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            with self.assertRaises(StoreValidationError) as caught:
                store.append(invalid)
            self.assertTrue(
                any(code.startswith("protocol_envelope_schema_invalid") for code in caught.exception.errors)
            )
            self.assertEqual([], store.list_sessions())

    def test_json_export_is_exact_canonical_payload_not_python_repr(self) -> None:
        first, *_ = fixture_log()
        with SessionStore(self.database, trusted_recorders=TRUSTED_RECORDERS) as store:
            result = store.append(without_sequence(first))
            row = store.connection.execute(
                "SELECT envelope_json FROM events WHERE session_id = ? AND sequence = 1",
                (first["session_id"],),
            ).fetchone()
            persisted = json.loads(row["envelope_json"])
            self.assertEqual(result.envelope, persisted)
            self.assertIn('"protocol":"w1-cip"', row["envelope_json"])


if __name__ == "__main__":
    unittest.main()
