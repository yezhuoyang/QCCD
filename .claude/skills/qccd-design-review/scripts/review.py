#!/usr/bin/env python3
"""Run every check a QCCD design is judged by, and lay out each rule's verdict.

Nothing here re-implements a rule.  The verdicts come from the repository's own checkers, the
ones the official grader runs:

  * the grader's rules stage (`qccd.workspace.evaluator`, which runs `qccd.verify`), exactly as
    a board grades an entry;
  * the rule language (`qccd.ruleset`), every rule of the board's set as written on the Rules
    page -- including R0, R24 and R25, which the rules stage of a `standard` board never runs --
    with the circuit the certificate names, so R24/R25 have something to judge;
  * for a bundle (or loose files that make one), the whole reference grade (`grade`): bundle,
    device, physics lock, program, rules, correspondence, certificate binding, the Lean checker,
    semantics, LER, metrics.

It also reports what a reviewer would otherwise work out by hand: how many cases each rule had
to check (a rule with none passes without saying anything), whether the board's circuit fits the
device, what the design changed against the shipped device of its name, whether re-cooling under
the board's physics clears a heat failure, a local grade against a given (official) one, and the
electrodes' design-rule check (information only).

    review.py --bundle entry.zip [--report official_report.json]
    review.py --device d.arch.json --program final.tsir.json --certificate c.qcert.json \\
              [--certified certified.tsir.json] --board "surface code memory, distance 3"
    review.py --device d.arch.json --board steane          # the device alone: drawing, budget, fit
    review.py --report report.json                         # a grade someone already ran
    review.py --list-boards

Writes rule-check.md and rule-check.json to --out (default: a new temporary folder) and prints the
markdown: the evidence a review is written from, not the review.  Exit 0 when every rule that
blocks a board entry is clear, 1 when one fails or cannot be checked, 2 when the inputs could not
be read.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SITE = "https://qccd.academy/rules/#"


def repo_root(arg: str | None = None) -> Path:
    for cand in (arg, os.environ.get("QCCD_REPO"), str(SKILL.parents[2])):
        if cand and (Path(cand) / "qccd" / "ruleset" / "library" / "catalogue.rules").is_file():
            return Path(cand).resolve()
    raise SystemExit("review: no QCCD checkout found (pass --repo, or set QCCD_REPO)")


#: rules whose `passed` says nothing about the programme: they pass by construction or because
#: the board's cost model has the property they ask for (qccd/verify/__init__.py, `verify`)
VACUOUS = {
    "R16": "a property of the cost model (it prices gate error from n-bar); no programme can fail it",
    "R17": "a property of the cost model (it accrues heating with time); no programme can fail it",
    "R18": "by construction: junctions are priced by the degree the expanded graph reports",
}
#: rules the device document decides on its own, with no programme (qccd/verify/rules.py:
#: architecture_violations, geometry_violations, c2lr_document_violations)
DEVICE_RULES = ("R11", "R18", "R19", "R20", "R21", "C10")
#: the stages that decide R10 between them (the evaluator's docstring)
R10_STAGES = ("certificate_binding", "lean_certificate", "semantics")
#: control fields worth naming when a design's control differs from the device it is named after
CONTROL_FIELDS = (("model",), ("max_simd_classes_per_cycle",), ("wiring", "scheme"), ("channels", "grouping"),
                  ("channels", "switch_per_site"), ("optical", "addressing"))


# --------------------------------------------------------------------------- inputs

def _load_json(p: Path) -> dict:
    from qccd.workspace.jsonsafe import strict_loads
    return strict_loads(Path(p).read_bytes())


def _board(name: str | None, fallback: str | None, notes: list):
    from qccd.workspace.tasks import DEFAULT_BOARD, find_board, find_release
    if name:
        return find_board(name)
    if fallback:
        return find_release(fallback)
    notes.append(f"no board was named: judged as an entry on `{DEFAULT_BOARD}` (the starter board). Boards "
                 "differ in physics (heat budget, timing tables), so name the board for the grader's verdict.")
    return find_release(DEFAULT_BOARD)


def _toolchain() -> dict:
    from qccd.workspace.evaluator import Toolchain
    tc = Toolchain.discover()
    extras = {}
    for m in ("stim", "qiskit", "pymatching"):
        try:
            __import__(m)
            extras[m] = True
        except ImportError:
            extras[m] = False
    return {"qccdc": str(tc.qccdc) if tc.qccdc else None, "qcheck": str(tc.qcheck) if tc.qcheck else None,
            "python": tc.python, "python_extras": extras}


def _declared(doc_or_spec, root: Path | None = None):
    """A declared rule set: a bundle's rules.json document, a shipped set's name, or a .rules file.
    Raises DeclaredRulesError, in the grader's words, for anything the grader would not read."""
    from qccd.ruleset.declared import RULES_KIND, DeclaredRulesError, load_declared, rules_doc
    doc = doc_or_spec if isinstance(doc_or_spec, dict) else rules_doc(str(doc_or_spec), root=root)
    if not isinstance(doc, dict) or doc.get("kind") != RULES_KIND or doc.get("version") != 1 \
            or not isinstance(doc.get("text"), str):
        raise DeclaredRulesError("rules.json is not a qccd.rules v1 document with the rule set's text")
    return doc, load_declared(doc["text"])


