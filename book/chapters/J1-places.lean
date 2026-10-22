import QCCDC.Cert.Sound

/-!
# Places: a gadget is a box with doors and a promise

*Part J, lesson J1. You need Parts A and B of this tutorial (sites, rails, shuttles, gates) and
lesson E1 (a device as data). This lesson starts Part J, where single devices become a whole
fault-tolerant machine; it uses only lists, records and `decide`.*

A fault-tolerant computer is too big to compile ion by ion. The site builds it the way chips are
built: from **places**, each a small verified device with a job -- prepare a logical block,
extract its syndrome, measure `Z̄Z̄` between two blocks by lattice surgery, distil a magic state,
keep a block in memory -- and from **roads** that carry bundles of ions between them. A place is
compiled and checked once (as hardware, by the device rules of Part E; as logic, by the flows
and fault distance its specification states), and then used as a black box with **doors**.

Here is one, the magic-state factory, standing in the town every example runs on. It is
selected: the panel on the right lists its doors -- `in` takes fresh ions, `out` gives the magic
state -- and the timeline below shows when each place is busy.

{{gadget msf15}}

And here is what the box hides: the factory's one op, `produce`, on its own device, every step
of it checked by the device rules.

{{gadget msf15.produce}}

Every door says what crosses it. A door of a place carries **ions** (a bundle of `width` ions
with a `role`: a block's `data`, a `magic` state, `fresh` ions from a depot) or **bits** (a
`signal`: a `syndrome` for the decoder, an `outcome`, a `decision` that switches an op on).
Each is entered (`in`), left (`out`), or both (`inout`, the four arms of a junction).

This lesson writes doors as Lean data -- the real ones, as the place library declares them
-- and the first floorplan rule as a Lean function.
-/

/-!
## 1. Doors
-/

inductive Medium where
  | ions
  | bits
deriving DecidableEq, Repr

inductive Dir where
  | inward
  | outward
  | both
deriving DecidableEq, Repr

structure Door where
  name : String
  medium : Medium
  dir : Dir
  role : String            -- ions: "data", "magic", "fresh" or "any"; bits: "any"
  width : Nat              -- ions in one bundle
  code : Option String     -- the code a data bundle is a block of
  signal : Option String   -- bits: "syndrome", "outcome", "decision", "frame"
deriving DecidableEq, Repr

/-- Doors of the verified places (`python -m qccd.gadget places` prints them all). -/
def bridgeInA : Door := ⟨"ls_d3.in_a", .ions, .inward, "data", 9, some "surface_d3", none⟩
def bridgeOutA : Door := ⟨"ls_d3.out_a", .ions, .outward, "data", 9, some "surface_d3", none⟩
def bridgeSyn : Door := ⟨"ls_d3.syn", .bits, .outward, "any", 0, none, some "syndrome"⟩
def junctionArm : Door := ⟨"junc9.s", .ions, .both, "any", 9, none, none⟩
def factoryOut : Door := ⟨"msf15.out", .ions, .outward, "magic", 1, none, none⟩
def pharmacyMagic : Door := ⟨"inj_d3.magic", .ions, .inward, "any", 1, none, none⟩
def pharmacyIn : Door := ⟨"inj_d3.in", .ions, .inward, "fresh", 8, none, none⟩
def decoderSyn : Door := ⟨"dec_d3.syn", .bits, .inward, "any", 0, none, some "syndrome"⟩
def workshopCtl : Door := ⟨"tcx_d3.ctl", .bits, .inward, "any", 0, none, some "decision"⟩
def archiveCtl : Door := ⟨"arch1.ctl", .bits, .outward, "any", 0, none, some "decision"⟩

/-!
## 2. F1: a road joins two doors that agree

The floorplan rule F1, as the site states it in the rule language (the floorplan rule set,
`floorplan.rules` on the Rules page):

```
rule F1 "A road or a wire joins two doors that agree" core
  (for all r in roads, a in ends(r), b in ends(r) where a != b:
     medium(a) = medium(b)
     and (role(a) = role(b) or role(a) = "any" or role(b) = "any")
     and (role(a) != "data" or role(b) != "data" or
          (width(a) = width(b) and (code(a) = none or code(b) = none or code(a) = code(b))))
     and (dir(a) != dir(b) or dir(a) = "inout"))
  and (for all w in wires, a in ends(w), b in ends(w) where a != b:
     medium(a) = medium(b)
     and (signal(a) = signal(b) or signal(a) = "any" or signal(b) = "any")
     and (dir(a) != dir(b) or dir(a) = "inout"))
```

Here is the same condition on one pair of doors, as a Lean function that computes a `Bool`.
-/

def roleOk (a b : Door) : Bool :=
  a.role == b.role || a.role == "any" || b.role == "any"

def bundleOk (a b : Door) : Bool :=
  !(a.role == "data" && b.role == "data") ||
    (a.width == b.width && (a.code.isNone || b.code.isNone || a.code == b.code))

