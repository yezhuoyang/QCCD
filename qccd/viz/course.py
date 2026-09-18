"""The course's prepared cases: the devices and programmes Python judges when a page is built.

Five of the twenty-seven rules are not re-implemented in the browser (R4d, R7b, R9, R10,
R16 -- see `docs/TUTORIAL_PLAN.md` §6).  The rule lessons that teach them cannot show a
verdict the page computed, so they show Python's: each case below is a device the studio
can rebuild from its generator call and a programme as the same `{method, args, kwargs}`
records the Write pane produces.  `render.py` runs every case through `verify()` under the
page's own cost model and ships the cases and the verdicts together as
`D.tutorial_cases` / `D.tutorial_verdicts`; the lesson loads the case with the editor's
`loadCase` verb -- the same generator and the same records -- and shows the verdict
labelled as what it is: computed when this page was built, by Python, not by the page.

`tests/test_tutorial.py` recomputes every verdict from this module and compares it with
what the page shipped, so the blob can never drift from the verifier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..api import DEFAULT_TEMPLATE, Machine
from ..arch import load as load_arch
from ..cost.hardware import hardware_report
from ..cost.models import CostModel
from ..ir.tsir import TSIR
from ..verify import verify

__all__ = ["CASES", "case_machine", "judge", "verdicts", "cases_for_page", "measured"]

ROOT = Path(__file__).resolve().parents[2]
ARCH_DIR = ROOT / "arch"
MATRIX = ROOT / "Compiler" / "build" / "matrix"
MICRO_QASM = ROOT / "Compiler" / "examples" / "micro.qasm"

#: The nine shipped architectures Part D compares, in the order the table shows them.
ARCHES = ("chain", "stationary_chain", "h2_racetrack", "cyclone_base", "cyclone_dual_loop",
          "ladder_2x72", "ring144_24v", "grid9x9", "deck_unit_cell")


def _rec(method: str, *args, **kwargs) -> dict:
    return {"method": method, "args": list(args), "kwargs": dict(kwargs)}


#: The six-site ring with two dock spurs every broadcasting lesson stands on.  `verticals=2`
#: puts spurs at S0 and S3; the loop sites are `data` (no gates), the spur ends `trap`.
RING6D = {"generator": "ring", "params": {"width": 3, "height": 2, "verticals": 2},
          "name": "ring6d"}

CASES: Mapping[str, dict] = {
    # R4d: one channel, one waveform.  Two ions on the loop asked to hop opposite ways in
    # one step; the fix gives each direction its own step.
    "R5": {
        "device": RING6D,
        "focus": "R4d",
        "program": [
            _rec("init", {"d0": "S0", "d1": "S3"}),
            _rec("simd", "shuttle", [["d0", "S0", "S1"], ["d1", "S3", "S2"]]),
        ],
        "fixed": [
            _rec("init", {"d0": "S0", "d1": "S3"}),
            _rec("simd", "shuttle", [["d1", "S3", "S2"]]),
            _rec("simd", "shuttle", [["d0", "S0", "S1"]]),
        ],
    },
    # R7 / R16: the ion crosses the spur junction at S0 (3 quanta) and meets a gate hot;
    # the cooling before the walk did nothing for it.  R16's gate error is read off the
    # replay for both the hot and the repaired programme.
    "R7": {
        "device": RING6D,
        "focus": "R16",
        "program": [
            _rec("init", {"d0": "S1", "d1": "A0"}),
            _rec("cool"),
            _rec("shuttle", "d0", ["S1", "S0", "A0"]),
            _rec("gate", "CX", [["d0", "d1"]]),
        ],
        "fixed": [
            _rec("init", {"d0": "S1", "d1": "A0"}),
            _rec("shuttle", "d0", ["S1", "S0", "A0"]),
            _rec("cool"),
            _rec("gate", "CX", [["d0", "d1"]]),
        ],
    },
    # R9: the programme claims totals its replay does not reach; the fix is the true claim.
    "R9": {
        "device": RING6D,
        "focus": "R9",
        "program": [
            _rec("init", {"d0": "S1", "d1": "A0"}),
            _rec("shuttle", "d0", ["S1", "S0", "A0"]),
            _rec("cool"),
            _rec("gate", "CX", [["d0", "d1"]]),
            _rec("claim", total_steps=3, total_cost=2),
        ],
        "fixed": [
            _rec("init", {"d0": "S1", "d1": "A0"}),
            _rec("shuttle", "d0", ["S1", "S0", "A0"]),
            _rec("cool"),
            _rec("gate", "CX", [["d0", "d1"]]),
            _rec("claim", total_steps=4, total_cost=3),
        ],
    },
}

_METRICS = ("total_cost", "total_steps", "runtime_us", "peak_quanta", "gate_error_sum")


def case_machine(case: Mapping) -> Machine:
    """The case's device, built the way the studio builds it: the named generator with
    the same parameters, borrowing the default template's physics and control plane."""
    dev = case["device"]
    ctor = getattr(Machine, str(dev["generator"]))
    return ctor(**dict(dev["params"]), name=str(dev.get("name", "case")),
                template=DEFAULT_TEMPLATE)


