#!/usr/bin/env python3
"""Replay this point's hardware program on its machine, and rebuild its noisy stim circuit.

    python regenerate.py

This script uses the QCCD package, from the public repository at the commit the zip was built
from (README.md and requirements.txt give the `pip install` line).  It does the two computed
(not sampled) halves of the data point:

0. THE GATE SET.  The native gate set the program's pulses are in is the one `experiment.json`
   records (`ms`: R, VZ, MS; or `rzz`: R, VZ, RZZ).
1. THE REPLAY.  `hardware_program.tsir.json` is executed step by step on `machine.arch.json`
   under the board's clock: the total time, and the verdict of every rule.  On a board ranked
   by time this is the whole reproduction.
2. THE STIM CIRCUIT (boards with a logical error rate).  The board's noise model writes the
   noisy stim circuit again from the hardware program, the machine, `circuit.qasm` and
   `detectors.json`, and the result is compared with `circuit.stim`, character for character.
   `run.py` then samples that file; it needs none of this.

Both are done by `qccd.qec.reproduce.replay` and `qccd.qec.reproduce.noisy_stim`: the same code
that built the zip.  Exit status 0 when everything is identical, 1 when anything differs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    for stream in (sys.stdout, sys.stderr):     # a title may carry a character the terminal lacks
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        from qccd.qec import reproduce
    except ImportError as exc:
        print(f"regenerate.py needs the QCCD package ({exc}).  Install it as README.md says:\n"
              f"    {_pip()}", file=sys.stderr)
        return 2
    spec = json.loads((HERE / "experiment.json").read_text(encoding="utf-8"))
    was = spec["replay"]
    cert_p = HERE / "certificate.qcert.json"
    cert = json.loads(cert_p.read_text(encoding="utf-8")) if cert_p.is_file() else None
    program = json.loads((HERE / "hardware_program.tsir.json").read_text(encoding="utf-8"))
    machine = json.loads((HERE / "machine.arch.json").read_text(encoding="utf-8"))
    ok = True
    print(f"{spec['board']['title']}  --  {spec['entry']['title']}")

    # ---- 0. the native gate set the program is in (a zip built before the record had it says none)
    if spec.get("gate_set"):
        want, got_gs = spec["gate_set"]["name"], (reproduce.gate_set(program, cert) or {}).get("name")
        ok &= got_gs == want
        print(f"gate set  {want} ({', '.join(spec['gate_set']['pulses'])})"
              + ("   identical" if got_gs == want else f"   DIFFERENT: the program is in {got_gs or 'no one set'}"))

    # ---- 1. the replay
    got = reproduce.replay(program, machine, was["table"], certificate=cert,
                           rule_set=(was.get("rule_set") or {}).get("name"),
                           replay_rules=was.get("rules") is not None)
    pub = (spec["published"]["time"] or {}).get("total_ms")
    same_t = abs(got["total_us"] - was["total_us"]) <= 1e-9 * max(1.0, abs(was["total_us"]))
    ok &= same_t
    print(f"replay    {got['total_ms']:.6f} ms on the {got['table']} table"
          + (f"   (published: {pub} ms)" if pub is not None else "")
          + ("   identical" if same_t else f"   DIFFERENT: the zip was built at {was['total_ms']:.6f} ms"))
    for key, title in (("rules", "rules"), ("rule_set", "rule set")):
        g, w = got.get(key), was.get(key)
        if w is None:
            continue
        same = sorted(g["failed"]) == sorted(w["failed"])
        ok &= same
        name = f" {g['name']}" if key == "rule_set" else ""
        n_ok = len(g.get("passed") or g.get("holds") or [])
        print(f"{title + name:9s} " + (f"every rule holds ({n_ok} checked)" if not g["failed"]
                                      else "breaks " + ", ".join(g["failed"]))
              + ("   identical" if same else f"   DIFFERENT: the zip was built with failed = {w['failed']}"))

    # ---- 2. the noisy stim circuit
    if spec["reproduces"] == "ler":
        text, ex = reproduce.noisy_stim(program, machine, (HERE / "circuit.qasm").read_text(encoding="utf-8"),
                                        json.loads((HERE / "detectors.json").read_text(encoding="utf-8")),
                                        spec["noise"]["id"], cert)
        have = (HERE / "circuit.stim").read_text(encoding="utf-8").replace("\r\n", "\n")
        same = text == have
        ok &= same
        digest = ex.evaluation.get("digest")
        print(f"noise     {spec['noise']['id']}, text digest {str(digest)[:23]}"
              + ("" if digest == spec["noise"]["digest"] else
                 f"   DIFFERENT TEXT: the zip was built with {str(spec['noise']['digest'])[:23]}"))
        print(f"stim      {len(text.splitlines())} lines, {ex.stats.get('detectors')} detectors, "
              f"{ex.stats.get('observables')} observable(s), {ex.stats.get('ions')} qubits"
              + ("   identical to circuit.stim" if same else "   DIFFERENT from circuit.stim"))
        if not same:
            a, b = text.splitlines(), have.splitlines()
            at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            print(f"          first difference at line {at + 1}:\n            rebuilt : "
                  f"{a[at] if at < len(a) else '(end)'}\n            in zip  : {b[at] if at < len(b) else '(end)'}")
    else:
        print("stim      none: this board ranks by time, so the replay above is the whole reproduction")
    print("verdict   " + ("REPRODUCED: everything computed is identical." if ok else
                          "NOT REPRODUCED: see the lines marked DIFFERENT."))
    return 0 if ok else 1


def _pip() -> str:
    try:
        b = json.loads((HERE / "experiment.json").read_text(encoding="utf-8"))["built"]
        return f'pip install "qccd @ git+{b["repository"]}@{b["commit"] or "main"}"'
    except Exception:  # noqa: BLE001
        return "pip install the qccd package from its public repository"


if __name__ == "__main__":
    raise SystemExit(main())
