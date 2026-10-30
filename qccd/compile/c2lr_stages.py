"""The independent re-check of the C2LR router's native stages (docs/c2lr.md section 6d).

`qccdc check-stages` re-checks the saved evidence of qccdc's own seven native stages
(Compiler/ocaml/lib/stages.ml) and cannot read a C2LR programme: a swap-or-stay step names no
routes -- the device's matching does -- and its exchanges are the very opposing junction
transits a plain move may not make.  This is the same re-check for the router
(`qccd.compile.c2lr_router`, engine `c2lr-router`).  It reads the saved stages and never runs
the router; each boundary is decided from what can be established without it:

  circuit normalization   the source as qccdc parses it (`qccdc parse`), op for op, and each
                          normalized op's unitary against its source op's (Compiler/bridge/unitary.py)
  dependency analysis     every wire's precedence: qubits, the classical bits measurements
                          write, barrier fences
  initial placement       a bijection of qubits onto crystals, one crystal at rest in each leg,
                          exactly as the executable's `init` places them
  interaction placement   each op at a site whose zone can do it, its operands in order and
                          within the site's capacity
  transport synthesis     every swap-or-stay step re-derived from the device's matching by
                          `qccd.verify.c2lr` -- the replay that judges C1-C9 -- with nothing it
                          reports broken, and every crystal at rest in a leg after it
  native gate lowering    each op's pulses multiplied out (unitary.py) equal to its gate up to
                          one phase, with its operands where the steps left them
  broadcast packing       every ion's ordered pulses unchanged by the grouping, and each
                          command's provenance exactly the ops whose pulses it carries

and then binds the evidence to the executable: its gates, readouts and resets are exactly the
packed commands, its motions exactly the checked steps, every packed command addresses its
crystals where the executable's own replay (`qccd.verify.replay`) puts them, and every source
wire's operations run in source order.  Any failure rejects the whole manifest, as
`check-stages` does.

    python Compiler/bridge/check_c2lr_stages.py PREFIX.stages.json --qasm Q --arch A
        --expanded-arch E --program PREFIX.native.tsir.json --qccdc QCCDC

prints the verdict as JSON (`accepted`, and one `stage_checks` entry per saved stage, which the
staged driver records); exit 0 only when accepted.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..arch import Architecture
from ..arch.c2lr import geometry, is_c2lr
from ..ir.tsir import TSIR, Instruction, validate_program

ENGINE = "c2lr-router"
SCHEMA = "qccd.compiler.stages.v1"
METHOD = "qccd.compile.c2lr_stages.check_manifest"
NATIVE = ("circuit_normalization", "dependency_analysis", "initial_placement",
          "interaction_placement", "transport_synthesis", "native_gate_lowering",
          "broadcast_packing")
COVERAGE = {
    "circuit_normalization": ["the source op for op as qccdc parses it",
                              "each normalized op's unitary against its source op's"],
    "dependency_analysis": ["independent replay of qubit and classical-bit precedence and barrier fences"],
    "initial_placement": ["complete bijective qubit/crystal mapping",
                          "one crystal at rest in each occupied leg", "the executable's init placement"],
    "interaction_placement": ["each op's site exists and its zone can gate or measure",
                              "ordered operands and site capacity"],
    "transport_synthesis": ["every swap-or-stay step re-derived from the device's matching (qccd.verify.c2lr)",
                            "C1, C2 and C6 as the replay judges them",
                            "every crystal at rest in a leg after each step"],
    "native_gate_lowering": ["each op's pulse product equal to its gate up to phase (Compiler/bridge/unitary.py)",
                             "operands at the assigned site"],
    "broadcast_packing": ["per-ion ordered atomic effects preserved by the grouping",
                          "command provenance equal to the ops whose pulses it carries"],
}
_BRIDGE = Path(__file__).resolve().parents[2] / "Compiler" / "bridge"


class Failed(ValueError):
    """The saved evidence does not establish a boundary, or disagrees with the executable."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise Failed(message)


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _unitary():
    if str(_BRIDGE) not in sys.path:
        sys.path.insert(0, str(_BRIDGE))
    import unitary
    return unitary


# ------------------------------------------------------------------ the evidence's values

