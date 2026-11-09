"""The ion machine as FormalRV's compiler reads it (docs/FULLSTACK_PLAN.md, slice S10 step 2): the
`.machine`, `.layout` and loose system specification for Gidney 2025's listing at our parameters.

    python tools/gidney_machine.py layout OUT_DIR                         ion.layout
    python tools/gidney_machine.py machine COLD.qec OUT_DIR [BOARD]       ion.machine, gadgets.sysspec

Every field follows FormalRV's own derivation of the paper machine, row by row
(FormalRV/Audit/Gidney2025/Resource/Inputs/Paper/README.md and Tools/Staged/make_paper_machine.py),
with the paper's numbers replaced by ours:

  distance        hot surface d = 11 (S9's distance study); cold storage FormalRV's
                  YokedSurface2D_d5_w16_ws (S10 step 1: d = 5 holds with a 6x margin)
  board           BOARD tile-equivalents, each one of our tiles, 2 (d + 1)^2 = 288 traps -- the
                  open budget: the paper's 739 (10^6 // 1352) is 3 % short of the gates the
                  background maintenance needs, so the board is the smallest the compiler takes
  gate times      qccd-physics-best: 1Q 5 us, MS 70 us, measure 120 us, reset 10 us
  route latency   the rest of our compiled round (5,822 us, `programs.IDLE` replayed, cooled): its
                  transport and cooling, so that FormalRV's routed round -- reset, one route, an H,
                  five CX slots, the measurement -- is ours to the microsecond.  FormalRV routes
                  only rounds whose checks leave one patch (`local-rounds`): the yoked code's inner
                  round is not routed and FormalRV times it 485 us, where our ions take 5,822 us
                  (FormalRV's count is low there; the concrete time, step 3, is our programs').
                  Putting the transport in the CX slots instead (1,137 us each) times every round
                  right, but FormalRV's scheduler then stretches a hot round to 18 ms and the
                  maintenance overruns the ancilla bandwidth
  services        the paper's rules: max_parallel_gates = every measure ion of the board in one
                  gate; ancilla_bandwidth = those ions reset within one reset (ceil(1000 /
                  reset_ns) qubits/us each, as the VM counts it: the paper's 4 x gates at 250 ns,
                  gates at our 10 us; each tile resets its own ions, so nothing shared binds);
                  classical data = each outcome sent within its readout (ceil(1000 / measure_ns)
                  bits/us each, as the VM counts it); decoders one per patch (126 compute + 144 hot + 1280 cold);
                  decoder_bandwidth one patch's syndrome per round; routing 90 patches reconnected
                  once per round -- each rounded up to a whole unit
  decoder latency one round (S9's assumption: an outcome is decoded within the next round)
  magic states    the paper's 150 rounds per CCZ and 23 per T at our round -- provisional until
                  step 4 builds cultivation at our noise
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

D = 11
TILE = 2 * (D + 1) ** 2            # 288 traps: one stationary tile
BLOCK = D * D                       # a delivered state's data ions
MEASURE = D * D - 1                 # measure ions of a patch
PAPER_BOARD = 1_000_000 // 1352     # the paper's 739 tile-equivalents
PATCHES = 7 * 18 + 8 * 18 + 1280    # compute, hot storage, cold logicals (the paper's figure)
ROUTING = 3 * 18 + 6 * 6            # workspace columns + hot-storage corridors
ROUND_NS = 5_822_000                # `qccd.fullstack.scale.lattice.Costs.round`
GATE1Q, GATE2Q, MEASURE_NS, RESET_NS = 5_000, 70_000, 120_000, 10_000
CX_SLOTS = 5                        # FormalRV's round: reset, route, H, five CX slots, measure
ROUTE_NS = ROUND_NS - (RESET_NS + GATE1Q + CX_SLOTS * GATE2Q + MEASURE_NS)
WINDOW = 10
FACTORIES = 6
CCZ_ROUNDS, T_ROUNDS = 150, 23      # the paper's (provisional: step 4)
FACTORY_TILES = 3 * 4
COLD = "YokedSurface2D_d5_w16_ws"


def per_us(x: float) -> int:
    return max(1, math.ceil(x))


def layout() -> str:
    return (f"Cold {COLD} d=20 wires=[0, expBase + m) stream-to=Hot2[accBase - 6, accBase)\n"
            f"Hot2 surface d={D} wires=[expBase + m, top)\n")


def machine(board: int = PAPER_BOARD) -> str:
    gates = board * MEASURE
    fields = [
        f"qubits: {board * TILE} total", f"factories: {FACTORIES} total", f"max_parallel_gates: {gates} total",
        f"decoders: {PATCHES} total", f"decoder_bandwidth: {per_us(MEASURE * 1000 / ROUND_NS)} bits/us",
        f"decoder_window_rounds: {WINDOW} total",
        f"classical_data_bandwidth: {gates * per_us(1000 / MEASURE_NS)} bits/us",
        f"qubit_routing_bandwidth: {per_us(ROUTING * TILE * 1000 / ROUND_NS)} qubits/us",
        f"ancilla_bandwidth: {gates * per_us(1000 / RESET_NS)} qubits/us",
        f"gate1q_ns: {GATE1Q} ns", f"gate2q_ns: {GATE2Q} ns", f"measure_ns: {MEASURE_NS} ns",
        f"reset_ns: {RESET_NS} ns", f"route_latency_ns: {ROUTE_NS} ns", f"decoder_latency_ns: {ROUND_NS} ns",
        f"magic_state_latency_ns: {CCZ_ROUNDS * ROUND_NS} ns", "pauli_frame_update_ns: 40 ns",
    ]
    out = "machine { " + ", ".join(fields) + " }\n"

    def fac(o, w, rounds, tiles):
        return ("  resource { output_qubits: %d total, workspace_qubits: %d total, latency: %d ns, gates: %d total, "
                "decoders: %d total, classical_data_bandwidth: %d bits/us, qubit_routing_bandwidth: %d qubits/us }\n"
                % (o, w, rounds * ROUND_NS, tiles * MEASURE, tiles, tiles * MEASURE * per_us(1000 / MEASURE_NS),
                   per_us(o * 1000 / ROUND_NS)))
    out += "\nspec factory:\n" + fac(BLOCK, TILE, T_ROUNDS, 1)
    # the CCZ factory's workspace is its twelve tiles less the three its output lands on (FormalRV's
    # owner decision 2026-10-13); |AutoCCZ> adds six tiles of routing pairs (2026-10-09)
    out += "\nspec factory_ccz:\n" + fac(3 * BLOCK, (FACTORY_TILES - 3) * TILE, CCZ_ROUNDS, FACTORY_TILES)
    auto = FACTORY_TILES + 6
    out += "\nspec factory_autoccz:\n" + fac(9 * BLOCK, (auto - 9) * TILE, CCZ_ROUNDS, auto)
    return out


def sysspec(cold_qec: str, mach: str) -> str:
    """FormalRV's loose system specification: every authored gadget may use the machine's services
    and any room; the program's own bounds open.  FormalRV's runtime bound, 10^18 ns, is below what
    the primes take at our round (6.3e18 ns as the VM counts it); 10^21 is as open."""
    f = dict((k.strip(), v.strip()) for k, v in (e.split(":", 1) for e in
             mach.splitlines()[0][len("machine {"):-1].split(",")))
    num = lambda k: int(f[k].split()[0])
    caps = (f"gates: {num('max_parallel_gates')} total, factories: {num('factories')} total, decoders: "
            f"{num('decoders')} total, classical_data_bandwidth: {num('classical_data_bandwidth')} bits/us, "
            f"qubit_routing_bandwidth: {num('qubit_routing_bandwidth')} qubits/us, ancilla_bandwidth: "
            f"{num('ancilla_bandwidth')} qubits/us")
    names = [g for g in re.findall(r"^gadget ([A-Za-z0-9_!]+)\[", cold_qec, re.M) if not g.startswith("surfaceRound")]
    out = "".join(f"spec {g}:\n  resource {{ qubits: 1000000000000 total, runtime: 1000000000000000000000 ns, {caps} }}\n"
                  for g in names)
    out += ("spec program:\n  resource { qubits_min: 1 total, qubits: 1000000000000 total, "
            "runtime_min: 1 ns, runtime: 1000000000000000000000 ns }\n")
    return out


def main(argv) -> None:
    if len(argv) >= 2 and argv[0] == "layout":
        out = Path(argv[1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "ion.layout").write_text(layout(), encoding="utf-8", newline="\n")
        print(layout(), end="")
    elif len(argv) >= 3 and argv[0] == "machine":
        cold, out = Path(argv[1]).read_text(encoding="utf-8"), Path(argv[2])
        out.mkdir(parents=True, exist_ok=True)
        m = machine(int(argv[3]) if len(argv) > 3 else PAPER_BOARD)
        (out / "ion.machine").write_text(m, encoding="utf-8", newline="\n")
        (out / "gadgets.sysspec").write_text(sysspec(cold, m), encoding="utf-8", newline="\n")
        print(m, end="")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
