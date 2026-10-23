"""R22 -- make every transport cycle one waveform, by calling the pass that does it.

`qccd/compile/uniform.py` splits a `simd` instruction whose ions do unlike things into
one instruction per motion signature, using the same `motion_signature` the verifier's
R22 calls.  This is the command-line front end, and the piece the pass itself has no
business knowing about: the **certificate**.

A certificate's gate witnesses name the instruction that performed each circuit op
(`gates[].instr`).  The pass renumbers, so those handles have to be translated or the
proved checker is handed a witness pointing at whatever instruction now happens to hold
the number.  A gate instruction is never split -- only `simd` instructions are -- so each
old index maps to exactly one new id; the translation takes the LAST id an instruction
became, which is that same instruction for everything but a split transport cycle.

Nothing else in the certificate is an instruction handle.  `moves[].t`, `gates[].t` and
`rotations[].t` are cycle ORDINALS in the certificate's own replay (`check_cert.py`
::`replay_positions`), and splitting a cycle neither adds nor reorders a witness: the same
ions make the same hops between the same gates.  `gates[].dag`, `circuit_ops[].i` and
`unrealised` index the source circuit, not the programme.

    python Compiler/bridge/make_uniform.py build/out/ghz8_grid9x9.tsir.json \
        --arch arch/grid9x9.arch.json -o build/out/ghz8_grid9x9.uniform.tsir.json \
        --cert build/out/ghz8_grid9x9.qcert.json \
        --cert-out build/out/ghz8_grid9x9.uniform.qcert.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.compile.uniform import UniformReport, make_uniform  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402


def rewrite_cert(cert: dict, report: UniformReport) -> tuple[dict, int]:
    """The certificate with every instruction handle translated.  Returns (cert, n)."""
    out = dict(cert)
    n = 0
    gates = []
    for g in cert.get("gates", ()):
        g = dict(g)
        old = g.get("instr")
        news = report.id_map.get(old) if isinstance(old, int) else None
        if news:
            # the LAST id it became.  A gate instruction is never split, so that is the
            # only id it became; taking the last is what makes the rule total anyway.
            g["instr"] = news[-1]
            n += news[-1] != old
        gates.append(g)
    out["gates"] = gates
    return out, n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program", help="a .tsir.json")
    ap.add_argument("--arch", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--cert", default=None,
                    help="a .qcert.json whose instruction handles to translate")
    ap.add_argument("--cert-out", default=None,
                    help="where to write the translated certificate (needs --cert)")
    ap.add_argument("--id-map", default=None,
                    help="also write the old id -> new ids map, as JSON")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args(argv)

    if args.cert and not args.cert_out:
        ap.error("--cert needs --cert-out: the translated certificate has to go somewhere")

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    prog = TSIR.load(args.program)

    r = make_uniform(prog, arch)
    r.program.save(args.out, indent=None)

    n_cert = 0
    if args.cert:
        cert = json.loads(Path(args.cert).read_text(encoding="utf-8"))
        cert, n_cert = rewrite_cert(cert, r)
        Path(args.cert_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.cert_out).write_text(json.dumps(cert) + "\n", encoding="utf-8")

    if args.id_map:
        Path(args.id_map).parent.mkdir(parents=True, exist_ok=True)
        Path(args.id_map).write_text(
            json.dumps(r.id_map_json()) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(dict(r.as_dict(), witnesses_retargeted=n_cert,
                              out=args.out, cert_out=args.cert_out), indent=1))
    else:
        print(f"{prog.name}: {r.cycles_before} -> {r.cycles_after} transport cycles "
              f"({len(r.splits)} instruction(s) split, "
              f"{r.instructions_before} -> {r.instructions_after} instructions)")
        for note in r.notes:
            print(f"  {note}")
        if args.cert:
            print(f"  {n_cert} gate witness(es) retargeted -> {args.cert_out}")
        print(f"  -> {args.out}")
    # A cycle the pass could not order is a programme it did not make legal; say so with
    # the exit status rather than only in the prose.
    return 1 if r.dependency_cycles or r.unsplit else 0


if __name__ == "__main__":
    raise SystemExit(main())
