import QCCDC.Cert.Sound

/-!
# The machine's rules, and why parallel work is safe

*Part J, lesson J2. You need lesson J1 (places and their doors) and lesson E3 (rules as
propositions). Everything here is lists, numbers and `decide`, and one real proof.*

A fault-tolerant machine runs many things at once. While a bridge measures `Z̄Z̄` between two
blocks, a factory distils the next magic state, a memory refreshes a resting block, and bundles
of ions travel on roads between them. The scheduler lets anything run as soon as its blocks
are ready and the places, roads and junctions it needs are free. What makes that safe is a
handful of rules, written as assertions in the rule language (the schedule rule set,
`machine.rules` on the Rules page) and checked on every schedule:

```
rule G5 "A place runs one op at a time"
  for all a, b in events where a != b and place(a) = place(b) and duration(a) > 0 and duration(b) > 0:
    not simultaneous(a, b)

rule G13 "A road carries one bundle at a time"
  for all c1, c2 in carries where c1 != c2 and road(c1) = road(c2): not simultaneous(c1, c2)
```

This lesson writes those rules as Lean propositions on a small schedule, decides them, and
then proves the reason they are enough: **two activities that hold different resources
commute** -- in whatever order the machine happens to run them, the result is the same. So a
schedule that keeps every place and every road exclusive may run its activities in parallel.
-/

/-!
## 1. Activities

An **activity** is something the machine does for a while: an op at a place, a pass at a
junction, a bundle on a road. It belongs to one instruction of the program, holds one resource
(the place, junction or road), and runs from `t0` to `t1` (microseconds).
-/

structure Act where
  what : String
  instr : Nat
  res : String
  t0 : Nat
  t1 : Nat
deriving DecidableEq, Repr

/-- Two activities overlap in time (an interval ends where the next may begin). -/
def overlap (a b : Act) : Bool := a.t0 < b.t1 && b.t0 < a.t1

/-- G5 and G13 in one: no two different activities that overlap hold the same resource. -/
def exclusive (acts : List Act) : Bool :=
  acts.all fun a => acts.all fun b => a == b || !overlap a b || a.res != b.res

/-!
Here is a slice of the schedule Shor's algorithm runs on its floorplan `shor`, as the build of
2026-10-20 wrote it (the schedule the design page replays; times in microseconds, to the
microsecond; instruction numbers are
positions in the program after its gates expand and its refresh rounds are added). The factory
produces the magic state for one T gate (instruction 21) while a bridge runs another
instruction's lattice surgery (19) and a memory refreshes a resting block (84); then the magic
ion leaves the factory down the road in front of its door, and the pharmacy injects it.

The same moment on the design page, 1.065 s into the run: `factory1`, `bridge2` and `mem5` are
all at work. Press play to watch the magic state leave the factory.

{{gadget algorithms/shor15 focus=factory1 at=1065000}}
-/

def slice : List Act :=
  [ ⟨"produce", 21, "factory1", 954640, 1149300⟩,
    ⟨"zz", 19, "bridge2", 960050, 1166075⟩,
    ⟨"refresh", 84, "mem5", 1061400, 1074630⟩,
    ⟨"carry", 21, "door_factory1_out", 1148600, 1148650⟩,
    ⟨"inject", 21, "pharmacy2", 1150890, 1180710⟩ ]

#eval exclusive slice

/-!
#### Exercise 1 (★) `slice_exclusive`
Prove that the slice keeps every resource exclusive.
-/

theorem slice_exclusive : exclusive slice = true := by
  decide

/-!
#### Exercise 2 (★) `factory_runs_beside_surgery`
And that it really is parallel: the factory's `produce` overlaps the bridge's `zz`.
-/

theorem factory_runs_beside_surgery : overlap ⟨"produce", 21, "factory1", 954640, 1149300⟩
    ⟨"zz", 19, "bridge2", 960050, 1166075⟩ = true := by
  decide

/-!
A schedule that sent two bundles down one road at once would break G13 -- they could not
even pass each other on a conveyor. The real one never does; this one is made up, on a real
road of the floorplan `shor`.
-/

def collision : List Act :=
  [ ⟨"carry", 12, "street1_j1_2_j1_3", 100000, 101300⟩,
    ⟨"carry", 14, "street1_j1_2_j1_3", 100900, 102200⟩ ]

/-!
#### Exercise 3 (★) `collision_fails`
Prove that `collision` is not exclusive.
-/

theorem collision_fails : exclusive collision = false := by
  decide

/-!
## 2. Capacity

