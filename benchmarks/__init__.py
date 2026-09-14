"""NEXUS Benchmarks Suite Registry."""

from __future__ import annotations

from .aa_intelligence_index import AAIntelligenceIndexSuite
from .automationbench import AutomationBenchSuite
from .base import BenchmarkSuite
from .exploitbench import ExploitBenchSuite
from .frontiermath_tier4 import FrontierMathTier4Suite
from .mrcr_v2 import MRCRv2Suite
from .osworld2 import OSWorld2Suite
from .srebench import SREBenchSuite
from .terminalbench4 import TerminalBench4Suite
from .terminalbench_science import TerminalBenchScienceSuite

__all__ = [
    "AAIntelligenceIndexSuite",
    "AutomationBenchSuite",
    "BenchmarkSuite",
    "ExploitBenchSuite",
    "FrontierMathTier4Suite",
    "MRCRv2Suite",
    "OSWorld2Suite",
    "SREBenchSuite",
    "TerminalBench4Suite",
    "TerminalBenchScienceSuite",
    "get_all_suites",
]


def get_all_suites() -> list[BenchmarkSuite]:
    return [
        AutomationBenchSuite(),
        OSWorld2Suite(),
        TerminalBench4Suite(),
        TerminalBenchScienceSuite(),
        FrontierMathTier4Suite(),
        ExploitBenchSuite(),
        SREBenchSuite(),
        MRCRv2Suite(),
        AAIntelligenceIndexSuite(),
    ]
