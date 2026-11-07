"""Each Goal 3 submission judged by its own rule set, for board/goal3/'s table of rules.

    python goals/goal3/rules.py --work <work dir> [<work dir> ...] [--code surface3 ...] [--workers 2]

A family's points are one submission of the reference sweep, and a family has its own rule set
(`families.RULES`): the rules of the paper its machine comes from, where they are written in the
rule language, and this website's machine where they are not.  Every point the sweep compiled
(`results/<code>.jsonl`, the last line of a point winning) is judged by its family's set exactly
as the grader judges an entry's declared set (qccd.workspace.evaluator._declared_rules): the
program replayed on its device under the sweep's model, against the circuit its certificate
names, with the set's own parameters.  A size the compiler refused has no program and no entry.

Work folders are found under each `--work` root as pages.py finds them (the sweep ran in several
trees).  A folder whose replay does not end at the row's time holds another compile of the point
(an earlier sweep's) and is passed over; a point with no matching folder stops the script.

Writes `rules.json` beside this file: each set's title and rules; per code, per family its set,
and per point the rules that fail (how often) and those that do not apply or could not be
checked; each reason once.  The site reads it (qccd/site/goal3.py); rerun after a sweep.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sweep  # noqa: E402  (puts the repository on the path)
from families import RULES  # noqa: E402

OUT = HERE / "rules.json"


def compiled(code: str) -> list[dict]:
    """The code's rows that have a program."""
    seen: dict = {}
    for ln in (HERE / "results" / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            seen[(r["family"], r["point"])] = r
    return [r for r in seen.values() if r.get("T_ms") is not None]


def judge(code: str, row: dict, roots: list[Path]) -> dict:
    """The point's program, from the first folder that replays to the row's time, judged by its
    family's set."""
    from qccd.arch import load
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.ruleset import FAILS, HOLDS, check, legacy_config, load as load_rules
    from qccd.ruleset.compile import circuit_of_certificate
    from qccd.ruleset.programme import programme_execution
    rs = load_rules(RULES[row["family"]])
    ends = []
    for r in roots:
        w = r / code / row["point"]
        if not (w / "prog.cooled.tsir.json").is_file():
            continue
        cert = json.loads((w / "prog.qcert.json").read_text(encoding="utf-8"))
        exe = programme_execution(TSIR.load(str(w / "prog.cooled.tsir.json")), load(str(w / "device.arch.json")),
                                  corrected_model(sweep.TABLE), circuit=circuit_of_certificate(cert),
                                  rule_config=legacy_config(rs) or None, check_metrics=False)
        t_ms = max(exe.instant_time) / 1000.0
        if abs(t_ms - row["T_ms"]) > 1e-6 * max(1.0, row["T_ms"]):
            ends.append(f"{w}: {t_ms} ms")
            continue
        rep = check(rs, exe)
        return {"fails": {x.id: x.count for x in rep.results if x.verdict == FAILS},
                "other": {x.id: [x.verdict, x.reason] for x in rep.results if x.verdict not in (HOLDS, FAILS)}}
    raise SystemExit(f"{code}/{row['point']}: no work folder replays to the row's {row['T_ms']} ms"
                     + (" (" + "; ".join(ends) + ")" if ends else ""))


def main() -> None:
    from qccd.ruleset import load as load_rules
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", type=Path, nargs="+", required=True)
    ap.add_argument("--code", nargs="+", default=sorted(p.stem for p in (HERE / "results").glob("*.jsonl")))
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if "sets" not in doc:            # the earlier layout (one shared set) is replaced whole
        doc = {"sets": {}, "reasons": {}, "codes": {}}
    # only the sets the families use now: a set a family has left does not stay listed
    doc["sets"] = {}
    for name in sorted(set(RULES.values())):
        rs = load_rules(name)
        doc["sets"][name] = {"title": rs.title, "ids": rs.ids}
    with ProcessPoolExecutor(a.workers) as pool:
        for code in a.code:
            rows = compiled(code)
            futs = [(row, pool.submit(judge, code, row, a.work)) for row in rows]
            fams: dict = {}
            for row, f in futs:
                v = f.result()
                for rid, (verdict, why) in v.pop("other").items():
                    v.setdefault("skip", []).append(rid)
                    doc["reasons"].setdefault(rid, f"{verdict}: {why}")
                fam = fams.setdefault(row["family"], {"set": RULES[row["family"]], "points": {}})
                fam["points"][row["point"]] = {k: (dict(sorted(x.items())) if isinstance(x, dict) else sorted(x))
                                               for k, x in v.items() if x}
                bad = {k: n for k, n in v["fails"].items()}
                print(f"{code} {row['family']} {row['point']}: {RULES[row['family']]}"
                      + (f" fails {bad}" if bad else " holds"), flush=True)
            doc["codes"][code] = {f: {"set": d["set"], "points": dict(sorted(d["points"].items()))}
                                  for f, d in sorted(fams.items())}
            OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
