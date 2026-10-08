"""Unit tests for W1-CIP native accelerator bridge and fallback mechanisms."""

import unittest
from w1cip.native_accelerator import (
    TransactionalState,
    compute_state_hash,
    create_merkle_receipt,
    diagnose_native_status,
    verify_dag_acyclic,
)


class TestNativeAccelerator(unittest.TestCase):
    def test_state_hash_determinism(self):
        h1 = compute_state_hash("GoalContract: deterministic test")
        h2 = compute_state_hash("GoalContract: deterministic test")
        self.assertEqual(h1, h2)
        self.assertTrue(len(h1) >= 64)

    def test_merkle_receipt_generation(self):
        receipt = create_merkle_receipt(
            goal_data="GoalContract#1",
            evidence_data="Evidence#1: all tests passed",
        )
        self.assertIn("goal_hash", receipt)
        self.assertIn("evidence_hash", receipt)
        self.assertIn("combined_root", receipt)
        self.assertIn("backend", receipt)
        self.assertNotEqual(receipt["goal_hash"], receipt["evidence_hash"])

    def test_dag_acyclic_valid(self):
        edges = [
            ("GoalContract", "TeamPlan"),
            ("TeamPlan", "TaskA"),
            ("TeamPlan", "TaskB"),
            ("TaskA", "EvidenceA"),
            ("TaskB", "EvidenceB"),
            ("EvidenceA", "FinalResult"),
            ("EvidenceB", "FinalResult"),
        ]
        self.assertTrue(verify_dag_acyclic(edges))

    def test_dag_cyclic_rejection(self):
        edges = [
            ("TaskA", "TaskB"),
            ("TaskB", "TaskC"),
            ("TaskC", "TaskA"),  # Cycle!
        ]
        self.assertFalse(verify_dag_acyclic(edges))

    def test_transactional_state_rollback(self):
        state = TransactionalState({"system_state": "GREEN", "version": 1})
        snap_id = state.snapshot()

        # Apply speculative agent edit
        state.set("system_state", "RED_REGRESSION")
        state.set("version", 2)
        self.assertEqual(state.get("system_state"), "RED_REGRESSION")

        # Peer challenge rejects -> Atomic rollback O(1)
        success = state.rollback(snap_id)
        self.assertTrue(success)
        self.assertEqual(state.get("system_state"), "GREEN")
        self.assertEqual(state.get("version"), 1)

    def test_diagnostic_status(self):
        status = diagnose_native_status()
        self.assertIn("rust_core_available", status)
        self.assertIn("active_hash_backend", status)
        self.assertIn("active_dag_backend", status)


if __name__ == "__main__":
    unittest.main()
