"""The Compiler leaderboard's benchmark: a suite of (circuit, device) pairs, the contract a
submitted compiler follows, and the harness that runs and grades it.  docs/PLAN-boards.md, Phase 3.

    contract   qccd.compiler@1: a directory with qccd-compiler.json; how it is invoked; exit 4 refuses
    suite      immutable suites (suites/<name>@<n>/): circuits, devices, pairs, the reference's results
    check      one output graded: native gates, qubit map, rules, semantics, metrics, LER
    harness    run_suite(suite, compiler) -> qccd.compiler_report (coverage, speedup, ler_ratio, wrong)
    baseline   the reference compiler (qccdc + cooling) as a contract compiler
    sandbox    the official grader's runner: each pair in a separate container with no secrets

    qccd bench run --compiler DIR        grade a compiler on the public pairs, locally
"""

from .contract import CompilerSpec, ContractError, load_compiler, starter
from .harness import REPORT_KIND, reference_compiler, run_suite
from .suite import Suite, SuiteError, find_suite, list_suites

__all__ = ["CompilerSpec", "ContractError", "load_compiler", "starter", "run_suite", "reference_compiler",
           "REPORT_KIND", "Suite", "SuiteError", "find_suite", "list_suites"]
