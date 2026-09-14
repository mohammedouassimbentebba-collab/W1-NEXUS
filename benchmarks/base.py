"""Base specification for all isolated benchmark adapters."""

from __future__ import annotations

import abc
from typing import Any, Callable, Optional, Sequence, Tuple

from w1cip.benchmark_core.runner import BenchmarkTaskSpec


class BenchmarkSuite(abc.ABC):
    """Abstract interface for isolated benchmark suite implementations."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def version(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def release_tag(self) -> str:
        pass

    @property
    def is_public_local(self) -> bool:
        """Indicates if this is local public-set evaluation as opposed to official private held-out set."""
        return True

    @abc.abstractmethod
    def get_tasks(self) -> Sequence[BenchmarkTaskSpec]:
        """Returns the full task suite for this benchmark."""
        pass

    @abc.abstractmethod
    def get_smoke_task(self) -> BenchmarkTaskSpec:
        """Returns 1 tiny, fast smoke task for preflight qualification."""
        pass

    def validate_output(
        self,
        task_id: str,
        candidate_output: str,
        expected_answer: Optional[str] = None,
    ) -> Tuple[bool, float, Optional[str]]:
        """Evaluates model candidate output against task expected answer."""
        if expected_answer is None:
            return (bool(candidate_output.strip()), 1.0 if candidate_output.strip() else 0.0, None)

        clean_cand = candidate_output.strip().lower()
        clean_exp = str(expected_answer).strip().lower()
        if clean_exp in clean_cand:
            return True, 1.0, None
        return False, 0.0, f"Expected '{clean_exp}' in answer"