def _op(j: Any) -> dict:
    """A circuit op, strictly: `i`, `name`, `qubits`, `clbits`, `params` and nothing else that
    changes what it does (a condition is refused: the router compiles none)."""
    _require(isinstance(j, Mapping), "an op must be an object")
    extra = set(j) - {"i", "name", "qubits", "clbits", "params", "line", "source_line"}
    _require(not extra, f"op {j.get('i')} carries {sorted(extra)}, which the router never writes")
    ints = lambda xs: isinstance(xs, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in xs)
    _require(isinstance(j.get("i"), int) and isinstance(j.get("name"), str), "op without index or name")
    _require(ints(j.get("qubits")) and ints(j.get("clbits", [])), f"op {j['i']}: bad operands")
    params = j.get("params", [])
    _require(isinstance(params, list) and all(isinstance(p, (int, float)) and not isinstance(p, bool)
                                              for p in params), f"op {j['i']}: bad params")
    return {"i": j["i"], "name": j["name"], "qubits": list(j["qubits"]),
            "clbits": list(j.get("clbits", [])), "params": [float(p) for p in params]}


def _positions(j: Any) -> dict[str, str]:
    _require(isinstance(j, Mapping) and all(isinstance(k, str) and isinstance(v, str) for k, v in j.items()),
             "positions must map crystals to sites")
    return dict(j)


def source_circuit(qccdc, circuit_path) -> dict:
    """The source circuit as qccdc parses it: the parse the router's normalization is held to."""
    _require(qccdc is not None, "no qccdc to parse the source circuit with")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "parsed.json"
        got = subprocess.run([str(qccdc), "parse", str(circuit_path), "-o", str(out)],
                             capture_output=True, text=True, timeout=120)
        _require(got.returncode == 0 and out.exists(),
                 f"qccdc could not parse the source: {(got.stderr or got.stdout)[-400:]}")
        doc = json.loads(out.read_text(encoding="utf-8"))
    # every field but the source line, so a condition (`cond`) is seen -- and refused -- not dropped
    return {"n_qubits": int(doc["n_qubits"]), "n_clbits": int(doc["n_clbits"]),
            "ops": [_op({k: v for k, v in o.items() if k not in ("line", "source_line")})
                    for o in doc["ops"]]}


# ------------------------------------------------------------------ the boundaries

def _unitary_of(ops: Sequence[dict], qubits: Sequence[int]) -> Any:
    """The product of `ops` (in order) on the local register `qubits`."""
    u = _unitary()
    import numpy as np
    local = {q: k for k, q in enumerate(qubits)}
    U = np.eye(1 << len(qubits), dtype=complex)
    for o in ops:
        got = u.gate_matrix(o["name"], o["params"])
        _require(got is not None and got[1] == len(o["qubits"]), f"op {o['i']} ({o['name']}) has no defining unitary")
        U = u.apply_gate(U, got[0], [local[q] for q in o["qubits"]], len(qubits))
    return U


def check_normalization(source: dict, stage: Mapping) -> dict:
    inp, out, wit = stage["input"], stage["output"], stage["witness"]
    _require(inp.get("n_qubits") == source["n_qubits"] and inp.get("n_clbits") == source["n_clbits"],
             "normalization input declares another register than the source")
    ops_in = [_op(o) for o in inp["ops"]]
    _require(ops_in == source["ops"], "normalization input differs from the source as qccdc parses it")
    norm = [_op(o) for o in out["ops"]]
    _require(out.get("n_qubits") == source["n_qubits"] and out.get("n_clbits") == source["n_clbits"],
             "normalization output changes the register")
    _require([o["i"] for o in norm] == list(range(len(norm))), "normalized ops are not numbered in order")
    src = wit["source_op_by_normalized_op"]
    _require(isinstance(src, list) and len(src) == len(norm), "normalization witness does not cover every op")
    by_src: dict[int, list[dict]] = {}
    last = -1
    for o, s in zip(norm, src):
        _require(isinstance(s, int) and 0 <= s < len(ops_in), f"normalized op {o['i']} names no source op")
        _require(s >= last, "normalization reorders source operations")
        last = s
        by_src.setdefault(s, []).append(o)
    _require(sorted(by_src) == list(range(len(ops_in))), "normalization drops a source operation")
    for s, group in by_src.items():
        so = ops_in[s]
        if so["name"] in ("measure", "reset", "barrier") or len(group) == 1 and group[0]["name"] == so["name"]:
            _require(len(group) == 1 and {**group[0], "i": so["i"]} == so,
                     f"source op {s} ({so['name']}) is changed by normalization")
            continue
        u = _unitary()
        _require(all(set(o["qubits"]) <= set(so["qubits"]) for o in group),
                 f"the normalization of op {s} touches other qubits")
        ok, _, worst = u.same_up_to_phase(_unitary_of(group, so["qubits"]), _unitary_of([so], so["qubits"]))
        _require(ok, f"the normalization of op {s} ({so['name']}) differs from it (worst entry {worst:.1e})")
    return {"n_qubits": source["n_qubits"], "n_clbits": source["n_clbits"], "ops": norm,
            "source": [int(s) for s in src]}


