"""Upload an APPROVED local submission to an official server, and compare the two reports.

Credentials belong to this upload layer only: `QCCD_UPLOAD_TOKEN`, or
`~/.qccd/credentials.json` (`{"<server url>": "<token>"}`, readable by the user only).
They are never written into a workspace, a bundle, a URL or a log, and nothing on the
grading side ever sees them.

`upload_approved` goes through `Workspace.publish`, which re-hashes the frozen bundle and
checks it against the approval's digest and parameters before sending one byte.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

__all__ = ["upload_approved", "fetch_server_report", "compare_reports", "credential_for"]


def credential_for(server: str) -> str:
    tok = os.environ.get("QCCD_UPLOAD_TOKEN")
    if tok:
        return tok
    p = Path.home() / ".qccd" / "credentials.json"
    if p.exists():
        creds = json.loads(p.read_text(encoding="utf-8"))
        tok = creds.get(server.rstrip("/"))
        if tok:
            return tok
    raise SystemExit(f"no upload credential for {server}: set QCCD_UPLOAD_TOKEN or add it to ~/.qccd/credentials.json")


def _check_server(server: str) -> str:
    s = server.rstrip("/")
    if not (s.startswith("https://") or s.startswith("http://127.0.0.1:") or s.startswith("http://localhost:")):
        raise SystemExit("the server must be https:// (or a loopback http:// development server)")
    return s


def upload_approved(root: Path, approval_id: str, server: str) -> dict:
    from .app import Workspace
    server = _check_server(server)
    token = credential_for(server)
    ws = Workspace(root)
    try:
        def uploader(archive: bytes, meta: dict) -> dict:
            req = urllib.request.Request(f"{server}/v1/submissions", data=archive, method="POST")
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Content-Type", "application/zip")
            req.add_header("X-QCCD-Task", meta["task"])
            req.add_header("X-QCCD-Visibility", meta.get("visibility", "public"))
            if meta.get("display_name"):
                req.add_header("X-QCCD-Display-Name", meta["display_name"])
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise SystemExit(f"the server refused the upload ({exc.code}): {exc.read().decode('utf-8', 'replace')[:500]}")
        return ws.publish(approval_id, uploader)
    finally:
        ws.close()


def fetch_server_report(server: str, submission_id: str, token: str | None = None) -> dict:
    server = _check_server(server)
    req = urllib.request.Request(f"{server}/v1/submissions/{submission_id}/report")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def compare_reports(local: dict, server: dict) -> dict:
    """Local/server parity on the same (task, bundle, evaluator): eligibility, stage
    statuses and metrics within the release's numerical policy; timing and run ids are
    excluded as non-deterministic."""
    from .evaluator import metrics_agree
    same_inputs = (local.get("task") == server.get("task") and
                   (local.get("bundle") or {}).get("digest") == (server.get("bundle") or {}).get("digest") and
                   {k: (local.get("evaluator") or {}).get(k) for k in ("name", "version")} ==
                   {k: (server.get("evaluator") or {}).get(k) for k in ("name", "version")})
    stages_l = {s["id"]: s["status"] for s in local.get("stages", [])}
    stages_s = {s["id"]: s["status"] for s in server.get("stages", [])}
    metric_diff = metrics_agree(local.get("metrics") or {}, server.get("metrics") or {},
                                local.get("numerical_policy"))
    return {"same_inputs": same_inputs,
            "eligibility_agrees": (local.get("eligibility") or {}).get("eligible") ==
                                  (server.get("eligibility") or {}).get("eligible"),
            "stage_differences": {k: [stages_l.get(k), stages_s.get(k)] for k in set(stages_l) | set(stages_s)
                                  if stages_l.get(k) != stages_s.get(k)},
            "metric_differences": metric_diff,
            "toolchain_local": (local.get("evaluator") or {}).get("toolchain"),
            "toolchain_server": (server.get("evaluator") or {}).get("toolchain")}
