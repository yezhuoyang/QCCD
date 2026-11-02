"""The rules each Goal 3 point was judged by, for board/goal3/'s table of rules.

    python goals/goal3/rules.py [--code surface3 ...]

For every point the sweep graded (`results/<code>.jsonl`, the last line of a point winning), the
rules its rules stage checked, skipped and checked only in part, as `qccd.verify.verify` reports
them on the point's own device under the sweep's model (corrected_model("quantinuum"),
check_metrics=False).  Which rules verify marks is set by the device (a C2LR lattice adds the C
rules), the model and check_metrics, never by the program, so an empty program on the point's
device gives the lists the sweep's verify gave; each row's `rules_skipped` must agree, or the
script stops.  A size the compiler refused has no program and no entry.

Writes `rules.json` beside this file: the distinct lists once each (`kinds`), every point's
kind, and the release's rule set.  The site reads it (qccd/site/goal3.py); rerun after a sweep.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sweep  # noqa: E402  (puts the repository on the path)

OUT = HERE / "rules.json"


def judged(code: str) -> list[dict]:
    """The code's rows that reached the rules stage."""
    seen: dict = {}
    for ln in (HERE / "results" / f"{code}.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            seen[(r["family"], r["point"])] = r
    return [r for r in seen.values() if "rules_skipped" in r]


def kind_of(row: dict, rel, tmp: Path) -> dict:
    from qccd.arch import load
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.verify import verify
    point = {"family": row["family"], "name": row["point"], "generator": row["generator"],
             "params": row["params"], "control": row.get("control") or {},
             **({"c2lr": row["c2lr"]} if row.get("c2lr") else {})}
    f = tmp / "device.arch.json"
    f.write_text(json.dumps(sweep.device_doc(point, rel)), encoding="utf-8")
    empty = TSIR.from_json({"name": "empty", "arch_spec": str(f), "instructions": []})
    rules = verify(empty, load(str(f)), corrected_model(sweep.TABLE), check_metrics=False).rules
    if sorted(rules.skipped) != sorted(row["rules_skipped"]):
        raise SystemExit(f"{row['code']}/{row['point']}: verify now skips {sorted(rules.skipped)}, "
                         f"the sweep's row says {sorted(row['rules_skipped'])}: rerun the sweep first")
    return {"checked": sorted(rules.checked), "skipped": dict(sorted(rules.skipped.items())),
            "partial": dict(sorted(rules.partial.items()))}


def main() -> None:
    import tempfile
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--code", nargs="+", default=sorted(p.stem for p in (HERE / "results").glob("*.jsonl")))
    a = ap.parse_args()
    doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"kinds": [], "codes": {}}
    kinds = doc["kinds"]
    with tempfile.TemporaryDirectory() as tmp:
        for code in a.code:
            rel = sweep.release(code)
            pts: dict = {}
            for row in judged(code):
                k = kind_of(row, rel, Path(tmp))
                if k not in kinds:
                    kinds.append(k)
                pts[row["point"]] = kinds.index(k)
            doc["codes"][code] = {"set": rel.manifest.get("rules") or "standard", "points": pts}
            print(f"{code}: {len(pts)} points, kinds {sorted(set(pts.values()))}")
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