def signalOk (a b : Door) : Bool :=
  a.signal == b.signal || a.signal == some "any" || b.signal == some "any"

def dirOk (a b : Door) : Bool := a.dir != b.dir || a.dir == .both

def accepts (a b : Door) : Bool :=
  a.medium == b.medium && dirOk a b &&
    (match a.medium with
     | .ions => roleOk a b && bundleOk a b
     | .bits => signalOk a b)

#eval accepts junctionArm bridgeInA
#eval accepts bridgeSyn junctionArm

/-!
#### Exercise 1 (★) `road_to_a_bridge`
A road from a junction's arm to a bridge's door is allowed. Prove it.
-/

theorem road_to_a_bridge : accepts junctionArm bridgeInA = true := by
  decide

/-!
#### Exercise 2 (★) `factory_to_pharmacy`
A factory's magic ion may go straight to the pharmacy's magic door.
-/

theorem factory_to_pharmacy : accepts factoryOut pharmacyMagic = true := by
  decide

/-!
#### Exercise 3 (★) `bits_are_not_ions`
No road joins a door of bits to a door of ions: a syndrome never travels on a conveyor.
-/

theorem bits_are_not_ions : accepts bridgeSyn junctionArm = false := by
  decide

/-!
#### Exercise 4 (★) `wires_carry_their_signal`
A wire from the classical memory's decision door to a workshop's control door is fine; a
wire from a bridge's syndrome door to that control door is not.
-/

theorem wires_carry_their_signal :
    accepts archiveCtl workshopCtl = true ∧ accepts bridgeSyn workshopCtl = false := by
  decide

/-!
#### Exercise 5 (★★) `outs_never_meet`
Two doors that are both left (`outward`) never accept each other, whatever else they say.
Prove it for all doors. (`simp [accepts, dirOk, ha, hb]` unfolds what you need.)
-/

theorem outs_never_meet (a b : Door) (ha : a.dir = .outward) (hb : b.dir = .outward) :
    accepts a b = false := by
  simp [accepts, dirOk, ha, hb]

/-!
#### Exercise 6 (★★) `data_widths_match`
When two data doors accept each other, they carry bundles of the same width. Prove it.
-/

theorem data_widths_match (a b : Door) (h : accepts a b = true) (hm : a.medium = .ions)
    (ra : a.role = "data") (rb : b.role = "data") : a.width = b.width := by
  simp [accepts, hm, bundleOk, ra, rb] at h
  exact h.2.2.1

/-!
## 3. A place's promise: what goes in comes out

An op of a place moves bundles through its doors. Its **transfers** say which door, which way,
and how many ions -- measured by replaying the op's program, not typed beside it. A place keeps
its own resident ions (a bridge's 23 ancillas, a pharmacy's 8): every op ends with them home,
so whatever comes in, in one op, goes out again in the same op.
-/

structure Transfer where
  door : String
  dir : Dir
  count : Nat
deriving DecidableEq, Repr

def ins (ts : List Transfer) : Nat := ((ts.filter (·.dir == .inward)).map (·.count)).foldl (· + ·) 0
def outs (ts : List Transfer) : Nat := ((ts.filter (·.dir == .outward)).map (·.count)).foldl (· + ·) 0

/-- `ls_d3.zz` (203.5 ms) and `inj_d3.inject` (28.6 ms), as characterized. -/
def zz : List Transfer :=
  [⟨"in_a", .inward, 9⟩, ⟨"in_b", .inward, 9⟩, ⟨"out_a", .outward, 9⟩, ⟨"out_b", .outward, 9⟩]
def inject : List Transfer :=
  [⟨"magic", .inward, 1⟩, ⟨"in", .inward, 8⟩, ⟨"out", .outward, 9⟩]

/-!
#### Exercise 7 (★) `ops_conserve_ions`
Prove that both ops send out exactly as many ions as they take in.
-/

theorem ops_conserve_ions : ins zz = outs zz ∧ ins inject = outs inject := by
  decide

/-!
#### Exercise 8 (★★) `injection_makes_a_block`
The pharmacy turns one magic ion and eight fresh ions into one block of nine: its out door is
a data door of the code, so it is accepted by a bridge's in door. Prove that the pharmacy's
`out` door, written below, accepts `bridgeInA`.
-/

def pharmacyOut : Door := ⟨"inj_d3.out", .ions, .outward, "data", 9, some "surface_d3", none⟩

theorem injection_makes_a_block : accepts pharmacyOut bridgeInA = true := by
  decide

/-!
## What is checked, and what is not

F1 judges each road at its two ends, so a junction's arm, which accepts any role, joins any
door. A road never checks that what arrives through a chain of junctions is what the far door
expects. That is the schedule's job: G1 requires every bundle an op takes in to arrive in the
exact order its verified program takes it, and G2 follows every ion. Lesson J2 writes those
schedule rules down; lesson J3 builds whole floorplans out of places and roads.
-/
