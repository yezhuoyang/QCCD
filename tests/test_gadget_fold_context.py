"""What a folded body owes the calls inside it, and a block leaving its memory.

Found building FormalRV's full Shor for the site (2026-10-30), after merges began to emit their
extraction rounds at a clinic (1d2766c):

* a body passes its calls the rest places ITS caller holds, not only its own: the top level
  had a block resting in mem26, a gate two levels down rested an ancilla there, and the gate
  between could never fit (`needs mem26, which a resting block holds`; five calls refused,
  and the site's expansion stopped on the first);
* a block that has rested LEAVE_FRESH_US or more takes one round as it leaves its memory: a
  block left at 359 ms with its last round at the call's start, queued for the clinic, and
  met its next round at 520 ms -- past the half budget at a call's edge (G8).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"

NESTED = """\
gate inner b:
  se b 1
gate outer a:
  inner a
prep r Z
prep q Z
outer q
read r Z -> m
decode r
"""

#: a gate whose block rests in its home while twelve others are prepared and read (well over
#: LEAVE_FRESH_US), then is read itself
RESTING = ("gate g x:\n"
           + "".join(f"  prep b{k} Z\n  read b{k} Z -> m{k}\n  decode b{k}\n" for k in range(12))
           + "  read x Z -> u\n  decode x\nprep q Z\ng q\n")


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


@pytest.fixture(scope="module")
def machine(leaves, tmp_path_factory):
    from qccd.gadget.floorplan import load_file, write
    from qccd.gadget.town import town
    rows = [[("depot1", "depot9"), ("prep", "prep_d3"), ("post1", "read_d3")],
            [("mem1", "mem_d3"), ("mem2", "mem_d3"), ("mem3", "mem_d3"), ("depot2", "depot9")]]
    fp = tmp_path_factory.mktemp("context") / "context.floor"
    fp.write_text(write(town(leaves, name="context_d3", rows=rows), title="three memories"), encoding="utf-8")
    return load_file(fp, leaves)


def test_a_body_passes_on_the_places_its_caller_holds(leaves, machine, monkeypatch):
    from qccd.gadget import fold as F
    lib, _ = machine
    seen = []
    orig = F.Folder.macro

    def spy(self, gate, homes, guard, ext, taken, depth):
        seen.append((gate, tuple(taken)))
        return orig(self, gate, homes, guard, ext, taken, depth)
    monkeypatch.setattr(F.Folder, "macro", spy)
    g = F.schedule_folded(F.parse_folded(NESTED, name="nested"), lib, leaves, rounds_at="memory")
    assert g["refused"] == []
    outer = [t for gate, t in seen if gate == "outer"]
    inner = [t for gate, t in seen if gate == "inner"]
    # `r` rests where it is not anyone's home while `outer` runs: taken for `outer`, and so for
    # `inner`, two levels down
    assert outer and outer[0], seen
    assert inner and set(outer[0]) <= set(inner[0]), seen


def test_a_block_that_rested_long_takes_a_round_as_it_leaves(leaves, machine):
    from qccd.gadget import fold as F
    from qccd.gadget.place_checks import check_places
    lib, _ = machine
    g = F.schedule_folded(F.parse_folded(RESTING, name="resting"), lib, leaves, rounds_at="memory")
    assert g["refused"] == []
    m = next(m for m in g["macros"].values() if m["gate"] == "g")
    leaving = [h for h in m["holds"] if h["block"] == "arg0" and h["n"] == 1]
    rel = [e for e in m["events"] if e[4] == "release" and e[3] == m["homes"][0]]
    assert rel and rel[0][1] >= F.LEAVE_FRESH_US, ("the block leaves its home, late enough", rel[:1])
    # (ending as it leaves: a few microseconds before, its memory's place in the stagger)
    assert leaving and any(0 <= rel[0][1] - (h["from"] + h["every"]) < 100 for h in leaving), (leaving, rel[:1])
    ck = check_places(F.view(g, next(k for k, x in g["macros"].items() if x["gate"] == "g")), lib, leaves)
    assert "G8" not in ck["failed"], ck["violations"].get("G8")