G3 and G4 count ions: no place holds more than its capacity, no road more than its sites.
Give each activity the ions it brings, and count what is inside a resource at a moment.
-/

structure Load where
  res : String
  ions : Nat
  t0 : Nat
  t1 : Nat
deriving DecidableEq, Repr

def inside (ls : List Load) (r : String) (t : Nat) : Nat :=
  ((ls.filter fun l => l.res == r && l.t0 ≤ t && t < l.t1).map (·.ions)).foldl (· + ·) 0

/-- A road of `n` sites holds at most `n` ions, at every moment any load begins. -/
def fits (ls : List Load) (r : String) (n : Nat) : Bool :=
  ls.all fun l => inside ls r l.t0 ≤ n

/-!
A memory has capacity 17: eight resident ancillas and one resting block of nine. In the same
build, `mem4` stores a block from 113,190 µs until it releases it at 215,055 µs, and the next
one from 247,645 µs to 1,144,260 µs.
-/

def memLoads : List Load :=
  [ ⟨"mem4", 8, 0, 2000000⟩,           -- the resident ancillas, all along
    ⟨"mem4", 9, 113190, 215055⟩,       -- one block: store ... release
    ⟨"mem4", 9, 247645, 1144260⟩ ]     -- the next

/-!
#### Exercise 4 (★) `memory_fits`
Prove that `mem4` never holds more than 17 ions in `memLoads`.
-/

theorem memory_fits : fits memLoads "mem4" 17 = true := by
  decide

/-!
#### Exercise 5 (★★) `two_blocks_do_not_fit`
If the second stay began before the first ended -- two blocks in one memory -- it would not
fit. Prove it for `memLoads` with the second stay starting at 200,000 µs.
-/

theorem two_blocks_do_not_fit :
    fits [⟨"mem4", 8, 0, 2000000⟩, ⟨"mem4", 9, 113190, 215055⟩, ⟨"mem4", 9, 200000, 1144260⟩]
      "mem4" 17 = false := by
  decide

/-!
## 3. Why exclusive is enough: activities on different resources commute

Model the machine's state as what every resource holds -- a number per resource is enough to
make the point -- and an activity as a change to *its own* resource only. Then the order of two
activities on different resources cannot matter.
-/

abbrev State := String → Nat

/-- Run activity `a`, whose effect on the resource it holds is `f`. -/
def step (a : Act) (f : Nat → Nat) (s : State) : State :=
  fun r => if r = a.res then f (s r) else s r

/-!
#### Exercise 6 (★★★) `disjoint_commute`
Prove it: if `a` and `b` hold different resources, running `a` then `b` leaves the same state as
running `b` then `a`, whatever their effects. (`funext r` turns an equation between functions
into one per resource; then split on whether `r` is `a`'s resource, and `b`'s.)
-/

theorem disjoint_commute (a b : Act) (f g : Nat → Nat) (s : State) (h : a.res ≠ b.res) :
    step a f (step b g s) = step b g (step a f s) := by
  funext r
  by_cases ha : r = a.res
  · subst ha
    simp [step, h]
  · by_cases hb : r = b.res
    · subst hb
      simp [step, Ne.symm h]
    · simp [step, ha, hb]

/-!
#### Exercise 7 (★★★) `exclusive_overlaps_commute`
Now the statement the scheduler relies on: in an exclusive schedule, any two different
activities that overlap commute. (From `exclusive acts = true`, `simp [exclusive]` gives you
a statement about every pair; then use `disjoint_commute`.)
-/

theorem exclusive_overlaps_commute (acts : List Act) (hx : exclusive acts = true)
    (a b : Act) (ha : a ∈ acts) (hb : b ∈ acts) (hne : a ≠ b) (ho : overlap a b = true)
    (f g : Nat → Nat) (s : State) :
    step a f (step b g s) = step b g (step a f s) := by
  apply disjoint_commute
  intro heq
  simp [exclusive] at hx
  have := hx a ha b hb
  simp [hne, ho, heq] at this

/-!
## What the site checks

The rules on the site are this lesson's propositions over the real schedule: `exclusive` is G5
for places and junctions and G13 for roads, `fits` is G3 and G4, and every one is evaluated on
every schedule the design page shows (its Checks panel lists all of them, each opening to its
own assertion). Shor's run on the floorplan `shor` has 4,435 events and 3,668 carries; G5 and G13 hold
on all of them. Exercise 7 is the reason that is enough: whatever runs at the same time holds
different things, so it could have run in either order.
-/
