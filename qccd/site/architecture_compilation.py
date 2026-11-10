"""Four physical architectures, each compiled and judged by its own rule set.

The Rules page shows one compact, real run for each transport architecture it profiles.  The three
architecture compilers consume the same small QASM circuit; the general conveyor plays its
native rigid shift, which has no input circuit behind it.
"""

from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path
from typing import Callable

from ..api import Machine
from ..arch import Architecture, load as load_arch
from ..cost.models import corrected_model
from ..ir.source_map import build as build_source
from ..ir.tsir import TSIR
from ..ruleset import FAILS, HOLDS, NOT_APPLICABLE, check, legacy_config, load as load_rules
from ..ruleset.compile import circuit_of_certificate
from ..ruleset.profiles import architecture_profiles
from ..ruleset.programme import programme_execution
from ..verify import verify
from ..viz.render import render_html

ROOT = Path(__file__).resolve().parents[2]
PHYSICS = ROOT / "qccd" / "workspace" / "physics" / "qccd-physics@3.json"

QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[4];
creg c[4];
h q[0];
h q[1];
cx q[0],q[2];
cx q[1],q[3];
cx q[0],q[3];
measure q -> c;
"""

CSS = r"""
.archruns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:18px 0 28px}
.archrun{border:1px solid var(--line);border-radius:14px;background:var(--paper);overflow:hidden;box-shadow:0 5px 18px #17213a10}
.archrun .pic{height:230px;display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#f7f9fc,#edf2f8);border-bottom:1px solid var(--line);overflow:hidden}
.archrun .pic svg{display:block;max-width:100%;height:auto}
.archrun .body{padding:15px 17px 17px}
.archrun h3{margin:0 0 3px;font-size:21px}.archrun .machine{color:var(--ink3);font-size:13px;margin:0 0 10px}
.archrun .status{display:flex;gap:7px;flex-wrap:wrap;margin:8px 0 11px}.archrun .status b{font:700 11px/1 var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:6px 8px;border-radius:999px;background:#dff5e8;color:#0b6b43}
.archrun .status .na{background:#fff2cf;color:#74520b}.archrun .rules{font:600 13px/1.55 var(--sans);color:#283653;margin:8px 0}
.archrun .change{font-size:13px;color:var(--ink3);margin:9px 0 13px}.archrun .flow{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin:11px 0 14px}
.archrun .flow span{position:relative;border-radius:8px;background:#eef3f8;padding:8px 6px;text-align:center;font:600 11px/1.25 var(--sans);color:#30405f}
.archrun .flow span:not(:last-child):after{content:'\2192';position:absolute;right:-7px;top:50%;transform:translateY(-50%);z-index:1;color:#60708d}
.archrun .open{display:inline-block;background:#1c2a4a;color:#fff;border-radius:8px;padding:9px 12px;font:700 13px/1 var(--sans);text-decoration:none}
@media(max-width:760px){.archruns{grid-template-columns:1fr}.archrun .pic{height:210px}}
"""


def _physics() -> dict:
    return json.loads(PHYSICS.read_text(encoding="utf-8"))


def _wise_arch() -> Architecture:
    from ..arch import wise

    phys = _physics()
    doc = wise.device_doc(3, 3, 3, name="wise_h3_3x3")
    doc.update(primitives=wise.primitives(phys["primitives"]), heating=phys["heating"],
               species=phys["species"], budget=phys["budget"])
    return Architecture.from_json(doc)


def _cyclone_arch() -> Architecture:
    """The pulled 72-trap floorplan, with the control contract its router consumes.

    ``arch/cyclone_base.arch.json`` predates the Goal 3 router: it has the physical loop and
    broadcast wiring, but its compact document does not identify the family or declare the
    router's one-step ancilla rotation.  Expand that exact graph, then add only those declarations.
    The resulting page therefore keeps all 72 original nodes while making Y1--Y4 applicable.
    """
    from ..arch import cyclone

    arch = load_arch(str(ROOT / "arch" / "cyclone_base.arch.json"))
    doc = arch.to_json(expanded=True)
    generated = cyclone.device_doc(72, 3)
    shift = next(c for c in generated["control"]["classes"]["extra"]
                 if c["id"] == "cyclone_shift")
    extra = doc["control"]["classes"].setdefault("extra", [])
    if not any(c.get("id") == "cyclone_shift" for c in extra):
        extra.append(shift)
    doc["geometry"].setdefault("params", {}).update(family="cyclone", ions_per_trap=3)
    return Architecture.from_json(doc)


def _compile_circuit(kind: str, arch: Architecture, work: Path) -> tuple[TSIR, dict, Path]:
    """Compile ``QASM`` with the architecture's real router."""
    from ..compile import c2lr_router, cyclone_router, wise_router

    arch_path = work / "device.arch.json"
    arch_path.write_text(json.dumps(arch.to_json(), indent=1), encoding="utf-8")
    if kind == "c2lr":
        final, cert_path = c2lr_router.compile_with_router(
            QASM, arch_path, work, table="helios", name="architecture demo", reads_together=True)
    elif kind == "wise":
        final, cert_path = wise_router.compile_with_router(
            QASM, arch_path, work, table="helios", name="architecture demo")
    elif kind == "cyclone":
        final, cert_path = cyclone_router.compile_with_router(
            QASM, arch_path, work, n_data=2, table="helios", name="architecture demo")
    else:  # pragma: no cover - the closed list below is the public surface
        raise KeyError(kind)
    return TSIR.load(str(final)), json.loads(cert_path.read_text(encoding="utf-8")), work / "circuit.qasm"


def _conveyor() -> tuple[Architecture, TSIR]:
    from ..__main__ import program_for

    arch = load_arch(str(ROOT / "arch" / "h2_racetrack.arch.json"))
    return arch, program_for(arch, "rotate", "")


def _judge(arch: Architecture, prog: TSIR, ruleset: str, model, cert: dict | None):
    rs = load_rules(ruleset)
    circuit = circuit_of_certificate(cert) if cert is not None else None
    exe = programme_execution(prog, arch, model, circuit=circuit,
                              rule_config=legacy_config(rs) or None, check_metrics=False)
    rep = check(rs, exe)
    if rep.failed():
        raise RuntimeError(f"{arch.name} fails its architecture rule set {ruleset}: {rep.failed()}")
    return rs, rep


def _page_rules(rep, title: str) -> dict:
    return {
        "ruleset": rep.ruleset,
        "title": rep.title,
        "results": [r.to_json() for r in rep.results],
        "note": (f"This is the architecture-specific verdict for {title}. Rules marked not applicable "
                 "lack an object to judge in this particular short run; they are not suppressed."),
    }


def _initial_ions(prog: TSIR) -> dict[str, list[str]]:
    placement = next((i.placement for i in prog.instructions if i.type == "init"), {}) or {}
    ions: dict[str, list[str]] = {}
    for ion, site in sorted(placement.items()):
        ions.setdefault(site, []).append(ion)
    return ions


def build_pages(out: Path, put: Callable, device_svg: Callable) -> list[dict]:
    """Write four one-template Studio pages and return the Compilation-page card data."""
    profiles = {p["id"]: p for p in architecture_profiles()}
    specs = (
        ("c2lr", load_arch(str(ROOT / profiles["c2lr"]["architecture"]["reference"])), "c2lr"),
        ("wise", _wise_arch(), "wise"),
        ("cyclone", _cyclone_arch(), "cyclone-deck"),
    )
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="qccd-architecture-compilation-") as td:
        tmp = Path(td)
        compiled: list[tuple[str, Architecture, TSIR, dict | None, Path | None, str]] = []
        for kind, arch, ruleset in specs:
            work = tmp / kind
            work.mkdir()
            prog, cert, qasm = _compile_circuit(kind, arch, work)
            compiled.append((kind, arch, prog, cert, qasm, ruleset))
        conveyor_arch, conveyor_prog = _conveyor()
        compiled.append(("conveyor", conveyor_arch, conveyor_prog, None, None, "conveyor"))

        for kind, arch, prog, cert, qasm, ruleset in compiled:
            profile = profiles[kind]
            model = corrected_model("helios")
            rs, rep = _judge(arch, prog, ruleset, model, cert)
            source = build_source(prog, cert, qasm) if cert is not None and qasm is not None else None
            replay = verify(prog, arch, model, rule_config=legacy_config(rs) or None,
                            check_metrics=False).result
            rel = f"compilation/architecture/{kind}.html"
            render_html(
                arch, prog, replay, model, out / rel,
                kicker=f"{kind.upper()} · COMPILED",
                headline=f"{profile['title']} · {prog.name}",
                lede=("A real circuit compiled by this architecture's router and judged by its own rule set."
                      if source is not None else
                      "A rigid conveyor shift on the physical H2 racetrack, judged without a transport exemption."),
                source=source,
                open_pane="M",
                ruleset=_page_rules(rep, profile["title"]),
                notes={"title": profile["title"], "index": "../../rules/#architecture-rule-demos",
                       "facts": profile["architecture"]["facts"],
                       "claim": profile["scope"], "marks": []},
            )
            put(rel, (out / rel).read_text(encoding="utf-8"), 2, "compilation", app=True)
            held = [r.id for r in rep.results if r.verdict == HOLDS]
            other = [{"id": r.id, "verdict": r.verdict, "reason": r.reason}
                     for r in rep.results if r.verdict not in (HOLDS, FAILS)]
            focus = set(profile["rule_ids"])
            focus_held = [r.id for r in rep.results if r.id in focus and r.verdict == HOLDS]
            rows.append({
                "id": kind,
                "profile": profile,
                "page": f"architecture/{kind}.html",
                "instructions": len(prog.instructions),
                "held": held,
                "other": other,
                "focus_held": focus_held,
                "ruleset": rep.title,
                "poster": device_svg(Machine(arch), labels=len(arch.device.nodes) <= 40,
                                     ions=_initial_ions(prog) or None, fit=(620, 230)),
            })
    return rows


def section(rows: list[dict], href_prefix: str = "../compilation/") -> str:
    """The architecture-specific executable demos at the end of the Rules page."""
    cards = []
    for row in rows:
        p = row["profile"]
        not_applicable = [r for r in row["other"] if r["verdict"] == NOT_APPLICABLE]
        other = len(row["other"]) - len(not_applicable)
        status = (f'<b>{len(row["held"])} rules hold</b>'
                  + (f'<b class="na">{len(not_applicable)} not applicable</b>' if not_applicable else "")
                  + (f'<b class="na">{other} not checked</b>' if other else ""))
        changes = "; ".join(
            f"{c['kind']}: {', '.join(c['rules'])} — {c['reason']}" for c in p["changes"])
        flow = "".join(f"<span>{html.escape(s['label'])}</span>" for s in p["visual"]["steps"])
        focus = ", ".join(p["rule_ids"])
        cards.append(
            f'<article class="archrun" id="architecture-{html.escape(row["id"])}">'
            f'<div class="pic">{row["poster"]}</div><div class="body">'
            f'<h3>{html.escape(p["title"])}</h3>'
            f'<p class="machine">{html.escape(p["architecture"]["label"])} · '
            f'{row["instructions"]} compiled instructions</p>'
            f'<div class="status">{status}</div>'
            f'<p class="rules">Architecture focus: {html.escape(focus)} — '
            f'{len(row["focus_held"])} of {len(p["rule_ids"])} hold in this run.</p>'
            f'<div class="flow" aria-label="{html.escape(p["visual"]["title"])}">{flow}</div>'
            f'<p class="change">{html.escape(changes)}</p>'
            f'<a class="open" href="{html.escape(href_prefix + row["page"])}#step=1">'
            'Open compiled run and architecture rules</a>'
            f'</div></article>')
    return (
        '<h2 id="architecture-rule-demos">Architecture-specific rule demos</h2>'
        '<p class="sub">Executable evidence for the architecture profiles above. C2LR, WISE and Cyclone '
        'compile a small four-qubit circuit with their own routers; the conveyor runs its native rigid '
        'loop shift. Every linked page opens on Hardware with the architecture&rsquo;s complete checker rule '
        'set, including rules that do not apply to that short run.</p>'
        '<div class="archruns">' + "".join(cards) + '</div>')