def _get(d, path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


# --------------------------------------------------------------------------- the device

def review_device(doc: dict, release, notes: list) -> tuple[dict, object | None]:
    """The device as the board scores it: loads, physics lock, budget, size, drawing facts."""
    from qccd.arch.device import Architecture
    from qccd.workspace.tasks import scored_device
    info: dict = {"name": doc.get("name"), "errors": []}
    scored, own = scored_device(doc, release)
    info["physics"] = own
    try:
        arch = Architecture.from_json(scored)
    except (ValueError, KeyError, TypeError) as exc:
        info["errors"].append(f"the device does not load: {exc}")
        return info, None
    dev = arch.device
    s = dev.summary()
    limits = release.manifest.get("limits") or {}
    info.update(nodes=s["n_nodes"], sites=s["n_sites"], segments=s["n_segments"], loops=s["n_loops"],
                docks=s.get("n_docks"), junction_nodes=s["n_junction_nodes"], degree_histogram=s["degree_histogram"],
                total_capacity=s["total_capacity"], control_model=str(arch.control.get("model", "simd_classes")),
                switch_per_site=bool(_get(arch.control, ("channels", "switch_per_site"))),
                max_nodes=limits.get("max_nodes"), generator=dev.generator,
                reproducible_from_generator=dev.reproducible_from_generator())
    if limits.get("max_nodes") and s["n_nodes"] > int(limits["max_nodes"]):
        info["errors"].append(f"{s['n_nodes']} nodes exceeds the board's limit {limits['max_nodes']}")
    caps = {"gate": 0, "spam": 0, "cool": 0}
    for n in dev.nodes.values():
        z = (arch.zone_types or {}).get(n.zone_type or "", {})
        for k in caps:
            caps[k] += bool(z.get(k))
    info["sites_that_can"] = caps
    # the budget, as the device stage judges it (requirements B1/B2 of qccd/evalset/library/board.eval)
    info["budget"] = budget_of(arch, release)
    for r in info["budget"]["over"]:
        info["errors"].append(f"over the board's budget ({r})")
    if own["added"]:
        notes.append("the device brings physics the board lacks (" + ", ".join(own["added"]) + "): it is used, "
                     "and the entry is ranked with the 'any rules' entries, not on the board's own ranking")
    if own["ignored"]:
        notes.append("the device's own physics was not used where the board has a number (" +
                     ", ".join(own["ignored"]) + "): the design is scored with the board's")
    return info, arch


def budget_of(arch, release) -> dict:
    from qccd.cost.hardware import WIRING_FLOORS, hardware_report
    from qccd.evalset import load as load_evaluation
    from qccd.ir.tsir import TSIR
    budget = dict(release.physics().get("budget") or {})
    board = load_evaluation("board").with_params({"max_dacs": budget.get("max_dacs"),
                                                  "max_junctions": budget.get("max_junctions")})
    brun = board.run(TSIR(name="device", arch_spec=arch.name, instructions=[]), arch)
    vals = board.metrics(brun, only=["dacs", "n_junctions"])
    over = [r for r in board.requirements_check(brun) if r["verdict"] != "holds"]
    hw = hardware_report(arch, floors=WIRING_FLOORS, budget=budget)
    return {"dacs": vals["dacs"], "max_dacs": budget.get("max_dacs"), "junctions": vals["n_junctions"],
            "max_junctions": budget.get("max_junctions"), "over": [f"{r['id']}: {r['text']}" for r in over],
            "notes": [n for n in hw.notes if "counted at" in n or "count as" in n]}


def board_fit(release, info: dict) -> dict:
    """Does the board's circuit fit the device at all, and which compiler will route it?"""
    q = sum(int(n) for n in re.findall(r"^\s*qreg\s+\w+\s*\[\s*(\d+)\s*\]", release.circuit_text(), re.M))
    cap = int(info.get("total_capacity") or 0)
    loops = int(info.get("loops") or 0)
    return {"circuit_qubits": q, "ion_slots": cap, "spare_slots": cap - q,
            "gate_sites": (info.get("sites_that_can") or {}).get("gate"), "loops": loops, "docks": info.get("docks"),
            "compiler": ("the conveyor drives the first closed loop (its riders and docks), else the router"
                         if loops else "the router (no closed loop for the conveyor)")}


def compare_named(doc: dict, arch, release, repo: Path) -> dict | None:
    """What the design changed against the shipped device of its name, when there is one."""
    from qccd.arch.device import Architecture
    from qccd.workspace.jsonsafe import canonical_text, normalize_numbers
    from qccd.workspace.tasks import scored_device
    name = str(doc.get("name") or "")
    p = repo / "arch" / f"{name}.arch.json"
    if not name or not p.is_file():
        return None
    base_doc = json.loads(p.read_text(encoding="utf-8"))
    base = Architecture.from_json(scored_device(base_doc, release)[0])
    a, b = arch.device, base.device
    same_graph = (set(a.nodes) == set(b.nodes) and set(a.segments) == set(b.segments)
                  and all(tuple(a.nodes[n].pos) == tuple(b.nodes[n].pos) for n in a.nodes)
                  and all(set(a.segments[s].ends) == set(b.segments[s].ends) for s in a.segments))
    control = [{"field": ".".join(f), "shipped": _get(base_doc.get("control"), f), "design": _get(doc.get("control"), f)}
               for f in CONTROL_FIELDS if _get(base_doc.get("control"), f) != _get(doc.get("control"), f)]

    def zt(d):
        return canonical_text(normalize_numbers(d.get("zone_types") or {}))
    return {"shipped": f"arch/{name}.arch.json", "same_geometry": same_graph, "control_differs": control,
            "zone_types_differ": zt(doc) != zt(base_doc), "shipped_budget": budget_of(base, release)}


def phys_summary(arch, tech: str) -> dict:
    """The electrodes the device implies and their design-rule check: information, never a grade."""
    from qccd.phys.build import build_layout
    from qccd.phys.drc import check as drc_check
    from qccd.phys.tech import load_technology
    t = load_technology(tech)
    lay = build_layout(arch, t)
    d = drc_check(lay, arch).as_dict()
    disc = []
    for x in d.get("disclosures") or []:
        disc.append(str(x.get("topic") or x.get("title") or "disclosure") if isinstance(x, dict) else str(x))
    return {"technology": t.name, "polygons": d.get("n_polys"), "clean": d.get("clean"),
            "violations_by_rule": {k: v for k, v in (d.get("by_rule") or {}).items() if v},
            "refused": len(lay.refused), "refused_examples": [f"{r.kind} {r.owner}: {r.reason}" for r in lay.refused[:3]],
            "disclosures": disc[:4]}


# --------------------------------------------------------------------------- the rules

def _model(release):
    from qccd.cost.models import corrected_model
    return corrected_model(release.manifest["physics"].get("rank_table", "qccdsim_jones"))


def _verify_view(release, arch, prog, declared_doc, cert) -> dict:
    """The grader's rules stage itself, run on this device and programme."""
    from qccd.workspace.evaluator import _rules_stage, _Stage
    st = _Stage("rules", True)
    ctx: dict = {}
    _rules_stage(st, release, arch, prog, declared=declared_doc, cert=cert, ctx=ctx)
    return {"status": st.status, "coverage": st.coverage, "detail": st.detail, "diagnostics": st.diagnostics,
            "breaks": (ctx.get("rules") or {}).get("breaks", [])}


def _language(rs, release, arch, prog, cert, computes):
    from qccd.ruleset import check, legacy_config
    from qccd.ruleset.compile import circuit_of_certificate
    from qccd.ruleset.programme import programme_execution
    exe = programme_execution(prog, arch, _model(release),
                              circuit=circuit_of_certificate(cert) if cert else None,
                              computes_circuit=computes, rule_config=legacy_config(rs) or None,
                              check_metrics=False)
    return check(rs, exe), exe


def rule_cases(rs, exe) -> dict:
    """How many cases each rule's quantifiers range over in this execution.

    Every top-level `for all ... where ...:` of a rule is evaluated again with its body replaced by
    `false`, so each case is a counterexample and the count is the number of cases the rule had to
    judge.  A rule whose every quantifier ranges over nothing passes without having checked
    anything -- no splits, no loops, no pairs of hops on one rail."""
    from qccd.ruleset import FAILS, HOLDS
    from qccd.ruleset.evaluate import Evaluator
    from qccd.ruleset.syntax import BinOp, Const, Quant, Rule, show
    ev = Evaluator(exe, rs.params)
    out: dict = {}
    for r in rs.rules:
        parts: list = []

        def conj(n):
            if isinstance(n, BinOp) and n.op == "and":
                conj(n.left)
                conj(n.right)
            else:
                parts.append(n)
        conj(r.formula)
        rows = []
        for p in parts:
            if not (isinstance(p, Quant) and p.kind == "all"):
                continue
            label = ", ".join(", ".join(b.names) + " in " + show(b.domain) for b in p.binders)
            probe = Rule(r.id, r.title, Quant("all", p.binders, p.where, Const(False)), when=r.when,
                         when_text=r.when_text)
            try:
                res = ev.run(probe)
                n = res.count if res.verdict == FAILS else (0 if res.verdict == HOLDS else None)
            except Exception:  # a probe that cannot be evaluated says nothing either way
                n = None
            rows.append({"over": label, "cases": n})
        out[r.id] = rows
    return out


def certificate_fit(cert: dict, release) -> dict:
    """Is the certificate about this board's circuit at all?  The binding stage decides that op by
    op (and needs qiskit); this compares only what needs no tool: the qubit count the certificate
    maps against the qubits the board's circuit declares (`qreg`)."""
    text = release.circuit_text()
    board_q = sum(int(n) for n in re.findall(r"^\s*qreg\s+\w+\s*\[\s*(\d+)\s*\]", text, re.M))
    cert_q = len((cert or {}).get("map") or {})
    return {"certificate_qubits": cert_q, "board_qubits": board_q, "matches": cert_q == board_q,
            "certificate_ops": len((cert or {}).get("circuit_ops") or [])}


def _claims(release, arch, prog) -> dict:
    """R9, which the grader skips: do the programme's own figures match its replay?"""
    from qccd.verify import verify
    rep = verify(prog, arch, _model(release), check_metrics=True, only_rules=["R9"])
    s = rep.rules.summary()
    msgs = [v.message for v in rep.rules.violations if v.rule == "R9"][:3]
    status = ("failed" if "R9" in s["failed"] else "skipped" if "R9" in s["skipped"]
              else "partial" if "R9" in s["partial"] else "passed")
    return {"status": status, "why": s["skipped"].get("R9") or s["partial"].get("R9") or "", "examples": msgs}


def recool(release, arch, certified_doc: dict, final_prog) -> dict:
    """Does re-cooling the certified programme under the board's physics clear the heat rules?
    Inserting `cool` is the one change a board allows between the certified and the final
    programme (`insert:cool`), so this is a fix that keeps `correspondence` passing."""
    from qccd.compile.cooling import CoolingPolicy, insert_cooling
    from qccd.ir.tsir import TSIR
    from qccd.verify import verify
    model = _model(release)
    cooled = insert_cooling(TSIR.from_json(certified_doc), arch, model, policy=CoolingPolicy()).program
    before = verify(final_prog, arch, model, check_metrics=False)
    after = verify(cooled, arch, model, check_metrics=False)
    sa = after.rules.summary()
    return {"cools_before": sum(1 for i in final_prog.instructions if i.type == "cool"),
            "cools_after": sum(1 for i in cooled.instructions if i.type == "cool"),
            "runtime_us_before": round(before.result.total_us, 3), "runtime_us_after": round(after.result.total_us, 3),
            "failed_after": sorted(sa["failed"]), "clears": [r for r in ("R7", "R7c") if r not in sa["failed"]],
            "program": cooled.to_json()}


def _rows(board_rs, declared_rs, vview: dict | None, lang, dlang, release, arch_info: dict,
          stages: dict | None, device_only: bool, claims: dict | None, cases: dict | None,
          cert_fit: dict | None = None) -> list[dict]:
    from qccd.ruleset import FAILS, HOLDS, NOT_APPLICABLE, UNCHECKED, catalogue, rule_order
    from qccd.workspace.evaluator import ALLOWED_PARTIAL, ALLOWED_SKIPS, BOARD_RULES
    cat = {r.id: r for r in catalogue().rules}
    lv = {r.id: r for r in lang.results} if lang is not None else {}
    dv = {r.id: r for r in dlang.results} if dlang is not None else {}
    det = (vview or {}).get("detail") or {}
    diags = (vview or {}).get("diagnostics") or []
    release_set = bool(release.manifest.get("rules"))
    ids = list(board_rs.ids)
    if declared_rs is not None:
        ids += [i for i in declared_rs.ids if i not in ids]
    ids.sort(key=rule_order)
    drops = set()
    if declared_rs is not None:
        from qccd.ruleset.declared import changes
        ch = changes(board_rs, declared_rs)
        drops = set(ch["drops"]) | set(ch["replaces"])
    out = []
    for rid in ids:
        r = cat.get(rid) or (declared_rs.rule(rid) if declared_rs is not None and rid in declared_rs.ids else None)
        row = {"id": rid, "title": r.title if r is not None else rid, "url": SITE + rid,
               "core": bool(getattr(r, "core", False)), "in_board_set": rid in board_rs.ids,
               "in_declared_set": (rid in declared_rs.ids) if declared_rs is not None else None,
               "verify": None, "language": None, "declared": None, "cases": None, "grader": "", "review": "",
               "notes": []}
        if vview is not None:
            if rid in det.get("failed", []):
                v = "failed"
            elif rid in (det.get("skipped") or {}):
                v = "skipped"
            elif rid in (det.get("partial") or {}):
                v = "partial"
            elif rid in det.get("passed", []):
                v = "passed"
            else:
                v = None
            ex = [d for d in diags if d.get("rule") == rid or d.get("id") == f"RULE.{rid}"]
            row["verify"] = {"status": v, "count": len(ex),
                             "why": (det.get("skipped") or {}).get(rid) or (det.get("partial") or {}).get(rid) or "",
                             "examples": [{"message": d["message"], "instructions": d.get("instructions")}
                                          for d in ex[:3]]}
        for key, src in (("language", lv), ("declared", dv)):
            x = src.get(rid)
            # on an empty programme a programme rule holds vacuously: that verdict says nothing
            if x is not None and not (device_only and rid not in DEVICE_RULES):
                row[key] = {"verdict": x.verdict, "count": x.count, "examples": list(x.examples[:3]),
                            "reason": x.reason}
        if cases is not None and not (device_only and rid not in DEVICE_RULES):
            row["cases"] = cases.get(rid)
        v = (row["verify"] or {}).get("status")
        lverdict = (row["language"] or {}).get("verdict")
        # ---- what the grader's rules stage makes of it (the board's set)
        if not row["in_board_set"]:
            g = "not a board rule (the entry's own set adds it)"
        elif device_only and rid not in DEVICE_RULES:
            g = "needs a programme"
        elif v == "failed":
            g = "FAIL" if rid not in drops else "fails (the entry's own set drops or rewrites it)"
        elif v == "skipped":
            g = "allowed skip" if rid in ALLOWED_SKIPS else "BLOCKS: skipped"
        elif v == "partial":
            g = "allowed partial" if rid in ALLOWED_PARTIAL else "BLOCKS: partial"
        elif v == "passed":
            g = "pass"
        elif release_set and lverdict is not None:
            g = {HOLDS: "pass (rule language)", FAILS: "FAIL", UNCHECKED: "BLOCKS: could not be checked",
                 NOT_APPLICABLE: "not judged (does not apply in the rules stage)"}.get(lverdict, lverdict)
        else:
            g = "not judged by the rules stage"
        if rid == "R10" and stages:
            got = {s: (stages.get(s) or {}).get("status") for s in R10_STAGES}
            g += "; certificate stages: " + ", ".join(f"{s} {got[s] or 'not run'}" for s in R10_STAGES)
        row["grader"] = g
        # ---- the reviewer's reading: a failure in either checker is a finding
        counted = [c["cases"] for c in (row["cases"] or []) if c["cases"] is not None]
        if device_only and rid not in DEVICE_RULES:
            rv = "needs a programme"
        elif v == "failed" or lverdict == FAILS or (row["declared"] or {}).get("verdict") == FAILS:
            rv = "FAIL"
        elif g.startswith("BLOCKS"):
            rv = "BLOCKS"
        elif rid in VACUOUS and v == "passed":
            rv = "pass (vacuous)"
            row["notes"].append(VACUOUS[rid])
        elif v == "passed" or (lverdict == HOLDS and v is None):
            rv = "pass (nothing to check)" if counted and not any(counted) and len(counted) == len(row["cases"]) else "pass"
            if rv == "pass" and counted and not all(counted):
                empty = [c["over"] for c in row["cases"] if c["cases"] == 0]
                row["notes"].append("part of it has nothing to check here: " + "; ".join(empty))
        elif v in ("skipped", "partial"):
            rv = g
        elif lverdict == NOT_APPLICABLE:
            rv = "does not apply"
        elif lverdict == UNCHECKED:
            rv = "could not be checked"
        else:
            rv = "not checked"
        if rid == "R10" and stages:
            got = [(stages.get(s) or {}).get("status") for s in R10_STAGES]
            rv = "pass (certificate stages)" if all(x == "passed" for x in got) else (
                "FAIL" if any(x == "failed" for x in got) else "not established: " +
                ", ".join(f"{s} {x or 'not run'}" for s, x in zip(R10_STAGES, got)))
        elif rid == "R10" and not device_only:
            rv = "not established here"
            row["notes"].append("only the certificate stages decide R10: give the certified programme too "
                                "(--certified), or review the bundle")
        row["review"] = rv
        # ---- notes the two checkers' disagreement deserves
        if v == "passed" and lverdict == FAILS:
            row["notes"].append("the rule language finds counterexamples the grader's verifier does not report "
                                "under this id" + (" (the verifier files drivability failures under R4)"
                                                   if rid == "R4d" else ""))
        if v == "failed" and lverdict == HOLDS:
            row["notes"].append("the grader's verifier fails it but the rule language holds: read the "
                                "verifier's messages; R4 failures may be R4d's (drivability)")
        if rid == "R22" and arch_info.get("control_model") == "direct" and v == "passed":
            row["notes"].append("vacuous on a direct-wired device: every site has its own waveform")
        if rid == "R4d" and v == "passed" and arch_info.get("switch_per_site"):
            row["notes"].append("the device declares a switch at every site, so the hold clause does not apply")
        if v is None and not release_set and row["in_board_set"] and not device_only and lverdict:
            row["notes"].append(f"the grader's rules stage does not run it on a `{BOARD_RULES}` board; "
                                f"the rule language says: {lverdict}" +
                                (f" ({(row['language'] or {}).get('reason')})" if (row["language"] or {}).get("reason") else ""))
        if rid == "R9" and claims is not None:
            row["notes"].append(f"advisory R9 (the programme's own figures vs its replay): {claims['status']}"
                                + (f" -- {claims['why']}" if claims["why"] else "")
                                + ("; " + "; ".join(claims["examples"]) if claims["examples"] else ""))
        if rid in drops:
            row["notes"].append("the entry's declared rule set drops or rewrites this board rule: the entry "
                                "can be eligible under its own rules, but it does not keep the board's")
        if rid in ("R24", "R25") and cert_fit is not None and not cert_fit["matches"] and lverdict is not None:
            row["notes"].append("judged against the certificate's own circuit, which is not the board's: this "
                                "verdict says nothing about the board's circuit")
        out.append(row)
    return out


def device_only_rules(board_rs, release, arch, info) -> list[dict]:
    """The rules a device decides alone, with an empty programme; the rest need a programme."""
    from qccd.ir.tsir import TSIR
    prog = TSIR(name="device-only", arch_spec=arch.name, instructions=[])
    vview = _verify_view(release, arch, prog, None, None)
    lang, exe = _language(board_rs, release, arch, prog, None, None)
    return _rows(board_rs, None, vview, lang, None, release, info, None, True, None, rule_cases(board_rs, exe))


# --------------------------------------------------------------------------- a whole grade

def grade_bundle(release, bdir: Path, profile: str, timeout_s: float) -> dict:
    from qccd.workspace.evaluator import grade
    cancel = threading.Event()
    timer = threading.Timer(timeout_s, cancel.set)
    timer.start()
    try:
        with tempfile.TemporaryDirectory() as td:
            return grade(release, bdir, profile, workdir=Path(td) / "work", cancel=cancel)
    finally:
        timer.cancel()


def stages_of(report: dict | None) -> dict:
    return {s["id"]: s for s in (report or {}).get("stages", [])}


def _grade_view(report: dict, source: str) -> dict:
    return {"profile": report.get("profile"), "source": source, "eligibility": report.get("eligibility"),
            "conformance": report.get("conformance"), "metrics": report.get("metrics"),
            "evaluator": {k: (report.get("evaluator") or {}).get(k) for k in ("name", "version", "source_digest")},
            "stages": [{k: s.get(k) for k in ("id", "required", "status", "coverage", "diagnostics")}
                       for s in report.get("stages", [])]}


def compare_grades(given: dict, local: dict, policy) -> dict:
    from qccd.workspace.evaluator import metrics_agree
    g, l_ = stages_of(given), stages_of(local)
    differ = [{"stage": s, "given": (g.get(s) or {}).get("status"), "local": (l_.get(s) or {}).get("status")}
              for s in list(g) + [x for x in l_ if x not in g]
              if (g.get(s) or {}).get("status") != (l_.get(s) or {}).get("status")]
    gm, lm = given.get("metrics") or {}, local.get("metrics") or {}
    bad = metrics_agree(gm, lm, policy)
    return {"stages_differ": differ,
            "metrics_differ": [k for k in bad if (lm.get(k) or {}).get("value") is not None],
            "metrics_not_computed_locally": [k for k in bad if (lm.get(k) or {}).get("value") is None]}


# --------------------------------------------------------------------------- output

def _cell(s) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def _stage_why(s: dict) -> str:
    why = s.get("coverage") or ""
    if s.get("status") != "passed":
        bad = [x.get("message", "") for x in (s.get("diagnostics") or []) if x.get("severity") != "warning"]
        if bad:
            why += " -- " + "; ".join(bad[:2])
    return why


def render(res: dict) -> str:
    b = res.get("board") or {}
    d = res.get("device") or {}
    lines = [f"# Rule check: {d.get('name') or 'design'} on {b.get('title', '?')} (`{b.get('id', '?')}`)", "",
             "The checkers' evidence, rule by rule; the review is written from it (SKILL.md, step 4).", ""]
    lines.append(f"**Verdict:** {res['verdict']}")
    lines.append("")
    for n in res.get("notes", []):
        lines.append(f"- {n}")
    if res.get("notes"):
        lines.append("")
    given, local = (res.get("grades") or {}).get("given"), (res.get("grades") or {}).get("local")
    for label, gv in (("Grade given", given), ("Local grade", local)):
        if not gv or (label == "Local grade" and given):
            continue
        el = gv.get("eligibility") or {}
        lines.append(f"## {label} ({gv.get('profile')} profile, {gv.get('source')})")
        lines.append("")
        lines.append(f"Eligible: **{'yes' if el.get('eligible') else 'no'}**"
                     + (f" ({'; '.join(el.get('reasons') or [])})" if el.get("reasons") else ""))
        lines.append("")
        if given and local:
            ls = {s["id"]: s for s in local.get("stages", [])}
            lines.append("| Stage | Required | Given report | Local grade (this machine) | Local note |")
            lines.append("|---|---|---|---|---|")
            for s in given.get("stages", []):
                lo = ls.get(s["id"]) or {}
                lines.append(f"| {s['id']} | {'yes' if s.get('required') else 'no'} | {s['status']} | "
                             f"{lo.get('status', '-')} | {_cell(_stage_why(lo))[:240] if lo.get('status') != s['status'] else ''} |")
            cmp_ = res.get("grade_comparison") or {}
            lines.append("")
            lines.append("Local against given: " + (
                "every stage the same" if not cmp_.get("stages_differ") else
                "differ at " + ", ".join(f"{x['stage']} ({x['given']} vs {x['local']})" for x in cmp_["stages_differ"]))
                + "; metrics " + ("agree within the board's numerical policy" if not cmp_.get("metrics_differ") else
                                  "differ: " + ", ".join(cmp_["metrics_differ"]))
                + (f" (not computed locally: {', '.join(cmp_['metrics_not_computed_locally'])})"
                   if cmp_.get("metrics_not_computed_locally") else "")
                + ". Local eligibility: " + ("yes" if (local.get("eligibility") or {}).get("eligible") else
                                              "no (" + "; ".join((local.get("eligibility") or {}).get("reasons") or []) + ")"))
        else:
            lines.append("| Stage | Required | Status | What it covered / why not |")
            lines.append("|---|---|---|---|")
            for s in gv.get("stages", []):
                lines.append(f"| {s['id']} | {'yes' if s.get('required') else 'no'} | {s['status']} | {_cell(_stage_why(s))[:300]} |")
        c = gv.get("conformance")
        if c:
            lines.append("")
            lines.append(f"Conformance: keeps the board's rules and physics: **{'yes' if c.get('board') else 'no'}**"
                         f" (rule set `{(c.get('rules') or {}).get('set')}`; breaks "
                         f"{', '.join((c.get('rules') or {}).get('breaks') or []) or 'none'}; physics added "
                         f"{', '.join((c.get('physics') or {}).get('added') or []) or 'none'})")
        m = gv.get("metrics") or {}
        if m:
            lines.append("")
            lines.append("Metrics: " + "; ".join(f"{k} {v.get('value')} {v.get('unit') or ''}".strip()
                                                  + (" (ranked)" if v.get("ranked") else "") for k, v in m.items()))
        lines.append("")
    rows = res.get("rules") or []
    if rows:
        sets = res.get("rule_sets") or {}
        lines.append(f"## Rules: `{sets.get('board')}`" + (f" + the entry's own `{sets['declared']}`"
                                                             if sets.get("declared") else ""))
        lines.append("")
        lines.append("Cases: how many bindings each of the rule's `for all` quantifiers ranged over here, as the rule "
                     "is written (a pair quantifier without `a != b` counts each element with itself too). 0 means "
                     "that part of the rule had nothing to judge.")
        lines.append("")
        lines.append("| Rule | Title | Grader's rules stage | Rule language | Cases | Review | Evidence / notes |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            v = r.get("verify") or {}
            lg = r.get("language") or {}
            ev = []
            for e in v.get("examples") or []:
                ins = [i for i in (e.get("instructions") or []) if i is not None]
                where = ("device: " if ins and all(i == -1 for i in ins) else
                         f"instr {', '.join(map(str, ins))}: " if ins else "")
                ev.append(where + e["message"])
            if lg.get("verdict") == "fails":
                ev += [f"({lg.get('count')} cases) " + "; ".join(lg.get("examples") or [])]
            ev += r.get("notes") or []
            lang_cell = lg.get("verdict") or "-"
            if r.get("declared"):
                lang_cell += f" / own set: {r['declared']['verdict']}"
            cases = r.get("cases")
            ccell = "-" if not cases else " · ".join(str(c["cases"]) if c["cases"] is not None else "?" for c in cases)
            lines.append(f"| [{r['id']}]({r['url']}) | {_cell(r['title'])} | {_cell(r['grader'])} | {_cell(lang_cell)} | "
                         f"{ccell} | **{_cell(r['review'])}** | {_cell(' / '.join(ev))[:600]} |")
        lines.append("")
    rc = res.get("recool")
    if rc:
        lines.append("## Re-cooling under the board's physics")
        lines.append("")
        lines.append(f"The certified programme re-cooled with the board's physics (`insert_cooling`, the board's one "
                     f"allowed change): {rc['cools_before']} -> {rc['cools_after']} cools, runtime "
                     f"{rc['runtime_us_before']} -> {rc['runtime_us_after']} us; rules failing after: "
                     f"{', '.join(rc['failed_after']) or 'none'}. "
                     + (f"Written to `{rc['path']}`." if rc.get("path") else ""))
        lines.append("")
    if d:
        lines.append("## Device")
        lines.append("")
        bud = d.get("budget") or {}
        lines.append(f"- {d.get('nodes')} nodes ({d.get('sites')} sites, {d.get('junction_nodes')} junction nodes), "
                     f"{d.get('segments')} segments, {d.get('loops')} loops; total capacity {d.get('total_capacity')}; "
                     f"degrees {d.get('degree_histogram')}; geometry: "
                     + ("explicit" if d.get("generator") == "explicit" else f"generator `{d.get('generator')}`"
                        + ("" if d.get("reproducible_from_generator") else ", edited after generation")))
        lines.append(f"- control `{d.get('control_model')}`, switch per site: {d.get('switch_per_site')}; sites that "
                     f"can gate / measure-reset / cool: {(d.get('sites_that_can') or {}).get('gate')} / "
                     f"{(d.get('sites_that_can') or {}).get('spam')} / {(d.get('sites_that_can') or {}).get('cool')}")
        if bud:
            lines.append(f"- budget: {bud.get('dacs')} DACs of {bud.get('max_dacs')}, {bud.get('junctions')} junctions "
                         f"of {bud.get('max_junctions')}" + (f" -- OVER: {'; '.join(bud['over'])}" if bud.get("over") else ""))
            for n in bud.get("notes") or []:
                lines.append(f"  - {n}")
        ph = d.get("physics") or {}
        if ph.get("added") or ph.get("ignored"):
            lines.append(f"- physics: added {ph.get('added') or 'none'}; ignored {ph.get('ignored') or 'none'}")
        f = res.get("fit")
        if f:
            lines.append(f"- board fit: the circuit has {f['circuit_qubits']} qubits; the device has {f['ion_slots']} ion "
                         f"slots ({f['spare_slots']} spare) and {f['gate_sites']} gate sites; {f['compiler']}")
        cn = res.get("named")
        if cn:
            parts = [f"same geometry: {'yes' if cn['same_geometry'] else 'no'}",
                     "control differs: " + ("; ".join(f"{x['field']} {x['shipped']!r} -> {x['design']!r}"
                                                      for x in cn["control_differs"]) or "no"),
                     f"zone types differ: {'yes' if cn['zone_types_differ'] else 'no'}",
                     f"the shipped device needs {cn['shipped_budget']['dacs']} DACs and {cn['shipped_budget']['junctions']} "
                     f"junctions on this board" + (" (over budget)" if cn["shipped_budget"]["over"] else "")]
            lines.append(f"- against `{cn['shipped']}` (the shipped device of this name): " + "; ".join(parts))
        for e in d.get("errors") or []:
            lines.append(f"- ERROR: {e}")
        lines.append("")
    ph = res.get("phys")
    if ph:
        lines.append("## Buildability (information only, not graded)")
        lines.append("")
        if ph.get("error"):
            lines.append(f"- could not derive the electrodes: {ph['error']}")
        else:
            lines.append(f"- `qccd.phys` under `{ph['technology']}`: {ph['polygons']} polygons; DRC clean: {ph['clean']}; "
                         f"violations by rule: {ph['violations_by_rule'] or 'none'}; refused shapes: {ph['refused']}"
                         + (f" (e.g. {ph['refused_examples'][0]})" if ph.get("refused_examples") else ""))
            if ph.get("disclosures"):
                lines.append(f"- disclosures: {'; '.join(ph['disclosures'])}")
            lines.append("- Many shipped designs are not DRC-clean under this preset; report this as an observation, "
                         "never as a failure.")
        lines.append("")
    tc = res.get("toolchain") or {}
    if tc:
        missing = [k for k, ok in (tc.get("python_extras") or {}).items() if not ok]
        lines.append("## What this machine could run")
        lines.append("")
        lines.append(f"- compiler (qccdc): {tc.get('qccdc') or 'not found'}; Lean checker (qcheck): "
                     f"{tc.get('qcheck') or 'not found'}; Python extras missing: {', '.join(missing) or 'none'}")
        lines.append("- Without qcheck the Lean stage is `unsupported`; without qiskit the certificate binding and "
                     "semantics cannot run; without stim/pymatching the LER stage cannot. None of these is a pass.")
        lines.append("")
    if res.get("reproduce"):
        lines.append("## Reproduce")
        lines.append("")
        lines.append("```")
        lines.extend(res["reproduce"])
        lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


def verdict_of(res: dict) -> tuple[str, int]:
    rows = res.get("rules") or []
    fails = [r["id"] for r in rows if r["review"] == "FAIL" and r["in_board_set"]]
    blocks = [r["id"] for r in rows if r["review"] == "BLOCKS"]
    dev_err = (res.get("device") or {}).get("errors") or []
    grades = res.get("grades") or {}
    g = grades.get("given") or grades.get("local")
    if res.get("input_errors"):
        return "the inputs could not be read: " + "; ".join(res["input_errors"]), 2
    parts = []
    if dev_err:
        parts.append("device: " + "; ".join(dev_err))
    if res.get("declared_rules_error"):
        parts.append("its rules.json cannot be read, so the rules stage fails (RULESET.INVALID)")
    fit = res.get("certificate")
    if fit and not fit["matches"]:
        parts.append(f"the certificate is for a {fit['certificate_qubits']}-qubit circuit, not the board's "
                     f"{fit['board_qubits']}-qubit one")
    bf = res.get("fit")
    if bf and bf["spare_slots"] < 0:
        parts.append(f"the device holds {bf['ion_slots']} ions but the circuit needs {bf['circuit_qubits']}")
    if fails:
        parts.append("fails " + ", ".join(fails))
    if blocks:
        parts.append("not checkable here: " + ", ".join(blocks))
    if g:
        el = g.get("eligibility") or {}
        src = "the given report" if grades.get("given") else "the local grade"
        if el.get("eligible"):
            return (f"eligible on this board ({src})" + (" -- but " + "; ".join(parts) if parts else "")), (1 if parts else 0)
        why = "; ".join(el.get("reasons") or [])
        return f"not eligible ({src}): " + "; ".join(parts + ([why] if why else [])), 1
    if res.get("device_only"):
        return ("device-level rules " + ("clear" if not parts else "NOT clear: " + "; ".join(parts))
                + "; every programme rule still needs a programme"), (1 if parts else 0)
    if parts:
        return "would not be eligible: " + "; ".join(parts), 1
    return ("every board rule the rules stage judges is clear; eligibility also needs the certificate "
            "stages" + (" and the LER stage" if (res.get("board") or {}).get("memory") else "")), 0


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", help="a submission bundle: a folder or a .zip")
    ap.add_argument("--report", help="an evaluation report already run (qccd grade --json, or the official /report)")
    ap.add_argument("--device", help="a device document (*.arch.json)")
    ap.add_argument("--program", help="the final hardware programme (*.tsir.json) to replay on the device")
    ap.add_argument("--certificate", help="the compiler's certificate (*.qcert.json), for R24/R25's circuit")
    ap.add_argument("--certified", help="the certified programme (*.tsir.json, the compiler's own output): with the "
                                        "other three files it makes a bundle, which is graded whole")
    ap.add_argument("--board", help="the board: its title, task name, or task@release")
    ap.add_argument("--rules", help="the rule set the entry declares: a shipped name or a .rules file")
    ap.add_argument("--profile", default="reference", choices=["reference", "draft"])
    ap.add_argument("--grade-timeout", type=float, default=1800.0, help="seconds before the grade is cancelled")
    ap.add_argument("--tech", default="eth_junction_2201.12579", help="technology preset for the electrode DRC")
    ap.add_argument("--no-phys", action="store_true", help="skip the electrode DRC (information only)")
    ap.add_argument("--no-claims", action="store_true", help="skip the advisory R9 check of the programme's claims")
    ap.add_argument("--no-recool", action="store_true", help="do not try re-cooling when a heat rule fails")
    ap.add_argument("--cases-limit", type=int, default=20000,
                    help="count each rule's cases only for programmes up to this many instructions")
    ap.add_argument("--out", help="folder for rule-check.md and rule-check.json (default: a new temporary folder)")
    ap.add_argument("--repo", help="the QCCD checkout (default: the one this skill is in)")
    ap.add_argument("--list-boards", action="store_true")
    a = ap.parse_args(argv)

    repo = repo_root(a.repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from qccd.ruleset import load
    from qccd.workspace.tasks import list_boards

    if a.list_boards:
        for b in list_boards():
            print(f"{b.id:28s} {b.manifest.get('rules') or 'standard':9s} {b.title}")
        return 0
    if not (a.bundle or a.report or a.device):
        ap.error("give --bundle, --report, or --device (with --program for the programme rules)")

    t0 = time.time()
    notes: list = []
    res: dict = {"kind": "qccd-design-review.result", "version": 2, "notes": notes, "input_errors": [],
                 "inputs": {k: getattr(a, k) for k in ("bundle", "report", "device", "program", "certificate",
                                                       "certified", "board", "rules")},
                 "grades": {"given": None, "local": None}}
    res["toolchain"] = _toolchain()
    out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="qccd-review-"))
    out.mkdir(parents=True, exist_ok=True)

    report = None
    bundle = None
    bdir = None
    tmp = tempfile.TemporaryDirectory()
    try:
        from qccd.workspace.bundle import BundleError, extract_archive, read_bundle, write_bundle
        if a.bundle:
            src = Path(a.bundle)
            try:
                if src.is_dir():
                    bdir = src
                else:
                    bdir = Path(tmp.name) / "bundle"
                    extract_archive(src.read_bytes(), bdir)
                bundle = read_bundle(bdir)
            except (BundleError, OSError, ValueError) as exc:
                res["input_errors"].append(f"the bundle cannot be read: {exc}")
        if a.report:
            try:
                report = _load_json(Path(a.report))
                report = report.get("report", report) if isinstance(report, dict) else report
            except (OSError, ValueError) as exc:
                res["input_errors"].append(f"the report cannot be read: {exc}")
        if res["input_errors"]:
            res["verdict"], code = verdict_of(res)
            print(render(res))
            return code

        task_id = ((bundle.manifest.get("task") or {}).get("id") if bundle else None) or \
            (((report or {}).get("task") or {}).get("id"))
        release = _board(a.board, task_id, notes)
        if a.board and task_id and release.id != task_id:
            notes.append(f"the bundle/report is for `{task_id}`, but the review was asked for `{release.id}`: "
                         "the grader would refuse it (BUNDLE.WRONG_TASK)")
        board_set_name = release.manifest.get("rules") or "standard"
        board_rs = load(board_set_name)
        res["board"] = {"id": release.id, "title": release.title, "rule_set": board_set_name,
                        "required_checks": release.required_checks, "memory": bool(release.manifest.get("qec")),
                        "rank_by": release.manifest.get("rank_by")}

        # the documents under review
        device_doc = bundle.docs["device"] if bundle else (_load_json(Path(a.device)) if a.device else None)
        prog_doc = bundle.docs.get("program") if bundle else (_load_json(Path(a.program)) if a.program else None)
        cert = bundle.docs.get("certificate") if bundle else (_load_json(Path(a.certificate)) if a.certificate else None)
        certified_doc = bundle.docs.get("certified_program") if bundle else (
            _load_json(Path(a.certified)) if a.certified else None)
        declared_doc, declared_rs = None, None
        from qccd.ruleset.declared import DeclaredRulesError
        try:
            if a.rules:
                declared_doc, declared_rs = _declared(a.rules, root=Path.cwd())
            elif bundle and bundle.docs.get("rules"):
                declared_doc, declared_rs = _declared(bundle.docs["rules"])
        except DeclaredRulesError as exc:
            # the grader reads it the same way: RULESET.INVALID, and the rules stage fails
            notes.append(f"the entry's own rule set cannot be read ({exc}): the grader fails the rules stage "
                         "(RULESET.INVALID). The board's rules are reviewed below; a bundle without rules.json is "
                         "judged by them")
            res["declared_rules_error"] = str(exc)
            declared_doc = bundle.docs.get("rules") if bundle else None
        res["rule_sets"] = {"board": board_set_name, "declared": declared_rs.name if declared_rs else None}

        # loose files that make a whole bundle are graded as one, as the workspace would build it
        if bundle is None and device_doc is not None and prog_doc is not None and cert and certified_doc:
            from qccd.workspace.tasks import board_device
            bdir = Path(tmp.name) / "assembled"
            bundle = write_bundle(bdir, task_id=release.id, task_digest=release.digest,
                                  device=board_device(device_doc, release), program=prog_doc,
                                  certified_program=certified_doc, certificate=cert, rules=declared_doc,
                                  provenance={"made_by": "qccd-design-review"})
            notes.append("the four files were assembled into a bundle (the device as entered on the board) and "
                         "graded whole")
        elif bundle is None and prog_doc is not None and cert and not certified_doc:
            notes.append("no certified programme (--certified, the compiler's own output) was given, so the four "
                         "files a bundle needs are not all here: the grading stages were not run")

        # the grade: a bundle is graded here; a report given is kept as given
        if bundle is not None:
            res["grades"]["local"] = _grade_view(grade_bundle(release, bdir, a.profile, a.grade_timeout),
                                                 "graded here with the reference evaluator")
        if report is not None:
            res["grades"]["given"] = _grade_view(report, "the report given")
        if res["grades"]["given"] and res["grades"]["local"]:
            res["grade_comparison"] = compare_grades(res["grades"]["given"], res["grades"]["local"],
                                                     release.manifest.get("numerical_policy"))
        decisive = res["grades"]["given"] or res["grades"]["local"]
        stages = stages_of(decisive) if decisive else None

        arch = None
        if device_doc is not None:
            info, arch = review_device(device_doc, release, notes)
            res["device"] = info
            if arch is not None:
                res["fit"] = board_fit(release, info)
                if res["fit"]["spare_slots"] < 0:
                    info["errors"].append(f"the circuit needs {res['fit']['circuit_qubits']} ions; the device holds "
                                          f"{res['fit']['ion_slots']}")
                try:
                    res["named"] = compare_named(device_doc, arch, release, repo)
                except Exception as exc:  # a comparison is context, never a verdict
                    notes.append(f"the comparison with the shipped device of this name failed: {exc}")
                if not a.no_phys:
                    try:
                        res["phys"] = phys_summary(arch, a.tech)
                    except Exception as exc:
                        res["phys"] = {"error": f"{type(exc).__name__}: {exc}"}
        prog = None
        if prog_doc is not None and arch is not None:
            from qccd.ir.tsir import TSIR, validate_program
            try:
                prog = TSIR.from_json(prog_doc)
                errs = validate_program(prog)
                if errs:
                    res["input_errors"].append("the programme is not a valid TSIR: " + "; ".join(map(str, list(errs)[:5])))
                    prog = None
            except (ValueError, KeyError, TypeError) as exc:
                res["input_errors"].append(f"the programme does not parse: {exc}")

        computes = None
        if stages:
            sem = (stages.get("semantics") or {}).get("status")
            computes = True if sem == "passed" else (False if sem == "failed" else None)
        if cert:
            fit = certificate_fit(cert, release)
            res["certificate"] = fit
            if not fit["matches"]:
                notes.append(f"the certificate maps {fit['certificate_qubits']} qubits, but the board's circuit "
                             f"declares {fit['board_qubits']}: it is about another circuit, so the board's "
                             "certificate_binding stage fails (the programme was compiled for a different task)")

        if prog is not None:
            vview = _verify_view(release, arch, prog, declared_doc, cert)
            lang, exe = _language(board_rs, release, arch, prog, cert, computes)
            dlang = _language(declared_rs, release, arch, prog, cert, computes)[0] if declared_rs is not None else None
            claims = None if a.no_claims else _claims(release, arch, prog)
            cases = rule_cases(board_rs, exe) if len(prog) <= a.cases_limit else None
            if cases is None:
                notes.append(f"the programme has {len(prog)} instructions, over --cases-limit {a.cases_limit}: cases "
                             "were not counted")
            res["rules"] = _rows(board_rs, declared_rs, vview, lang, dlang, release, res.get("device") or {},
                                 stages, False, claims, cases, res.get("certificate"))
            res["rules_stage"] = {"status": vview["status"], "coverage": vview["coverage"], "breaks": vview["breaks"]}
            if not cert:
                notes.append("no certificate was given: R24 and R25 have no circuit to judge against (they do not "
                             "apply), and nothing here speaks to R10")
            hot = [r["id"] for r in res["rules"] if r["id"] in ("R7", "R7c") and r["review"] == "FAIL"]
            if hot and not a.no_recool:
                if certified_doc:
                    rc = recool(release, arch, certified_doc, prog)
                    p = out / "recooled.tsir.json"
                    p.write_text(json.dumps(rc.pop("program"), indent=1), encoding="utf-8")
                    rc["path"] = str(p)
                    res["recool"] = rc
                    notes.append(f"{' and '.join(hot)} {'fails' if len(hot) == 1 else 'fail'}; re-cooling the certified "
                                 "programme under the board's physics "
                                 + ("clears " + ", ".join(x for x in hot if x in rc["clears"]) if any(x in rc["clears"] for x in hot)
                                    else "does not clear them") + f" (rules failing after: {', '.join(rc['failed_after']) or 'none'})")
                else:
                    notes.append(f"{' and '.join(hot)} {'fails' if len(hot) == 1 else 'fail'}; give the certified "
                                 "programme (--certified) to check whether re-cooling under the board's physics clears it")
        elif arch is not None and decisive is None:
            res["device_only"] = True
            res["rules"] = device_only_rules(board_rs, release, arch, res.get("device") or {})
            notes.append("no programme was given: only the rules a device decides alone were checked "
                         f"({', '.join(r for r in DEVICE_RULES if r in board_rs.ids)}) plus the budget; compile the "
                         "board's circuit onto it (`qccd submit --local --board ...` in a workspace) for the rest")
        elif decisive is not None:
            # a report alone: its rules stage, read as the grader wrote it
            st = stages.get("rules") or {}
            vview = {"detail": st.get("detail") or {}, "diagnostics": st.get("diagnostics") or [],
                     "status": st.get("status"), "coverage": st.get("coverage"), "breaks": []}
            res["rules"] = _rows(board_rs, declared_rs, vview, None, None, release, {}, stages, False, None, None)
            notes.append("read from the report alone: the rule language was not run (pass --bundle as well for "
                         "R0, R24 and R25)")

        res["verdict"], code = verdict_of(res)
        here = Path(__file__).resolve()
        cmd = ["python " + (str(here.relative_to(repo)) if here.is_relative_to(repo) else str(here))]
        for k in ("bundle", "report", "device", "program", "certificate", "certified", "board", "rules"):
            v = getattr(a, k)
            if v:
                cmd.append(f"  --{k} {json.dumps(v) if ' ' in v else v}")
        res["reproduce"] = [" \\\n".join(cmd)]
        res["seconds"] = round(time.time() - t0, 1)
        md = render(res)
        (out / "rule-check.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        (out / "rule-check.md").write_text(md, encoding="utf-8")
        print(md)
        print(f"(written: {out / 'rule-check.md'} and {out / 'rule-check.json'}; {res['seconds']} s)")
        return code
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
