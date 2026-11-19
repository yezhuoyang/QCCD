"""Grade device files on a leaderboard with the reference evaluator, as `qccd submit --local` does.

Each device is imported into a throwaway workspace, the board's circuit is compiled for it by the
reference compiler (qccdc, compiler "auto"), and the result is frozen and graded under the reference
profile. Run it from the root of a checkout of yezhuoyang/QCCD's `compiler` branch at 84e8c5d with
this folder copied in, with compiler c774b87b677f and qcheck e8acd6640fd3 (README.md has the steps):

    python Codesign/hillclimb/grade.py rep9@1 Codesign/hillclimb/rep9/designs/*.arch.json

Prints the toolchain it grades with, then one line per device: the board's ranked metric,
eligibility, and the DACs, traps and junctions as the grader counts them.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qccd  # noqa: E402
from qccd.arch.device import Architecture  # noqa: E402
from qccd.workspace import evaluator_identity  # noqa: E402
from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.design import base_from_arch  # noqa: E402
from qccd.workspace.evaluator import Toolchain  # noqa: E402

USER = {"kind": "human", "id": "cli"}
DONE = ("succeeded", "failed", "cancelled", "timeout", "internal_error")


def toolchain() -> str:
    """The QCCD source Python imported (its commit when it is a git checkout) with the evaluator's
    digests of it, and the compiler and checker the evaluator resolves: QCCD_QCCDC / QCCD_QCHECK if
    set, else a build in the checkout, else the ones `qccd toolchain install` installed."""
    src = Path(qccd.__file__).resolve().parents[1]
    commit = "unknown"
    if (src / ".git").exists():
        try:
            commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True,
                                    text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    tc = Toolchain.discover()
    ids = tc.identity()
    lines = [f"QCCD    {src} at commit {commit}",
             f"        evaluator source {evaluator_identity()['source_digest']}, bridge {ids['bridge']}"]
    for tool, path in (("qccdc", tc.qccdc), ("qcheck", tc.qcheck)):
        lines.append(f"{tool:<7} {path or 'not found'} {ids[tool] or ''}".rstrip())
    return "\n".join(lines)


def wait(ws: Workspace, job_id: str, timeout: float = 3600.0) -> dict:
    t0 = time.time()
    while (job := ws.job(job_id))["status"] not in DONE:
        if time.time() - t0 > timeout:
            raise SystemExit(f"timed out waiting for {job_id}")
        time.sleep(0.3)
    return job


def grade(board: str, path: Path) -> str:
    arch = Architecture.from_json(json.loads(path.read_text()))
    base = base_from_arch(arch)
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace.init(Path(tmp) / "ws", board)
        try:
            sync = {"type": "studio_sync", "base": {"geom": base.geom, "seed": base.seed, "post": base.post}}
            change = ws.apply_change_set({"expected_revision": ws.head().revision, "request_id": "import",
                                          "mode": "apply", "summary": "import", "operations": [sync]}, USER)
            if change.get("status") != "committed":
                return f"import refused: {change}"
            job = wait(ws, ws.submit_design(USER, board=board, profile="reference")["job_id"])
            result = job.get("result") or {}
            if job["status"] != "succeeded":
                # the summary says why: a compiler fault's log is that of a compile that succeeded
                why = result.get("summary") or job.get("error")
                return f"submit {job['status']}: {why}\n{str(result.get('log') or '')[-600:]}".rstrip()
            graded = wait(ws, result["grading_job"])
            if graded["status"] != "succeeded":
                return f"grading {graded['status']}: {graded.get('error')}"
            report = ws.submission(result["submission_id"])["report"] or {}
        finally:
            ws.close()
    metric = report.get("rank_by")
    value = ((report.get("metrics") or {}).get(metric) or {}).get("value")
    eligibility = report.get("eligibility") or {}
    # the counts the device stage judged the budget with: DACs under the physics' wiring floors (#15)
    device = next((s.get("detail") or {} for s in report.get("stages") or [] if s.get("id") == "device"), {})
    traps = sum(1 for n in arch.device.nodes.values() if n.kind == "site")  # as hardware_report counts traps
    verdict = "eligible" if eligibility.get("eligible") else f"NOT eligible {eligibility.get('reasons')}"
    return (f"{metric} {value}  {verdict}  dacs {device.get('dacs')} traps {traps} "
            f"junctions {device.get('junctions')}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    print(toolchain(), flush=True)
    for device in sys.argv[2:]:
        print(f"{device}: {grade(sys.argv[1], Path(device))}", flush=True)
