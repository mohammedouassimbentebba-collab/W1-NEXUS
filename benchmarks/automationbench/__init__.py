"""AutomationBench Suite: 6 Enterprise Workflow Domains."""

from __future__ import annotations

from typing import Sequence, Tuple
from ..base import BenchmarkSuite
from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class AutomationBenchSuite(BenchmarkSuite):
    name = "AutomationBench"
    version = "1.2.0"
    release_tag = "automationbench-v1.2-public"
    is_public_local = True

    DOMAINS = ("Sales", "Marketing", "Operations", "Support", "Finance", "HR")

    def get_smoke_task(self) -> BenchmarkTaskSpec:
        return BenchmarkTaskSpec(
            task_id="auto-smoke-01",
            benchmark=self.name,
            category="Sales",
            prompt="Identify the status of account ACME-991 from the CRM record: 'ACME-991: Renewal closed on 2026-08-01, Tier: Enterprise Enterprise, Status: Active'. Answer with just the Status value.",
            expected_answer="Active",
            timeout_seconds=30.0,
        )

    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        return [
            self.get_smoke_task(),
            BenchmarkTaskSpec(
                task_id="auto-sales-01",
                benchmark=self.name,
                category="Sales",
                prompt="Given lead score 88, budget $50,000, and timeline 30 days, determine if lead qualifies for Enterprise SDR outreach (Threshold: Score >= 80 and Budget >= $40k). Answer with QUALIFIED or DISQUALIFIED.",
                expected_answer="QUALIFIED",
            ),
            BenchmarkTaskSpec(
                task_id="auto-mktg-01",
                benchmark=self.name,
                category="Marketing",
                prompt="Calculate Click-Through Rate (CTR) percentage given 12,500 impressions and 375 clicks. Answer with the exact number percentage.",
                expected_answer="3.0",
            ),
            BenchmarkTaskSpec(
                task_id="auto-ops-01",
                benchmark=self.name,
                category="Operations",
                prompt="Inventory count is 420 units. Reorder point is 500. Order batch size is 250. How many units should be ordered? Answer with the number.",
                expected_answer="250",
            ),
            BenchmarkTaskSpec(
                task_id="auto-supp-01",
                benchmark=self.name,
                category="Support",
                prompt="Customer ticket states: 'Payment failed with error code ERR_INSUFFICIENT_FUNDS'. Classify priority (P1, P2, P3, or P4) where billing failures affecting active renewals are P2.",
                expected_answer="P2",
            ),
            BenchmarkTaskSpec(
                task_id="auto-fin-01",
                benchmark=self.name,
                category="Finance",
                prompt="Calculate Net Operating Income (NOI) given Revenue $1,200,000 and Operating Expenses $850,000. Answer with the dollar amount.",
                expected_answer="350000",
            ),
            BenchmarkTaskSpec(
                task_id="auto-hr-01",
                benchmark=self.name,
                category="HR",
                prompt="Employee has 18 days PTO accrued, took 5 days in Q1 and 4 days in Q2. How many days remain? Answer with the number.",
                expected_answer="9",
            ),
        ]
