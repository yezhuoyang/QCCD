"""The logical error rate and the Compiler board, as workspace capabilities.

  _job_ler              job kind `ler`: compile a memory experiment onto a design with the
                        reference compiler, check the program runs it, sample its logical error
                        rate (qccd.qec.evaluate_memory).  The same measurement a memory board's
                        grade makes, on any design, any time.
  bench_reports         the Compiler board's local reports (`qccd bench run` writes them to
                        .qccd/bench/); agents read them, they never start one: running a compiler
                        runs the person's own code, which belongs in their shell, not in a
                        service an agent token can drive
  official_leaderboard  a board of the official server, fetched read-only (public data)
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

__all__ = ["QecMixin", "OFFICIAL_URL", "save_bench_report"]

OFFICIAL_URL = os.environ.get("QCCD_OFFICIAL_URL", "https://qccd.academy/official")
_RID = re.compile(r"^b_[0-9a-f]{12}$")


def _bench_dir(root: Path) -> Path:
    return Path(root) / ".qccd" / "bench"


def save_bench_report(root: Path, report: Mapping) -> str:
    """Keep a `qccd.compiler_report` in the workspace; returns its id."""
    import secrets
    d = _bench_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    rid = "b_" + secrets.token_hex(6)
    (d / f"{rid}.json").write_text(json.dumps({"id": rid, "saved_at": time.time(), **report}), encoding="utf-8")
    return rid


class QecMixin:

    # ------------------------------------------------------------------ the logical error rate

    def _check_ler_params(self, params: dict) -> None:
        from .core import WorkspaceError
        exp = params.get("experiment")
        if not exp or not isinstance(exp, str):
            raise WorkspaceError("bad_request", "name the memory experiment: a memory board's title, or one of "
                                 "the experiments in qccd_read_reference(section='noise')", status=422)
        shots = params.get("max_shots", 100000)
        if not isinstance(shots, int) or not 1000 <= shots <= 2_000_000:
            raise WorkspaceError("bad_request", "max_shots must be an integer from 1000 to 2000000", status=422)

    def _memory_experiment(self, ref: str):
        """A memory board (by title) or a named experiment -> (experiment, noise id, budget, label)."""
        from ..qec import EXPERIMENTS, MemoryExperiment, get_experiment
        from .core import WorkspaceError
        if ref in EXPERIMENTS:
            e = get_experiment(ref)
            return e, "qccd-noise@1", {}, e.name
        try:
            rel = self.board(ref)
        except Exception as exc:
            raise WorkspaceError("bad_request", f"{exc}; or one of the experiments: {', '.join(EXPERIMENTS)}",
                                 status=422) from None
        if not rel.manifest.get("qec"):
            raise WorkspaceError("bad_request", f"{rel.title} is not a memory board: its circuit has no detectors. "
                                 f"Memory experiments: {', '.join(EXPERIMENTS)}", status=422)
        q = rel.manifest["qec"]
        return (MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text()), q["noise"],
                {k: v for k, v in (q.get("ler") or {}).items() if k != "seed"}, rel.title)

    def _job_ler(self, jid, params, actor, origin_prompt_id, cancel) -> dict:
        from ..qec import CompileFailed, compile_with_qccdc, evaluate_memory
        from .core import WorkspaceError
        branch, rev = params["branch"], params["revision"]
        r = self.replayed(branch, rev)
        if not r.ok:
            raise WorkspaceError("design_invalid", "the design at that revision does not build", status=422)
        exp, noise, budget, label = self._memory_experiment(params["experiment"])
        budget = {**budget, "max_shots": int(params.get("max_shots", budget.get("max_shots", 100000)))}
        work = self.root / ".qccd" / "runs" / jid
        self._progress(jid, "compile", f"compiling {label} onto the design with the reference compiler")
        try:
            prog, cert = compile_with_qccdc(exp.qasm, r.arch_doc, work, cool=True,
                                            timeout=float(params.get("timeout_s", 900)))
        except CompileFailed as exc:
            return {"_status": "failed", "summary": f"the reference compiler could not compile {label} onto this "
                    f"design: {exc}", "refusal": str(exc), "log": exc.log[-2000:]}
        if cancel.is_set():
            return {"_status": "cancelled", "summary": "cancelled after compiling"}
        self._progress(jid, "ler", f"sampling the logical error rate of {label}")
        rep = evaluate_memory(prog, r.arch_doc, exp, noise_id=params.get("noise") or noise,
                              cert=json.loads(Path(cert).read_text(encoding="utf-8")), ler_budget=budget,
                              seed=params.get("seed"))
        from ..verify import verify
        from ..cost.models import corrected_model
        from ..ir.tsir import TSIR
        from ..arch import Architecture
        rules = verify(TSIR.load(prog), Architecture.from_json(r.arch_doc), corrected_model("qccdsim_jones"),
                       check_metrics=False).rules.summary()
        failed = sorted(rules.get("failed") or [])
        chk, est = rep.get("check") or {}, rep.get("ler")
        if not chk.get("ok"):
            summary = f"{label}: the compiled program does not run the experiment ({chk.get('reason')})"
        else:
            summary = (f"{label} on {r.arch_doc.get('name') or branch} r{rev}: LER {est['ler']:.2e} "
                       f"({est['per_round']:.2e} per round; {est['errors']} errors in {est['shots']} shots, "
                       f"{est['decoder']}), noise {rep['noise']['id']}")
            if failed:
                summary += f"; the program breaks rules {failed}, so a board would not accept it"
        top = sorted(((k, v) for k, v in (rep.get("budget") or {}).items() if k != "total"), key=lambda kv: -kv[1])[:3]
        return {"summary": summary, "ler_report": rep, "rules_failed": failed,
                "dominant_channels": [{"channel": k, "sum_p": v} for k, v in top],
                "program": {"path": str(prog), "compiler": "qccdc (reference) + cooling"}}

    # ------------------------------------------------------------------ the Compiler board (local)

    def bench_reports(self, limit: int = 50) -> list[dict]:
        d = _bench_dir(self.root)
        out = []
        for p in sorted(d.glob("b_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, min(limit, 500))]:
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            out.append({"id": r.get("id"), "saved_at": r.get("saved_at"), "compiler": r.get("compiler"),
                        "suite": r.get("suite"), "split": r.get("split"),
                        "metrics": {k: (v or {}).get("value") for k, v in (r.get("metrics") or {}).items()},
                        "counts": (r.get("summary") or {}).get("counts"),
                        "policy_met": not [x for x in (r.get("eligibility") or {}).get("reasons", [])
                                           if not x.startswith("profile")],
                        "label": "Local result - not published"})
        return out

    def bench_report(self, rid: str, part: str = "summary", offset: int = 0, limit: int = 50) -> dict:
        from .core import WorkspaceError
        if not _RID.match(str(rid)):
            raise WorkspaceError("not_found", "no such bench report", status=404)
        p = _bench_dir(self.root) / f"{rid}.json"
        if not p.exists():
            raise WorkspaceError("not_found", "no such bench report", status=404)
        r = json.loads(p.read_text(encoding="utf-8"))
        head = {"id": rid, "compiler": r.get("compiler"), "suite": r.get("suite"), "split": r.get("split"),
                "label": "Local result - not published"}
        if part == "pairs":
            pairs = r.get("pairs") or []
            lim = max(1, min(int(limit), 200))
            return {**head, "total": len(pairs), "offset": offset,
                    "pairs": [{k: x.get(k) for k in ("id", "status", "reason", "seconds", "speedup", "ler_ratio",
                                                     "reference")} | {"T_jones": (x.get("metrics") or {}).get("T_jones"),
                                                                      "ler": (x.get("ler") or {}).get("ler")}
                              for x in pairs[offset:offset + lim]]}
        return {**head, "summary": r.get("summary"), "metrics": r.get("metrics"), "eligibility": r.get("eligibility"),
                "build": r.get("build"), "evaluator": {k: (r.get("evaluator") or {}).get(k) for k in ("name", "version")}}

    # ------------------------------------------------------------------ the official boards (read-only)

    def official_leaderboard(self, board: str | None = None) -> dict:
        """The official server's boards, or one board's ranked rows: public data, fetched read-only."""
        from .core import WorkspaceError

        def get(path: str) -> Any:
            try:
                with urllib.request.urlopen(OFFICIAL_URL.rstrip("/") + path, timeout=20) as r:
                    return json.loads(r.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise WorkspaceError("official_unreachable", f"the official server did not answer ({exc})",
                                     status=502) from None
        tasks = get("/v1/tasks").get("tasks") or []
        if not board:
            return {"server": OFFICIAL_URL, "boards": [{"id": t["id"], "title": t.get("title"), "track": t.get("track"),
                                                        "rank_by": t.get("rank_by")} for t in tasks]}
        want = "".join(c for c in board.lower() if c.isalnum())
        hit = [t for t in tasks if want in "".join(c for c in (t.get("title") or t["id"]).lower() if c.isalnum())
               or t["id"] == board]
        if len(hit) != 1:
            raise WorkspaceError("bad_request", f"{board!r} matches {len(hit)} official boards: "
                                 + "; ".join(t.get("title") or t["id"] for t in tasks), status=422)
        lb = get(f"/v1/leaderboard/{urllib.request.quote(hit[0]['id'], safe='@')}")
        return {"server": OFFICIAL_URL, "board": hit[0].get("title"), "track": hit[0].get("track"), **lb}
