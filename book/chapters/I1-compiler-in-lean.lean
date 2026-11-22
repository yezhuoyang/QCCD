import QCCDC.Cert.Sound

/-!
# The inputs, the event frame and the core checks

*Part I, lesson I1. You need Part E (machines and programmes as data) and the Lean of Part F.*

This part teaches one compiler exactly: **QCCDCert**, the verified compiler of the paper
"QCCDCert: Formally Verified Compilation for QCCD Co-Design". It reads three inputs, a
**hardware program** that describes a device, a **rule program** that states how the device may
be operated, and an **OpenQASM** source, and it returns **target code** with a timeline. Its
correctness is one theorem, `verified_compilation`:

```
compile(H, R, P, κ) ⇓ (K, T)   ⟹   Meaning ∧ Legality ∧ Time
```

for every hardware program `H`, rule program `R`, source `P` and search configuration `κ`:
if the compiler returns target code `K` with timeline `T`, then `K` computes what `P` computes
(**Meaning**), every event of `K` passes the device's core checks and every rule of `R`
(**Legality**), and the reported finish is the time the device declares (**Time**). Lesson I2 is
Legality, I3 Meaning, I4 Time, and I5 puts the theorem together and says what an extension of
the compiler must do to keep it. The compiler of this platform is such an extension: whatever it
adds, the hardware syntax, the assertion syntax, the passes and the proof are QCCDCert's.

**About the Lean on these pages.** QCCDCert is written and proved in Lean. Its definitions carry
more than a page can run (exact source positions, real-valued angles, quantum states, every
optional feature). The Lean here is a **faithful miniature** of the QCCDCert declarations it
names: the same names (`Frame`, `CoreLegal`, `Legal`, `Run`, `evalRule_correct`,
`Assertions.elaborate_correct`, `verified_compilation`, ...), the same statement shapes, the same
checks, small enough for your browser. Where the miniature simplifies, the text says so. Every
example is a real compilation from the paper: the inputs are the ones printed there, and every
listing and number is the compiler's own output. The [Compilation page](../compilation/#inputs)
draws the same three examples.
-/

/-!
## 1. Three inputs

