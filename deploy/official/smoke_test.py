"""Operator check for a deployed official service: one PRIVATE submission, end to end.

    python deploy/official/smoke_test.py https://qccd.academy/official --token-file PATH

Builds and grades a local submission of the starter task with this machine's toolchain,
uploads it as PRIVATE (it never appears on the leaderboard), waits for the server's own
grade, and compares the two reports.  The token is read from a file and never printed.
Exit status 0 only when the server graded it eligible, the reports agree, and the public
leaderboard does not list it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.bundle import archive_bundle, read_bundle  # noqa: E402
from qccd.workspace.publish import compare_reports  # noqa: E402

HUMAN = {"kind": "human", "id": "cli"}
TERMINAL = ("eligible", "ineligible", "grading_failed")


def _req(method, url, token=None, data=None, headers=None):
    r = urllib.request.Request(url, data=data, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:500]}


def local_submission(tmp: Path) -> dict:
    ws = Workspace.init(tmp / "ws", "ghz4@1")
    try:
        def wait(jid):
            while ws.job(jid)["status"] not in ("succeeded", "failed", "internal_error", "cancelled", "timeout"):
                time.sleep(0.3)
            return ws.job(jid)
        j = wait(ws.start_job(HUMAN, "compile", {})["job_id"])
        if j["status"] != "succeeded":
            raise SystemExit(f"local compile {j['status']}: {j.get('error')}")
        ws.apply_change_set({"expected_revision": 0, "request_id": "smoke", "mode": "apply",
                             "operations": [j["result"]["adopt_with"]]}, HUMAN)
        s = ws.submit_local(HUMAN, profile="reference")
        wait(s["job_id"])
        sub = ws.submission(s["submission_id"])
        b = read_bundle(ws.root / sub["snapshot"]["dir"] / "bundle")
        return {"archive": archive_bundle(b), "report": sub["report"], "digest": b.digest,
                "eligible": sub["report"]["eligibility"]["eligible"]}
    finally:
        ws.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("server")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args()
    server = a.server.rstrip("/")
    token = Path(a.token_file).read_text(encoding="utf-8").strip()
    out: dict = {"server": server}
    st, h = _req("GET", f"{server}/v1/health")
    out["health"] = {"status": st, **(h if isinstance(h, dict) else {})}
    with tempfile.TemporaryDirectory(prefix="qccd-smoke-") as t:
        loc = local_submission(Path(t))
    out["local"] = {"digest": loc["digest"], "eligible": loc["eligible"]}
    st, sub = _req("POST", f"{server}/v1/submissions", token, loc["archive"],
                   {"Content-Type": "application/zip", "X-QCCD-Task": "ghz4@1", "X-QCCD-Visibility": "private",
                    "X-QCCD-Display-Name": "deploy smoke test"})
    out["upload"] = {"http": st, **{k: sub.get(k) for k in ("submission_id", "status", "visibility", "error")}}
    sid = sub.get("submission_id")
    ok = False
    if sid:
        t0 = time.time()
        while True:
            st, cur = _req("GET", f"{server}/v1/submissions/{sid}", token)
            if cur.get("status") in TERMINAL or time.time() - t0 > a.timeout:
                break
            time.sleep(3)
        out["server_status"] = cur.get("status")
        st, rep = _req("GET", f"{server}/v1/submissions/{sid}/report", token)
        if st == 200:
            server_report = rep.get("report", rep)
            out["server_eligible"] = server_report.get("eligibility", {}).get("eligible")
            out["server_stages"] = {x["id"]: x["status"] for x in server_report.get("stages", [])}
            out["parity"] = compare_reports(loc["report"], server_report)
        else:
            out["report_error"] = {"status": st, **rep}
        st, board = _req("GET", f"{server}/v1/leaderboard/ghz4@1")
        listed = json.dumps(board).find(sid) >= 0
        out["leaderboard"] = {"status": st, "lists_private_submission": listed}
        par = out.get("parity") or {}
        ok = (out.get("server_eligible") is True and par.get("eligibility_agrees") is True
              and not par.get("stage_differences") and not listed)
    out["ok"] = ok
    print(json.dumps(out, indent=1, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