def _wires(c: Mapping, o: Mapping) -> list[int]:
    return sorted(set(o["qubits"]) | {c["n_qubits"] + b for b in o["clbits"]})


def check_dependencies(c: dict, stage: Mapping) -> None:
    inp = stage["input"]
    _require(set(inp) == {"n_qubits", "n_clbits", "ops"} and inp["n_qubits"] == c["n_qubits"]
             and inp["n_clbits"] == c["n_clbits"] and [_op(o) for o in inp["ops"]] == c["ops"],
             "dependency input differs from the normalized circuit")
    lay = stage["output"]["layers"]
    _require(isinstance(lay, list) and len(lay) == len(c["ops"]), "dependency layers do not cover every op")
    last: dict[int, int] = {}
    for o, k in zip(c["ops"], lay):
        _require(isinstance(k, int) and k >= (-1 if o["name"] == "barrier" else 0), f"op {o['i']}: bad layer")
        for w in _wires(c, o):
            if w in last:
                before = lay[last[w]]
                _require(k >= before if o["name"] == "barrier" else k > before,
                         f"dependency {last[w]} -> {o['i']} violated")
            last[w] = o["i"]


# ------------------------------------------------------------------ the whole manifest

def check_manifest(doc: Mapping, *, circuit_path, arch_path, expanded_arch_path, program_path,
                   qccdc) -> dict:
    """The verdict on a saved stage manifest, or `Failed` naming the first thing wrong."""
    from ..cost.models import corrected_model
    from ..verify.c2lr import C2LRTracker, StepError
    from ..verify.replay import replay
    u = _unitary()

    _require(doc.get("schema") == SCHEMA, "unknown stage schema")
    _require(doc.get("engine") == ENGINE, f"engine {doc.get('engine')!r} is not the C2LR router")
    _require(doc.get("accepted") is True, "stage manifest is an unaccepted candidate")
    b = doc.get("bindings") or {}
    for key, path in (("circuit_sha256", circuit_path), ("architecture_sha256", arch_path),
                      ("expanded_architecture_sha256", expanded_arch_path), ("output_tsir_sha256", program_path)):
        _require(b.get(key) == _sha(path), f"stage binding mismatch: {key}")
    arch = Architecture.from_json(json.loads(Path(arch_path).read_text(encoding="utf-8")))
    _require(is_c2lr(arch), f"{arch.name} does not declare C2LR control")
    legs = set(geometry(arch).legs)
    program = TSIR.from_json(json.loads(Path(program_path).read_text(encoding="utf-8")))
    _require(not validate_program(program), "serialized executable has invalid TSIR structure")
    source = source_circuit(qccdc, circuit_path)

    # the executable, replayed on its own: where every crystal stands before each command
    before: dict[int, dict[str, str]] = {}

    def on_cycle(view) -> None:
        before.setdefault(view.instr.id, dict(view.pos_before))
    try:
        replay(program, arch, corrected_model(), check_rules=False, on_cycle=on_cycle)
    except Exception as exc:          # a ReplayError, a StepError: the executable is not playable
        raise Failed(f"the executable does not replay: {exc}") from None
    inits = [i for i in program.instructions if i.type == "init"]
    _require(len(inits) == 1 and program.instructions[0].type == "init",
             "the executable needs exactly one initial placement, first")

    stages = doc.get("stages")
    _require(isinstance(stages, list) and stages, "the manifest saves no stages")
    c = None
    l2i: list[str] = []
    pos: dict[str, str] = {}
    trk = None
    sited = native = None
    sited_ops: list[dict] = []
    realized: list[int] = []
    packed: list[dict] = []
    steps: list[tuple] = []
    site_of: dict[int, dict[str, str]] = {}       # packed command id -> crystal -> its checked site
    seen: set[str] = set()
    for k, st in enumerate(stages):
        _require(isinstance(st, Mapping) and st.get("id") in NATIVE, f"stage {k} is no native stage")
        sid = st["id"]
        _require(all(key in st for key in ("input", "output", "witness")), f"stage {k} lacks its snapshots")
        if sid == "circuit_normalization":
            _require(not seen, "normalization is not the first stage")
            c = check_normalization(source, st)
        elif sid == "dependency_analysis":
            _require(seen == {"circuit_normalization"}, "dependency analysis out of order")
            check_dependencies(c, st)
        elif sid == "initial_placement":
            _require(seen == {"circuit_normalization", "dependency_analysis"}, "placement out of order")
            l2i = st["witness"]["logical_to_ion"]
            placement = _positions(st["output"]["placement"])
            _require(isinstance(l2i, list) and len(l2i) == c["n_qubits"] and len(set(l2i)) == len(l2i),
                     "placement is not a bijection of qubits onto crystals")
            _require(set(placement) == set(l2i), "placement has missing or extra crystals")
            load: dict[str, int] = {}
            for ion, s in placement.items():
                _require(s in legs, f"{ion} starts at {s}, which is no leg")
                load[s] = load.get(s, 0) + 1
                _require(load[s] <= 1, f"two crystals start in the leg {s}")
            _require(dict(inits[0].placement) == placement, "checked placement differs from the executable's init")
            pos = dict(placement)
            trk = C2LRTracker(arch)
            trk.init({}, pos)
        else:
            _require({"circuit_normalization", "dependency_analysis", "initial_placement"} <= seen,
                     f"{sid} before the circuit is placed")
            if sid == "transport_synthesis":
                _require(_positions(st["input"]["positions"]) == pos, "transport state does not follow earlier stages")
                for n, s in enumerate(st["output"]["steps"]):
                    ins = Instruction(type="simd", id=-1 - len(steps), cls=s["class"], mode="inter",
                                      template=dict(s["template"]))
                    try:
                        step, after = trk.expand(ins, pos)
                    except StepError as exc:
                        raise Failed(f"transport step {n}: {exc}") from None
                    broken = list(step.c1) + list(step.c2) + list(step.c6)
                    _require(not broken, f"transport step {n}: {broken[0] if broken else ''}")
                    _require(all(v in legs for v in after.values()), f"transport step {n} leaves a crystal off a leg")
                    pos = after
                    steps.append((s["class"], json.dumps(s["template"], sort_keys=True)))
                _require(_positions(st["output"]["final_positions"]) == pos, "transport final snapshot differs from replay")
            elif sid == "interaction_placement":
                _require(_positions(st["input"]["positions"]) == pos, "interaction state does not follow earlier stages")
                ops = [_op(o) for o in st["input"]["operations"]]
                _require(all(o in c["ops"] for o in ops), "interaction input invents an operation")
                out = st["output"]
                _require(isinstance(out, list) and [_op(e["operation"]) for e in out] == ops,
                         "interaction output is not its input's operations, in order")
                claimed: set[str] = set()
                for e, o in zip(out, ops):
                    a = e["assignment"]
                    _require(o["name"] != "barrier" and a is not None, f"op {o['i']} has no execution site")
                    s, ions = a["site"], list(a["ions"])
                    _require(s in arch.device.nodes and arch.device.nodes[s].kind == "site", f"op {o['i']}: unknown site {s}")
                    spam = o["name"] in ("measure", "reset")
                    _require(arch.can(s, "spam" if spam else "gate"), f"op {o['i']}: {s} cannot {'measure' if spam else 'gate'}")
                    _require(ions == [l2i[q] for q in o["qubits"]], f"op {o['i']}: wrong ordered operands")
                    _require(len(ions) <= arch.device.nodes[s].capacity, f"op {o['i']}: operands exceed {s}'s capacity")
                    if not spam and len(ions) > 1:
                        _require(s not in claimed, f"two simultaneous gates share {s}")
                        claimed.add(s)
                sited, sited_ops, native = out, ops, None
            elif sid == "native_gate_lowering":
                _require(sited is not None and st["input"] == sited, "native input does not match the checked interactions")
                out = st["output"]
                _require(isinstance(out, list) and len(out) == len(sited), "native lowering drops or adds operations")
                for e, s, o in zip(out, sited, sited_ops):
                    _require(_op(e["operation"]) == o and e["site"] == s["assignment"]["site"]
                             and list(e["ions"]) == s["assignment"]["ions"],
                             "native lowering reordered operations or changed operands or sites")
                    ions = list(e["ions"])
                    for ion in ions:
                        _require(pos.get(ion) == e["site"], f"op {o['i']}: {ion} has not arrived at {e['site']}")
                    ps = e["pulses"]
                    if o["name"] in ("measure", "reset"):
                        _require(ps == [], f"native lowering changed the non-unitary op {o['i']}")
                    else:
                        import numpy as np
                        local = {ion: k for k, ion in enumerate(ions)}
                        U = np.eye(1 << len(ions), dtype=complex)
                        for p in ps:
                            g, on, par = p["gate"], list(p["ions"]), [float(x) for x in p["params"]]
                            _require(all(x in local for x in on), f"op {o['i']}: a pulse names a foreign ion")
                            if g == "R" and len(on) == 1 and len(par) == 2:
                                m = u.r_pulse(*par)
                            elif g == "VZ" and len(on) == 1 and len(par) == 1:
                                m = u.vz(par[0])
                            elif g == "MS" and len(on) == 2 and len(par) == 1:
                                m = u.ms(par[0])
                            else:
                                raise Failed(f"op {o['i']}: an invalid native pulse {g}")
                            U = u.apply_gate(U, m, [local[x] for x in on], len(ions))
                        ok, _, worst = u.same_up_to_phase(U, _unitary_of([o], o["qubits"]))
                        _require(ok, f"native pulse semantics differ for op {o['i']} ({o['name']}), worst entry {worst:.1e}")
                    realized.append(o["i"])
                native, sited = out, None
            else:   # broadcast_packing
                _require(native is not None and st["input"] == native, "packing input does not match the checked native program")
                cmds = [Instruction.from_json(j) for j in st["output"]]
                _check_packing(native, cmds)
                sites = {}
                for e in native:
                    for ion in e["ions"]:
                        _require(sites.get(ion, e["site"]) == e["site"], "packing moves an ion between sites in one batch")
                        sites[ion] = e["site"]
                for ins in cmds:
                    site_of[ins.id] = {ion: sites[ion] for ion in _addressed(ins)}
                packed += [i.to_json() for i in cmds]
                native = None
        seen.add(sid)
    _require(seen == set(NATIVE), "missing stages: " + ", ".join(s for s in NATIVE if s not in seen))
    want = sorted(o["i"] for o in c["ops"] if o["name"] != "barrier")
    _require(sorted(realized) == want, "checked native stages omit or duplicate source operations")

    # the evidence against the executable
    commands = [i for i in program.instructions if i.type in ("gate", "measure", "reset")]
    _require([i.to_json() for i in commands] == packed, "packed stage outputs differ from the executable's gates, readout and resets")
    motions = [(i.cls, json.dumps(dict(i.template or {}), sort_keys=True)) for i in program.instructions if i.type == "simd"]
    _require(all(i.template and not i.participants for i in program.instructions if i.type == "simd"),
             "the executable moves a crystal by something other than a swap-or-stay template")
    _require(motions == steps, "checked transport differs from the executable's motion")
    for ins in commands:
        at = before.get(ins.id)
        _require(at is not None, f"command {ins.id} was not replayed")
        for ion, s in site_of[ins.id].items():
            _require(at.get(ion) == s, f"command {ins.id} addresses {ion} at {at.get(ion)}, not its checked site {s}")
    intervals: dict[int, tuple[int, int]] = {}
    for k, ins in enumerate(program.instructions):
        if ins.type in ("gate", "measure", "reset"):
            for v in ins.meta.get("op", ()):
                first = intervals.get(v, (k, k))[0]
                intervals[v] = (first, k)
    last = {w: -1 for w in range(c["n_qubits"] + c["n_clbits"])}
    for o in c["ops"]:
        ws = _wires(c, o)
        if o["name"] == "barrier":
            fence = max((last[w] for w in ws), default=-1)
            for w in ws:
                last[w] = fence
            continue
        _require(o["i"] in intervals, f"the executable omits op {o['i']}")
        first, final = intervals[o["i"]]
        for w in ws:
            _require(first > last[w], f"the executable runs op {o['i']} before an op it depends on")
            last[w] = final
    return {"schema": "qccd.c2lr.stage_check.v1", "accepted": True, "engine": ENGINE, "checker": METHOD,
            "stage_checks": [{"status": "passed", "method": METHOD, "coverage": COVERAGE[s["id"]]} for s in stages],
            "checked": {"stages": len(stages), "steps": len(steps), "commands": len(commands),
                        "operations": len(want)}}