def _state(summary: dict, rule: str) -> str:
    if rule in summary.get("failed", ()):
        return "failed"
    if rule in (summary.get("partial") or {}):
        return "partial"
    if rule in (summary.get("skipped") or {}):
        return "skipped"
    if rule in summary.get("passed", ()):
        return "passed"
    return "unknown"


def judge(case: Mapping, records: list, model: CostModel) -> dict:
    """Python's verdict on one programme of a case, shaped for the page.

    `focus` is the rule the lesson is about; its messages come from a second run that asks
    for that rule alone, because R4d's violations are filed under R4 (the channel clause
    is a derivation of R4, not a separate statute) and a lesson about R4d must show R4d's
    own sentences and nothing else's.
    """
    m = case_machine(case)
    prog = m.program("case", provenance="off").apply_calls(records).build()
    rep = verify(prog, m.arch, model)
    summary = rep.rules.summary()
    focus = str(case.get("focus", ""))
    out = {
        "model": model.name,
        "rules": {k: summary[k] for k in ("passed", "failed", "partial", "skipped", "by_rule")},
        "messages": [{"rule": v.rule, "instr": v.instr_id, "message": v.message}
                     for v in rep.rules.violations[:8]],
        "metrics": {k: rep.result.metrics().get(k) for k in _METRICS},
    }
    if focus:
        only = verify(prog, m.arch, model, only_rules=[focus],
                      check_metrics=(focus == "R9"))
        msgs = [{"rule": focus, "instr": v.instr_id, "message": v.message}
                for v in only.rules.violations[:4]]
        state = "failed" if msgs else _state(summary, focus)
        why = ""
        if state == "skipped":
            why = str((summary.get("skipped") or {}).get(focus, ""))
        elif state == "partial":
            why = str((summary.get("partial") or {}).get(focus, ""))
        out["focus"] = {"rule": focus, "state": state, "why": why, "messages": msgs}
    return out


def verdicts(model: CostModel) -> dict:
    """Every case judged under `model`: `{id: {"loaded": verdict, "fixed": verdict | None}}`."""
    out = {}
    for cid, case in CASES.items():
        out[cid] = {"loaded": judge(case, case["program"], model),
                    "fixed": judge(case, case["fixed"], model) if case.get("fixed") else None}
    return out


def measured(model: CostModel) -> dict:
    '''Part D's numbers, measured at page-build time and shipped with their provenance.

    `planes`: the hardware report of every shipped architecture (electrodes, DACs, the
    wiring scheme, junctions).  `micro`: the compiler's `micro.qasm` on each of them,
    replayed here under `model` -- the raw artifact and the cooled one -- as the totals the
    verifier computed.  An architecture whose artifact is missing is simply absent; nothing
    is invented for it.
    '''
    planes, micro = {}, {}
    for stem in ARCHES:
        ap = ARCH_DIR / f"{stem}.arch.json"
        if not ap.exists():
            continue
        arch = load_arch(ap)
        hw = hardware_report(arch)
        planes[stem] = {"scheme": hw.scheme, "dacs": hw.dacs, "electrodes": hw.electrodes,
                        "switches": hw.switches, "junctions": hw.n_junctions,
                        "traps": hw.n_traps, "budget_dacs": (hw.budget or {}).get("max_dacs")}
        for kind, name in (("raw", f"micro_{stem}.tsir.json"),
                           ("cooled", f"micro_{stem}.cooled.tsir.json")):
            tp = MATRIX / name
            if not tp.exists():
                continue
            prog = TSIR.load(tp)
            rep = verify(prog, arch, model)
            m = rep.result.metrics()
            micro.setdefault(stem, {})[kind] = {
                "instructions": len(prog), "total_cost": m["total_cost"],
                "total_steps": m["total_steps"], "runtime_us": m["runtime_us"],
                "peak_quanta": m["peak_quanta"], "failed": list(rep.rules.summary()["failed"]),
                # THE ARTIFACT'S NAME, not where it sits in this repository.  `micro_chain
                # .cooled.tsir.json` is a thing the reader can recognise and export for
                # themselves; `Compiler/build/matrix/` in front of it is a build directory
                # in a tree they do not have, and no more theirs than a source folder.
                "file": tp.name}
    return {"model": model.name, "circuit": "micro.qasm", "planes": planes, "micro": micro,
            "provenance": "hardware_report(arch) and verify(TSIR.load(artifact), arch, model) "
                          "at page-build time"}


def cases_for_page() -> dict:
    """The cases as the page needs them to rebuild each one: device call and records."""
    return {cid: {"device": dict(case["device"]), "program": list(case["program"]),
                  "fixed": list(case.get("fixed") or [])}
            for cid, case in CASES.items()}
