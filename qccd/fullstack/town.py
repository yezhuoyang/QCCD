"""A FormalRV program on the gadget town -- the default way the full stack runs and is shown.

    .prog --prepare_qppm--> .qppm --prepare_qec--> .qec          FormalRV, pinned, unchanged
    .qec --qec2alg--> .alg                                         QCCD's own lowering, every line linked
    .alg  == the .prog's gate, in every branch?                     logic.contract (state vector)
    .alg --city--> GIR on the full-stack machine                   verified places, roads, decoder, archive
          place checks G1-G11, machine rules, sign-off             the gadget build (verified.build_algorithm)
    page: the gadget design tool, with the stack above its Program panel

and, when a machine profile is given, FormalRV's own `.sysprog` and VM verdict as the ABSTRACT
estimate beside the concrete one.  The flat Studio compile (`pipeline.run`) stays as the
ion-exact cross-check of slice 1; this is the architecture the user designed for scale.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import formalrv, layers, provenance, qec2alg
from .page import HOW

#: a .prog whose whole body is one of these gates is checked against it as a contract
PROG_GATES = {"CX": "CX", "CNOT": "CX", "CZ": "CZ", "H": "H", "S": "S", "X": "X", "Z": "Z", "Y": "Y",
              "T": "T", "SWAP": "SWAP", "CCX": "CCX", "CCZ": "CCZ"}


def _stage(st: layers.Stage) -> dict:
    return {"file": Path(st.path).name, "members": [[m, f, n] for m, f, n in st.members], "lines": st.lines}


def output_pairs(prog: layers.Stage, chain, low: qec2alg.Lowered, alg) -> list:
    """The lowered program's outcomes that are the .prog's measurement results, in execution
    order: the instructions whose .qec -> .qppm -> .prog chain ends at a `measure q ↦ c[i]`."""
    import re
    out = []
    for ins in alg.instructions:
        if not ins.cvar or ins.kind not in ("ppm", "read"):
            continue
        q = low.qec_of.get(ins.src_line or ins.line)
        p = chain.qec_to_qppm.get(q) if q else None
        g = chain.qppm_to_prog.get(p.stmt.line) if p else None
        if g and re.match(r"measure\s+.+↦\s*c\[", g.stmt.text.strip()):
            out.append(ins.cvar)
    return out


def distribution_check(prog: layers.Stage, chain, low: qec2alg.Lowered, params: dict,
                       inputs: dict | None = None) -> dict:
    """The lowered program's results have exactly the .prog's distribution (both from the
    basis state `inputs`, {qubit: 1}, the spec assumes)."""
    import re
    from ..gadget.algorithm import parse_algorithm
    from ..gadget.logic.merged import TooManyBranches, distribution
    from .progsim import NotSimulated, distribution as prog_distribution
    m = re.search(r"\bc:\s*([^\]]+)\]", prog.program.header.text)
    env = {k: int(v) for k, v in params.items()}
    try:
        recs = int(eval(m[1].replace("·", "*"), {"__builtins__": {}}, env)) if m else 0  # noqa: S307
        order: list = []
        pstates: dict = {}
        ref = prog_distribution(prog, env, recs, order, pstates, inputs=inputs)
    except (NotSimulated, Exception) as exc:                     # noqa: BLE001 -- say why
        return {"checked": False, "why": f"the .prog could not be simulated: {exc}"}
    alg = parse_algorithm(low.text)
    outs = output_pairs(prog, chain, low, alg)
    if len(order) != len(outs):
        return {"checked": False, "why": f"{len(order)} .prog measurements, {len(outs)} lowered ones"}
    try:
        got = distribution(alg, outs)
    except TooManyBranches as exc:
        return {"checked": False, "why": str(exc)}
    mine: dict = {}
    for k, p in got["distribution"].items():
        key = tuple(k[order.index(i)] if i in order else 0 for i in range(recs))
        mine[key] = mine.get(key, 0.0) + p
    keys = set(mine) | set(ref)
    worst = max((abs(mine.get(k, 0.0) - ref.get(k, 0.0)) for k in keys), default=0.0)
    # and, result by result, the state left on the qubits both programs still hold
    from ..gadget.logic.contract import State
    from ..gadget.logic.merged import _factor_out
    fid, compared = 1.0, 0
    for k, sts in got["states"].items():
        ck = tuple(k[order.index(i)] if i in order else 0 for i in range(recs))
        if ck not in pstates:
            continue
        n, amp = pstates[ck]
        for g in sts:
            ps = State()
            ps.names = [f"q{i}" for i in range(n)]
            ps.amp = dict(amp)
            gs = g.copy()
            common = set(ps.names) & set(gs.names)
            ok = all(_factor_out(ps, q) for q in [q for q in list(ps.names) if q not in common])
            ok = ok and all(_factor_out(gs, q) for q in [q for q in list(gs.names) if q not in common])
            fid = min(fid, ps.overlap(gs) if ok else 0.0)
            compared += 1
    return {"checked": True, "kind": "distribution", "passed": worst < 1e-6 and fid > 1 - 1e-6,
            "records": recs, "outcomes": outs, "worst_difference": worst,
            "states_compared": compared, "worst_state_fidelity": round(fid, 9),
            "prog": {"".join(map(str, k)): p for k, p in ref.items()},
            "gadget": {"".join(map(str, k)): round(p, 9) for k, p in sorted(mine.items())},
            "peak_branches": got["peak_branches"],
            "method": "the .prog simulated as written; the gadget program simulated with branches "
                      "merged once nothing reads their outcomes (exact)"}


def semantic_check(prog: layers.Stage, low: qec2alg.Lowered, params: dict, chain=None,
                   inputs: dict | None = None) -> dict:
    """What the lowered gadget program computes, against the .prog: a single gate as its
    contract (every branch, state vector); otherwise the exact distribution of its results
    against the .prog's own; and an outcome spec `Pr[ c[0,K) = E ] = 1` symbolically where the
    program is Clifford.  Anything else is said to be unchecked."""
    import re
    rep = contract_check(prog, low)
    if rep.get("checked"):
        return rep
    if chain is not None:
        rep = distribution_check(prog, chain, low, params, inputs=inputs)
        if rep.get("checked"):
            return rep
    spec = next((u for u in prog.units if u.kind == "spec" and prog.program and u.name == prog.program.name), None)
    text = " ".join(s.text for s in spec.statements()) if spec else ""
    m = re.search(r"Pr\[\s*c\[0,\s*([^)\]]+)\)\s*=\s*([^\]]+?)\s*\]\s*=\s*1", text)
    if not m:
        return {"checked": False, "why": rep.get("why", "") + "; and the spec states no deterministic outcome"}
    from ..gadget.algorithm import parse_algorithm
    from .semantics import NotClifford, check_outcome_spec
    from .sysprog import evaluate
    env = {k: int(v) for k, v in params.items()}
    try:
        width, value = evaluate(m[1], env), evaluate(m[2], env)
    except Exception as exc:                             # noqa: BLE001 -- say why, do not guess
        return {"checked": False, "why": f"the outcome spec names {exc}"}
    data = [f"q{i}" for i in range(width)]
    try:
        out = check_outcome_spec(parse_algorithm(low.text), data, value, width)
    except NotClifford as exc:
        return {"checked": False, "why": f"not Clifford: {exc}"}
    out["spec_text"] = m[0]
    return out


def contract_check(prog: layers.Stage, low: qec2alg.Lowered) -> dict:
    """Is the lowered gadget program exactly the .prog's gate, in every branch?"""
    import re
    from ..gadget.algorithm import Gate, parse_algorithm
    from ..gadget.logic.contract import check_gate
    body = prog.program.body if prog.program else []
    if len(body) != 1:
        return {"checked": False, "why": "the .prog is not a single gate"}
    m = re.fullmatch(r"(\w+)\(([^)]*)\)", body[0].text.strip())
    if not m or m[1].upper() not in PROG_GATES:
        return {"checked": False, "why": f"no contract for `{body[0].text.strip()}`"}
    U = PROG_GATES[m[1].upper()]
    qubits = [f"q{int(a)}" for a in m[2].split(",")]
    alg = parse_algorithm(low.text)
    inputs = {f"prep {q} Z" for q in qubits}
    lines = [(i.line, i.text) for i in alg.instructions if i.text.strip() not in inputs]
    rep = check_gate(Gate("prog", qubits, lines, U, 0, ""), {})
    br = rep.get("branches") or []
    out = {"checked": True, "contract": U, "on": qubits, "passed": bool(rep.get("passed")),
           "branches": len(br), "worst_fidelity": min((b.get("fidelity", 0) for b in br), default=None)}
    # the control: the same program is NOT some other gate
    other = "CZ" if U != "CZ" else "CX"
    if len(qubits) == 2:
        out["control"] = {"contract": other, "passed": bool(check_gate(Gate("prog", qubits, lines, other, 0, ""),
                                                                         {}).get("passed"))}
    return out


