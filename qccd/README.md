# The platform — design it, program it, evaluate it

The main [README](../README.md) is the tour. This is the working detail: how a device is
built, how a program is written, and what `run` actually reports.

| | |
|---|---|
| [`arch/`](arch/) | the architecture description language and its generators |
| [`ir/`](ir/) | TSIR, the control IR a hardware program is written in |
| [`verify/`](verify/) | the replay and the 25 rules, as machine-checkable invariants |
| [`cost/`](cost/) | the objective: combinatorial (steps, templates) and physical (µs, quanta) |
| [`compile/`](compile/) | placement, ordering, cooling insertion, the program builders |
| [`viz/`](viz/) | the renderer, and the browser design tool it emits |
| [`phys/`](phys/) | the electrodes a device implies: polygons, the RF field, GDSII |

Reference: [architecture language](../docs/adl.md) · [control IR](../docs/tsir.md) ·
[the rules](../docs/rules.md) · [the electrodes](../docs/phys.md)

---

## 1 · Design an architecture

![designing a device](../docs/img/design.gif)

A device is a **graph of traps and the segments between them**. Nothing declares "this is a
junction": attach a spur to a rail node and it *becomes* degree 3, and the cost model finds
that out by counting. Above, one parameter — the number of vertical shuttling lines — trades
ancillas against junctions.

Three ways in, all producing the same object:

```python
from qccd import Machine

m = Machine.ring(width=72, height=2, verticals=24, name="my_ring")   # a generator
#   Machine.grid(9, 9)                              …or a lattice
#   Machine.load("arch/ring144_24v.arch.json")      …or a document on disk

m.set_zone("data", capacity=4)                          # retune the device
m.set_curve("shuttle_segment", [(5, 0.10), (12, 1.0)])  # its (µs, quanta) physics
m.save("arch/my_ring.arch.json")
```

- **Python** — `qccd.Machine`, above. See [examples/program_in_python.py](../examples/program_in_python.py).
- **A document** — [`arch/*.arch.json`](../arch/), the architecture description language
  ([docs/adl.md](../docs/adl.md)).
- **A browser** — `python -m qccd studio -o out/studio.html` opens a design tool as one
  self-contained page: drag traps, draw segments, write the program. `python -m qccd open
  my_design.qccd.json` brings it back for the full verifier.

Then program it. Space is node ids, time is cycles, and **one cycle is one machine step**:

```python
p = m.program("one_turn")
p.init({f"d{i}": f"S{i}" for i in range(144)})
p.rotate(+13)                                      # one template, every ion
```

## 2 · Evaluate it

![evaluating a design](../docs/img/evaluate.gif)

`run` replays the program instruction by instruction, charges every hop to a cost model,
and reports what the [25 rules](../docs/rules.md) say about it:

```python
r = m.run(p)                          # replay + every rule + the objective
r.cost, r.steps, r.runtime_ms, r.rules_failed
m.render(p, "out/my_ring.html")       # one self-contained, animated page
```

A rule reports *passed* only if the check actually ran — otherwise it says `skipped`, with
the reason. And the rules bite:

```python
bad = m.program("squeeze")
bad.init({"d0": "S0", "d1": "S1", "d2": "S143"})
with bad.cycle("shuttle") as c:                    # one machine step
    c.move("d1", "S1", "S0")
    c.move("d2", "S143", "S0")

m.run(bad).rules_failed
# ['R1', 'R11', 'R2', 'R4']
#  [R1] site S0 holds 3 ions but capacity is 2
#  [R2] 2 ions cross junction S0 in one cycle
#  [R4] channel 'linear_h.all.0' drives 168 sites with one waveform, but they are
#       asked to do 2 different things (L0:+1 and L0:-1); that needs 2 channels
```

`run` exits non-zero when a rule fails, and on the shipped schedule two do — under the
default cost model the deck's own program blows its heating budget, because it schedules no
cooling anywhere. That is a result, not a broken install; add `--model deck` to reproduce
the artifact's own figures instead (397,184 cost / 8,808 steps, all rules pass).

## 3 · Sweep a design knob

Beyond a single run, `python -m qccd sweep` varies one parameter and prints the curve —
the question an architect actually asks:

```console
$ python -m qccd sweep budget ring144_24v scale.junction 0.25,0.5,1,2
budget on ring144_24v: scale.junction over 4 settings
scale.junction     total_error   heating_error     floor_error  dominant_share
------------------------------------------------------------------------------
0.25                  671.7777        670.1880          1.5898          0.4381
0.5                   965.3922        963.8025          1.5898          0.6093
1                    1552.6212       1551.0315          1.5898          0.7572
2                    2727.0792       2725.4895          1.5898          0.8618
```

Junction heating is 76% of the summed gate error at nominal — which is what makes the
number of vertical shuttling lines a design decision rather than a detail. See
`python -m qccd analyses` for the rest, and `python -m qccd reach <device>` for what a
machine can do before anyone writes a program for it.

## Every command

| | |
|---|---|
| `python -m qccd studio` | **the design tool**, as one page → `out/studio.html` |
| `python -m qccd demo` | every device × program, rendered → `out/index.html` |
| `python -m qccd devices` · `show <device>` | the architectures, and one in detail |
| `python -m qccd run ring144_24v --program deck --html out/r.html` | replay, rule-check, price, render |
| `python -m qccd reach <device>` | what a machine can do before any program exists |
| `python -m qccd analyses` · `sweep <analysis> <device> <knob> <values>` | the knobs, and one curve |
| `python -m qccd phys <device> [--svg f.svg] [--html f.html]` | the electrodes it implies, and their design rules |
| `python -m qccd gds <device> -o x.gds` | the same metal as GDSII, for a fab tool |
| `python -m qccd open <export>` | re-verify a design the browser produced |
| `python -m qccd regen` | rebuild every emitted page |

`studio` also opens on a **compiled** program, with the circuit beside it:

```bash
python -m qccd studio --tsir Compiler/build/out/steane.cooled.tsir.json \
    --qasm Compiler/examples/steane_esm.qasm -o out/studio.html
```

## Tests

```bash
python -m pytest
```

A new architecture is a `.arch.json` in [`arch/`](../arch/); a new routing strategy is a
builder in [`compile/programs.py`](compile/programs.py); a new claim about hardware is a
rule in [`verify/rules.py`](verify/rules.py) **with a test that breaks it**.
