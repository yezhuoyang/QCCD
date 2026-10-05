"""The memory boards as real leaderboards: every architecture, ranked by logical error rate.

    rep5_mem       repetition code, distance 5, five rounds
    surface3_mem   rotated surface code, distance 3, three rounds

Each board's memory experiment (the release's own circuit and detectors) is compiled onto
every shipped device and onto small members of the study's families with the REFERENCE
compile (`qccd.qec.compile_with_qccdc`: what the grader's reference does), cooled, replayed
against the rules, and graded exactly as a submission is: the compiled hardware program is
turned into a noisy stim circuit under the board's noise model, checked noiselessly, sampled
and decoded with the release's budget and seed (`qccd.qec.evaluate_memory`).

Pages land in `MemoryBoard/<board>/`: one studio page per design -- the ion animation, the
circuit, and a Noise panel that shows the stim program instruction by instruction, with the
numbers each noise line was priced from -- and an index that ranks them by LER per round.

    python Codesign/scripts/memory_boards.py                 # everything
    python Codesign/scripts/memory_boards.py --board surface3_mem --pages-only

Set QCCD_QCCDC to the published compiler: a compiler built in the tree may be someone's
unfinished work, and the board must show what the official reference gives.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))

import bb_studio as B  # noqa: E402
import small_codes as SC  # noqa: E402
from q06_campaign import broadcast_use, buildability, score  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402

OUT = ROOT / "MemoryBoard"
TASKS = ROOT / "tasks"
BOARDS = {"rep5_mem": {"order": 6, "short": "rep-5 memory"},
          "surface3_mem": {"order": 7, "short": "surface-3 memory"}}
SCALE = 1e4          # the index shows rates in units of 1e-4


def devices(release) -> list[dict]:
    """The shipped devices, the board's own starter, and small members of the families."""
    from qccd.api import Machine
    st = release.manifest.get("starter") or {}
    out = []
    if st.get("generator"):
        m = getattr(Machine, st["generator"])(**st.get("params", {}), name=st.get("name") or "starter")
        p = ", ".join(f"{k} {v}" for k, v in sorted(st.get("params", {}).items()))
        out.append({"key": st.get("name") or "starter", "family": "ring + docks",
                    "title": f"{st.get('name') or 'starter'} — the board's starter ({st['generator']}: {p})",
                    "claim": "the design a new workspace starts from on this board",
                    "doc": m.arch.to_json()})
    shipped = [
        ("ring144_24v", "ring + docks", "ring144_24v — the shipped ring, 24 docks", "the study's shipped conveyor: rigid rotation with dock spurs"),
        ("cyclone_base", "junction-free loop", "cyclone_base — a loop with no junctions", "Cyclone's base: rotation on a loop without spurs"),
        ("h2_racetrack", "junction-free loop", "h2_racetrack — Quantinuum's H2 loop", "one continuous racetrack, no junctions"),
        ("cyclone_dual_loop", "two loops", "cyclone_dual_loop — data loop and ancilla loop", "two loops, the ancilla loop rotating"),
        ("ladder_2x72", "rails + highways", "ladder_2x72 — rails and highways", "two rails joined by rungs"),
        ("grid9x9", "lattice", "grid9x9 — the baseline lattice", "traps on the wires of a 9x9 junction grid"),
        ("deck_unit_cell", "lattice", "deck_unit_cell — the same lattice, broadcast-wired", "grid9x9 with the 32-channel WISE control block"),
        ("chain", "line", "chain — a line of traps", "one linear trap"),
        ("stationary_chain", "line", "stationary_chain — no transport at all", "one trap holding everything: no heating from transport, one long chain"),
    ]
    out += [{"key": k, "family": f, "title": t, "claim": c, "arch": SC.ARCH / f"{k}.arch.json"} for k, f, t, c in shipped]
    out += [
        {"key": "ring24_8", "family": "ring + docks", "title": "ring(12, 2, 8) — a small ring with 8 docks",
         "claim": "24 slots, 8 dock spurs",
         "doc": SC.gen_doc(SC.RING_TEMPLATE, "ring24_8", "ring", {"width": 12, "height": 2, "verticals": 8}, "a 24-slot ring with 8 docks")},
        {"key": "torus4x4", "family": "torus", "title": "torus 4x4 — a periodic lattice",
         "claim": "every row and column a closed loop, 32 traps",
         "doc": SC.gen_doc(SC.GRID_TEMPLATE, "torus4x4", "grid", {"a": 4, "b": 4, "periodic": True}, "a 4x4 torus")},
        {"key": "cylinder6x4", "family": "torus", "title": "cylinder 6x4 — four concentric rings with spokes",
         "claim": "the torus drawn as a chip: 42 traps, no crossings",
         "doc": SC.gen_doc(SC.GRID_TEMPLATE, "cylinder6x4", "cylinder", {"a": 6, "b": 4}, "four concentric rings joined by six spokes")},
        {"key": "grid5x5", "family": "lattice", "title": "grid5x5 — a small planar lattice",
         "claim": "40 traps on the wires of a 5x5 junction grid",
         "doc": SC.gen_doc(SC.GRID_TEMPLATE, "grid5x5", "grid", {"a": 5, "b": 5}, "a 5x5 planar lattice")},
        {"key": "random16", "family": "random graph", "title": "random 4-regular graph, 16 junctions",
         "claim": "32 traps on a random expander: short paths, no drawing",
         "doc": SC.random_doc("random16", 16, 7)},
    ]
    return out


