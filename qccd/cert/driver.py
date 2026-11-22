"""Compile a circuit for a studio machine with QCCDCert.

    from qccd.cert.driver import compile_for_machine
    r = compile_for_machine(arch, model, "bell.qasm", "out/bell")
    r.compilation.finish        # QCCDCert's finish time (µs, the translation's unit)
    r.tsir                      # the same target code as a TSIR programme for the player

1. the compiler lists the program's exact waveforms (`qccd-dynamic catalogue`);
2. `machine.translate` writes the machine as a QCCDCert hardware program;
3. the rule program: `cap` (no site over capacity during an event), `rest` (no ion rests on a
   junction), `wave` (one waveform per transport channel per event);
4. QCCDCert compiles (`compile.compile_dir`): every stage, the timed target code, the
   parallel schedule;
5. `tsir.to_tsir` turns the target code into TSIR, with each instruction's source statement.

Steps 1-4 are QCCDCert's and Theorem `verified_compilation` covers their result.  What the
studio's own replay then reports about the TSIR (its R-rules, heat, cooling) is this
project's extension and is labelled so wherever it is shown.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import machine as M
from .compile import Compilation, compile_dir
from .examples import CAP, ONE_WAVEFORM, const, observe
from .toolchain import ToolFailed, Toolchain, find
from .tsir import certificate, to_tsir


#: R12 as an assertion: one optical action per site per event.
ONE_PER_TRAP = {"name": "beam", "category": "operationTiming",
                "formula": {"all": {"name": "s", "domain": "sites",
                                    "body": {"le": {"lhs": observe("pulsesAt", "s"), "rhs": const(1)}}}}}
#: R4 as an assertion: one movement class per step -- a transport uses one channel only
#: (the shared shuttle channel, or one loop's rotation channel).
ONE_CLASS = {"name": "class", "category": "transportControl",
             "formula": {"all": {"name": "c", "domain": "channels",
                                 "body": {"or": {"lhs": {"eq": {"lhs": observe("channelUse", "c"), "rhs": const(0)}},
                                                 "rhs": {"eq": {"lhs": observe("channelUse", "c"),
                                                                "rhs": {"atom": {"expression": {"base": {"expression": "moveCount"}}}}}}}}}}}
#: A designer's placement rule: at most one ion per site at the start.
SPREAD = {"name": "spread", "category": "staticOccupancy",
          "formula": {"all": {"name": "s", "domain": "sites",
                              "body": {"implies": {"lhs": {"and": {"lhs": {"eq": {"lhs": {"atom": {"expression": {"base": {"expression": "start"}}}}, "rhs": const(0)}},
                                                                    "rhs": {"eq": {"lhs": {"atom": {"expression": {"base": {"expression": "duration"}}}}, "rhs": const(0)}}}},
                                                   "rhs": {"le": {"lhs": observe({"occupancy": {"phase": "before"}}, "s"), "rhs": const(1)}}}}}}}


@dataclass
class Result:
    translation: M.Translation
    rules: list[dict]
    compilation: Compilation
    tsir: dict
    certificate: dict          # the circuit side of qccd.ir.source_map.build


def rest_rule(tr: M.Translation, n_traps: int) -> dict | None:
    """`assert rest: N+(J..) = 0 and ...;` -- after every event, no ion sits on a junction
    (a pure junction, or a trap where three or more rails meet: studio rule R23)."""
    js = list(tr.rest)
    if not js:
        return None
    f = None
    for j in js:
        eq = {"eq": {"lhs": observe({"occupancy": {"phase": "after"}}, j), "rhs": const(0)}}
        f = eq if f is None else {"and": {"lhs": f, "rhs": eq}}
    return {"name": "rest", "category": "staticOccupancy", "formula": f}


def compile_for_machine(arch, model, qasm_path: str | Path, folder: str | Path, *,
                        tc: Toolchain | None = None, extra_rules: list[dict] = (),
                        search_seconds: float = 120) -> Result:
    """`search_seconds` boxes QCCDCert's own transport search; past it, or when the search
    finds no route, this project's router proposes the routes and QCCDCert checks them."""
    tc = tc or find()
    qasm_path = Path(qasm_path)
    waves = M.waveforms(qasm_path, tc)
    tr = M.translate(arch, model, waves)
    n_traps = len(arch.device.sites())
    rules = [CAP, *([r] if (r := rest_rule(tr, n_traps)) else []), ONE_WAVEFORM, ONE_PER_TRAP,
             *([ONE_CLASS] if tr.loop_channels else []), *extra_rules]
    text = qasm_path.read_text(encoding="utf-8")
    routing = "QCCDCert's transport search"
    searched = Toolchain(tc.root, timeout=min(tc.timeout, search_seconds))
    try:
        comp = compile_dir(folder, tr.hardware, rules, text, tc=searched, profile="qiskit")
    except (ToolFailed, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, ToolFailed) and "No placement, route" not in exc.output:
            raise
        # the search found no route, or not in time: this project's router proposes one, and
        # QCCDCert's checked routing-proposal stage admits each transport with its legality proof
        from .planner import propose
        plan = propose(tr.hardware, tr.rest, qasm_path, tc)
        comp = compile_dir(folder, tr.hardware, rules, text, tc=tc, profile="qiskit", route_plan=plan)
        routing = ("this site's router (qccd.cert.planner), proposing; QCCDCert's routing-proposal stage "
                   "admitted every transport with its legality proof")
    tr.notes.append(f"Routing: {routing}.")
    prog = to_tsir(tr, comp.execution, name=qasm_path.stem, arch_spec=arch.name, stages=comp.stages, qasm=comp.qasm)
    (Path(folder) / "prog.tsir.json").write_text(json.dumps(prog, indent=1) + "\n", encoding="utf-8")
    (Path(folder) / "translation.json").write_text(json.dumps(
        {"site_ids": tr.site_ids, "path_segments": tr.path_segments, "path_nodes": tr.path_nodes,
         "notes": tr.notes}, indent=1) + "\n", encoding="utf-8")
    cert = certificate(prog, comp.qasm)
    (Path(folder) / "prog.qcert.json").write_text(json.dumps(cert, indent=1) + "\n", encoding="utf-8")
    return Result(tr, rules, comp, prog, cert)