def _commit() -> str:
    try:
        return formalrv.commit()[:10]
    except formalrv.ToolchainError:
        return formalrv.PIN_COMMIT[:10] + " (pinned; clone absent)"


def stack_payload(prog, qppm, qec, chain, low: qec2alg.Lowered, foot: str) -> dict:
    absorbed_into: dict = {}
    for ql, alg_lines in low.linked.items():
        for al in alg_lines:
            if low.qec_of.get(al) != ql:
                absorbed_into.setdefault(al, []).append(ql)
    return {
        "layers": {"prog": _stage(prog), "qppm": _stage(qppm), "qec": _stage(qec)},
        "alg": {al: [ql, "lowered"] for al, ql in low.qec_of.items()},
        "qec_absorbed_into": absorbed_into,
        "qec_links": {ln: [l.stmt.line, l.how] for ln, l in chain.qec_to_qppm.items()},
        "qppm_links": {ln: [l.stmt.line, l.how] for ln, l in chain.qppm_to_prog.items()},
        "input_lines": [al for al, r in low.role_of.items() if r == "input"],
        "how": {**HOW, "lowered": "QCCD's own lowering of this .qec statement into the gadget program the machine runs"},
        "formalrv": _commit(),
        "foot": foot,
    }


def run(prog: Path, *, out: Path, code: str = "surface", distance: int = 3, specialize: dict | None = None,
        machine: Path | None = None, sysspec: Path | None = None, title: str = "",
        floorplan: str = "fullstack", fold: bool = False, inputs: dict | None = None,
        leaves=None, cache_dir="out/gadgets/cache", studio_dir=None, site_dir=None) -> dict:
    """`fold`: schedule every gate once per context and keep the calls folded
    (`qccd.gadget.fold`); the page plays it a window at a time.  `inputs` {qubit: 1}: the
    basis state the program is given (its spec's `assumes`), |0…0⟩ by default.

    The page goes to `site_dir` (default `out/gadgets/`); the website builds every run into its
    own section with one leaf library (`leaves`) and one folder of studio pages (`studio_dir`)."""
    from ..gadget.verified import build_algorithm
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    steps = []

    def step(name, fn):
        t = time.time()
        r = fn()
        steps.append({"step": name, "s": round(time.time() - t, 3)})
        return r

    stem = Path(prog).name.split(".")[0]
    qppm_p, qec_p, sys_p = out / f"{stem}.qppm", out / f"{stem}.qec", out / f"{stem}.sysprog"
    for p in (qppm_p, qec_p, sys_p):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    step("prog -> qppm (FormalRV)", lambda: formalrv.prog_to_qppm(prog, qppm_p))
    step("qppm -> qec (FormalRV)", lambda: formalrv.qppm_to_qec(qppm_p, qec_p, code, distance, specialize))
    abstract = None
    if machine and sysspec:
        step("qec -> sysprog (FormalRV)", lambda: formalrv.qec_to_sys(qec_p, machine, sysspec, sys_p))
        vm = step("FormalRV VM", lambda: formalrv.vm(sys_p))
        abstract = {"vm_status": vm.get("status"), "runtime_ns": vm.get("runtime_upper_bound_ns"),
                    "peak_qubits": vm.get("peak_qubits_upper_bound")}
    st_prog, st_qppm, st_qec = layers.read(prog, "prog"), layers.read(qppm_p, "qppm"), layers.read(qec_p, "qec")
    from . import structure
    templates = step("FormalRV's gate templates", lambda: structure.learn_templates())
    chain = step("link .qec -> .qppm -> .prog", lambda: structure.link(st_prog, st_qppm, st_qec, templates,
                                                                     prog_path=Path(prog), specialize=specialize))
    low = step("lower .qec -> .alg (QCCD)", lambda: qec2alg.lower(
        st_qec, title=title or f"{stem}: a FormalRV program on the gadget town", floorplan=floorplan,
        ones=[q for q, v in (inputs or {}).items() if v]))
    alg_p = out / f"{stem}.alg"
    alg_p.write_text(low.text, encoding="utf-8")
    contract = step("what the .alg computes, against the .prog", lambda: semantic_check(st_prog, low, specialize or {}, chain,
                                                                                         inputs=inputs))
    a_txt = (f"FormalRV's VM on its abstract machine: {abstract['runtime_ns'] / 1e6:.1f} ms, "
             f"{abstract['peak_qubits']} qubits. " if abstract and abstract.get("runtime_ns") else "")
    if contract.get("passed") and contract.get("kind") == "distribution":
        c_txt = (f"The gadget program's results have exactly the .prog's distribution "
                 f"({', '.join(f'{k}: {v:.3f}' for k, v in contract['prog'].items())}; every branch, "
                 f"merged as outcomes stop being read: at most {contract['peak_branches']} live). ")
    elif contract.get("passed") and contract.get("kind") == "outcome":
        c_txt = (f"The gadget program gives {contract['spec']} in every branch "
                 f"({contract['random_outcomes']} random outcomes; {contract['method']}). ")
    elif contract.get("passed"):
        c_txt = (f"The gadget program is exactly {contract['contract']} in all {contract['branches']} branches "
                 f"(and is not {contract['control']['contract']}). ")
    else:
        c_txt = f"Semantics: {contract.get('why', 'FAILED')}. "
    foot = (a_txt + c_txt + "A badge says how a link between two layers was made; hover it. "
            "Click a line to jump to the first instruction that carries it.")
    payload = stack_payload(st_prog, st_qppm, st_qec, chain, low, foot)
    site = Path(site_dir).resolve() if site_dir else out / "gadgets"
    if fold:
        from ..gadget.fold import build_folded
        r = step("schedule folded and check on the town", lambda: build_folded(
            alg_p, out_dir=site, stack=payload, log=None, leaves=leaves, cache_dir=cache_dir))
        fs = r["stats"]
        r["stats"] = {"makespan_ms": fs["makespan_ms"], "ions": fs["ions"], "events": None,
                      "instructions": fs["instructions"], "refused": fs["refused"],
                      "rules": fs["rules"], "signed_off": None,
                      "macros": fs["macros"], "reused": fs["reused"]}
        r["checks"] = r["checks"] or {"passed": []}
    else:
        r = step("schedule, check and sign off on the town", lambda: build_algorithm(
            alg_p, out_dir=site, stack=payload, log=None, leaves=leaves, cache_dir=cache_dir,
            studio_dir=studio_dir,
            semantics=contract if contract.get("checked") and contract.get("kind") == "distribution" else None))
    s = r["stats"]
    reach = structure.reachable(st_qec)
    qec_lines = [s.line for u in st_qec.units if u.kind == "program" or (u.kind == "gadget" and u.name in reach)
                 for s in u.statements() if s.text.strip() != "skip"]
    result = {
        "program": stem, "formalrv": formalrv.provenance(), "fold": fold, "floorplan": floorplan,
        "alg": low.text, "qec_statements": len(qec_lines),
        "linked": sum(1 for q in qec_lines if q in low.linked),
        "linked_up": sum(1 for q in qec_lines if q in chain.qec_to_qppm),
        "absorbed": len(low.absorbed), "contract": contract, "abstract": abstract,
        "concrete": {"makespan_ms": s["makespan_ms"], "ions": s["ions"], "events": s["events"],
                     "instructions": s["instructions"], "refused": s["refused"]},
        "checks": {"places": {"passed": len(r["checks"]["passed"]), "failed": r["checks"].get("failed")},
                   "machine_rules": s["rules"], "signed_off": s["signed_off"],
                   # a folded run too large to expand is checked view by view and by composition
                   "checked": (r["checks"].get("metrics") or {}).get("checked"),
                   "views": (r["checks"].get("metrics") or {}).get("views"),
                   "composition_problems": (r["checks"].get("metrics") or {}).get("composition_problems")},
        "page": str(site / "index.html"), "steps": steps, "total_s": round(time.time() - t0, 1),
    }
    (out / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