def grade_point(out: Path, release, exp, dev: dict) -> dict:
    """Compile, replay, and grade one design as the evaluator's `ler` stage does."""
    from qccd.qec import CompileFailed, compile_with_qccdc, evaluate_memory
    row = {"key": dev["key"], "device": dev["key"], "family": dev["family"], "title": dev["title"], "claim": dev["claim"]}
    work = out / "work" / dev["key"]
    work.mkdir(parents=True, exist_ok=True)
    if "doc" in dev:
        arch_p = out / f"{dev['key']}.arch.json"
        arch_p.write_text(json.dumps(dev["doc"], indent=1), encoding="utf-8")
    else:
        arch_p = Path(dev["arch"])
    row["arch"] = str(arch_p)
    t0 = time.time()
    try:
        cooled, cert_p = compile_with_qccdc(release.circuit_text(), arch_p, work, timeout=900)
    except CompileFailed as exc:
        return {**row, "status": "refused", "reason": f"{exc}"[:300]}
    except Exception as exc:  # noqa: BLE001 -- a device the tools cannot load is a refusal too
        return {**row, "status": "refused", "reason": f"{type(exc).__name__}: {exc}"[:300]}
    row["compile_seconds"] = round(time.time() - t0, 1)
    row["compiler_mode"] = json.loads((work / "compile.json").read_text(encoding="utf-8")).get("mode")
    cert = json.loads(cert_p.read_text(encoding="utf-8"))
    q = release.manifest["qec"]
    rep = evaluate_memory(str(cooled), str(arch_p), exp, noise_id=q["noise"], cert=cert,
                          ler_budget={k: v for k, v in (q.get("ler") or {}).items() if k != "seed"},
                          seed=(q.get("ler") or {}).get("seed"))
    (work / "ler_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    chk = rep.get("check") or {}
    stats = rep.get("stats") or {}
    arch = load(str(arch_p)); prog = TSIR.load(str(cooled))
    numbers: dict = {}
    transport_us = 0.0
    for table, tag in (("qccdsim_jones", "jones"), ("transport_excitation", "transport")):
        try:
            s = score(arch, prog, table=table)
        except Exception:  # noqa: BLE001
            continue
        numbers[f"T_{tag}"] = round(s["T_round_ms"], 3)
        if tag == "jones":
            numbers["p_eff_vs_threshold"] = round(s["p_eff_vs_threshold"], 3)
            transport_us = sum(us for k, us in s["us_by_class"].items() if k not in ("gate", "measure", "reset", "cool"))
    bc = broadcast_use(arch, prog)
    numbers.update(ions_per_instruction=round(bc["ions_per_instruction"], 2), transport_instructions=bc["transport_instructions"],
                   us_per_ion=round(transport_us / bc["ion_moves"], 2) if bc["ion_moves"] else None)
    try:
        b = buildability(arch_p)
        numbers.update(dacs=b["dacs"], area_mm2=b["area_mm2"], pads_per_trap=b["pads_per_trap"], buildable=b["buildable"], drc_clean=b["drc_clean"], drc=b["drc"])
    except Exception as exc:  # noqa: BLE001
        numbers["build_error"] = type(exc).__name__
    row["rules_failed_replay"] = list(stats.get("rules_failed") or [])
    if not chk.get("ok"):
        return {**row, "status": "refused", "reason": "the compiled program does not run the memory experiment: "
                + str(chk.get("reason") or chk)[:240]}
    est = rep["ler"]
    bud = rep.get("budget") or {}
    numbers.update(
        ler_round_1e4=round(est["per_round"] * SCALE, 4), ler_round_lo_1e4=round(est["per_round_lo"] * SCALE, 4),
        ler_round_hi_1e4=round(est["per_round_hi"] * SCALE, 4), ler_shot_1e4=round(est["ler"] * SCALE, 4),
        ler_per_round=est["per_round"], ler=est["ler"], shots=est["shots"], errors=est["errors"],
        noise_total=round(bud.get("total", 0.0), 5),
        noise_ms=round(bud.get("ms_base", 0.0) + bud.get("ms_heating", 0.0) + bud.get("ms_chain", 0.0), 5),
        noise_heating=round(bud.get("ms_heating", 0.0), 5), noise_chain=round(bud.get("ms_chain", 0.0), 5),
        noise_idle=round(bud.get("idle", 0.0), 5), nbar_max=round(float(stats.get("nbar_max", 0.0)), 3),
        chain_max=stats.get("chain_max"), two_qubit_gates=stats.get("n_ms"))
    # R10: the semantic checker and the proved Lean checker, on the certificate (small: seconds)
    prefix = work / "prog"
    if SC.QCHECK.exists():
        qc = Path(str(prefix) + ".qcheck.json")
        SC.sh([sys.executable, SC.BRIDGE / "mk_qcheck_input.py", prefix, "--arch", work / "device.expanded.json", "-o", qc])
        try:
            _, clog, lsecs = SC.sh([sys.executable, SC.BRIDGE / "check_cert.py", prefix, "--qasm", work / "circuit.qasm",
                                    "--arch", arch_p, "--qcheck", qc], timeout=1800)
            numbers["r10"] = "R10 passed" in clog
            row["r10_verdict"] = next((ln.strip() for ln in clog.splitlines() if "-> R10" in ln), "")[:160]
            row["lean_seconds"] = round(lsecs, 1)
        except subprocess.TimeoutExpired:
            numbers["r10"] = False
    row.update(status="ok", numbers=numbers, instructions=len(prog), prefix=str(prefix),
               qasm=str(work / "circuit.qasm"), cooled=str(cooled), ler=est,
               budget=bud, stats={k: v for k, v in stats.items() if k != "rule_violations"})
    return row


# --------------------------------------------------------------------------- the Noise panel

PANEL_CSS = """
#mbpill{position:fixed;right:132px;bottom:12px;z-index:59;background:#fff;color:#1c2a4a;border:1px solid #cfceca;
 border-radius:16px;padding:6px 12px;font:600 12.5px ui-sans-serif,system-ui,sans-serif;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,.12)}
#mbpill[data-open="1"]{display:none}
#mbnoise{position:fixed;right:12px;bottom:52px;top:140px;width:min(470px,36vw);z-index:60;background:#fff;color:#0b0b0b;
 border:1px solid #cfceca;border-radius:10px;box-shadow:0 4px 18px rgba(0,0,0,.18);display:none;flex-direction:column;
 font:12.5px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
#mbnoise[data-open="1"]{display:flex}
#mbnoise .mbh{display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid #e6e5e1}
#mbnoise .mbh b{flex:1;font-size:13px} #mbnoise .mbh button,#mbnoise .mbh a{font:inherit;font-size:12px;border:1px solid #cfceca;
 background:#fff;border-radius:6px;padding:3px 8px;cursor:pointer;color:#1c2a4a;text-decoration:none}
#mbnoise .mbs{padding:8px 10px;border-bottom:1px solid #e6e5e1;max-height:44%;overflow:auto}
#mbnoise .mbs .big{font-size:15px;font-weight:600} #mbnoise .mbs small{color:#52514e}
#mbnoise table{border-collapse:collapse;width:100%;margin-top:6px} #mbnoise td,#mbnoise th{padding:2px 4px;border-bottom:1px solid #efeeeb;text-align:left;vertical-align:top}
#mbnoise th{color:#8a8985;font-weight:500} #mbnoise td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
#mbnoise td code,#mbnoise .mbl pre{font:11.5px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace}
#mbnoise .mbl{flex:1;overflow:auto;padding:4px 0}
#mbnoise .blk{padding:4px 10px;border-left:3px solid transparent;cursor:pointer}
#mbnoise .blk:hover{background:#f6f8fc} #mbnoise .blk.cur{background:#eef3fb;border-left-color:#2a78d6}
#mbnoise .blk .hd{color:#1c2a4a;font-weight:600} #mbnoise .blk .hd span{color:#8a8985;font-weight:400}
#mbnoise .blk .why{color:#52514e;margin:1px 0}
#mbnoise .blk pre{margin:2px 0 0;white-space:pre-wrap;word-break:break-word;color:#0b0b0b}
#mbnoise .blk pre i{font-style:normal;color:#b3261e}
"""

PANEL_JS = r"""
(function(){
  var D = window.MB_NOISE; if(!D) return;
  function $(id){ return document.getElementById(id); }
  function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
  function cur(){ try { return (typeof frame === 'number') ? frame : 0; } catch(e){ return 0; } }
  function go(i){ try { if(typeof seek === 'function') seek(i); } catch(e){} }
  var box = $('mbnoise'), pill = $('mbpill'), list = $('mblist');
  function open(on){ box.setAttribute('data-open', on ? '1' : '0'); pill.setAttribute('data-open', on ? '1' : '0');
    try { localStorage.setItem('mb.noise', on ? '1' : '0'); } catch(e){} if(on) mark(true); }
  pill.addEventListener('click', function(){ open(true); });
  $('mbx').addEventListener('click', function(){ open(false); });
  var NOISE = /^(DEPOLARIZE1|DEPOLARIZE2|X_ERROR|Z_ERROR)/;
  var h = '';
  for(var i=0;i<D.blocks.length;i++){ var b = D.blocks[i];
    var pre = b.s.map(function(l){ return NOISE.test(l) ? '<i>' + esc(l) + '</i>' : esc(l); }).join('\n');
    h += '<div class="blk" data-i="' + i + '"' + (b.f != null ? ' data-f="' + b.f + '"' : '') + '><div class="hd">' + esc(b.h) +
         (b.f != null ? ' <span>step ' + (b.f + 1) + '</span>' : '') + '</div>' +
         b.why.map(function(w){ return '<div class="why">' + esc(w) + '</div>'; }).join('') +
         (b.s.length ? '<pre>' + pre + '</pre>' : '<div class="why">no stim line: it changes no qubit and takes no time</div>') + '</div>'; }
  list.innerHTML = h;
  list.addEventListener('click', function(ev){ var t = ev.target; while(t && t !== list && !(t.classList && t.classList.contains('blk'))) t = t.parentElement;
    if(t && t !== list && t.getAttribute('data-f') != null) go(parseInt(t.getAttribute('data-f'), 10)); });
  var byF = {}; var els = list.children; for(var j=0;j<els.length;j++){ var f = els[j].getAttribute('data-f'); if(f != null) byF[f] = els[j]; }
  var last = -1;
  function mark(force){ var f = cur(); if(f === last && !force) return; last = f;
    var old = list.querySelector('.cur'); if(old) old.classList.remove('cur');
    var el = byF[String(f)]; if(!el) return; el.classList.add('cur');
    if(box.getAttribute('data-open') === '1'){ var top = el.offsetTop - list.offsetTop; if(top < list.scrollTop || top > list.scrollTop + list.clientHeight - 60) list.scrollTop = Math.max(0, top - 40); } }
  setInterval(function(){ mark(false); }, 200);
  $('mbdl').addEventListener('click', function(ev){ var txt = D.blocks.map(function(b){ return (b.s.length ? '# ' + b.h + '\n' + b.s.join('\n') : '# ' + b.h); }).join('\n') + '\n';
    try { ev.currentTarget.href = URL.createObjectURL(new Blob([txt], {type: 'text/plain'})); } catch(e){} });
  window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, {
    'noise:open': {t: 'Noise model', d: 'Opens the panel that shows how this hardware program becomes a noisy stim circuit, instruction by instruction, and the logical error rate measured from it.'},
    'noise:close': {t: 'Close the noise panel', d: 'Hides the noise panel; the program and the animation are unchanged.'},
    'noise:download': {t: 'Download the stim circuit', d: 'Saves the noisy stim circuit this page was graded on, annotated with the hardware instruction each block came from.'}});
  var want = null; try { want = localStorage.getItem('mb.noise'); } catch(e){}
  if(want === '1' || /[#&]noise\b/.test(location.hash)) open(true);
})();
"""


def _head(ins) -> str:
    g = getattr(ins, "gate", None) or getattr(ins, "cls", None) or ""
    n = len(getattr(ins, "participants", None) or []) or len(getattr(ins, "pairs", None) or []) or len(getattr(ins, "ions", None) or [])
    what = {"simd": "transport", "gate": "gate", "cool": "cooling", "measure": "measure", "reset": "reset", "init": "load"}.get(ins.type, ins.type)
    unit = {"simd": "ion(s) move", "gate": "operand(s)", "measure": "ion(s)", "reset": "ion(s)"}.get(ins.type, "")
    return f"{what}{(' ' + str(g)) if g else ''}{(' · %d %s' % (n, unit)) if n and unit else ''}"


def noise_panel(row: dict, release, exp) -> str:
    """The panel for one entry: the LER, the error budget by channel, and the stim program
    block by block, each block one hardware instruction with what its noise was priced from."""
    from qccd.compile.decode import insert_decodes
    from qccd.qec import describe_noise, extract
    q = release.manifest["qec"]
    cert = json.loads(Path(row["prefix"] + ".qcert.json").read_text(encoding="utf-8"))
    ex = extract(row["cooled"], row["arch"], exp, noise=q["noise"], cert=cert)
    prog = TSIR.load(row["cooled"])
    by_id = {ins.id: ins for ins in prog.instructions}
    shown = insert_decodes(prog)                       # the page's frame i is instruction i of this
    frame_of = {ins.id: i for i, ins in enumerate(shown.instructions)}
    names = {"prepare": "prepare", "readout": "final data readout (appended by the grader)",
             "detectors": "detectors and the logical observable"}
    blocks = []
    for t in ex.trace:
        ins = by_id.get(t["id"]) if t["id"] is not None else None
        blocks.append({"f": frame_of.get(t["id"]) if ins is not None else None,
                       "h": _head(ins) if ins is not None else names.get(t["type"], t["type"]),
                       "why": t["why"], "s": ex.lines[t["a"]:t["b"]]})
    est, bud = row["ler"], row["budget"]
    tot = bud.get("total") or 1.0
    ch = {c["channel"]: c for c in describe_noise(q["noise"])["channels"]}
    label = {"ms_base": "two-qubit gate, ground state", "ms_heating": "two-qubit gate, transport heating",
             "ms_chain": "two-qubit gate, chain length", "gate_1q": "one-qubit gate", "measure": "readout",
             "reset": "reset", "idle": "idling (every instruction takes time)"}
    stim_of = {"ms_base": "DEPOLARIZE2", "ms_heating": "DEPOLARIZE2", "ms_chain": "DEPOLARIZE2",
               "gate_1q": "DEPOLARIZE1", "measure": "X_ERROR before M", "reset": "X_ERROR after R", "idle": "Z_ERROR"}
    rows = "".join(
        f'<tr title="{html.escape(ch.get(k, {}).get("formula", ""))} [{html.escape(ch.get(k, {}).get("source", ""))}]">'
        f'<td>{html.escape(label.get(k, k))}</td><td><code>{html.escape(stim_of.get(k, ""))}</code></td>'
        f'<td class="n">{v:.4f}</td><td class="n">{100 * v / tot:.0f}%</td></tr>'
        for k, v in bud.items() if k != "total")
    st = row["stats"]
    summary = (
        f'<div class="big">LER per round {est["per_round"]:.2e} '
        f'<small>(95%: {est["per_round_lo"]:.2e} to {est["per_round_hi"]:.2e})</small></div>'
        f'<small>{est["errors"]} logical errors in {est["shots"]:,} shots of {est["rounds"]} rounds, decoder {html.escape(est["decoder"])}, '
        f'seed {est["seed"]}; noise model {html.escape(q["noise"])}. The stim circuit below has {st.get("detectors")} detectors '
        f'and {st.get("ions")} qubits, one per ion. Highest n&#772; at a two-qubit gate: {st.get("nbar_max", 0):.3g}; '
        f'longest chain at a gate: {st.get("chain_max")}.</small>'
        f'<table><tr><th>where the error comes from</th><th>stim</th><th>expected errors</th><th>share</th></tr>{rows}'
        f'<tr><td><b>total</b></td><td></td><td class="n"><b>{tot:.4f}</b></td><td></td></tr></table>'
        f'<small>Hover a row for its formula and the device parameter it reads; <a href="../noise/">the noise model, channel by channel</a>. '
        f'Red lines below are noise. Each block is one hardware instruction: click it to move the animation there; '
        f'the block of the current step is highlighted as the program plays.</small>')
    data = json.dumps({"blocks": blocks}, separators=(",", ":")).replace("</", "<\\/")
    return (f"<style>{PANEL_CSS}</style>"
            f'<button id="mbpill" data-open="0" data-hint="noise:open" title="how this program becomes a noisy stim circuit">Noise model &middot; stim</button>'
            f'<div id="mbnoise" data-open="0"><div class="mbh"><b>From ions to noise: the stim program</b>'
            f'<a id="mbdl" data-hint="noise:download" download="{html.escape(row["key"])}.stim" href="#">download .stim</a>'
            f'<button id="mbx" data-hint="noise:close" title="close">&times;</button></div>'
            f'<div class="mbs">{summary}</div><div class="mbl" id="mblist"></div></div>'
            f"<script>window.MB_NOISE={data};</script><script>{PANEL_JS}</script>")


# --------------------------------------------------------------------------- pages

LER_METRICS = (
    " {k:'ler_round_1e4', name:'Logical error rate per round (×10⁻⁴)', unit:'', better:'low', nd:3},\n"
    " {k:'ler_round_lo_1e4', name:'Logical error rate per round — 95% low (×10⁻⁴)', unit:'', better:'low', nd:3},\n"
    " {k:'ler_round_hi_1e4', name:'Logical error rate per round — 95% high (×10⁻⁴)', unit:'', better:'low', nd:3},\n"
    " {k:'ler_shot_1e4', name:'Logical error rate per shot (×10⁻⁴)', unit:'', better:'low', nd:3},\n"
    " {k:'noise_total', name:'Noise — expected errors in the circuit', unit:'', better:'low', nd:4},\n"
    " {k:'noise_ms', name:'Noise — from two-qubit gates', unit:'', better:'low', nd:4},\n"
    " {k:'noise_heating', name:'Noise — from transport heating', unit:'', better:'low', nd:4},\n"
    " {k:'noise_chain', name:'Noise — from chain length', unit:'', better:'low', nd:4},\n"
    " {k:'noise_idle', name:'Noise — from idling', unit:'', better:'low', nd:4},\n"
    " {k:'nbar_max', name:'Heating — highest n̄ at a gate', unit:'quanta', better:'low', nd:3},\n")


def pages(board: str, release, exp, rows: list[dict], out: Path) -> None:
    ok = [r for r in rows if r["status"] == "ok"]
    sound = [r for r in ok if not r.get("rules_failed_replay")]
    # ties are real on a board whose code is too good for its shot budget (zero errors in
    # every shot): they break on the interval's upper end, then on the round's time
    def rank(r):
        n = r.get("numbers", {})
        return (n.get("ler_per_round", 1e9), n.get("ler_round_hi_1e4", 1e9), n.get("T_jones", 1e9))
    # no design is singled out when the leaders are statistically tied
    few = bool(sound) and max(r["ler"]["errors"] for r in sound) < 20
    best = min(sound, key=rank) if (sound and not few) else None
    order = sorted(rows, key=lambda r: (r["status"] != "ok", bool(r.get("rules_failed_replay")), rank(r)))
    manifest = []
    for i, r in enumerate(order):
        e = {"key": f"{i + 1:02d}_{r['device']}", "family": r["family"], "title": f"{release.title} on {r['title']}",
             "arch": Path(r["arch"]), "claim": r["claim"], "kicker": BOARDS[board]["short"].upper() + " · LOGICAL ERROR RATE",
             "short": r["device"], "answer": best is not None and r is best}
        if r["status"] == "ok":
            n = r["numbers"]
            e.update(prefix=Path(r["prefix"]), qasm=Path(r["qasm"]), numbers=dict(n), r10=n.get("r10", False),
                     claim=f"{r['claim']}. LER per round {n['ler_per_round']:.2e}, 95% interval "
                           f"{r['ler']['per_round_lo']:.1e} to {r['ler']['per_round_hi']:.1e} "
                           f"({r['ler']['errors']} errors in {r['ler']['shots']:,} shots)")
        else:
            e["refusal"] = r.get("reason", "refused")
        m = B.build_one(e, out, max_frames=20000)
        m["short"] = e["short"]; m["answer"] = e["answer"]
        if r["status"] == "ok" and m.get("status") == "ok":
            page = out / m["page"]
            src = page.read_text(encoding="utf-8")
            page.write_text(src.replace("</body>", noise_panel({**r, "key": e["key"]}, release, exp) + "</body>", 1),
                            encoding="utf-8", newline="")
            m["ler"] = r["ler"]; m["budget"] = r["budget"]
            m["bytes"] = page.stat().st_size
        manifest.append(m)
        print(f"   page {m['key']:26s} {m['status']}", flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    q = release.manifest["qec"]["ler"]
    title = f"{release.title} on QCCD — every design, ranked"
    idx = B.write_index(
        manifest, out, title=title,
        subtitle=f"{release.manifest.get('description', '')} Every design ran the same experiment, compiled by the reference "
                 f"compiler; its hardware program was turned into a noisy stim circuit under the noise model built from the "
                 f"device's own physics, and sampled (up to {int(q['max_shots']):,} shots or {int(q['max_errors'])} logical "
                 f"errors, decoder {q['decoder']}, fixed seed). Ranked by the logical error rate per round, lower is better; "
                 f"click a dot to watch the ions and read the stim program that was sampled."
                 + (" At this budget the designs give at most a few logical errors each, so their 95% intervals "
                    "overlap: the order among them is not significant." if few else ""))
    s = idx.read_text(encoding="utf-8")
    for a, b in (("var METRICS = [\n", "var METRICS = [\n" + LER_METRICS),
                 (": 'T_jones'; xsel.value", ": 'ler_round_1e4'; xsel.value"),
                 ("var cols=['T_jones',", "var cols=['ler_round_1e4','ler_round_lo_1e4','ler_round_hi_1e4','noise_heating','nbar_max','T_jones',")):
        assert s.count(a) == 1, a
        s = s.replace(a, b)
    idx.write_text(s, encoding="utf-8")


def write_task(board: str, release, exp) -> None:
    d = TASKS / board
    d.mkdir(parents=True, exist_ok=True)
    (d / "circuit.qasm").write_text(release.circuit_text(), encoding="utf-8", newline="\n")
    q = release.manifest["qec"]
    doc = {"kind": "qccd.task", "version": 1, "id": board, "order": BOARDS[board]["order"],
           "title": release.title, "short": BOARDS[board]["short"],
           "description": release.manifest.get("description", ""), "circuit": "circuit.qasm",
           "n_data": exp.summary()["n_data"],
           "physics": {"model": "corrected", "tables": ["qccdsim_jones", "qccdsim_transport"]},
           "metrics": ["ler_per_round", "ler", "T_jones", "ions_per_instruction", "dacs", "area_mm2", "buildable"],
           "rank_by": "ler_per_round", "release": release.id,
           "qec": {"experiment": q.get("experiment"), "noise": q["noise"], "ler": q["ler"]},
           "seed": f"MemoryBoard/{board}"}
    (d / "task.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    from qccd.qec import MemoryExperiment
    from qccd.workspace.tasks import find_release
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--pages-only", action="store_true", help="rebuild pages from rows.json")
    a = ap.parse_args(argv)
    for board in BOARDS:
        if a.board and board != a.board:
            continue
        release = find_release(board + "@1")
        exp = MemoryExperiment.from_spec(release.detectors(), release.circuit_text())
        out = OUT / board
        out.mkdir(parents=True, exist_ok=True)
        rj = out / "rows.json"
        if a.pages_only and rj.exists():
            rows = json.loads(rj.read_text(encoding="utf-8"))
        else:
            rows = []
            print(f"== {board}: {release.title}", flush=True)
            for dev in devices(release):
                if a.device and dev["key"] != a.device:
                    continue
                r = grade_point(out, release, exp, dev)
                rows.append(r)
                if r["status"] == "ok":
                    n = r["numbers"]
                    print(f"   {dev['key']:18s} LER/round {n['ler_per_round']:.2e}  n-bar max {n['nbar_max']:6.3f}  "
                          f"{n.get('T_jones', 0):8.2f} ms  rules {'ok' if not r['rules_failed_replay'] else 'FAIL ' + str(r['rules_failed_replay'])}"
                          f"  R10 {n.get('r10')}  ({r['compile_seconds']:.0f}s, {r['compiler_mode']})", flush=True)
                else:
                    print(f"   {dev['key']:18s} refused: {r.get('reason', '')[:110]}", flush=True)
            if a.device and rj.exists():
                rows = [r for r in json.loads(rj.read_text(encoding="utf-8")) if r["device"] != a.device] + rows
            rj.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
        pages(board, release, exp, rows, out)
        write_task(board, release, exp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