def _addressed(ins: Instruction) -> list[str]:
    return list(ins.ions) + [x for p in ins.pairs for x in p]


def _check_packing(native: Sequence[Mapping], cmds: Sequence[Instruction]) -> None:
    """Compare per-ion ordered atomic effects, flattening both sides independently (as
    stages.ml's `check_packing`): a swapped angle, a missing frame, a reversed MS pair or a
    reordered same-ion pulse cannot pass."""
    expected: dict[str, list] = {}
    owners: dict[str, list[int]] = {}
    for e in native:
        oid = e["operation"]["i"]

        def expect(gate, ions, params):
            for i in ions:
                expected.setdefault(i, []).append((gate, tuple(ions), tuple(params)))
                owners.setdefault(i, []).append(oid)
        name = e["operation"]["name"]
        if name in ("measure", "reset"):
            for i in e["ions"]:
                expect(name, [i], [])
        for p in e["pulses"]:
            expect(p["gate"], list(p["ions"]), [float(x) for x in p["params"]])
    actual: dict[str, list] = {}
    consumed: dict[str, int] = {}
    for ins in cmds:
        _require(ins.type in ("gate", "measure", "reset"), "packing emitted an unrelated command")
        groups = [list(p) for p in ins.pairs] if ins.gate == "MS" else [[i] for i in ins.ions]
        params = [tuple(float(x) for x in p) for p in ins.params] if ins.type == "gate" else [()] * len(groups)
        _require(len(groups) == len(params), "packing parameter/operand arity mismatch")
        flat = [i for g in groups for i in g]
        _require(len(flat) == len(set(flat)), "packing double-addresses an ion")
        gate = ins.gate if ins.type == "gate" else ins.type
        ids = []
        for g, par in zip(groups, params):
            got = []
            for ion in g:
                k = consumed.get(ion, 0)
                _require(k < len(owners.get(ion, [])), "packing emits an extra atomic effect")
                consumed[ion] = k + 1
                got.append(owners[ion][k])
            _require(len(set(got)) == 1, "a packed multi-ion pulse combines different operations")
            ids.append(got[0])
            for ion in g:
                actual.setdefault(ion, []).append((gate, tuple(g), par))
        claimed = ins.meta.get("op")
        _require(isinstance(claimed, list), "a packed command lacks operation provenance")
        _require(sorted(set(claimed)) == sorted(set(ids)), "packed provenance does not match its actual effects")
    _require(set(actual) == set(expected), "packing changes the participating ions")
    for ion, effects in expected.items():
        _require(actual[ion] == effects, f"packing changed the pulse order or parameters of {ion}")


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stages")
    ap.add_argument("--qasm", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--expanded-arch", required=True)
    ap.add_argument("--program", required=True)
    ap.add_argument("--qccdc", required=True)
    a = ap.parse_args(argv)
    try:
        verdict = check_manifest(json.loads(Path(a.stages).read_text(encoding="utf-8")),
                                 circuit_path=a.qasm, arch_path=a.arch, expanded_arch_path=a.expanded_arch,
                                 program_path=a.program, qccdc=a.qccdc)
    except (Failed, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"schema": "qccd.c2lr.stage_check.v1", "accepted": False, "engine": ENGINE,
                          "checker": METHOD, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps(verdict))
    return 0
