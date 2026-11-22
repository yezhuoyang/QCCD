import QCCDC.Cert.Sound

/-!
# Legality: rules are assertions over one frame

*Part I, lesson I2. You need lesson I1: its devices, target code, frames and core checks are
restated, unseen, at the top of this page.*

Operating rules differ between devices and change during co-design. A laboratory may limit a
gate zone to one ion, allow one laser pulse per event, or forbid pulses until the optics have
settled. Built into the compiler, every new rule would need a new proof. QCCDCert writes them
instead in one **assertion language**, as inputs: the compiler returns target code only if
every rule is true on every frame of that code, and the soundness of that check is proved once,
for all rules and all programs. This lesson is the **Legality** clause of
`verified_compilation`: the rules ([Compilation page](../compilation/#rules)), their meaning,
the elaboration lemma, and legal runs, on Examples 1 and 2.

As in lesson I1, the Lean is a faithful miniature of the QCCDCert declarations it names
(`Rule`, `evalRule_correct`, `Assertions.Formula.lower_correct`, `Assertions.elaborate`,
`Assertions.elaborate_correct`, `Legal`, `Run`, `InitialLegal`).
-/

-- setup
inductive Expr where
  | pi
  | rat (num : Int) (den : Nat)
  | neg (a : Expr)
  | add (a b : Expr)
  | sub (a b : Expr)
  | div (a b : Expr)
  deriving DecidableEq, Repr
def e0 : Expr := .rat 0 1
def half : Expr := .div .pi (.rat 2 1)
def mhalf : Expr := .neg half
inductive Wave where
  | r (theta phi : Expr)
  | ms (theta : Expr)
  deriving DecidableEq, Repr
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
structure BroadcastProfile where
  wave : Wave
  sites : List Nat
  channel : Nat
  duration : Nat
  limit : Nat
  deriving Repr
structure AcquisitionCalibration where
  site : Nat
  channel : Nat
  duration : Nat
  deriving Repr
abbrev PumpCalibration := AcquisitionCalibration
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
  paths := [⟨0, [1], 2, 0, 0, 2⟩]
  shared := []
  calibrations :=
    [⟨.r (.div .pi (.rat 7 1)) (.sub half e0), 2, 7, 2⟩,
     ⟨.r .pi e0, 2, 7, 2⟩,
     ⟨.r .pi (.div (.add e0 e0) (.rat 2 1)), 2, 7, 2⟩,
     ⟨.r half half, 2, 7, 2⟩,
     ⟨.ms half, 2, 7, 4⟩,
     ⟨.r mhalf e0, 2, 7, 2⟩,
     ⟨.r mhalf half, 2, 7, 2⟩]
  broadcasts := [⟨.r mhalf e0, [2], 7, 2, 2⟩]
  readouts := [⟨2, 7, 3⟩]
  pumps := [⟨2, 7, 4⟩]
  controller := some 1
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
def ex1 : Execution where
  initial := [0, 2]
  instructions :=
    [.transport [⟨0, 0⟩],
     .idle 1, .idle 1, .idle 1,
     .pulse (.r half half) [0] false 3,
     .pulse (.ms half) [0, 1] false 4,
     .broadcast 0 [(.r mhalf e0, 0), (.r mhalf e0, 1)] false [5, 5],
     .pulse (.r mhalf half) [0] false 6,
     .readout 1 0 false 0]
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
def frames (d : Device) : Placement → Nat → List HardwareInstruction → List Frame
  | _, _, [] => []
  | pos, t, h :: rest =>
      stepFrame d pos h t :: frames d (nextPlacement d pos h) (t + instructionDuration d h) rest
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
def F1 : Frame := stepFrame primer [0, 2] (.transport [⟨0, 0⟩]) 0
def capacityOf (d : Device) (s : Nat) : Nat := ((d.sites[s]?).map Site.capacity).getD 0
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
def pulseLegal (d : Device) (qubits : Nat) (pos : Placement) (w : Wave) (ions : List Nat)
    (k : Nat) : Bool :=
  pulseOK qubits w ions &&
  match d.calibrations[k]? with
  | some c => c.wave == w && ions.all fun q => pos[q]? == some c.site
  | none => false
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
def HardwareInstruction.isGuarded : HardwareInstruction → Bool
  | .pulse _ _ g _ | .broadcast _ _ g _ | .readout _ _ g _ | .pump _ g _ => g
  | _ => false
def realize (d : Device) (saved : Bool) (h : HardwareInstruction) : HardwareInstruction :=
  if h.isGuarded && !saved then .idle (instructionDuration d h) else h
def coreLegal (d : Device) (qubits bits : Nat) (pos : Placement) (h : HardwareInstruction) : Bool :=
  activeCoreLegal d qubits bits pos h && activeCoreLegal d qubits bits pos (realize d false h)
def CoreLegal (d : Device) (qubits bits : Nat) (pos : Placement) (h : HardwareInstruction) : Prop :=
  coreLegal d qubits bits pos h = true
def coreRun (d : Device) (qubits bits : Nat) : Placement → List HardwareInstruction → Bool
  | _, [] => true
  | pos, h :: rest => coreLegal d qubits bits pos h && coreRun d qubits bits (nextPlacement d pos h) rest
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
## 1. The assertion syntax

A rule program is a list of named assertions, `assert name: formula;`. A formula has the
Boolean connectives (`and`, `or`, `not`, `=>`), the comparisons `<=` and `=` (the listings also
write `>` and `>=`, which the reader turns into `<=`), natural-number terms with `+` and
truncated `-`, and finite quantifiers over the
declared sites, paths and transport channels: `forall s:Sites.`, `exists p:Paths.`,
`forall c:Channels.`. A term reads one frame (lesson I1, section 6):

| written | means | written | means |
|---|---|---|---|
| `t` | start time | `N-(s)`, `N+(s)` | ions at `s` before, after |
| `delta` | duration | `Ntr(s)` | transit reservation of `s` |
| `cap(s)` | declared capacity | `M(p)`, `M(c)` | moves along path `p`, on channel `c` |
| `n_optical` | optical actions | `W(c)` | distinct waveforms on channel `c` |
| `n_move`, `n_R`, `n_MS` | moves, R, MS actions | `P(s)` | optical actions at `s` |
| `n_readout`, `n_pump`, `n_latch` | readouts, pumps, latches | | |

The rules fall in three families. **Occupancy**: `forall s:Sites. Ntr(s) <= cap(s)` restates
the core capacity check, and `Ntr(G) <= 1` would run the two-ion zone `G` with one ion without
changing the device. **Transport and control**: `forall p:Paths. M(p) <= 1` allows one ion per
path, `forall c:Channels. W(c) <= 1` one waveform per channel. **Operation and timing**:
`n_optical <= 1` allows one optical action per event, `forall s:Sites. P(s) <= 1` one per site,
and `n_optical > 0 => t >= 5` sets a release time.

## 2. Core rules and their evaluator

Inside the compiler a rule has no quantifiers and no names: it is a **core rule** over the
observations of lesson I1. `evalRule` decides one on a frame; `RuleHolds` is what it means.
-/

inductive Rule where
  | le (lhs rhs : NatExpr)
  | eq (lhs rhs : NatExpr)
  | and (lhs rhs : Rule)
  | or (lhs rhs : Rule)
  | not (rule : Rule)
  | implies (premise conclusion : Rule)
  deriving DecidableEq, Repr

abbrev RuleSet := List Rule

def evalRule (F : Frame) : Rule → Bool
  | .le a b => decide (evalNat F a ≤ evalNat F b)
  | .eq a b => evalNat F a == evalNat F b
  | .and p q => evalRule F p && evalRule F q
  | .or p q => evalRule F p || evalRule F q
  | .not p => !evalRule F p
  | .implies p q => !evalRule F p || evalRule F q

def RuleHolds (F : Frame) : Rule → Prop
  | .le a b => evalNat F a ≤ evalNat F b
  | .eq a b => evalNat F a = evalNat F b
  | .and p q => RuleHolds F p ∧ RuleHolds F q
  | .or p q => RuleHolds F p ∨ RuleHolds F q
  | .not p => ¬ RuleHolds F p
  | .implies p q => RuleHolds F p → RuleHolds F q

/-!
The evaluator is right, by induction on the rule: a comparison evaluates two numbers computed
from the finite frame, and each connective combines its parts as its truth table does.
`simp` closes every case once it is told the definitions and the induction hypotheses.
-/

theorem evalRule_correct (F : Frame) (rule : Rule) :
    evalRule F rule = true ↔ RuleHolds F rule := by
  induction rule with
  | le a b => simp [evalRule, RuleHolds]
  | eq a b => simp [evalRule, RuleHolds]
  | and p q hp hq => simp [evalRule, RuleHolds, hp, hq]
  | or p q hp hq => simp [evalRule, RuleHolds, hp, hq]
  | not p hp => simp [evalRule, RuleHolds, ← hp]
  | implies p q hp hq =>
      simp only [evalRule, RuleHolds, ← hp, ← hq]
      cases evalRule F p <;> simp

def RulesHold (rules : RuleSet) (F : Frame) : Prop := ∀ r ∈ rules, RuleHolds F r

def rulesOK (rules : RuleSet) (F : Frame) : Bool := rules.all (evalRule F)

/-!
#### Exercise 1 (★★) `rulesOK_correct`
A rule set holds on a frame exactly when every rule evaluates to `true`. `simp` with the three
definitions and `evalRule_correct` does it.
-/

theorem rulesOK_correct (rules : RuleSet) (F : Frame) :
    rulesOK rules F = true ↔ RulesHold rules F := by
  simp [rulesOK, RulesHold, evalRule_correct]

/-!
## 3. Formulas as the designer writes them

A formula of the rule program names sites, paths and channels, by a declared index (`.id`) or
by a variable bound by a quantifier (`.var`). An environment gives each variable its value.
`Term.denote` and `Formula.Holds` are the meaning of a formula as written: `F;ξ ⊨ φ` of the
paper, with `ξ` the environment.
-/

inductive Domain where
  | sites | paths | channels
  deriving DecidableEq, Repr

def Domain.values (d : Device) : Domain → List Nat
  | .sites => List.range d.sites.length
  | .paths => List.range d.paths.length
  | .channels => (d.paths.map Path.channel).eraseDups

inductive Reference where
  | id (value : Nat)
  | var (name : String)
  deriving DecidableEq, Repr

abbrev Context := List (String × Domain)
abbrev Environment := List (String × Nat)

def Reference.resolve (env : Environment) : Reference → Nat
  | .id v => v
  | .var x => ((env.find? fun e => e.1 == x).map Prod.snd).getD 0

def Reference.valid (d : Device) (ctx : Context) (dom : Domain) : Reference → Bool
  | .id v => (dom.values d).contains v
  | .var x => ((ctx.find? fun e => e.1 == x).map fun e => e.2 == dom).getD false

/-- What a formula observes about a named site, path or channel. -/
inductive Observation where
  | occupancy (phase : Phase)
  | capacity | pulsesAt | pathUse | channelUse | channelWaves
  deriving DecidableEq, Repr

def Observation.domain : Observation → Domain
  | .occupancy _ | .capacity | .pulsesAt => .sites
  | .pathUse => .paths
  | .channelUse | .channelWaves => .channels

def Observation.lower (d : Device) (i : Nat) : Observation → NatExpr
  | .occupancy ph => .occupancy ph i
  | .capacity => .const (capacityOf d i)
  | .pulsesAt => .pulsesAt i
  | .pathUse => .pathUse i
  | .channelUse => .channelUse i
  | .channelWaves => .channelWaves i

inductive Term where
  | constant (value : Nat)
  | atom (e : NatExpr)
  | observe (o : Observation) (r : Reference)
  | add (a b : Term)
  | sub (a b : Term)
  deriving DecidableEq, Repr

def Term.valid (d : Device) (ctx : Context) : Term → Bool
  | .constant _ | .atom _ => true
  | .observe o r => r.valid d ctx o.domain
  | .add a b | .sub a b => a.valid d ctx && b.valid d ctx

def Term.lower (d : Device) (env : Environment) : Term → NatExpr
  | .constant v => .const v
  | .atom e => e
  | .observe o r => o.lower d (r.resolve env)
  | .add a b => .add (a.lower d env) (b.lower d env)
  | .sub a b => .sub (a.lower d env) (b.lower d env)

def Term.denote (d : Device) (env : Environment) (F : Frame) : Term → Nat
  | .constant v => v
  | .atom e => evalNat F e
  | .observe o r => evalNat F (o.lower d (r.resolve env))
  | .add a b => a.denote d env F + b.denote d env F
  | .sub a b => a.denote d env F - b.denote d env F

theorem Term.lower_correct (d : Device) (env : Environment) (F : Frame) (t : Term) :
    evalNat F (t.lower d env) = t.denote d env F := by
  induction t <;> simp_all [Term.lower, Term.denote, evalNat]

inductive Formula where
  | truth | falsity
  | le (a b : Term)
  | eq (a b : Term)
  | and (p q : Formula)
  | or (p q : Formula)
  | not (p : Formula)
  | implies (p q : Formula)
  | all (x : String) (dom : Domain) (body : Formula)
  | any (x : String) (dom : Domain) (body : Formula)
  deriving DecidableEq, Repr

def Formula.valid (d : Device) (ctx : Context) : Formula → Bool
  | .truth | .falsity => true
  | .le a b | .eq a b => a.valid d ctx && b.valid d ctx
  | .and p q | .or p q | .implies p q => p.valid d ctx && q.valid d ctx
  | .not p => p.valid d ctx
  | .all x dom body | .any x dom body => body.valid d ((x, dom) :: ctx)

def Formula.Holds (d : Device) (env : Environment) (F : Frame) : Formula → Prop
  | .truth => True
  | .falsity => False
  | .le a b => a.denote d env F ≤ b.denote d env F
  | .eq a b => a.denote d env F = b.denote d env F
  | .and p q => p.Holds d env F ∧ q.Holds d env F
  | .or p q => p.Holds d env F ∨ q.Holds d env F
  | .not p => ¬ p.Holds d env F
  | .implies p q => p.Holds d env F → q.Holds d env F
  | .all x dom body => ∀ i ∈ dom.values d, body.Holds d ((x, i) :: env) F
  | .any x dom body => ∃ i ∈ dom.values d, body.Holds d ((x, i) :: env) F

/-!
Example 1's three rules, as the compiler's reader returns them. The reader desugars: `x > 0`
becomes `1 <= x` and `t >= 5` becomes `5 <= t`.
-/

def initRule : Formula :=
  .implies (.and (.eq (.atom .start) (.constant 0)) (.eq (.atom .duration) (.constant 0)))
    (.eq (.observe (.occupancy .before) (.id 0)) (.constant 1))

def capRule : Formula :=
  .all "s" .sites (.le (.observe (.occupancy .during) (.var "s")) (.observe .capacity (.var "s")))

def relRule : Formula :=
  .implies (.le (.constant 1) (.atom .pulseCount)) (.le (.constant 5) (.atom .start))

/-!
To see them in the rule program's own spelling, here is a printer (it is for `#eval` only;
nothing below depends on it):
-/

def NatExpr.print : NatExpr → String
  | .start => "t" | .duration => "delta" | .finish => "t + delta"
  | .pulseCount => "n_optical" | .moveCount => "n_move" | .rCount => "n_R" | .msCount => "n_MS"
  | .readoutCount => "n_readout" | .pumpCount => "n_pump" | .latchCount => "n_latch"
  | .const n => toString n
  | _ => "?"

def Reference.print (d : Device) (dom : Domain) : Reference → String
  | .var x => x
  | .id i => match dom with
    | .sites => ((d.sites[i]?).map Site.name).getD "?"
    | .paths => "p" ++ toString i
    | .channels => toString i

def Term.print (d : Device) : Term → String
  | .constant v => toString v
  | .atom e => e.print
  | .observe o r =>
      let name := match o with
        | .occupancy .before => "N-" | .occupancy .after => "N+" | .occupancy .during => "Ntr"
        | .capacity => "cap" | .pulsesAt => "P" | .pathUse | .channelUse => "M" | .channelWaves => "W"
      name ++ "(" ++ r.print d o.domain ++ ")"
  | .add a b => a.print d ++ " + " ++ b.print d
  | .sub a b => a.print d ++ " - " ++ b.print d

def Domain.print : Domain → String
  | .sites => "Sites" | .paths => "Paths" | .channels => "Channels"

def Formula.print (d : Device) : Formula → String
  | .truth => "true" | .falsity => "false"
  | .le a b => a.print d ++ " <= " ++ b.print d
  | .eq a b => a.print d ++ " = " ++ b.print d
  | .and p q => "(" ++ p.print d ++ " and " ++ q.print d ++ ")"
  | .or p q => "(" ++ p.print d ++ " or " ++ q.print d ++ ")"
  | .not p => "not " ++ p.print d
  | .implies p q => p.print d ++ " => " ++ q.print d
  | .all x dom body => "forall " ++ x ++ ":" ++ dom.print ++ ". " ++ body.print d
  | .any x dom body => "exists " ++ x ++ ":" ++ dom.print ++ ". " ++ body.print d

#eval [initRule, capRule, relRule].map (Formula.print primer)

/-!
## 4. Elaboration

A designer writes a rule in general terms: "no site exceeds its capacity". **Elaboration**
(`Formula.lower`) compiles it into explicit checks for one device: it replaces each variable by
its value and each `cap(s)` by the declared constant, keeps the connectives, and expands a
quantifier over the declarations of the device, `forall` into a conjunction and `exists` into a
disjunction. An empty conjunction is `0 = 0`, an empty disjunction `not (0 = 0)`.
-/

def trueRule : Rule := .eq (.const 0) (.const 0)
def falseRule : Rule := .not trueRule

def conjunction (rules : List Rule) : Rule := rules.foldr Rule.and trueRule
def disjunction (rules : List Rule) : Rule := rules.foldr Rule.or falseRule

def Formula.lower (d : Device) (env : Environment) : Formula → Rule
  | .truth => trueRule
  | .falsity => falseRule
  | .le a b => .le (a.lower d env) (b.lower d env)
  | .eq a b => .eq (a.lower d env) (b.lower d env)
  | .and p q => .and (p.lower d env) (q.lower d env)
  | .or p q => .or (p.lower d env) (q.lower d env)
  | .not p => .not (p.lower d env)
  | .implies p q => .implies (p.lower d env) (q.lower d env)
  | .all x dom body => conjunction ((dom.values d).map fun i => body.lower d ((x, i) :: env))
  | .any x dom body => disjunction ((dom.values d).map fun i => body.lower d ((x, i) :: env))

/-!
#### Exercise 2 (★) `cap_elaborates`
On the primer, `forall s:Sites. Ntr(s) <= cap(s)` elaborates to
`Ntr(S) <= 1 and (Ntr(J) <= 1 and (Ntr(G) <= 2 and 0 = 0))`, exactly as the paper prints it.
-/

theorem cap_elaborates :
    capRule.lower primer [] =
      .and (.le (.occupancy .during 0) (.const 1))
        (.and (.le (.occupancy .during 1) (.const 1))
          (.and (.le (.occupancy .during 2) (.const 2)) trueRule)) := by
  decide +kernel

/-!
Why does a new rule need no new proof? Because elaboration preserves truth, for every device,
frame, environment and formula: the elaborated rule holds on a frame exactly when the formula
as written does. This is the lemma `elab ⇓ ψ → (eval ψ F = true ↔ F ⊨ φ)` of the paper, with
`evalRule_correct` above for its `eval` half. The proof is by induction on the formula; the
quantifier cases need the meaning of a conjunction and a disjunction of a list.

#### Exercise 3 (★★★) `conjunction_correct`
A conjunction of a list holds exactly when every rule of the list holds. Induction on the list;
`simp_all` with `conjunction`, `trueRule` and `RuleHolds` closes both cases.
-/

theorem conjunction_correct (F : Frame) (rules : List Rule) :
    RuleHolds F (conjunction rules) ↔ ∀ r ∈ rules, RuleHolds F r := by
  induction rules with
  | nil => simp [conjunction, trueRule, RuleHolds]
  | cons r rest ih => simp_all [conjunction, RuleHolds]

theorem disjunction_correct (F : Frame) (rules : List Rule) :
    RuleHolds F (disjunction rules) ↔ ∃ r ∈ rules, RuleHolds F r := by
  induction rules with
  | nil => simp [disjunction, falseRule, trueRule, RuleHolds]
  | cons r rest ih => simp_all [disjunction, RuleHolds]

/-- **Elaboration preserves meaning** (`Assertions.Formula.lower_correct`). -/
theorem Formula.lower_correct (d : Device) (env : Environment) (F : Frame) (φ : Formula) :
    RuleHolds F (φ.lower d env) ↔ φ.Holds d env F := by
  induction φ generalizing env with
  | truth => simp [Formula.lower, Formula.Holds, trueRule, RuleHolds]
  | falsity => simp [Formula.lower, Formula.Holds, falseRule, trueRule, RuleHolds]
  | le a b => simp [Formula.lower, Formula.Holds, RuleHolds, Term.lower_correct]
  | eq a b => simp [Formula.lower, Formula.Holds, RuleHolds, Term.lower_correct]
  | and p q hp hq => simp [Formula.lower, Formula.Holds, RuleHolds, hp, hq]
  | or p q hp hq => simp [Formula.lower, Formula.Holds, RuleHolds, hp, hq]
  | not p hp => simp [Formula.lower, Formula.Holds, RuleHolds, hp]
  | implies p q hp hq => simp [Formula.lower, Formula.Holds, RuleHolds, hp, hq]
  | all x dom body ih => simp [Formula.lower, Formula.Holds, conjunction_correct, ih]
  | any x dom body ih =>
      simp only [Formula.lower, Formula.Holds, disjunction_correct]
      constructor
      · intro ⟨r, hr, holds⟩
        obtain ⟨i, hi, rfl⟩ := List.mem_map.mp hr
        exact ⟨i, hi, (ih _).mp holds⟩
      · intro ⟨i, hi, holds⟩
        exact ⟨_, List.mem_map.mpr ⟨i, hi, rfl⟩, (ih _).mpr holds⟩

/-!
## 5. Rule programs

A declaration carries its name and a category (occupancy, transport and control, operation and
timing; a category organizes, it never skips a check). `Assertions.elaborate` checks every name
and reference against the device, rejects the whole program if one is wrong, and otherwise
elaborates every formula with the empty environment.
-/

inductive Category where
  | staticOccupancy | transportControl | operationTiming
  deriving DecidableEq, Repr

structure Declaration where
  name : String
  category : Category
  formula : Formula
  deriving Repr

abbrev Program := List Declaration

def lowerProgram (d : Device) (program : Program) : RuleSet :=
  program.map fun decl => decl.formula.lower d []

def ProgramHolds (d : Device) (program : Program) (F : Frame) : Prop :=
  ∀ decl ∈ program, decl.formula.Holds d [] F

theorem lowerProgram_correct (d : Device) (program : Program) (F : Frame) :
    RulesHold (lowerProgram d program) F ↔ ProgramHolds d program F := by
  simp [RulesHold, lowerProgram, ProgramHolds, Formula.lower_correct]

def elaborate (d : Device) (program : Program) : Except String RuleSet :=
  match program.find? (fun decl => !decl.formula.valid d []) with
  | some decl => .error ("invalid hardware reference in assertion " ++ decl.name)
  | none => .ok (lowerProgram d program)

def ex1Program : Program :=
  [⟨"init", .staticOccupancy, initRule⟩,
   ⟨"cap", .staticOccupancy, capRule⟩,
   ⟨"rel", .operationTiming, relRule⟩]

/-!
#### Exercise 4 (★) `unknown_site_rejected`
A rule that names site 7 of the three-site primer is rejected, and with it the whole program.
(`Except.toOption` turns the result into an `Option`, which `decide` can compare.)
-/

theorem unknown_site_rejected :
    (elaborate primer (ex1Program ++
      [⟨"bad", .staticOccupancy, .le (.observe (.occupancy .during) (.id 7)) (.constant 1)⟩])).toOption
      = none := by
  decide +kernel

/-!
#### Exercise 5 (★★) `elaborate_correct`
QCCDCert's `Assertions.elaborate_correct`: if elaboration returns the core rules, they hold on a
frame exactly when the program as written does. `unfold elaborate at success`, `split at
success`, and in the successful case use `lowerProgram_correct`.
-/

theorem elaborate_correct {d : Device} {program : Program} {rules : RuleSet}
    (success : elaborate d program = .ok rules) (F : Frame) :
    RulesHold rules F ↔ ProgramHolds d program F := by
  unfold elaborate at success
  split at success
  · contradiction
  · cases success
    exact lowerProgram_correct _ _ _

/-!
## 6. Legal events and legal runs

Now the rules join the core checks. An instruction `h` at placement `pos` and time `t` is
**legal** (`Legal`, the paper's rule `Step`) when it is core legal, and the rules hold on its
frame and on the frame of its second realization (a guarded event becomes an idle when its bit
is false). A **run** (`Run`) is a list of legal steps, each starting where and when the last
ended; `InitialLegal` asks a valid initial placement whose frame satisfies the rules
(QCCDCert's also asks that every core rule names only declared sites, paths and channels). The
rules can only reject a step: `Legal` keeps the core premise whatever they say.
-/

def Legal (d : Device) (rules : RuleSet) (qubits bits : Nat) (pos : Placement) (t : Nat)
    (h : HardwareInstruction) : Prop :=
  CoreLegal d qubits bits pos h ∧
  RulesHold rules (stepFrame d pos h t) ∧
  RulesHold rules (stepFrame d pos (realize d false h) t)

inductive Run (d : Device) (rules : RuleSet) (qubits bits : Nat) :
    Placement → Nat → List HardwareInstruction → Placement → Nat → Prop where
  | nil (pos : Placement) (t : Nat) : Run d rules qubits bits pos t [] pos t
  | cons {pos final : Placement} {t finish : Nat} {h : HardwareInstruction}
      {rest : List HardwareInstruction}
      (head : Legal d rules qubits bits pos t h)
      (tail : Run d rules qubits bits (nextPlacement d pos h) (t + instructionDuration d h)
        rest final finish) :
      Run d rules qubits bits pos t (h :: rest) final finish

def InitialLegal (d : Device) (rules : RuleSet) (qubits : Nat) (pos : Placement) : Prop :=
  placementValid d qubits pos = true ∧ RulesHold rules (initialFrame d pos)

/-!
Legality is decidable, and the compiler decides it: `legal` checks one step, `runB` a whole
program. The compiler runs these; the proofs below say that whatever they accept is a `Run`.
-/

def legal (d : Device) (rules : RuleSet) (qubits bits : Nat) (pos : Placement) (t : Nat)
    (h : HardwareInstruction) : Bool :=
  coreLegal d qubits bits pos h && rulesOK rules (stepFrame d pos h t) &&
    rulesOK rules (stepFrame d pos (realize d false h) t)

theorem legal_correct {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement}
    {t : Nat} {h : HardwareInstruction} :
    legal d rules qubits bits pos t h = true ↔ Legal d rules qubits bits pos t h := by
  simp [legal, Legal, CoreLegal, rulesOK_correct, Bool.and_eq_true, and_assoc]

def finalPlacement (d : Device) : Placement → List HardwareInstruction → Placement
  | pos, [] => pos
  | pos, h :: rest => finalPlacement d (nextPlacement d pos h) rest

def finalTime (d : Device) : Nat → List HardwareInstruction → Nat
  | t, [] => t
  | t, h :: rest => finalTime d (t + instructionDuration d h) rest

def runB (d : Device) (rules : RuleSet) (qubits bits : Nat) :
    Placement → Nat → List HardwareInstruction → Bool
  | _, _, [] => true
  | pos, t, h :: rest => legal d rules qubits bits pos t h &&
      runB d rules qubits bits (nextPlacement d pos h) (t + instructionDuration d h) rest

/-!
#### Exercise 6 (★★★) `runB_sound`
A program the check accepts is a legal run, ending at its final placement and time. Induction on
the program (generalizing the placement and the time); in the step, split the `&&` with
`simp only [runB, Bool.and_eq_true] at ok` and use `legal_correct` and `Run.cons`.
-/

theorem runB_sound {d : Device} {rules : RuleSet} {qubits bits : Nat} :
    ∀ {pos : Placement} {t : Nat} {code : List HardwareInstruction},
      runB d rules qubits bits pos t code = true →
      Run d rules qubits bits pos t code (finalPlacement d pos code) (finalTime d t code) := by
  intro pos t code
  induction code generalizing pos t with
  | nil => intro _; exact .nil pos t
  | cons h rest ih =>
      intro ok
      simp only [runB, Bool.and_eq_true] at ok
      exact .cons (legal_correct.mp ok.1) (ih ok.2)

theorem run_of_check {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos final : Placement}
    {t finish : Nat} {code : List HardwareInstruction}
    (ok : runB d rules qubits bits pos t code = true)
    (place : finalPlacement d pos code = final) (time : finalTime d t code = finish) :
    Run d rules qubits bits pos t code final finish :=
  place ▸ time ▸ runB_sound ok

/-!
## 7. Example 1: the rule that adds three idles

Elaboration returns Example 1's core rules, and its target code is a legal run from the initial
placement `q0@S, q1@G` at tick 0 to `q0@G, q1@G` at tick 18
([Compilation page](../compilation/#ex1)).
-/

def ex1Rules : RuleSet := lowerProgram primer ex1Program

theorem ex1_elaborates : elaborate primer ex1Program = .ok ex1Rules := rfl

theorem ex1_initial : InitialLegal primer ex1Rules 2 [0, 2] :=
  ⟨by decide +kernel, (rulesOK_correct _ _).mp (by decide +kernel)⟩

/-!
#### Exercise 7 (★) `ex1_run`
Prove the run. `run_of_check` takes three facts the kernel can compute.
-/

theorem ex1_run : Run primer ex1Rules 2 1 [0, 2] 0 ex1.instructions [2, 2] 18 :=
  run_of_check (by decide +kernel) (by decide +kernel) (by decide +kernel)

/-!
After the transport the clock reads 2 and the next instruction is the pulse on `q0`. Its frame
has `n_optical = 1` and `t = 2`, which falsifies `rel`, so `Legal` does not hold for it. An
`idle 1` has no optical action and is legal; after three of them the clock reads 5 and the
pulse is legal. Changing the rules changes the target code.

#### Exercise 8 (★) `pulse_at_2_breaks_rel`
-/

theorem pulse_at_2_breaks_rel :
    evalRule (stepFrame primer [2, 2] (.pulse (.r half half) [0] false 3) 2)
      (relRule.lower primer []) = false := by
  decide +kernel

theorem no_idles_no_run :
    runB primer ex1Rules 2 1 [0, 2] 0
      (ex1.instructions.filter fun h => !(h matches .idle _)) = false := by
  decide +kernel

/-!
## 8. Example 2: feedback, and a rule that changes the target code

Example 2 ([Compilation page](../compilation/#ex2)) is a parity check with feedback on the
`Tee` device: two data ions each interact with one ancilla, the ancilla is read, and it is reset
only if it read 1.

```
OPENQASM 2.0;
qreg q[3];
creg c[1];
CX q[0],q[2];
CX q[1],q[2];
measure q[2] -> c[0];
if(c==1) reset q[2];
```

`Tee`'s optical declarations, all at `G` on channel 7, are

```
calibration c0:R(pi/2,pi/2) @G channel 7 duration 2;
calibration c1:MS(pi/2) @G channel 7 duration 4;
calibration c2:R(-pi/2,0) @G channel 7 duration 2;
calibration c3:R(-pi/2,pi/2) @G channel 7 duration 2;
broadcast b0:R(-pi/2,0) sites G channel 7 duration 2 limit 2;
acquire a0 @G channel 7 duration 3;
pump p0 @G channel 7 duration 4;
controller duration 1;
```

and the rule program is

```
assert init: (t = 0 and delta = 0) => N-(S) = 2;
assert cap:  forall s:Sites. Ntr(s) <= cap(s);
assert traffic:  forall p:Paths. M(p) <= 1;
```

The compiler turns the test `if(c==1)` into a **latch** that samples the comparison once, and
marks the guarded operation `when saved`. The target code, 14 events, 34 ticks:

```
init q0@S,q1@S,q2@G;
transport q0:S->G[p0];                 // t=0, d=2
pulse R(pi/2,pi/2) q0 @G via c0;       // t=2, d=2
pulse MS(pi/2) q0,q2 @G via c1;        // t=4, d=4
broadcast R(-pi/2,0) q0,q2 @G via b0;  // t=8, d=2
pulse R(-pi/2,pi/2) q0 @G via c3;      // t=10, d=2
transport q0:G->S[p1];                 // t=12, d=2
transport q1:S->G[p0];                 // t=14, d=2
pulse R(pi/2,pi/2) q1 @G via c0;       // t=16, d=2
pulse MS(pi/2) q1,q2 @G via c1;        // t=18, d=4
broadcast R(-pi/2,0) q1,q2 @G via b0;  // t=22, d=2
pulse R(-pi/2,pi/2) q1 @G via c3;      // t=24, d=2
readout q2 -> c0 @G via a0;            // t=26, d=3
latch saved <- (c0) == 1;              // t=29, d=1
when saved pump q2 @G via p0;          // t=30, d=4
```
-/

def cxAt (a b : Nat) : List HardwareInstruction :=
  [.pulse (.r half half) [a] false 0,
   .pulse (.ms half) [a, b] false 1,
   .broadcast 0 [(.r mhalf e0, a), (.r mhalf e0, b)] false [2, 2],
   .pulse (.r mhalf half) [a] false 3]

def ex2 : Execution where
  initial := [0, 0, 2]
  instructions :=
    [.transport [⟨0, 0⟩]] ++ cxAt 0 2 ++
    [.transport [⟨0, 1⟩], .transport [⟨1, 0⟩]] ++ cxAt 1 2 ++
    [.readout 2 0 false 0, .latch [0] 1, .pump 2 true 0]

def ex2Program : Program :=
  [⟨"init", .staticOccupancy, .implies (.and (.eq (.atom .start) (.constant 0))
      (.eq (.atom .duration) (.constant 0)))
      (.eq (.observe (.occupancy .before) (.id 0)) (.constant 2))⟩,
   ⟨"cap", .staticOccupancy, capRule⟩,
   ⟨"traffic", .transportControl, .all "p" .paths (.le (.observe .pathUse (.var "p")) (.constant 1))⟩]

def ex2Rules : RuleSet := lowerProgram tee ex2Program

theorem ex2_run : Run tee ex2Rules 3 1 [0, 0, 2] 0 ex2.instructions [0, 2, 2] 34 :=
  run_of_check (by decide +kernel) (by decide +kernel) (by decide +kernel)

/-!
The time and the placement of the guarded pump do not depend on the outcome: its second
realization is an `idle` of the pump's 4 ticks, and `Legal` checked the core checks and the
rules on both frames. One target code covers both branches.

#### Exercise 9 (★) `pump_realizes_as_idle`
-/

theorem pump_realizes_as_idle : realize tee false (.pump 2 true 0) = .idle 4 := by
  decide +kernel

/-!
**Rules are inputs.** Rule program B adds one assertion and keeps the device and the source:

```
assert one:  n_optical <= 1;
```

The broadcast frame has `n_optical = 2` (it plays two pulses in one shot), so the code above is
no longer a legal run. Stage 8 of the compiler (lesson I3) then keeps the two pulses apart, and
the target code takes 16 events and 38 ticks instead of 34. Nothing in the compiler or its
proof names this rule.
-/

def oneRule : Declaration := ⟨"one", .operationTiming, .le (.atom .pulseCount) (.constant 1)⟩

def ex2RulesB : RuleSet := lowerProgram tee (ex2Program ++ [oneRule])

theorem broadcast_breaks_one : runB tee ex2RulesB 3 1 [0, 0, 2] 0 ex2.instructions = false := by
  decide +kernel

def cxSplit (a b : Nat) : List HardwareInstruction :=
  [.pulse (.r half half) [a] false 0,
   .pulse (.ms half) [a, b] false 1,
   .pulse (.r mhalf e0) [a] false 2,
   .pulse (.r mhalf e0) [b] false 2,
   .pulse (.r mhalf half) [a] false 3]

def ex2B : Execution where
  initial := [0, 0, 2]
  instructions :=
    [.transport [⟨0, 0⟩]] ++ cxSplit 0 2 ++
    [.transport [⟨0, 1⟩], .transport [⟨1, 0⟩]] ++ cxSplit 1 2 ++
    [.readout 2 0 false 0, .latch [0] 1, .pump 2 true 0]

/-!
#### Exercise 10 (★★) `ex2B_run`
The compiler's target code under rule program B is a legal run that ends at tick 38.
-/

theorem ex2B_run : Run tee ex2RulesB 3 1 [0, 0, 2] 0 ex2B.instructions [0, 2, 2] 38 :=
  run_of_check (by decide +kernel) (by decide +kernel) (by decide +kernel)

/-!
## 9. The rules as written, on every frame

The compiler checks the elaborated core rules. By `elaborate_correct`, a legal step satisfies
the designer's formulas themselves, not only their expansions (QCCDCert's
`Assertions.legal_satisfies`; `run_satisfies` is the same for every step of a run):
-/

theorem legal_satisfies {d : Device} {program : Program} {rules : RuleSet} {qubits bits : Nat}
    {pos : Placement} {t : Nat} {h : HardwareInstruction}
    (elaborated : elaborate d program = .ok rules) (legal : Legal d rules qubits bits pos t h) :
    ProgramHolds d program (stepFrame d pos h t) :=
  (elaborate_correct elaborated _).mp legal.2.1

/-!
That is the Legality clause for one instruction. Lesson I4 adds what Legality says about the
timeline (steps that share a resource never overlap, a broadcast channel plays one waveform at
a time, chain order); lesson I3 asks what the legal code computes.
-/
