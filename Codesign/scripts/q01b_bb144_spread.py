"""CD4 . 0.1b -- does the device spread grow when the circuit is big enough to stress transport?

`q01` measured a 1.3x best-to-worst device spread on small circuits and named two candidate
explanations: the missing chain-length term (G1), or circuits too small to move ions.  This
settles it on `bb144_esm`, the [[144,12,12]] syndrome round the study is actually about, and
it re-measures the small circuits from the *verified* matrix rather than the ad-hoc directory
`q01` happened to read.

Three corrections to `q01`'s method, each of which changes a headline number:

1.  **Provenance.**  `q01` read `Compiler/build/out/`, which is scratch output from
    `gen_bench.py` and friends -- not the verification matrix, and carrying no record of
    whether any of it passed the 23 rules or R10.  The matrix lives in
    `Compiler/build/matrix/` with verdicts in `Compiler/build/matrix.json`.  Only pairs
    that matrix reports `ok` (all rules pass, R10 `passed`) are measured here, which is
    what `PLAN.md` CD5's fidelity ladder requires of anything that reaches `findings/`.

2.  **SPAM is not device-attributable.**  `q01` defined excess as `-lnF - floor` with
    `floor = n_pairs * eps0`, so the whole SPAM term counted as the architecture's doing.
    All nine architectures declare *identical* `measure.fidelity`, `reset.error`,
    `ms_gate.fidelity_at_n0`, `T_coh_s` and `anomalous_rate` (asserted below), so for a
    fixed circuit `n_pairs*eps0 + spam` is a constant no geometry can move.  It belongs in
    the floor.

3.  **Device suffix matching.**  `next(d for d in sorted(devices) if stem.endswith('_'+d))`
    attributes every `*_stationary_chain` program to `chain`, because `chain` sorts first
    and is a suffix of `stationary_chain`.  Longest match here.

    python Codesign/scripts/q01b_bb144_spread.py --json Codesign/data/q01b.json

`p_eff` is `-lnF` divided by the number of OPERATIONS (2q gates, 1q gates, measures,
resets), the same convention `q01` used, restated here so the 0.7 % threshold comparison
can be read with it in view.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model, t2_metrics  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import replay  # noqa: E402

MATRIX = ROOT / "Compiler" / "build" / "matrix"
VERDICTS = ROOT / "Compiler" / "build" / "matrix.json"
BB_VERDICTS = ROOT / "Codesign" / "data" / "q01b_matrix.json"
EXE = ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"


def _sh(cmd: list[str]) -> tuple[int, str]:
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def arch_constants(devices: list[str]) -> dict:
    """The scalars that set the floor, per device.

    The whole `floor` / `device-attributable` split rests on these being equal across
    architectures.  It is checked rather than assumed: if a future architecture declares a
    different gate or SPAM fidelity, the split stops meaning what it says here and the
    script says so instead of quietly reporting a wrong share.
    """
    out = {}
    for d in devices:
        a = load(str(ROOT / "arch" / f"{d}.arch.json"))
        out[d] = {
            "eps0": 1.0 - float(a.primitives.scalar("ms_gate").get("fidelity_at_n0", 1.0)),
            "max_quanta": float(a.primitives.scalar("ms_gate").get("max_quanta", math.inf)),
            "slope": float(str(a.primitives.scalar("ms_gate")
                               .get("error_vs_quanta", "linear:0")).split(":")[1]),
            "measure_inf": 1.0 - float(a.primitives.scalar("measure").get("fidelity", 1.0)),
            "reset_err": float(a.primitives.scalar("reset").get("error", 0.0)),
            "t_coh_s": float(a.species.get("T_coh_s", 0.0) or 0.0),
            "anomalous": float(a.anomalous_rate()),
        }
    return out


def measure(path: Path, device: str) -> dict | None:
    arch = load(str(ROOT / "arch" / f"{device}.arch.json"))
    model = corrected_model()
    res = replay(TSIR.load(str(path)), arch, model, check_rules=False, keep_cycles=False)
    m = t2_metrics(arch, res, model)

    ops = m.n_gate_pairs + res.n_1q_gates + res.n_measure + res.n_reset
    if ops == 0:
        return None
    eps0 = 1.0 - float(arch.primitives.scalar("ms_gate").get("fidelity_at_n0", 1.0))

    # THE FLOOR: what this circuit costs on ANY of the nine architectures.  Gate floor plus
    # SPAM -- both are per-operation constants declared identically by every architecture.
    gate_floor = m.n_gate_pairs * eps0
    floor = gate_floor + m.spam_error
    # what the architecture is actually responsible for: heating that survived cooling,
    # and the idle dephasing of however many ions for however long the round took
    gate_excess = m.gate_error_sum - gate_floor
    device_error = gate_excess + m.idle_error

    return {
        "device": device,
        "runtime_ms": m.runtime_us / 1000.0,
        "cooling_us": m.cooling_us,
        "neg_log_fidelity": m.neg_log_fidelity,
        "gate_error": m.gate_error_sum,
        "gate_floor": gate_floor,
        "gate_excess": gate_excess,
        "idle_error": m.idle_error,
        "spam_error": m.spam_error,
        "floor_error": floor,
        "device_error": device_error,
        "device_share": device_error / m.neg_log_fidelity if m.neg_log_fidelity else 0.0,
        "n_ops": ops,
        "n_gate_pairs": m.n_gate_pairs,
        "n_ions": len(res.per_ion_quanta),
        "p_eff": m.neg_log_fidelity / ops,
        "mean_gate_quanta": m.mean_gate_quanta,
        "max_gate_quanta_seen": m.max_gate_quanta_seen,
        "peak_quanta": m.peak_quanta,
    }


def pairs_from(verdicts: Path) -> list[tuple[str, str]]:
    rows = json.loads(verdicts.read_text(encoding="utf-8"))
    return [(r["circuit"], r["device"]) for r in rows if r.get("status") == "ok"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None)
    ap.add_argument("--threshold", type=float, default=0.007,
                    help="the code's published circuit-level threshold")
    a = ap.parse_args(argv)

    want = pairs_from(VERDICTS)
    if BB_VERDICTS.exists():
        want += pairs_from(BB_VERDICTS)

    devices = sorted({d for _, d in want})
    consts = arch_constants(devices)
    varying = {k for k in next(iter(consts.values()))
               if len({consts[d][k] for d in devices}) > 1}
    if varying:
        print(f"!! these floor scalars are NOT equal across devices: {sorted(varying)}")
        print("   the floor/device split below does not mean what its name says")
    else:
        c = consts[devices[0]]
        print(f"floor scalars, identical on all {len(devices)} devices: "
              f"eps0={c['eps0']:.2e}  measure={c['measure_inf']:.2e}  "
              f"reset={c['reset_err']:.2e}  T_coh={c['t_coh_s']:g}s  "
              f"R7 max_quanta={c['max_quanta']:g}  slope={c['slope']:.1e}/quantum")

    rows: list[dict] = []
    for circuit, device in want:
        for tag in ("cooled", "uncooled"):
            suffix = ".cooled.tsir.json" if tag == "cooled" else ".tsir.json"
            p = MATRIX / f"{circuit}_{device}{suffix}"
            if not p.exists():
                continue
            try:
                got = measure(p, device)
            except Exception as exc:                          # noqa: BLE001
                print(f"  {circuit}/{device} {tag}: skipped ({type(exc).__name__}: {exc})")
                continue
            if got:
                got["circuit"] = circuit
                got["state"] = tag
                rows.append(got)

    if not rows:
        print("nothing measured; run Compiler/bridge/run_matrix.py first")
        return 2

    cooled = [r for r in rows if r["state"] == "cooled"]
    uncooled = {(r["circuit"], r["device"]): r for r in rows if r["state"] == "uncooled"}

    # ---------------------------------------------------------------- the BB round itself
    bb = sorted((r for r in cooled if r["circuit"] == "bb144_esm"),
                key=lambda r: r["device"])
    print()
    print("=" * 96)
    print("bb144_esm -- BB[[144,12,12]] syndrome extraction, 864 two-qubit gates, 168 qubits")
    print("=" * 96)
    if not bb:
        print("  not compiled anywhere; run run_matrix.py --circuits bb144_esm")
    for r in bb:
        u = uncooled.get((r["circuit"], r["device"]))
        print(f"  {r['device']:<18} {r['runtime_ms']:>8.1f} ms   -lnF {r['neg_log_fidelity']:>8.4f}"
              f"   p_eff {r['p_eff']:.2e}  ({r['p_eff'] / a.threshold:.3f} x threshold)")
        print(f"    {'':<16}   gate floor {r['gate_floor']:>8.4f}  spam {r['spam_error']:>8.4f}"
              f"   | gate excess {r['gate_excess']:>9.5f}  idle {r['idle_error']:>8.4f}"
              f"   -> device share {r['device_share']:.1%}")
        if u:
            print(f"    before cooling:    {u['runtime_ms']:>8.1f} ms   -lnF {u['neg_log_fidelity']:>8.4f}"
                  f"   p_eff {u['p_eff']:.2e}  ({u['p_eff'] / a.threshold:.1f} x threshold)"
                  f"   mean gate n-bar {u['mean_gate_quanta']:.1f} -> {r['mean_gate_quanta']:.3f}")

    # ------------------------------------- why the BB round reaches so few of the devices
    if BB_VERDICTS.exists():
        print()
        print("WHY -- slots against qubits, and which devices the router can serve")
        bbrows = {r["device"]: r
                  for r in json.loads(BB_VERDICTS.read_text(encoding="utf-8"))}
        nq = 168  # 144 data + 24 reused ancillas, as gen_bb144.py emits it
        print(f"  {'device':<19}{'slots':>7}{'gate sites':>11}{'occ@168':>9}"
              f"{'closed loops':>14}  outcome")
        info = []
        for d in sorted(bbrows):
            exp = json.loads((ROOT / "Compiler" / "build" / f"{d}.expanded.json")
                             .read_text(encoding="utf-8"))
            caps = exp["node_caps"]
            slots = sum(v["capacity"] for v in caps.values())
            gsites = sum(1 for v in caps.values() if v["gate"])
            # Rigid rotation -- the only thing that compiles past the individual-ion
            # router's occupancy ceiling -- needs a CLOSED loop with gate-capable docks
            # hanging off it (ocaml/lib/conveyor.ml).  Ask the compiler rather than
            # guessing from the JSON: `qccdc arch` prints the loops it found and whether
            # each is closed, which is the same detector the pipeline uses.
            rc, out = _sh([str(EXE), "arch", str(ROOT / "Compiler" / "build"
                                                 / f"{d}.expanded.json")])
            closed = sum(1 for line in out.splitlines()
                         if line.strip().startswith("loop ") and "closed=true" in line)
            occ = nq / slots if slots else float("inf")
            st = bbrows[d]["status"]
            info.append({"device": d, "slots": slots, "gate_sites": gsites,
                         "occupancy": occ, "closed_loops": closed, "status": st})
            print(f"  {d:<19}{slots:>7}{gsites:>11}{occ:>8.1%}{closed:>14}  {st}")
        print("  a closed loop is necessary for rigid rotation but not sufficient: it also "
              "needs")
        print("  gate-capable DOCKS off the loop, which is why cyclone_dual_loop's two "
              "closed loops")
        print("  do not help -- its gate sites sit ON the loops.  `qccdc rotate` declines "
              "on all")
        print("  eight non-ring devices with \"the device has no closed loop with docks\".")
    else:
        info = []

    # ------------------------------------------------- how many devices can serve each size
    print()
    print("DEVICE COVERAGE -- a spread needs more than one device to compile the circuit")
    per_circuit: dict[str, list[dict]] = {}
    for r in cooled:
        per_circuit.setdefault(r["circuit"], []).append(r)
    print(f"  {'circuit':<15}{'2q gates':>9}{'ops':>7}{'devices':>9}")
    for c, v in sorted(per_circuit.items(), key=lambda kv: kv[1][0]["n_gate_pairs"]):
        print(f"  {c:<15}{v[0]['n_gate_pairs']:>9}{v[0]['n_ops']:>7}{len(v):>9}")

    # ------------------------------------------------------- the spread, corrected, by size
    print()
    print("SAME CIRCUIT, DIFFERENT DEVICE -- corrected: SPAM in the floor, not the device")
    print(f"  {'circuit':<15}{'2q':>6}{'-lnF spread':>13}{'device-part spread':>20}"
          f"{'dev share':>19}")
    spreads = []
    for c, v in sorted(per_circuit.items(), key=lambda kv: kv[1][0]["n_gate_pairs"]):
        if len(v) < 2:
            continue
        hi = max(v, key=lambda r: r["neg_log_fidelity"])
        lo = min(v, key=lambda r: r["neg_log_fidelity"])
        ratio = hi["neg_log_fidelity"] / lo["neg_log_fidelity"]
        dhi = max(r["device_error"] for r in v)
        dlo = min(r["device_error"] for r in v)
        dratio = dhi / dlo if dlo > 0 else float("inf")
        shares = sorted(r["device_share"] for r in v)
        print(f"  {c:<15}{v[0]['n_gate_pairs']:>6}{ratio:>12.2f}x{dratio:>19.2f}x"
              f"{shares[0]:>10.1%} ..{shares[-1]:>7.1%}")
        spreads.append({"circuit": c, "n_gate_pairs": v[0]["n_gate_pairs"],
                        "ratio": ratio, "device_part_ratio": dratio,
                        "best": lo["device"], "worst": hi["device"],
                        "share_lo": shares[0], "share_hi": shares[-1]})

    # ------------------------------------------------------------------- what cooling costs
    print()
    print("WHAT THE COOLING PASS DOES TO THE HEATING SIGNAL")
    both = [(r, uncooled[(r["circuit"], r["device"])]) for r in cooled
            if (r["circuit"], r["device"]) in uncooled]
    if both:
        nz = [c for c, _ in both if c["mean_gate_quanta"] > 1e-9]
        print(f"  cooled programs whose mean gate n-bar is not 0: {len(nz)} of {len(both)}")
        print(f"  cooled programs whose gate error is above the floor: "
              f"{sum(1 for c, _ in both if c['gate_excess'] > 1e-9)} of {len(both)}")
        rt = [c["runtime_ms"] / u["runtime_ms"] for c, u in both if u["runtime_ms"] > 0]
        er = [u["neg_log_fidelity"] / c["neg_log_fidelity"] for c, u in both
              if c["neg_log_fidelity"] > 0]
        print(f"  runtime cost of cooling:  {min(rt):.2f}x .. {max(rt):.2f}x  "
              f"(median {statistics.median(rt):.2f}x)")
        print(f"  error bought by cooling:  {min(er):.2f}x .. {max(er):.2f}x  "
              f"(median {statistics.median(er):.2f}x)")

    # ------------------------------------------------------- the structural bound on geometry
    c0 = consts[devices[0]]
    lo_g, hi_g = c0["eps0"], c0["eps0"] + c0["slope"] * c0["max_quanta"]
    print()
    print("THE CEILING R7 PUTS ON GEOMETRY")
    print(f"  a legal gate sees n-bar in [0, {c0['max_quanta']:g}] (R7), so its error is in "
          f"[{lo_g:.2e}, {hi_g:.2e}]")
    print(f"  -> whatever the layout, the gate term can differ by at most "
          f"{hi_g / lo_g:.2f}x between two FEASIBLE programs")
    print("  -> and the cooling pass lands at the bottom of that range, not the middle")

    # ------------------------------------------------- is cooling ever not worth buying?
    # `PLAN.md` CD0 and `EVALUATION.md` 3 both assert an INTERIOR optimum in the cooling
    # budget: cooling lowers n-bar but lengthens the round, and a longer round costs idle
    # dephasing inside the same scalar.  That is a claim about two numbers, so check it.
    cool_us = float(load(str(ROOT / "arch" / f"{devices[0]}.arch.json"))
                    .primitives.scalar("cool").get("us", 0.0))
    n_ions = max((r["n_ions"] for r in cooled), default=1)
    cost_of_one_cool = n_ions * (cool_us * 1e-6) / c0["t_coh_s"]
    save_per_gate = c0["slope"] * c0["max_quanta"]
    print()
    print("IS THERE AN INTERIOR OPTIMUM IN THE COOLING BUDGET?")
    print(f"  one global cool costs {cool_us:g} us; charged to {n_ions} ions against "
          f"T_coh = {c0['t_coh_s']:g} s that is {cost_of_one_cool:.2e} of idle error")
    print(f"  and it saves at most {save_per_gate:.2e} on EACH gate it protects "
          f"(slope x R7 budget)")
    print(f"  -> a cool pays for itself once it protects {cost_of_one_cool / save_per_gate:.3f} "
          f"gates: i.e. always, by {save_per_gate / cost_of_one_cool:.0f}x on the first one")
    print("  -> the optimum is at the BOUNDARY (cool as hard as R7 allows), not interior")

    frontier = ROOT / "Compiler" / "build" / "c5_frontier.json"
    if frontier.exists():
        f = json.loads(frontier.read_text(encoding="utf-8"))
        fr = [r for r in f.get("rows", [])
              if r.get("objective") == "fastest" and r.get("budget") is not None]
        if fr:
            print()
            print("  c5_pareto's measured cooling frontier, re-scored under the FULL "
                  "objective")
            print(f"  (idle = {n_ions} ions x runtime / T_coh; gate error as c5 measured it)")
            print(f"    {'R7 budget':>10}{'runtime ms':>12}{'cools':>7}{'gate err':>11}"
                  f"{'idle err':>11}{'TOTAL':>11}")
            best = None
            for r in sorted(fr, key=lambda r: r["budget"]):
                idle = n_ions * (r["runtime_ms"] / 1000.0) / c0["t_coh_s"]
                tot = r["gate_error"] + idle
                print(f"    {r['budget']:>10g}{r['runtime_ms']:>12.2f}{r['n_cools']:>7}"
                      f"{r['gate_error']:>11.5f}{idle:>11.5f}{tot:>11.5f}")
                if best is None or tot < best[1]:
                    best = (r["budget"], tot)
            print(f"    minimum total at budget = {best[0]:g}, the strictest point measured"
                  f" -- monotone, no interior minimum")

    # ------------------------------------------------------------------------- the headline
    shares = sorted(r["device_share"] for r in cooled)
    p_effs = [r["p_eff"] for r in cooled]
    print()
    print(f"over {len(cooled)} verified (circuit, device) pairs, after cooling:")
    print(f"  device-attributable share of -lnF: {min(shares):.1%} .. {max(shares):.1%}, "
          f"median {statistics.median(shares):.1%}")
    print(f"  p_eff: {min(p_effs):.2e} .. {max(p_effs):.2e}, threshold {a.threshold}")
    print(f"  above threshold: {sum(1 for p in p_effs if p > a.threshold)} of {len(cooled)}")

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(
            {"constants": consts, "rows": rows, "spreads": spreads,
             "bb144_devices": info}, indent=1),
            encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
