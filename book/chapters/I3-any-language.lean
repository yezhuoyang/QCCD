import QCCDC.Cert.Sound

/-!
# Meaning: nine stages, one equation each

*Part I, lesson I3. You need lessons I1 and I2 (restated, unseen, at the top of this page) and
lesson G4, where the five pulses of a CNOT are proved.*

The **Meaning** clause of `verified_compilation` says the target code computes what the source
computes:

```
erase(e₁..eₘ) = K,    proj(K) = native(P),
∀ η σ. ∃ σ'.  ⟨P, obs σ⟩ ⇓η σ'  ∧  obs ⟦Kᵀ⟧(H,η) σ ≃ σ'
```

`native(P)` is the ordered native program that the source passes fix; `proj` deletes transports
and waits from the target code and unpacks shared pulses; `erase` deletes the chain exchanges
of the timeline (lesson I4). The third line is the semantic statement: for every assignment `η`
of the symbolic angles and every initial state `σ`, running `K` in the order of the timeline
gives the observable result of the source, up to the order in which measurement branches are
listed.

QCCDCert has the shape of CompCert: a chain of programs with one proved pass between
neighbours ([Compilation page](../compilation/#stages)). This lesson follows Example 1 through
all nine stages, and states for each pass the equation that relates its input to its output
([Compilation page](../compilation/#meaning)). As before, the Lean is a faithful miniature of
the QCCDCert declarations it names (`selectInstruction`, `Binding`, `Rules.Routes`,
`Rules.Packs`, `Rules.Times`, `hardwareProject`, `CoreExecution.prefix_invariant`,
`Binding.stage_correct` ... `Timed.stage_correct`).
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
theorem rulesOK_correct (rules : RuleSet) (F : Frame) :
    rulesOK rules F = true ↔ RulesHold rules F := by
  simp [rulesOK, RulesHold, evalRule_correct]
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
## 1. The programs of one compilation

| stage | program | what the stage adds | Example 1 |
|---|---|---|---|
| 1-3 | effects `P_E` | gates expanded, conditions saved | `CX q0,q1;` |
| 4 | pulses `P_P` | U and CX become R and MS pulses | `R; MS; R; R; R;` |
| 5 | bound | candidate controls | `R q0 {c3};` |
| 6 | placed | initial sites | `init q0@S,q1@G;` |
| 7 | routed | transports, chosen controls | `transport q0:S->G;` |
| 8 | packed | shared pulses | `broadcast R q0,q1;` |
| 9 | timed: target code `K` | durations and waits | `idle 1;` (t = 2..4) |
| + | timeline `T` | overlap, exchanges | starts 0, 2, 3, ... |

Stages 1 to 4 fix **what** is computed and never look at the device. Stages 5 to 9 decide
**how**: which control plays each pulse, where ions start, how they travel, which pulses share
a laser shot, and when everything happens. Stages 1 to 5 are functions. Stages 6 to 9 search,
so QCCDCert states each as a judgment that relates its input to its output, and the proof
depends only on the judgment, never on how the search found the output.

## 2. Stages 1 to 3: the effect program

Include resolution and domain analysis change no operation (domain analysis proves every angle
expression defined, without floating point). Effect expansion inlines gate definitions and
registers into flat effects: `U`, `CX`, `measure`, `reset`, `barrier`, and, for a condition
`if(c==n) O;`, a **latch** that samples the comparison once followed by `O` marked
`when saved`. Example 2's last two lines become

```
measure q2 -> c0;
latch saved <- (c0) == 1;
when saved reset q2;
```

Expansion keeps every operation, its order, and the moment a condition is read.
-/

inductive EffectInstruction where
  | u (theta phi lam : Expr) (q : Nat) (guarded : Bool)
  | cx (q q' : Nat) (guarded : Bool)
  | measure (q c : Nat) (guarded : Bool)
  | reset (q : Nat) (guarded : Bool)
  | latch (bits : List Nat) (value : Nat)
  | barrier (qs : List Nat) (guarded : Bool)
  deriving DecidableEq, Repr

def ex1Effects : List EffectInstruction := [.cx 0 1 false, .measure 1 0 false]

/-!
## 3. Stage 4: pulse selection

`selectInstruction` rewrites each unitary into native pulses with exact angles and keeps every
other instruction. A `U(θ,φ,λ)` becomes three rotations, a `CX` one MS pulse and four
rotations, in time order:

```
sel(U(θ,φ,λ) q) = R(θ, π/2-λ) q;  R(π, 0) q;  R(π, (φ+λ)/2) q
sel(CX q,q')    = R(π/2,π/2) q;  MS(π/2) q,q';  R(-π/2,0) q;  R(-π/2,0) q';  R(-π/2,π/2) q
```
-/

inductive PulseInstruction where
  | pulse (wave : Wave) (ions : List Nat) (guarded : Bool)
  | measure (q c : Nat) (guarded : Bool)
  | reset (q : Nat) (guarded : Bool)
  | latch (bits : List Nat) (value : Nat)
  | barrier (qs : List Nat) (guarded : Bool)
  deriving DecidableEq, Repr

def selectInstruction : EffectInstruction → List PulseInstruction
  | .u θ φ lam q g =>
      [.pulse (.r θ (.sub half lam)) [q] g, .pulse (.r .pi e0) [q] g,
       .pulse (.r .pi (.div (.add φ lam) (.rat 2 1))) [q] g]
  | .cx q q' g =>
      [.pulse (.r half half) [q] g, .pulse (.ms half) [q, q'] g, .pulse (.r mhalf e0) [q] g,
       .pulse (.r mhalf e0) [q'] g, .pulse (.r mhalf half) [q] g]
  | .measure q c g => [.measure q c g]
  | .reset q g => [.reset q g]
  | .latch cs v => [.latch cs v]
  | .barrier qs g => [.barrier qs g]

def selectProgram (code : List EffectInstruction) : List PulseInstruction :=
  code.flatMap selectInstruction

def ex1Pulses : List PulseInstruction :=
  [.pulse (.r half half) [0] false, .pulse (.ms half) [0, 1] false, .pulse (.r mhalf e0) [0] false,
   .pulse (.r mhalf e0) [1] false, .pulse (.r mhalf half) [0] false, .measure 1 0 false]

/-!
#### Exercise 1 (★) `ex1_select`
Stage 4 turns Example 1's effects into the six instructions the compiler printed.
-/

theorem ex1_select : selectProgram ex1Effects = ex1Pulses := by
  decide +kernel

/-!
Why is that correct? Because of operator identities, proved once in QCCDCert for the operators,
so they hold when the two ions are entangled with others. For the CNOT (matrix order: the
rightmost factor is the first pulse in time):

```
CX  ≃  R(-π/2,π/2)₀ · (R(-π/2,0)₀ ⊗ R(-π/2,0)₁) · MS(π/2) · R(π/2,π/2)₀
```

where `≃` is equality up to a global phase, which no measurement observes. Lesson G4 proves
exactly this identity over exact numbers (`cx_decomp_exact`, `cx_up_to_phase`). The `U` rule
is the identity `U(θ,φ,λ) ≃ R(π,(φ+λ)/2) · R(π,0) · R(θ,π/2-λ)`.

The primer's calibrations `c0` to `c2` are exactly the pulses of a `U(π/7,0,0)`: they serve
the U gate of the paper's running example, and Example 1, which has none, leaves them unused.

#### Exercise 2 (★) `u_pulses_are_c0_c1_c2`
-/

theorem u_pulses_are_c0_c1_c2 :
    (selectInstruction (.u (.div .pi (.rat 7 1)) e0 e0 0 false)).map
        (fun i => match i with | .pulse w _ _ => some w | _ => none) =
      (primer.calibrations.take 3).map fun c => some c.wave := by
  decide +kernel

/-!
## 4. Stage 5: capability binding

Binding annotates every instruction with **all** the declarations that can play it: the
calibrations with the same waveform tree for a pulse, every acquisition for a measurement,
every pump for a reset. It fails if a list is empty (a control is missing). It changes no
instruction (`Binding.stage_correct`: erasing the annotations gives the pulse program back).
-/

def eligible (d : Device) : PulseInstruction → List Nat
  | .pulse w _ _ => (List.range d.calibrations.length).filter fun k =>
      ((d.calibrations[k]?).map Calibration.wave) == some w
  | .measure .. => List.range d.readouts.length
  | .reset .. => List.range d.pumps.length
  | _ => []

/-!
#### Exercise 3 (★) `ex1_bound`
The bound program of Example 1 is `R q0 {c3}; MS q0,q1 {c4}; R q0 {c5}; R q1 {c5}; R q0 {c6};
measure q1 -> c0 {a0}`.
-/

theorem ex1_bound : ex1Pulses.map (eligible primer) = [[3], [4], [5], [5], [6], [0]] := by
  decide +kernel

/-!
## 5. Stage 6: initial placement

Placement outputs any placement `pos₀` with `InitialLegal` (lesson I2) and leaves the program
unchanged (`Placed.stage_correct`). For Example 1 it is `q0@S, q1@G`.

## 6. Stage 7: transport routing

Routing inserts transports and picks one declaration for each instruction. QCCDCert states it
as three inference rules (`Rules.Routes`):

* **R-Done**: the empty program routes to the empty program;
* **R-Move**: a transport that is core legal here may come first, and the rest is routed from
  the placement it reaches;
* **R-Emit**: the next instruction `i` may be emitted as any `h` that realizes it (`Real h i`:
  a pulse via some calibration, a readout or pump via some declaration, or the latch or barrier
  itself) and is core legal here.

Every proposal is checked: where the bounded search fails, QCCDCert accepts a complete
proposal from an external router and admits each proposed transport only with the legality
proof that the search requires.
-/

instance (d : Device) (qubits bits : Nat) (pos : Placement) (h : HardwareInstruction) :
    Decidable (CoreLegal d qubits bits pos h) :=
  inferInstanceAs (Decidable (coreLegal d qubits bits pos h = true))

def HardwareInstruction.project : HardwareInstruction → List PulseInstruction
  | .pulse w ions g _ => [.pulse w ions g]
  | .broadcast _ members g _ => members.map fun m => .pulse m.1 [m.2] g
  | .readout q c g _ => [.measure q c g]
  | .pump q g _ => [.reset q g]
  | .latch cs v => [.latch cs v]
  | .barrier qs g => [.barrier qs g]
  | .transport _ | .idle _ => []

/-- `proj`: delete transports and waits, unpack broadcasts. -/
def hardwareProject (code : List HardwareInstruction) : List PulseInstruction :=
  code.flatMap HardwareInstruction.project

abbrev Real (h : HardwareInstruction) (i : PulseInstruction) : Prop :=
  (h matches .pulse .. ∨ h matches .readout .. ∨ h matches .pump .. ∨ h matches .latch .. ∨
    h matches .barrier ..) ∧ h.project = [i]

inductive Routes (d : Device) (qubits bits : Nat) :
    Placement → List PulseInstruction → List HardwareInstruction → Prop where
  | done (pos : Placement) : Routes d qubits bits pos [] []
  | move {pos : Placement} {is : List PulseInstruction} {A : List HardwareInstruction}
      (moves : List Move) (legal : CoreLegal d qubits bits pos (.transport moves))
      (rest : Routes d qubits bits (nextPlacement d pos (.transport moves)) is A) :
      Routes d qubits bits pos is (.transport moves :: A)
  | emit {pos : Placement} {i : PulseInstruction} {is : List PulseInstruction}
      {A : List HardwareInstruction} (h : HardwareInstruction) (real : Real h i)
      (legal : CoreLegal d qubits bits pos h) (rest : Routes d qubits bits pos is A) :
      Routes d qubits bits pos (i :: is) (h :: A)

def ex1Routed : List HardwareInstruction :=
  [.transport [⟨0, 0⟩],
   .pulse (.r half half) [0] false 3, .pulse (.ms half) [0, 1] false 4,
   .pulse (.r mhalf e0) [0] false 5, .pulse (.r mhalf e0) [1] false 5,
   .pulse (.r mhalf half) [0] false 6, .readout 1 0 false 0]

/-!
Example 1's routed program has a derivation: one R-Move, then six R-Emit, then R-Done. Each
side condition is a computation.
-/

theorem ex1_routes : Routes primer 2 1 [0, 2] ex1Pulses ex1Routed :=
  .move _ (by decide +kernel) <|
  .emit _ ⟨by decide, rfl⟩ (by decide +kernel) <|
  .emit _ ⟨by decide, rfl⟩ (by decide +kernel) <|
  .emit _ ⟨by decide, rfl⟩ (by decide +kernel) <|
  .emit _ ⟨by decide, rfl⟩ (by decide +kernel) <|
  .emit _ ⟨by decide, rfl⟩ (by decide +kernel) <|
  .emit _ ⟨by decide, rfl⟩ (by decide +kernel) <|
  .done _

/-!
The equation of stage 7 is `proj(A) = P_P`: nothing is dropped, merged or reordered. A
transport projects to nothing, and an emitted `h` projects to the `i` it realizes.

#### Exercise 4 (★★★) `routes_project`
Prove it for every derivation, by induction on it. In the `emit` case, `simp only
[hardwareProject, List.flatMap_cons] at ih ⊢`, then rewrite with `real.2` and `ih`.
-/

theorem routes_project {d : Device} {qubits bits : Nat} {pos : Placement}
    {is : List PulseInstruction} {A : List HardwareInstruction}
    (routes : Routes d qubits bits pos is A) : hardwareProject A = is := by
  induction routes with
  | done => rfl
  | move moves _ _ ih => simpa [hardwareProject, HardwareInstruction.project] using ih
  | emit h real _ _ ih =>
      simp only [hardwareProject, List.flatMap_cons] at ih ⊢
      rw [real.2, ih]; rfl

/-!
**The routing invariant.** Throughout routing, every physical prefix reaches a legal placement
and has the same effect as its native prefix. For Example 1:

| prefix of the routed program | placement | pulses done |
|---|---|---|
| (empty) | `q0@S, q1@G` | none |
| transport | `q0@G, q1@G` | none |
| transport; R | `q0@G, q1@G` | R |
| transport; R; MS | `q0@G, q1@G` | R; MS |

The next step is either a transport that is legal here, or the next pulse with its ions already
at a site that has the control. In QCCDCert the invariant is `CoreExecution.prefix_invariant`,
about a **core run** (`CoreExecution`: every instruction core legal where the ones before it
left the ions). Its semantic half needs the meaning of programs. The miniature gives programs
a meaning for **any** action of a native instruction on any state type `σ`, so whatever it
proves holds for every model of the pulses, including the quantum one; a transport and a wait
do nothing to the logical state. (In QCCDCert, `σ` is an ensemble of quantum and classical
branches; a broadcast acts as one simultaneous operator, proved equal to its members in order
because they act on distinct ions, so there the per-instruction lemma
`hardwareInstruction_refines` takes the legality of the instruction as a premise.)
-/

def interpretPulseCode {σ : Type} (act : PulseInstruction → σ → σ) :
    List PulseInstruction → σ → σ
  | [], s => s
  | i :: rest, s => interpretPulseCode act rest (act i s)

def interpretHardwareCode {σ : Type} (act : PulseInstruction → σ → σ) :
    List HardwareInstruction → σ → σ
  | [], s => s
  | h :: rest, s => interpretHardwareCode act rest (interpretPulseCode act h.project s)

theorem interpretPulseCode_append {σ : Type} (act : PulseInstruction → σ → σ)
    (xs ys : List PulseInstruction) (s : σ) :
    interpretPulseCode act (xs ++ ys) s =
      interpretPulseCode act ys (interpretPulseCode act xs s) := by
  induction xs generalizing s with
  | nil => rfl
  | cons x xs ih => exact ih _

/-!
#### Exercise 5 (★★) `quantum_refines`
Running hardware code is running its projection. Induction on the code, generalizing the state;
in the step, unfold one instruction on each side and use `interpretPulseCode_append`.
-/

theorem quantum_refines {σ : Type} (act : PulseInstruction → σ → σ)
    (code : List HardwareInstruction) (s : σ) :
    interpretHardwareCode act code s = interpretPulseCode act (hardwareProject code) s := by
  induction code generalizing s with
  | nil => rfl
  | cons h rest ih =>
      simp only [interpretHardwareCode, hardwareProject, List.flatMap_cons] at ih ⊢
      rw [ih, interpretPulseCode_append]

inductive CoreExecution (d : Device) (qubits bits : Nat) :
    Placement → List HardwareInstruction → Prop where
  | nil (pos : Placement) : CoreExecution d qubits bits pos []
  | cons {pos : Placement} {h : HardwareInstruction} {rest : List HardwareInstruction}
      (head : CoreLegal d qubits bits pos h)
      (tail : CoreExecution d qubits bits (nextPlacement d pos h) rest) :
      CoreExecution d qubits bits pos (h :: rest)

theorem CoreExecution.split {d : Device} {qubits bits : Nat} {pos : Placement}
    {first second : List HardwareInstruction}
    (exec : CoreExecution d qubits bits pos (first ++ second)) :
    CoreExecution d qubits bits pos first ∧
      CoreExecution d qubits bits (finalPlacement d pos first) second := by
  induction first generalizing pos with
  | nil => exact ⟨.nil pos, exec⟩
  | cons h rest ih =>
      cases exec with
      | cons head tail =>
          obtain ⟨a, b⟩ := ih tail
          exact ⟨.cons head a, b⟩

/-- A core-legal step from a valid placement reaches a valid placement: for a transport this is
the premise `H ⊢ next(pos) : n_q` of `C-Transport`; every other instruction stays put. -/
theorem next_valid {d : Device} {qubits bits : Nat} {pos : Placement} {h : HardwareInstruction}
    (valid : placementValid d qubits pos = true) (legal : CoreLegal d qubits bits pos h) :
    placementValid d qubits (nextPlacement d pos h) = true := by
  cases h with
  | transport moves =>
      simp only [CoreLegal, coreLegal, activeCoreLegal, transportLegal, Bool.and_eq_true] at legal
      exact legal.1.1.1.2
  | _ => exact valid

/-!
#### Exercise 6 (★★★) `final_valid`
A core run from a valid placement ends in a valid placement. Induction on the run, with
`next_valid` in the step.
-/

theorem final_valid {d : Device} {qubits bits : Nat} {pos : Placement}
    {code : List HardwareInstruction} (exec : CoreExecution d qubits bits pos code)
    (valid : placementValid d qubits pos = true) :
    placementValid d qubits (finalPlacement d pos code) = true := by
  induction exec with
  | nil => exact valid
  | cons head _ ih => exact ih (next_valid valid head)

/-- **The routing invariant** (`CoreExecution.prefix_invariant`): after every prefix of a core
run, the placement reached is valid, and the prefix has the effect of its native prefix. -/
theorem CoreExecution.prefix_invariant {d : Device} {qubits bits : Nat} {pos : Placement}
    {first second : List HardwareInstruction}
    (exec : CoreExecution d qubits bits pos (first ++ second))
    (initial : placementValid d qubits pos = true) {σ : Type} (act : PulseInstruction → σ → σ)
    (s : σ) :
    placementValid d qubits (finalPlacement d pos first) = true ∧
      interpretHardwareCode act first s = interpretPulseCode act (hardwareProject first) s :=
  ⟨final_valid exec.split.1 initial, quantum_refines act first s⟩

/-!
A routed program is a core run:
-/

theorem routes_core {d : Device} {qubits bits : Nat} {pos : Placement}
    {is : List PulseInstruction} {A : List HardwareInstruction}
    (routes : Routes d qubits bits pos is A) : CoreExecution d qubits bits pos A := by
  induction routes with
  | done pos => exact .nil pos
  | move moves legal _ ih => exact .cons legal ih
  | @emit pos i is A h real legal _ ih =>
      have keep : nextPlacement d pos h = pos := by
        rcases real with ⟨kind, _⟩
        cases h <;> simp_all [nextPlacement]
      exact .cons legal (by rw [keep]; exact ih)

/-!
## 7. Stage 8: broadcast formation

Stage 8 replaces a run of adjacent pulses by one declared broadcast when they have the same
waveform and guard on distinct ions and the broadcast is core legal (`Rules.Packs`): **P-Keep**
keeps a plain instruction, **P-Join** joins pulses into a broadcast. Keeping everything is
always allowed, and the compiler falls back to it when stage 9 finds no timing for the packed
program. Under Example 2's rule `one` no broadcast frame is legal, so the pulses stay apart.
-/

def HardwareInstruction.plain : HardwareInstruction → Bool
  | .idle _ | .broadcast .. => false
  | _ => true

def membersAsPulses (g : Bool) : List (Wave × Nat) → List Nat → List HardwareInstruction
  | m :: ms, k :: ks => .pulse m.1 [m.2] g k :: membersAsPulses g ms ks
  | _, _ => []

inductive Packs (d : Device) (qubits bits : Nat) :
    Placement → List HardwareInstruction → List HardwareInstruction → Prop where
  | done (pos : Placement) : Packs d qubits bits pos [] []
  | keep {pos : Placement} {A A' : List HardwareInstruction} (h : HardwareInstruction)
      (plain : h.plain = true) (legal : CoreLegal d qubits bits pos h)
      (rest : Packs d qubits bits (nextPlacement d pos h) A A') :
      Packs d qubits bits pos (h :: A) (h :: A')
  | join {pos : Placement} {A A' : List HardwareInstruction} (b : Nat)
      (members : List (Wave × Nat)) (g : Bool) (ks : List Nat)
      (legal : CoreLegal d qubits bits pos (.broadcast b members g ks))
      (rest : Packs d qubits bits pos A A') :
      Packs d qubits bits pos (membersAsPulses g members ks ++ A) (.broadcast b members g ks :: A')

def ex1Packed : List HardwareInstruction :=
  [.transport [⟨0, 0⟩],
   .pulse (.r half half) [0] false 3, .pulse (.ms half) [0, 1] false 4,
   .broadcast 0 [(.r mhalf e0, 0), (.r mhalf e0, 1)] false [5, 5],
   .pulse (.r mhalf half) [0] false 6, .readout 1 0 false 0]

/-!
#### Exercise 7 (★★) `ex1_packs`
Give the derivation: three P-Keep, one P-Join for the two `R(-π/2,0)` pulses via `b0`, two
P-Keep, done. Every legality premise is `by decide +kernel`; P-Keep's `plain` is `rfl`.
-/

theorem ex1_packs : Packs primer 2 1 [0, 2] ex1Routed ex1Packed :=
  .keep _ rfl (by decide +kernel) <|
  .keep _ rfl (by decide +kernel) <|
  .keep _ rfl (by decide +kernel) <|
  .join 0 [(.r mhalf e0, 0), (.r mhalf e0, 1)] false [5, 5] (by decide +kernel) <|
  .keep _ rfl (by decide +kernel) <|
  .keep _ rfl (by decide +kernel) <|
  .done _

/-!
A broadcast acts as the pulses it replaces, so stage 8 keeps the projection. A legal broadcast
lists one calibration per member (a premise of `C-Broadcast`), which is what the unpacking needs.
-/

theorem broadcast_lengths {d : Device} {qubits bits : Nat} {pos : Placement} {b : Nat}
    {members : List (Wave × Nat)} {g : Bool} {ks : List Nat}
    (legal : CoreLegal d qubits bits pos (.broadcast b members g ks)) :
    members.length = ks.length := by
  simp only [CoreLegal, coreLegal, activeCoreLegal, broadcastLegal, Bool.and_eq_true] at legal
  split at legal
  · simp at legal
  · simp only [Bool.and_eq_true, beq_iff_eq] at legal
    exact legal.1.1.1.1.2

theorem project_members (g : Bool) :
    ∀ (members : List (Wave × Nat)) (ks : List Nat), members.length = ks.length →
      hardwareProject (membersAsPulses g members ks) = members.map fun m => .pulse m.1 [m.2] g
  | [], [], _ => rfl
  | m :: ms, k :: ks, h => by
      simp only [membersAsPulses, hardwareProject, List.flatMap_cons, List.map_cons,
        HardwareInstruction.project, List.singleton_append]
      rw [← project_members g ms ks (by simpa using h)]; rfl

theorem packs_project {d : Device} {qubits bits : Nat} {pos : Placement}
    {A A' : List HardwareInstruction} (packs : Packs d qubits bits pos A A') :
    hardwareProject A' = hardwareProject A := by
  induction packs with
  | done => rfl
  | keep h _ _ _ ih => simp only [hardwareProject, List.flatMap_cons] at ih ⊢; rw [ih]
  | join b members g ks legal _ ih =>
      have hm := project_members g members ks (broadcast_lengths legal)
      simp only [hardwareProject] at hm ih ⊢
      simp only [List.flatMap_cons, List.flatMap_append, hm, ih, HardwareInstruction.project]

/-!
## 8. Stage 9: time assignment

Stage 9 gives every event its start time and inserts waits (`Rules.Times`): **T-Emit** emits
the next instruction if it is `Legal` (lesson I2) at the current placement and clock; **T-Wait**
inserts `idle n` if that is legal. This is where the rules are checked, for both realizations
of a guarded event. A wait changes only the time.
-/

inductive Times (d : Device) (rules : RuleSet) (qubits bits : Nat) :
    Placement → Nat → List HardwareInstruction → List HardwareInstruction → Prop where
  | done (pos : Placement) (t : Nat) : Times d rules qubits bits pos t [] []
  | emit {pos : Placement} {t : Nat} {A K : List HardwareInstruction} (h : HardwareInstruction)
      (notWait : (h matches .idle _) = false) (legal : Legal d rules qubits bits pos t h)
      (rest : Times d rules qubits bits (nextPlacement d pos h) (t + instructionDuration d h) A K) :
      Times d rules qubits bits pos t (h :: A) (h :: K)
  | wait {pos : Placement} {t : Nat} {A K : List HardwareInstruction} (n : Nat)
      (legal : Legal d rules qubits bits pos t (.idle n))
      (rest : Times d rules qubits bits pos (t + n) A K) :
      Times d rules qubits bits pos t A (.idle n :: K)

/-!
#### Exercise 8 (★★) `times_run`
Timed code is a legal run (lesson I2's `Run`), ending at its final placement and time.
Induction on the derivation; each case is one constructor of `Run`.
-/

theorem times_run {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement} {t : Nat}
    {A K : List HardwareInstruction} (times : Times d rules qubits bits pos t A K) :
    Run d rules qubits bits pos t K (finalPlacement d pos K) (finalTime d t K) := by
  induction times with
  | done pos t => exact .nil pos t
  | emit h _ legal _ ih => exact .cons legal ih
  | wait n legal _ ih => exact .cons legal ih

theorem times_project {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement}
    {t : Nat} {A K : List HardwareInstruction} (times : Times d rules qubits bits pos t A K) :
    hardwareProject K = hardwareProject A := by
  induction times with
  | done => rfl
  | emit h _ _ _ ih => simp only [hardwareProject, List.flatMap_cons] at ih ⊢; rw [ih]
  | wait n _ _ ih => simpa [hardwareProject, HardwareInstruction.project] using ih

/-!
Example 1's timed program is lesson I1's `ex1`: the packed program with three waits after the
transport.

## 9. The chain of equations

Each pass has its own equation; chained, they give `proj(K) = native(P)`, for every device,
rule program and search result:

| pass | equation |
|---|---|
| 4 | `selectProgram P_E = P_P` (with the operator identities: same meaning up to phase) |
| 5, 6 | the program is unchanged |
| 7 | `hardwareProject A = P_P` (`routes_project`) |
| 8 | `hardwareProject A' = hardwareProject A` (`packs_project`) |
| 9 | `hardwareProject K = hardwareProject A'` (`times_project`) |
-/

theorem meaning_chain {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement}
    {effects : List EffectInstruction} {A A' K : List HardwareInstruction}
    (routes : Routes d qubits bits pos (selectProgram effects) A)
    (packs : Packs d qubits bits pos A A') (times : Times d rules qubits bits pos 0 A' K) :
    hardwareProject K = selectProgram effects := by
  rw [times_project times, packs_project packs, routes_project routes]

/-!
#### Exercise 9 (★) `ex1_meaning`
On Example 1 the chain can also be computed: the target code projects to stage 4's output.
-/

theorem ex1_meaning : hardwareProject ex1.instructions = selectProgram ex1Effects := by
  decide +kernel

/-!
#### Exercise 10 (★★) `same_result`
And so the target code has the effect of the native program, for every model of the pulses:
combine `meaning_chain` with `quantum_refines`.
-/

theorem same_result {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement}
    {effects : List EffectInstruction} {A A' K : List HardwareInstruction}
    (routes : Routes d qubits bits pos (selectProgram effects) A)
    (packs : Packs d qubits bits pos A A') (times : Times d rules qubits bits pos 0 A' K)
    {σ : Type} (act : PulseInstruction → σ → σ) (s : σ) :
    interpretHardwareCode act K s = interpretPulseCode act (selectProgram effects) s := by
  rw [quantum_refines, meaning_chain routes packs times]

/-!
In QCCDCert the last step is the source semantics itself: expansion simulates the source
(`compileFrontend_source_correct`), and the operator identities of stage 4 turn the native
program's meaning into the source's (`selectProgram_correct`), for every angle assignment and
input ensemble. Only the observable
part is compared: `obs` hides the compiler's saved bit.

What is left of Meaning is the timeline: the target code runs in the order of its start times,
not in program order, and the timeline inserts exchanges. Lesson I4 shows why that changes
nothing: events that overlap share no resource, and such events act in either order with the
same result.
-/