Example 1 of the paper ([on the Compilation page](../compilation/#ex1)) compiles one CNOT and a
measurement for a three-site device. Here are its three inputs, as the compiler prints them.

The **hardware program** declares what the device can do. It has three sites with their
capacities, one path, one optical channel, a calibration for every pulse the laser at `G` can
play, a broadcast profile, a readout (`acquire`), a reset (`pump`) and the controller's latency:

```
hardware Primer; site S[1]; site J[1]; site G[2];
path p0:S->J->G channel 0 waveform 0 duration 2;
optical channel 7;
calibration c0:R(pi/7,pi/2-0) @G channel 7 duration 2;
calibration c1:R(pi,0) @G channel 7 duration 2;
calibration c2:R(pi,(0+0)/2) @G channel 7 duration 2;
calibration c3:R(pi/2,pi/2) @G channel 7 duration 2;
calibration c4:MS(pi/2) @G channel 7 duration 4;
calibration c5:R(-pi/2,0) @G channel 7 duration 2;
calibration c6:R(-pi/2,pi/2) @G channel 7 duration 2;
broadcast b0:R(-pi/2,0) sites G channel 7 duration 2 limit 2;
acquire a0 @G channel 7 duration 3;
pump p0 @G channel 7 duration 4;
controller duration 1;
```

A **site** is any place an ion can stay or pass through: a trap, a junction, a gate zone. `S` is
a storage site for one ion, `J` a transit site for one, `G` a gate zone for two. A **path** is a
declared transport from a source site to a destination site, with the sites it passes through
(`p0` goes from `S` through `J` to `G`), the transport **channel** that drives it, the
**waveform** that channel applies for it, and its duration in ticks. A **calibration** binds one
native operation (a rotation `R(θ,φ)` of one ion or a Mølmer-Sørensen gate `MS(θ)` of two) to
a site, an optical channel and a duration. No declaration says where the ions start: that is the
compiler's choice.

The **rule program** adds operating rules, each an assertion over one event (lesson I2):

```
assert init: (t = 0 and delta = 0) => N-(S) = 1;
assert cap:  forall s:Sites. Ntr(s) <= cap(s);
assert rel:  n_optical > 0 => t >= 5;
```

The **source** is ordinary OpenQASM 2:

```
OPENQASM 2.0;
qreg q[2];
creg c[1];
CX q[0],q[1];
measure q[1] -> c[0];
```

and the **target code** the compiler returns is nine events after an initial placement:

```
init q0@S,q1@G;
transport q0:S->G[p0];                 // t=0, d=2
idle 1;                                // t=2, d=1
idle 1;                                // t=3, d=1
idle 1;                                // t=4, d=1
pulse R(pi/2,pi/2) q0 @G via c3;       // t=5, d=2
pulse MS(pi/2) q0,q1 @G via c4;        // t=7, d=4
broadcast R(-pi/2,0) q0,q1 @G via b0;  // t=11, d=2
pulse R(-pi/2,pi/2) q0 @G via c6;      // t=13, d=2
readout q1 -> c0 @G via a0;            // t=15, d=3
```

`q0` travels from `S` through `J` to join `q1` in `G`; the CNOT becomes five pulses (two of them
shared in one broadcast); the readout writes `c0`. Every instruction names the declared resource
it uses: `[p0]` a path, `via c3` a calibration, `via b0` a broadcast profile, `via a0` an
acquisition. The three `idle 1` are there because of the rule `rel`, as lesson I2 proves.

## 2. The hardware program as data

QCCDCert keeps an angle as its exact expression tree, never as a floating-point number: `pi/2`
is `pi` divided by the rational `2/1`. Two trees with the same value are still different trees,
and a pulse is played only by a calibration whose tree is the same.
-/

inductive Expr where
  | pi
  | rat (num : Int) (den : Nat)
  | neg (a : Expr)
  | add (a b : Expr)
  | sub (a b : Expr)
  | div (a b : Expr)
  deriving DecidableEq, Repr

def e0 : Expr := .rat 0 1                -- 0
def half : Expr := .div .pi (.rat 2 1)   -- pi/2
def mhalf : Expr := .neg half            -- -pi/2

/-- A native waveform: `R(θ,φ)` on one ion, `MS(θ)` on two. -/
inductive Wave where
  | r (theta phi : Expr)
  | ms (theta : Expr)
  deriving DecidableEq, Repr

/-!
Sites, paths and channels are numbered in the order they are declared; a name is for display.
In Example 1, `S`, `J`, `G` are sites 0, 1, 2, `p0` is path 0 and `c3` is calibration 3.
-/

structure Site where
  name : String
  capacity : Nat
  deriving Repr

structure Path where
  src : Nat
  via : List Nat
  dst : Nat
  channel : Nat
  waveform : Nat
  duration : Nat
  deriving Repr

structure Calibration where
  wave : Wave
  site : Nat
  channel : Nat
  duration : Nat
  deriving Repr

/-- `broadcast b: R(θ,φ) sites ... channel ... duration ... limit n`: one optical shot that
plays the same `R` on several ions, at most `n` per site. -/
structure BroadcastProfile where
  wave : Wave
  sites : List Nat
  channel : Nat
  duration : Nat
  limit : Nat
  deriving Repr

/-- `acquire a @s channel c duration d` (readout); a `pump` (reset) has the same fields. -/
structure AcquisitionCalibration where
  site : Nat
  channel : Nat
  duration : Nat
  deriving Repr

abbrev PumpCalibration := AcquisitionCalibration

/-!
A `Device` collects the declarations. `shared` lists the transport channels declared
`share channel n optional` (`false`) or `mandatory` (`true`); a channel not listed drives its
paths alone. `controller` is the latency of the latch, if the device has one. (QCCDCert's
`Device` also carries the list of optical channels and, optionally, gate calibrations by chain
length; the miniature leaves them out.)
-/

structure Device where
  name : String
  sites : List Site
  paths : List Path
  shared : List (Nat × Bool)
  calibrations : List Calibration
  broadcasts : List BroadcastProfile
  readouts : List AcquisitionCalibration
  pumps : List PumpCalibration
  controller : Option Nat
  deriving Repr

def primer : Device where
  name := "Primer"
  sites := [⟨"S", 1⟩, ⟨"J", 1⟩, ⟨"G", 2⟩]
  paths := [⟨0, [1], 2, 0, 0, 2⟩]                                   -- p0:S->J->G
  shared := []
  calibrations :=
    [⟨.r (.div .pi (.rat 7 1)) (.sub half e0), 2, 7, 2⟩,             -- c0:R(pi/7,pi/2-0)
     ⟨.r .pi e0, 2, 7, 2⟩,                                          -- c1:R(pi,0)
     ⟨.r .pi (.div (.add e0 e0) (.rat 2 1)), 2, 7, 2⟩,               -- c2:R(pi,(0+0)/2)
     ⟨.r half half, 2, 7, 2⟩,                                       -- c3:R(pi/2,pi/2)
     ⟨.ms half, 2, 7, 4⟩,                                           -- c4:MS(pi/2)
     ⟨.r mhalf e0, 2, 7, 2⟩,                                        -- c5:R(-pi/2,0)
     ⟨.r mhalf half, 2, 7, 2⟩]                                      -- c6:R(-pi/2,pi/2)
  broadcasts := [⟨.r mhalf e0, [2], 7, 2, 2⟩]                        -- b0
  readouts := [⟨2, 7, 3⟩]                                           -- a0
  pumps := [⟨2, 7, 4⟩]
  controller := some 1

/-!
#### Exercise 1 (★) `primer_capacities`
The sites `S`, `J`, `G` hold one, one and two ions.
-/

theorem primer_capacities : primer.sites.map Site.capacity = [1, 1, 2] := by
  decide +kernel

/-!
#### Exercise 2 (★) `exact_trees`
`c1` and `c2` both play a rotation by π about the axis at angle 0, since `(0+0)/2` is 0. Prove
that QCCDCert still treats them as different waveforms: their trees differ.
-/

theorem exact_trees :
    (primer.calibrations[1]?).map Calibration.wave ≠ (primer.calibrations[2]?).map Calibration.wave := by
  decide +kernel

/-!
## 3. Target code as data

The target code is an initial placement and a flat list of instructions. A `transport` lists
its moves, each an ion and a declared path: one instruction can move several ions at once
(lesson I4). A pulse, broadcast, readout or pump names the declaration it uses and carries a
`guarded` flag: `true` is the prefix `when saved`, which makes it act only if the last latch
saved `true` (lesson I2). A placement gives each ion its site.
-/

structure Move where
  ion : Nat
  path : Nat
  deriving DecidableEq, Repr

inductive HardwareInstruction where
  | transport (moves : List Move)
  | pulse (wave : Wave) (ions : List Nat) (guarded : Bool) (calibration : Nat)
  | broadcast (profile : Nat) (members : List (Wave × Nat)) (guarded : Bool)
      (calibrations : List Nat)
  | readout (ion bit : Nat) (guarded : Bool) (acquisition : Nat)
  | pump (ion : Nat) (guarded : Bool) (pump : Nat)
  | latch (bits : List Nat) (value : Nat)
  | idle (duration : Nat)
  | barrier (ions : List Nat) (guarded : Bool)
  deriving DecidableEq, Repr

abbrev Placement := List Nat

structure Execution where
  initial : Placement
  instructions : List HardwareInstruction
  deriving Repr

/-!
Example 1's target code, line for line. A broadcast lists its members (waveform and ion) and,
for each member, the calibration it stands for: here `c5` twice.
-/

def ex1 : Execution where
  initial := [0, 2]                                     -- init q0@S,q1@G
  instructions :=
    [.transport [⟨0, 0⟩],                               -- transport q0:S->G[p0]
     .idle 1, .idle 1, .idle 1,
     .pulse (.r half half) [0] false 3,                 -- pulse R(pi/2,pi/2) q0 @G via c3
     .pulse (.ms half) [0, 1] false 4,                  -- pulse MS(pi/2) q0,q1 @G via c4
     .broadcast 0 [(.r mhalf e0, 0), (.r mhalf e0, 1)] false [5, 5], -- broadcast ... via b0
     .pulse (.r mhalf half) [0] false 6,                -- pulse R(-pi/2,pi/2) q0 @G via c6
     .readout 1 0 false 0]                              -- readout q1 -> c0 @G via a0

/-!
## 4. What an instruction does to placement and clock

An event changes the placement only if it is a transport: every ion with a move goes to its
path's destination, all at once (`nextPlacement`). Its duration is what the hardware program
declares (`instructionDuration`): a transport takes its longest path, a pulse its calibration,
a broadcast its profile, a readout or pump its declaration, a latch the controller's latency,
`idle n` takes `n` and a barrier 0. Lesson I4 is about these numbers.
-/

def destination (d : Device) (moves : List Move) (ion here : Nat) : Nat :=
  match moves.find? (fun m => m.ion == ion) with
  | some m => ((d.paths[m.path]?).map Path.dst).getD here
  | none => here

def relocate (d : Device) (moves : List Move) : Nat → Placement → Placement
  | _, [] => []
  | ion, here :: rest => destination d moves ion here :: relocate d moves (ion + 1) rest

def nextPlacement (d : Device) (pos : Placement) : HardwareInstruction → Placement
  | .transport moves => relocate d moves 0 pos
  | _ => pos

def maxOf : List Nat → Nat
  | [] => 0
  | x :: xs => max x (maxOf xs)

def instructionDuration (d : Device) : HardwareInstruction → Nat
  | .transport moves => maxOf (moves.map fun m => ((d.paths[m.path]?).map Path.duration).getD 0)
  | .pulse _ _ _ k => ((d.calibrations[k]?).map Calibration.duration).getD 0
  | .broadcast b _ _ _ => ((d.broadcasts[b]?).map BroadcastProfile.duration).getD 0
  | .readout _ _ _ a => ((d.readouts[a]?).map AcquisitionCalibration.duration).getD 0
  | .pump _ _ p => ((d.pumps[p]?).map AcquisitionCalibration.duration).getD 0
  | .latch _ _ => d.controller.getD 0
  | .idle n => n
  | .barrier _ _ => 0

/-!
#### Exercise 3 (★) `transport_moves_q0`
The transport of Example 1 takes `q0` from `S` to `G` and leaves `q1` where it is.
-/

theorem transport_moves_q0 : nextPlacement primer [0, 2] (.transport [⟨0, 0⟩]) = [2, 2] := by
  decide +kernel

/-!
## 5. The event frame

What does a rule need to know to decide whether an instruction may run? A capacity rule needs
the placements and the sites ions pass through; a timing rule needs the start time and the
duration. QCCDCert collects these in the **event frame** `F = (H, pos⁻, pos⁺, u, t, δ)`
([Compilation page](../compilation/#frame)): the hardware, the placements before and after, the
instruction (none for initialization), its start time and its duration. A frame is the program
state as one instruction sees it; initialization is a frame at time 0 with duration 0 and no
instruction.
-/

structure Frame where
  device : Device
  before : Placement
  after : Placement
  instruction : Option HardwareInstruction
  start : Nat
  duration : Nat

def initialFrame (d : Device) (pos : Placement) : Frame := ⟨d, pos, pos, none, 0, 0⟩

def stepFrame (d : Device) (pos : Placement) (h : HardwareInstruction) (t : Nat) : Frame :=
  ⟨d, pos, nextPlacement d pos h, some h, t, instructionDuration d h⟩

/-- The frames of a program run one event after another from placement `pos` at time `t`. -/
def frames (d : Device) : Placement → Nat → List HardwareInstruction → List Frame
  | _, _, [] => []
  | pos, t, h :: rest =>
      stepFrame d pos h t :: frames d (nextPlacement d pos h) (t + instructionDuration d h) rest

#eval (frames primer ex1.initial 0 ex1.instructions).map fun F => (F.start, F.duration)

/-!
#### Exercise 4 (★) `ex1_clock`
The frames of Example 1 start at the ticks the compiler printed, with the declared durations;
the last ends at tick 18.
-/

theorem ex1_clock :
    (frames primer ex1.initial 0 ex1.instructions).map (fun F => (F.start, F.duration)) =
      [(0, 2), (2, 1), (3, 1), (4, 1), (5, 2), (7, 4), (11, 2), (13, 2), (15, 3)] := by
  decide +kernel

/-!
## 6. What a frame shows

A rule reads numbers off one frame. QCCDCert names them, and the rule program writes them as
shown in brackets:

* `N⁻(s)`, `N⁺(s)` (`N-(s)`, `N+(s)`): the ions at site `s` before and after the event;
* `Nᵗʳ(s)` (`Ntr(s)`), the **transit reservation**: the ions at `s` before or after the event,
  plus every move of the event whose path passes through `s`. It holds for the whole event and
  catches an ion that passes through a full trap or through a junction another ion uses;
* `M(p)`, `M(c)`: the ions that move along path `p`, and on transport channel `c`; `W(c)`: the
  distinct waveforms requested on channel `c`;
* `P(s)`: the optical actions at `s`; `n_k`: the actions of kind `k`, for `n_move`,
  `n_optical` (pulses, readouts and pumps; a broadcast of `m` pulses counts `m`), `n_R`,
  `n_MS`, `n_readout`, `n_pump`, `n_latch`; and `t`, `t + δ`, `δ`.

In QCCDCert these are the constructors of `NatExpr`, read by `evalNat`. (QCCDCert splits them in
two layers, a base one and one with the transport and classical counts; the miniature has one.)
`residentCount` is QCCDCert's one-pass count of the ions at `s` before or after.
-/

def occupancy : Placement → Nat → Nat
  | [], _ => 0
  | x :: xs, s => (if x == s then 1 else 0) + occupancy xs s

def residentCount (s : Nat) : List Nat → List Nat → Nat
  | [], after => occupancy after s
  | b :: bs, [] => (if b == s then 1 else 0) + residentCount s bs []
  | b :: bs, a :: as => (if b == s || a == s then 1 else 0) + residentCount s bs as

def HardwareInstruction.moves : HardwareInstruction → List Move
  | .transport moves => moves
  | _ => []

def passesThrough (d : Device) (s : Nat) (m : Move) : Bool :=
  ((d.paths[m.path]?).map fun p => p.via.contains s).getD false

def transitOccupancy (d : Device) (before after : Placement) (moves : List Move) (s : Nat) : Nat :=
  residentCount s before after + (moves.filter (passesThrough d s)).length

def HardwareInstruction.pulseCount : HardwareInstruction → Nat
  | .pulse .. | .readout .. | .pump .. => 1
  | .broadcast _ members _ _ => members.length
  | _ => 0

def HardwareInstruction.rCount : HardwareInstruction → Nat
  | .pulse (.r ..) .. => 1
  | .broadcast _ members _ _ => members.length
  | _ => 0

def HardwareInstruction.msCount : HardwareInstruction → Nat
  | .pulse (.ms ..) .. => 1
  | _ => 0

def HardwareInstruction.pulseSites (d : Device) : HardwareInstruction → List Nat
  | .pulse _ _ _ k => ((d.calibrations[k]?).map Calibration.site).toList
  | .broadcast _ _ _ ks => ks.filterMap fun k => (d.calibrations[k]?).map Calibration.site
  | .readout _ _ _ a => ((d.readouts[a]?).map AcquisitionCalibration.site).toList
  | .pump _ _ p => ((d.pumps[p]?).map AcquisitionCalibration.site).toList
  | _ => []

def HardwareInstruction.isReadout : HardwareInstruction → Nat | .readout .. => 1 | _ => 0
def HardwareInstruction.isPump : HardwareInstruction → Nat | .pump .. => 1 | _ => 0
def HardwareInstruction.isLatch : HardwareInstruction → Nat | .latch .. => 1 | _ => 0

def Frame.moves (F : Frame) : List Move := (F.instruction.map HardwareInstruction.moves).getD []

def Frame.count (F : Frame) (f : HardwareInstruction → Nat) : Nat := (F.instruction.map f).getD 0

def Frame.pathUse (F : Frame) (p : Nat) : Nat := F.moves.countP fun m => m.path == p

def Frame.channelWaveforms (F : Frame) (c : Nat) : List Nat :=
  F.moves.filterMap fun m =>
    match F.device.paths[m.path]? with
    | some p => if p.channel == c then some p.waveform else none
    | none => none

inductive Phase where
  | before | during | after
  deriving DecidableEq, Repr

inductive NatExpr where
  | const (n : Nat)
  | start | finish | duration
  | occupancy (phase : Phase) (site : Nat)
  | moveCount | pulseCount | rCount | msCount
  | pulsesAt (site : Nat)
  | readoutCount | pumpCount | latchCount
  | pathUse (path : Nat)
  | channelUse (channel : Nat)
  | channelWaves (channel : Nat)
  | add (a b : NatExpr)
  | sub (a b : NatExpr)
  deriving DecidableEq, Repr

def evalNat (F : Frame) : NatExpr → Nat
  | .const n => n
  | .start => F.start
  | .finish => F.start + F.duration
  | .duration => F.duration
  | .occupancy .before s => occupancy F.before s
  | .occupancy .after s => occupancy F.after s
  | .occupancy .during s => transitOccupancy F.device F.before F.after F.moves s
  | .moveCount => F.moves.length
  | .pulseCount => F.count HardwareInstruction.pulseCount
  | .rCount => F.count HardwareInstruction.rCount
  | .msCount => F.count HardwareInstruction.msCount
  | .pulsesAt s => F.count fun h => (h.pulseSites F.device).count s
  | .readoutCount => F.count HardwareInstruction.isReadout
  | .pumpCount => F.count HardwareInstruction.isPump
  | .latchCount => F.count HardwareInstruction.isLatch
  | .pathUse p => F.pathUse p
  | .channelUse c => (F.channelWaveforms c).length
  | .channelWaves c => (F.channelWaveforms c).eraseDups.length
  | .add a b => evalNat F a + evalNat F b
  | .sub a b => evalNat F a - evalNat F b

/-!
The transport frame `F₁` of Example 1: `q0` counts at `S`, which it leaves, at `J`, which it
crosses, and at `G`, where it joins `q1`.
-/

def F1 : Frame := stepFrame primer [0, 2] (.transport [⟨0, 0⟩]) 0

#eval [0, 1, 2].map fun s => (evalNat F1 (.occupancy .before s), evalNat F1 (.occupancy .after s))

/-!
#### Exercise 5 (★) `f1_transit`
On `F₁`, `Nᵗʳ` is 1, 1, 2 at `S`, `J`, `G`.
-/

theorem f1_transit : [0, 1, 2].map (fun s => evalNat F1 (.occupancy .during s)) = [1, 1, 2] := by
  decide +kernel

/-!
#### Exercise 6 (★) `f1_counts`
On `F₁`, one ion moves along `p0`, channel 0 is asked for one waveform, and no optical action
happens.
-/

theorem f1_counts :
    evalNat F1 (.pathUse 0) = 1 ∧ evalNat F1 (.channelWaves 0) = 1 ∧ evalNat F1 .pulseCount = 0 := by
  decide +kernel

/-!
The transit reservation is conservative: it never counts fewer ions than were at the site
before. That is a statement about every frame, so it needs a proof, not a computation.

#### Exercise 7 (★★★) `occupancy_le_transit`
Prove `N⁻(s) ≤ Nᵗʳ(s)` for every device, placements, moves and site. Prove first, by induction
on the placement before, that `occupancy before s ≤ residentCount s before after` for every
`after`; in the step, `cases` on `after` and on the two Boolean tests, then `omega`.
-/

theorem occupancy_le_transit (d : Device) (before after : Placement) (moves : List Move) (s : Nat) :
    occupancy before s ≤ transitOccupancy d before after moves s := by
  have key : ∀ b a : List Nat, occupancy b s ≤ residentCount s b a := by
    intro b
    induction b with
    | nil => intro a; simp [occupancy]
    | cons x xs ih =>
        intro a
        cases a with
        | nil => have := ih []; simp only [occupancy, residentCount]; omega
        | cons y ys =>
            have := ih ys
            simp only [occupancy, residentCount]
            cases x == s <;> cases y == s <;> simp <;> omega
  have := key before after
  unfold transitOccupancy
  omega

/-!
## 7. The three core checks

The device runs each instruction as one event, and an event is legal only if it passes three
**core checks**, whatever the rules say:

1. every ion it uses is where the event needs it: at the source of its path, or at the site of
   its calibration, broadcast, acquisition or pump;
2. no site holds more ions than its capacity, before, during or after the event;
3. each shared channel applies one waveform, and every ion that a channel without switches
   (`mandatory`) drives is listed as moving.

QCCDCert states them as one inference rule per kind of instruction (`ActiveCoreLegal`; the
transport rule is `TransportLegal`). The miniature writes each as a Boolean check. For a
transport, `compat` is the pairwise condition of check 3 and of junctions: two moves cross no
link in opposite directions, share no intermediate site, and use one waveform on a shared
channel; `complete` is the mandatory-channel condition.
-/

def capacityOf (d : Device) (s : Nat) : Nat := ((d.sites[s]?).map Site.capacity).getD 0

/-- A placement is valid: one site per ion, every site declared, no site over capacity. -/
def placementValid (d : Device) (qubits : Nat) (pos : Placement) : Bool :=
  pos.length == qubits && pos.all (fun s => decide (s < d.sites.length)) &&
    (List.range d.sites.length).all fun s => decide (occupancy pos s ≤ capacityOf d s)

def distinct : List Nat → Bool
  | [] => true
  | x :: xs => !xs.contains x && distinct xs

def pairwiseB (p : Move → Move → Bool) : List Move → Bool
  | [] => true
  | m :: ms => ms.all (p m) && pairwiseB p ms

def Path.stops (p : Path) : List Nat := p.src :: p.via ++ [p.dst]

def links : List Nat → List (Nat × Nat)
  | a :: b :: rest => (a, b) :: links (b :: rest)
  | _ => []

def isShared (d : Device) (c : Nat) : Bool := d.shared.any fun e => e.1 == c
def isMandatory (d : Device) (c : Nat) : Bool := d.shared.any fun e => e.1 == c && e.2

def compat (d : Device) (m m' : Move) : Bool :=
  match d.paths[m.path]?, d.paths[m'.path]? with
  | some p, some p' =>
      !((links p.stops).any fun l => (links p'.stops).contains (l.2, l.1)) &&
      !(p.via.any fun s => p'.via.contains s) &&
      (!(p.channel == p'.channel && isShared d p.channel) || p.waveform == p'.waveform)
  | _, _ => false

def complete (d : Device) (pos : Placement) (moves : List Move) : Bool :=
  moves.all fun m =>
    match d.paths[m.path]? with
    | none => false
    | some p =>
        !isMandatory d p.channel ||
          (List.range d.paths.length).all fun j =>
            match d.paths[j]? with
            | none => true
            | some p' =>
                !(p'.channel == p.channel && p'.waveform == p.waveform) ||
                  (List.range pos.length).all fun q =>
                    !(pos[q]? == some p'.src) || moves.contains ⟨q, j⟩

/-- `C-Transport`: moves listed, distinct ions, each at its path's source (check 1), pairwise
compatible and complete (check 3), a valid placement after and the transit reservation within
capacity everywhere (check 2), a positive duration. -/
def transportLegal (d : Device) (qubits : Nat) (pos : Placement) (moves : List Move) : Bool :=
  let next := relocate d moves 0 pos
  !moves.isEmpty && distinct (moves.map Move.ion) &&
  moves.all (fun m => decide (m.ion < qubits) &&
    match d.paths[m.path]? with
    | some p => pos[m.ion]? == some p.src
    | none => false) &&
  pairwiseB (compat d) moves && complete d pos moves &&
  placementValid d qubits next &&
  (List.range d.sites.length).all (fun s =>
    decide (transitOccupancy d pos next moves s ≤ capacityOf d s)) &&
  decide (0 < instructionDuration d (.transport moves))

def pulseOK (qubits : Nat) : Wave → List Nat → Bool
  | .r .., [q] => decide (q < qubits)
  | .ms .., [a, b] => decide (a < qubits) && decide (b < qubits) && a != b
  | _, _ => false

/-- `C-Pulse`: the calibration exists, plays exactly this waveform, and every ion is at its
site (check 1). (QCCDCert's rule also asks `fits`, for a device that declares gate calibrations
by chain length; no example here does.) -/
def pulseLegal (d : Device) (qubits : Nat) (pos : Placement) (w : Wave) (ions : List Nat)
    (k : Nat) : Bool :=
  pulseOK qubits w ions &&
  match d.calibrations[k]? with
  | some c => c.wave == w && ions.all fun q => pos[q]? == some c.site
  | none => false

/-- `C-Broadcast`: at least two members on distinct ions, each legal as a pulse through its
calibration, whose waveform, channel, duration and site agree with the profile, and at most
`limit` members per site. -/
def broadcastLegal (d : Device) (qubits : Nat) (pos : Placement) (b : Nat)
    (members : List (Wave × Nat)) (ks : List Nat) : Bool :=
  match d.broadcasts[b]? with
  | none => false
  | some prof =>
      decide (2 ≤ members.length) && members.length == ks.length &&
      distinct (members.map Prod.snd) &&
      (members.zip ks).all (fun (wq, k) =>
        pulseLegal d qubits pos wq.1 [wq.2] k && wq.1 == prof.wave &&
        match d.calibrations[k]? with
        | some c => c.channel == prof.channel && c.duration == prof.duration &&
            prof.sites.contains c.site
        | none => false) &&
      prof.sites.all fun s =>
        decide ((ks.filter fun k =>
          ((d.calibrations[k]?).map Calibration.site) == some s).length ≤ prof.limit)

def activeCoreLegal (d : Device) (qubits bits : Nat) (pos : Placement) :
    HardwareInstruction → Bool
  | .transport moves => transportLegal d qubits pos moves
  | .pulse w ions _ k => pulseLegal d qubits pos w ions k
  | .broadcast b members _ ks => broadcastLegal d qubits pos b members ks
  | .readout q c _ a => decide (q < qubits) && decide (c < bits) &&
      match d.readouts[a]? with
      | some r => pos[q]? == some r.site
      | none => false
  | .pump q _ p => decide (q < qubits) &&
      match d.pumps[p]? with
      | some r => pos[q]? == some r.site
      | none => false
  | .latch cs _ => cs.all (fun c => decide (c < bits)) && d.controller.isSome
  | .idle n => decide (0 < n)
  | .barrier qs _ => qs.all fun q => decide (q < qubits)

/-!
A guarded instruction has two **realizations**: the active one, and, when the saved bit is
false, an `idle` of the same duration (`realize`). So the time and the placement of a guarded
event never depend on a measurement outcome. QCCDCert's `CoreLegal` asks the core checks of
**both** realizations.
-/

def HardwareInstruction.isGuarded : HardwareInstruction → Bool
  | .pulse _ _ g _ | .broadcast _ _ g _ | .readout _ _ g _ | .pump _ g _ => g
  | _ => false

def realize (d : Device) (saved : Bool) (h : HardwareInstruction) : HardwareInstruction :=
  if h.isGuarded && !saved then .idle (instructionDuration d h) else h

def coreLegal (d : Device) (qubits bits : Nat) (pos : Placement) (h : HardwareInstruction) : Bool :=
  activeCoreLegal d qubits bits pos h && activeCoreLegal d qubits bits pos (realize d false h)

def CoreLegal (d : Device) (qubits bits : Nat) (pos : Placement) (h : HardwareInstruction) : Prop :=
  coreLegal d qubits bits pos h = true

/-- Every instruction is core legal where the instructions before it left the ions. -/
def coreRun (d : Device) (qubits bits : Nat) : Placement → List HardwareInstruction → Bool
  | _, [] => true
  | pos, h :: rest => coreLegal d qubits bits pos h && coreRun d qubits bits (nextPlacement d pos h) rest

/-!
#### Exercise 8 (★) `ex1_core`
Every event of Example 1 passes the core checks (two qubits, one classical bit).
-/

theorem ex1_core : coreRun primer 2 1 ex1.initial ex1.instructions = true := by
  decide +kernel

/-!
Three steps no device may take ([Compilation page](../compilation/#legality)). At the initial
placement, the MS pulse would need `q0` at `G` (check 1): that is why the target code starts
with a transport. With a third ion already in `G`, the same transport would put three ions in a
two-ion zone (check 2).
-/

theorem ms_needs_q0_at_G :
    coreLegal primer 2 1 [0, 2] (.pulse (.ms half) [0, 1] false 4) = false := by decide +kernel

theorem three_do_not_fit :
    coreLegal primer 3 1 [0, 2, 2] (.transport [⟨0, 0⟩]) = false := by decide +kernel

/-!
The third needs a junction. Example 2 ([Compilation page](../compilation/#ex2)) runs on this
device, `Tee`: storage `S` for two, junction `J` for one, gate zone `G` for two, parking site
`P` for one, and four paths through `J`, each on its own channel:

```
hardware Tee; site S[2]; site J[1]; site G[2]; site P[1];
path p0:S->J->G channel 0 waveform 0 duration 2;
path p1:G->J->S channel 1 waveform 0 duration 2;
path p2:G->J->P channel 2 waveform 0 duration 2;
path p3:P->J->G channel 3 waveform 0 duration 2;
```

(its optical declarations are those of the primer at `G`, numbered `c0` to `c3`; lesson I2
lists them).
-/

def tee : Device where
  name := "Tee"
  sites := [⟨"S", 2⟩, ⟨"J", 1⟩, ⟨"G", 2⟩, ⟨"P", 1⟩]
  paths := [⟨0, [1], 2, 0, 0, 2⟩, ⟨2, [1], 0, 1, 0, 2⟩, ⟨2, [1], 3, 2, 0, 2⟩, ⟨3, [1], 2, 3, 0, 2⟩]
  shared := []
  calibrations := [⟨.r half half, 2, 7, 2⟩, ⟨.ms half, 2, 7, 4⟩, ⟨.r mhalf e0, 2, 7, 2⟩,
    ⟨.r mhalf half, 2, 7, 2⟩]
  broadcasts := [⟨.r mhalf e0, [2], 7, 2, 2⟩]
  readouts := [⟨2, 7, 3⟩]
  pumps := [⟨2, 7, 4⟩]
  controller := some 1

/-!
#### Exercise 9 (★★) `junction_exchange_rejected`
With `q0`, `q1` in `S` and `q2` in `G`, one transport would exchange `q0` and `q2` through `J`
(`q0` along `p0`, `q2` along `p1`). The placement after it is valid, so a check of the
destinations alone would accept it. Prove that the core checks reject it, and then that the
transit reservation at `J` is 2, over `J`'s capacity of 1. (`compat` fails too: the two paths
cross the links `S-J` and `J-G` in opposite directions.)
-/

theorem junction_exchange_rejected :
    coreLegal tee 3 1 [0, 0, 2] (.transport [⟨0, 0⟩, ⟨2, 1⟩]) = false ∧
      placementValid tee 3 [2, 0, 0] = true ∧
      transitOccupancy tee [0, 0, 2] [2, 0, 0] [⟨0, 0⟩, ⟨2, 1⟩] 1 = 2 := by
  decide +kernel

/-!
## 8. What comes next

The core checks are the same for every device. A designer adds restrictions of their own, and
those can only reject more events: lesson I2 writes them as assertions over the frame you met
here, and proves that the compiler enforces them as written.

We assume, as the paper does, that the device carries out each declared event within its
declared duration whenever the core checks pass. That is the **primitive-realization
assumption**: every proof in this part is about the discrete model above, never about the
physics below it.
-/
