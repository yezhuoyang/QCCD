"""The worked examples of the QCCDCert paper, with the paper's own inputs.

    python -m qccd.cert.examples [--out qccd/site/compiled/cert]

writes, for each example, the three inputs and everything the compiler produces for them
(`qccd.cert.compile.compile_dir`).  The Compilation page and the tutorial read those files;
nothing on them is typed by hand.

  primer  the running example of the paper's Sections 2-5: storage S[1], transit J[1], gate
          zone G[2], path p0 S->J->G; U, CX, measurement, conditions, reset, barrier
  ex1     one CX and a readout on the same device; a release rule forces three waits
  ex2     a parity check with feedback on a junction device (Tee), under two rule programs
  ex3     three CX on a line of four traps with one broadcast transport channel: broadcast
          transport, the parallel schedule and chain order

The devices and rules are the paper's (its tutorial appendix and running example); the
builders below only write them as the compiler's JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compile import compile_dir, load
from .toolchain import ROOT

OUT = ROOT / "qccd" / "site" / "compiled" / "cert"
PI = "pi"


def rat(n: int) -> dict:
    return {"rational": {"numerator": n, "denominator": 1}}


HALF = {"div": {"left": PI, "right": rat(2)}}
MINUS_HALF = {"neg": {"argument": HALF}}
# The four waveforms of a CX: R(pi/2,pi/2), MS(pi/2), R(-pi/2,0), R(-pi/2,pi/2).
CX_WAVEFORMS = [({"r": {"theta": HALF, "phi": HALF}}, 2), ({"ms": {"theta": HALF}}, 4),
                ({"r": {"theta": MINUS_HALF, "phi": rat(0)}}, 2),
                ({"r": {"theta": MINUS_HALF, "phi": HALF}}, 2)]
CONFIG = {"broadcastPlan": None, "enableBroadcast": True, "initialPlacements": [], "maxBatch": 2,
          "maxCandidates": 256, "maxDepth": 24, "maxPlacements": 64, "maxStates": 2000,
          "routeLookahead": 0, "useOnlyInitialPlacements": False}


def device(name, sites, paths, durations, gate_sites, *, shared_transport=(), wiring=None,
           extra_calibrations=()):
    """A hardware file: sites (name, capacity), paths, their durations, and the gate sites
    (site, optical channel) that get the CX calibrations, a broadcast R, readout and pumping."""
    cals, profiles, readouts, pumps, channels = [], [], [], [], []
    for site, ch in gate_sites:
        cals += [dict(waveform=w, site=site, duration=d, channel=ch) for w, d in CX_WAVEFORMS]
        profiles.append(dict(theta=MINUS_HALF, phi=rat(0), sites=[site], maxPerSite=2, duration=2, channel=ch))
        readouts.append(dict(site=site, duration=3, channel=ch))
        pumps.append(dict(site=site, duration=4, channel=ch))
        channels.append(ch)
    cals = list(extra_calibrations) + cals
    return {"config": CONFIG, "rules": [], "wiring": {"motions": wiring} if wiring else None,
            "device": {"chainLengths": None, "controller": {"duration": 1}, "readouts": readouts,
                       "pumps": pumps,
                       "base": {"name": name, "pathDurations": durations, "opticalChannels": channels,
                                "geometry": {"sites": [dict(name=n, capacity=c, operations=[]) for n, c in sites],
                                             "paths": paths,
                                             "broadcasts": [dict(channel=c, mandatory=False) for c in shared_transport]},
                                "calibrations": cals, "broadcasts": profiles}}}


# ---------------------------------------------------------------------------- rules
def atom(name):
    return {"atom": {"expression": {"base": {"expression": name}}}}


def const(v):
    return {"constant": {"value": v}}


def observe(what, ref):
    r = {"var": {"name": ref}} if isinstance(ref, str) else {"id": {"value": ref}}
    return {"observe": {"observation": what, "reference": r}}


BEFORE = {"occupancy": {"phase": "before"}}
DURING = {"occupancy": {"phase": "during"}}
AT_START = {"and": {"lhs": {"eq": {"lhs": atom("start"), "rhs": const(0)}},
                    "rhs": {"eq": {"lhs": atom("duration"), "rhs": const(0)}}}}

CAP = {"name": "cap", "category": "staticOccupancy",
       "formula": {"all": {"name": "s", "domain": "sites",
                           "body": {"le": {"lhs": observe(DURING, "s"), "rhs": observe("capacity", "s")}}}}}
ONE_OPTICAL = {"name": "one", "category": "operationTiming",
               "formula": {"le": {"lhs": atom("pulseCount"), "rhs": const(1)}}}
PATH_TRAFFIC = {"name": "traffic", "category": "transportControl",
                "formula": {"all": {"name": "p", "domain": "paths",
                                    "body": {"le": {"lhs": observe("pathUse", "p"), "rhs": const(1)}}}}}
ONE_WAVEFORM = {"name": "wave", "category": "transportControl",
                "formula": {"all": {"name": "c", "domain": "channels",
                                    "body": {"le": {"lhs": observe("channelWaves", "c"), "rhs": const(1)}}}}}
ONE_PER_SITE = {"name": "init", "category": "staticOccupancy",
                "formula": {"all": {"name": "s", "domain": "sites",
                                    "body": {"implies": {"lhs": AT_START,
                                                         "rhs": {"eq": {"lhs": observe(BEFORE, "s"), "rhs": const(1)}}}}}}}


def initial(site: int, count: int) -> dict:
    return {"name": "init", "category": "staticOccupancy",
            "formula": {"implies": {"lhs": AT_START, "rhs": {"eq": {"lhs": observe(BEFORE, site), "rhs": const(count)}}}}}


def release(tick: int) -> dict:
    return {"name": "rel", "category": "operationTiming",
            "formula": {"implies": {"lhs": {"le": {"lhs": const(1), "rhs": atom("pulseCount")}},
                                    "rhs": {"le": {"lhs": const(tick), "rhs": atom("start")}}}}}


# ---------------------------------------------------------------------------- examples
def _sub(a, b):
    return {"sub": {"left": a, "right": b}}


def primer_device() -> dict:
    """The running example's device: S[1] -J[1]- G[2], one path p0 of two ticks, every
    optical control at G on channel 7.  c0..c2 serve the U gate of the running example."""
    u = [({"r": {"theta": {"div": {"left": PI, "right": rat(7)}}, "phi": _sub(HALF, rat(0))}}, 2),
         ({"r": {"theta": PI, "phi": rat(0)}}, 2),
         ({"r": {"theta": PI, "phi": {"div": {"left": {"add": {"left": rat(0), "right": rat(0)}}, "right": rat(2)}}}}, 2)]
    return device("Primer", [("S", 1), ("J", 1), ("G", 2)],
                  [dict(src=0, dst=2, via=[1], channel=0, waveform=0)], [2], [(2, 7)],
                  extra_calibrations=[dict(waveform=w, site=2, duration=d, channel=7) for w, d in u])


def examples() -> list[dict]:
    primer = primer_device()
    tee = device("Tee", [("S", 2), ("J", 1), ("G", 2), ("P", 1)],
                 [dict(src=0, dst=2, via=[1], channel=0, waveform=0), dict(src=2, dst=0, via=[1], channel=1, waveform=0),
                  dict(src=2, dst=3, via=[1], channel=2, waveform=0), dict(src=3, dst=2, via=[1], channel=3, waveform=0)],
                 [2, 2, 2, 2], [(2, 7)])
    right = [dict(src=i, dst=i + 1, via=[], channel=0, waveform=0) for i in range(3)]
    left = [dict(src=i + 1, dst=i, via=[], channel=0, waveform=1) for i in range(3)]
    line = device("Line4", [(f"C{i}", 2) for i in range(4)], right + left, [2] * 6,
                  [(i, 10 + i) for i in range(4)], shared_transport=[0], wiring=[["+x"]] * 3 + [["-x"]] * 3)
    ports = {"chains": None, "ports": {"leave": ["right"] * 3 + ["left"] * 3,
                                       "enter": ["left"] * 3 + ["right"] * 3,
                                       "exchangeDuration": [12, 12, 12, 12]}}
    return [
        dict(key="primer", hardware=primer, variants={"": [initial(0, 1), CAP, release(5)]},
             qasm="OPENQASM 2.0;\nqreg q[2];\ncreg c[2];\nU(pi/7,0,0) q[0];\nCX q[0],q[1];\n"
                  "measure q[0] -> c[0];\nif(c==1) CX q[0],q[1];\nif(c==1) measure q -> c;\n"
                  "reset q[0];\nif(c==1) reset q[1];\nbarrier q;\n"),
        dict(key="ex1", hardware=primer, variants={"": [initial(0, 1), CAP, release(5)]},
             qasm="OPENQASM 2.0;\nqreg q[2];\ncreg c[1];\nCX q[0],q[1];\nmeasure q[1] -> c[0];\n"),
        dict(key="ex2", hardware=tee, variants={"": [initial(0, 2), CAP, PATH_TRAFFIC],
                                                "-one-optical": [initial(0, 2), CAP, PATH_TRAFFIC, ONE_OPTICAL]},
             qasm="OPENQASM 2.0;\nqreg q[3];\ncreg c[1];\nCX q[0],q[2];\nCX q[1],q[2];\n"
                  "measure q[2] -> c[0];\nif(c==1) reset q[2];\n"),
        dict(key="ex3", hardware=line, variants={"": [ONE_PER_SITE, CAP, ONE_WAVEFORM]},
             qasm="OPENQASM 2.0;\nqreg q[4];\ncreg c[2];\nCX q[0],q[1];\nCX q[2],q[3];\nCX q[0],q[3];\n"
                  "measure q[0] -> c[0];\nmeasure q[3] -> c[1];\n",
             ports=ports, flags=("--broadcast-pack", "--broadcast-schedule")),
    ]


def build(out: Path = OUT) -> dict:
    summary = {}
    for ex in examples():
        folder = Path(out) / ex["key"]
        for suffix, rules in ex["variants"].items():
            c = compile_dir(folder, ex["hardware"], rules, ex["qasm"], flags=ex.get("flags", ()),
                            chain_ports=ex.get("ports"), suffix=suffix)
            row = {"finish": c.finish, "parallel": c.parallel.get("finish"),
                   "instructions": len(c.execution["instructions"])}
            if c.chain:
                row.update(chain_finish=c.chain["finish"], exchanges=c.chain["exchanges"],
                           final_chains=c.chain["finalChains"])
            summary[ex["key"] + suffix] = row
            print(f"  {ex['key'] + suffix:18s} {row}")
    (Path(out) / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def read(key: str, suffix: str = "", out: Path = OUT):
    ex = next(e for e in examples() if e["key"] == key)
    return load(Path(out) / key, ex["hardware"], ex["variants"][suffix], ex["qasm"], suffix=suffix)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    build(ap.parse_args().out)
