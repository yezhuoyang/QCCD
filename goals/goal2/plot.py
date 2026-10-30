"""Goal 2's two figures per code: time per round, and logical error rate per round, across the
architecture families.  Each family's best point is drawn in the accent colour and labelled;
every other size it was tried at is grey.  Sizes run left to right, smallest machine first.

    python goals/goal2/plot.py [--code surface5 ...]      (needs matplotlib)

A point is a design that compiled, passed the rules and ran BOTH memories (Z and X):
* time per round = the replayed program time under the "quantinuum" table / rounds, the mean
  of its two memories;
* LER per round = 1 - (1 - p_Z)(1 - p_X): a round fails when either memory does; the bar is
  the same combination of the two 95% Wilson bounds.
Writes figures/<code>.{png,pdf,svg}, figures/overview.{png,pdf} and figures/summary.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from families import CAVEATS, FAMILIES  # noqa: E402

ACCENT = "#2a78d6"     # the validated categorical slot 1: the best point of a family
GREY = "#9a9994"       # every other size: context, not a series
INK, INK2 = "#0b0b0b", "#52514e"
GRID = "#e4e3df"


def size_key(r: dict) -> float:
    p = r["params"]
    if r["generator"] == "grid":
        return p["a"] * p["b"]
    return 2 * p.get("width", 0) + p.get("verticals", 0)


def points(code: str) -> tuple[dict, dict]:
    """({family: [point dicts]}, {family: {"tried": n, "refused": n, "rules": n}})."""
    rows: dict = defaultdict(dict)
    f = HERE / "results" / f"{code}.jsonl"
    for ln in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        if ln.strip():
            r = json.loads(ln)
            rows[(r["family"], r["point"])][r["basis"]] = r    # the last line of a point wins
    out: dict = defaultdict(list)
    counts: dict = defaultdict(lambda: {"tried": 0, "refused": 0, "rules": 0, "other": 0})
    for (fam, name), by in rows.items():
        c = counts[fam]
        c["tried"] += 1
        sts = {r["status"] for r in by.values()}
        if len(by) < 2 or sts != {"ok"}:
            c["refused" if "refused" in sts else "rules" if "rules_failed" in sts else "other"] += 1
            continue
        z, x = by["Z"], by["X"]
        pz, px = z["ler"]["per_round"], x["ler"]["per_round"]
        comb = lambda a, b: 1 - (1 - a) * (1 - b)  # noqa: E731
        out[fam].append({
            "name": name, "family": fam, "size": size_key(z), "ions": z.get("ions"),
            "T_round_ms": (z["T_round_ms"] + x["T_round_ms"]) / 2,
            "ler": comb(pz, px),
            "lo": comb(z["ler"]["per_round_lo"], x["ler"]["per_round_lo"]),
            "hi": comb(z["ler"]["per_round_hi"], x["ler"]["per_round_hi"]),
            "ler_z": pz, "ler_x": px, "shots": (z["ler"]["shots"], x["ler"]["shots"]),
            "errors": (z["ler"]["errors"], x["ler"]["errors"]),
            "dacs": (z.get("metrics") or {}).get("dacs"),
        })
    for fam in out:
        out[fam].sort(key=lambda p: p["size"])
    return out, counts


def panel(ax, pts: dict, counts: dict, key: str, label: str, err: bool) -> None:
    import numpy as np
    fams = [f for f in FAMILIES if counts.get(f, {}).get("tried")]
    ax.set_yscale("log")
    for i, fam in enumerate(fams):
        ps = pts.get(fam, [])
        if not ps:
            continue
        best = min(ps, key=lambda p: p[key])
        n = len(ps)
        xs = i + (np.linspace(-0.28, 0.28, n) if n > 1 else np.zeros(1))
        for x, p in zip(xs, ps):
            is_best = p is best
            c = ACCENT if is_best else GREY
            if err:
                ax.plot([x, x], [p["lo"], p["hi"]], color=c, lw=1.0, alpha=0.9 if is_best else 0.6,
                        zorder=2 if is_best else 1, solid_capstyle="round")
            ax.scatter([x], [p[key]], s=46 if is_best else 26, color=c, edgecolors="white",
                       linewidths=1.2, zorder=3 if is_best else 2)
        bx = xs[ps.index(best)]
        ax.annotate(best["name"], (bx, best[key]), xytext=(6, -2), textcoords="offset points",
                    fontsize=7.5, color=INK, va="top", ha="left")
    ticks = []
    for f in fams:
        c = counts[f]
        ok = len(pts.get(f, []))
        bad = c["tried"] - ok
        ticks.append(f"{f}{'*' if f in CAVEATS else ''}\n{ok} of {c['tried']} sizes"
                     + (f"\n{bad} failed" if bad else ""))
    ax.set_xticks(range(len(fams)))
    ax.set_xticklabels(ticks, fontsize=8, color=INK2)
    ax.set_xlim(-0.6, len(fams) - 0.4)
    if key == "T_round_ms":
        # milliseconds read as plain numbers; a log axis would print 4x10^1
        from matplotlib.ticker import LogLocator, NullFormatter, ScalarFormatter
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_ylabel(label, fontsize=9, color=INK)
    ax.grid(axis="y", which="major", color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=INK2, labelsize=8)


def code_title(code: str) -> str:
    m = json.loads((HERE / "codes" / f"{code}_z" / "meta.json").read_text(encoding="utf-8"))
    n, k, d = m["nkd"]
    return f"{m['family']} [[{n},{k},{d}]], {m['rounds']} rounds, {m['ions']} ions"


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", nargs="*", default=[])
    a = ap.parse_args()
    codes = a.code or sorted(p.stem for p in (HERE / "results").glob("*.jsonl"))
    figs = HERE / "figures"
    figs.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.facecolor": "#fcfcfb",
                         "axes.facecolor": "#fcfcfb", "savefig.facecolor": "#fcfcfb"})
    table = ["# Goal 2 — best design per architecture family", "",
             "Each family's best point by each measure (they can differ). Time per round under the "
             "`quantinuum` table; LER per round = 1-(1-p_Z)(1-p_X) under qccd-noise@2, 95% interval.", ""]
    good = []
    for code in codes:
        pts, counts = points(code)
        if not counts:
            continue
        good.append((code, pts, counts))
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.5, 3.9))
        panel(a1, pts, counts, "T_round_ms", "time per syndrome round (ms)", err=False)
        panel(a2, pts, counts, "ler", "logical error rate per round", err=True)
        a1.set_title("(a) time per round", fontsize=9.5, loc="left", color=INK)
        a2.set_title("(b) logical error rate per round (Z and X memories)", fontsize=9.5, loc="left", color=INK)
        fig.suptitle(code_title(code), fontsize=10.5, x=0.01, ha="left", color=INK)
        notes = [f"* {f}: {CAVEATS[f]}" for f in FAMILIES if f in CAVEATS and counts.get(f, {}).get("tried")]
        if any(c["tried"] > len(pts.get(f, [])) for f, c in counts.items()):
            notes.append("failed: the compiler refused the size, or its program broke a rule "
                         "(results/<code>.jsonl says which)")
        fig.tight_layout(rect=(0, 0.035 * len(notes), 1, 1))
        for i, n in enumerate(notes):
            fig.text(0.01, 0.035 * (len(notes) - i) - 0.025, n, fontsize=7.5, color=INK2, ha="left")
        for ext in ("png", "pdf", "svg"):
            fig.savefig(figs / f"{code}.{ext}", dpi=200)
        plt.close(fig)
        table += [f"## {code_title(code)}", "",
                  "| family | sizes compiled | fastest | ms / round | lowest LER | LER / round [95%] |",
                  "|---|---|---|---|---|---|"]
        for fam in FAMILIES:
            ps = pts.get(fam, [])
            c = counts.get(fam)
            if not c or not c["tried"]:
                continue
            if not ps:
                table.append(f"| {fam} | 0 of {c['tried']} | — | — | — | — |")
                continue
            ft = min(ps, key=lambda p: p["T_round_ms"])
            fl = min(ps, key=lambda p: p["ler"])
            table.append(f"| {fam} | {len(ps)} of {c['tried']} | {ft['name']} | {ft['T_round_ms']:.1f} | "
                         f"{fl['name']} | {fl['ler']:.2e} [{fl['lo']:.1e}, {fl['hi']:.1e}] |")
        table.append("")
    if good:
        fig, axes = plt.subplots(len(good), 2, figsize=(10.5, 3.3 * len(good)), squeeze=False)
        for row, (code, pts, counts) in zip(axes, good):
            panel(row[0], pts, counts, "T_round_ms", "ms per round", err=False)
            panel(row[1], pts, counts, "ler", "LER per round", err=True)
            row[0].set_title(code_title(code), fontsize=9.5, loc="left", color=INK)
        notes = [f"* {f}: {CAVEATS[f]}" for f in FAMILIES if f in CAVEATS]
        h = fig.get_figheight()
        fig.tight_layout(rect=(0, 0.11 * len(notes) / h, 1, 1))
        for i, n in enumerate(notes):
            fig.text(0.01, (0.11 * (len(notes) - i) - 0.08) / h, n, fontsize=7.5, color=INK2, ha="left")
        for ext in ("png", "pdf"):
            fig.savefig(figs / f"overview.{ext}", dpi=200)
        plt.close(fig)
    (figs / "summary.md").write_text("\n".join(table) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
