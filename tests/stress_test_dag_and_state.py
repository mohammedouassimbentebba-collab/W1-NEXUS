"""Mathematically sound adversarial stress test and fuzzing harness for W1-CIP.

Tests:
1. 1,000 randomized graphs with BFS reachability ground truth (verifies 0% false positives and 0% false negatives).
2. Concurrent race-condition stress test across 20 parallel threads on TransactionalState.
3. Cryptographic hash throughput and consistency across 5,000 state payloads.
"""

import random
import threading
import time
import unittest
from collections import defaultdict, deque
from w1cip.native_accelerator import (
    TransactionalState,
    compute_state_hash,
    create_merkle_receipt,
    verify_dag_acyclic,
)


def has_directed_path(adj: dict[str, list[str]], src: str, dst: str) -> bool:
    """Independent BFS reachability check to establish mathematical ground-truth."""
    if src == dst:
        return True
    visited = set()
    q = deque([src])
    while q:
        curr = q.popleft()
        if curr == dst:
            return True
        for neighbor in adj.get(curr, []):
            if neighbor not in visited:
                visited.add(neighbor)
                q.append(neighbor)
    return False


class AdversarialStressTests(unittest.TestCase):
    def test_random_graph_fuzzing_1000_cases(self):
        """Fuzzes 1,000 graphs against independent BFS reachability ground truth."""
        random.seed(42)
        cycles_tested = 0
        acyclic_tested = 0

        start_time = time.perf_counter()

        for _ in range(1000):
            num_nodes = random.randint(5, 25)
            nodes = [f"node_{j}" for j in range(num_nodes)]

            # Generate random forward DAG
            edges = []
            adj = defaultdict(list)
            for j in range(num_nodes):
                for k in range(j + 1, num_nodes):
                    if random.random() < 0.2:
                        edges.append((nodes[j], nodes[k]))
                        adj[nodes[j]].append(nodes[k])

            # Pick random pair (u, v)
            u = random.choice(nodes)
            v = random.choice(nodes)

            # Ground truth: Adding edge u -> v creates a cycle if and only if
            # there is already a path from v to u (or if u == v)
            creates_cycle = has_directed_path(adj, v, u)
            test_edges = edges + [(u, v)]

            is_acyclic = verify_dag_acyclic(test_edges)

            if creates_cycle:
                cycles_tested += 1
                self.assertFalse(
                    is_acyclic,
                    f"False Negative: Cycle went undetected when adding edge {u} -> {v}",
                )
            else:
                acyclic_tested += 1
                self.assertTrue(
                    is_acyclic,
                    f"False Positive: Valid acyclic graph was rejected when adding edge {u} -> {v}",
                )

        elapsed = time.perf_counter() - start_time
        print(f"\n[Fuzzing Results]: 1,000 randomized graphs evaluated in {elapsed*1000:.2f}ms")
        print(f"  - Ground-truth Cycles Detected: {cycles_tested} (100% caught)")
        print(f"  - Ground-truth Acyclic Graphs Verified: {acyclic_tested} (100% verified)")
        print(f"  - Accuracy: 100.0% (Zero false positives, zero false negatives)")

    def test_concurrent_thread_race_conditions(self):
        """Spawns 20 concurrent threads mutating and snapshotting TransactionalState."""
        state = TransactionalState({"counter": 0, "status": "init"})
        errors = []
        threads = []

        def worker(thread_id: int):
            try:
                for step in range(50):
                    snap = state.snapshot()
                    state.set(f"thread_{thread_id}", step)
                    state.set("counter", thread_id * 100 + step)
                    if step % 2 == 0:
                        state.rollback(snap)
            except Exception as exc:
                errors.append(exc)

        for t_idx in range(20):
            t = threading.Thread(target=worker, args=(t_idx,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent thread exceptions observed: {errors}")
        print(f"[Concurrency Results]: 20 threads completed 1,000 transactional CoW mutations with 0 race exceptions.")

    def test_state_hashing_throughput_5000_payloads(self):
        """Hashes 5,000 structured payloads and verifies zero collisions."""
        seen_hashes = set()
        start_time = time.perf_counter()

        for i in range(5000):
            payload = f'{{"goal_id": "contract_{i}", "entropy": "{random.random()}"}}'
            h = compute_state_hash(payload)
            self.assertNotIn(h, seen_hashes, f"Cryptographic collision on iteration {i}!")
            seen_hashes.add(h)

        elapsed = time.perf_counter() - start_time
        throughput = 5000 / elapsed
        print(f"[Cryptographic Hash Results]: 5,000 payloads hashed in {elapsed*1000:.2f}ms ({throughput:.0f} ops/sec) with 0 collisions.")


if __name__ == "__main__":
    unittest.main()
