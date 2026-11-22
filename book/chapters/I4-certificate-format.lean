import QCCDC.Cert.Sound

/-!
# Time: durations, the parallel schedule, chain order

*Part I, lesson I4. You need lessons I1 to I3 (restated, unseen, at the top of this page).*

The **Time** clause of `verified_compilation` says the reported time is the time the device
declares:

```
t_end = max_i (t_i + δ_i) ≤ Σ_i δ_i,        ∀ pulse e_i.  δ_i = d(k_i)
```

The timeline `T` lists steps `e₁ ... eₘ` (the instructions of `K`, with chain exchanges
inserted), their start times `t_i` and a finish `t_end`; `δ_i` is the duration the hardware
program declares for `e_i`, and `k_i` the calibration of a pulse at the trap that holds its
ions. The reported finish is the latest end of a step, never more than the sum of the
durations. And Legality has three clauses about the timeline that only make sense here: steps
that share a resource never overlap, two steps on one broadcast channel are apart in time or
play one waveform together, and only the ion at the end of a chain leaves it.

This lesson computes all of it on the compiler's real outputs
([Compilation page](../compilation/#time)): Example 1, where nothing can overlap (18 ticks), and
Example 3, a line of four traps with one broadcast transport channel, where the parallel
schedule finishes at tick 32 instead of 44, and chain order adds two exchanges. As before, the
Lean is a faithful miniature of the QCCDCert declarations it names (`instructionDuration`,
`Parallel.instructionResources`, `Parallel.step`, `Parallel.timeline_exclusive`,
`Parallel.finish_le_sequential`, `Shared.choose`, `Chain.check`, `Chain.erase`).
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
def capRule : Formula :=
  .all "s" .sites (.le (.observe (.occupancy .during) (.var "s")) (.observe .capacity (.var "s")))
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
## 1. Durations come from the hardware program

Every event is charged the duration the device declares for it (`instructionDuration`, lesson
I1): a pulse that of its calibration, a broadcast its profile's, a transport its longest path,
a readout or pump its declaration, a latch the controller's latency, `idle n` exactly `n`.
Nothing is estimated. One after another, the events of Example 1 take
`2 + 1 + 1 + 1 + 2 + 4 + 2 + 2 + 3` ticks.

#### Exercise 1 (★) `ex1_durations`
-/

theorem ex1_durations :
    ex1.instructions.map (instructionDuration primer) = [2, 1, 1, 1, 2, 4, 2, 2, 3] ∧
      finalTime primer 0 ex1.instructions = 18 := by
  decide +kernel

/-!
The waits are the timing stage's (lesson I2): `idle n` changes only the clock, and stage 9
inserts one wherever the next event is not yet legal. A guarded event reserves the same
duration on both branches, so time never depends on a measurement outcome.

## 2. Resources and conflict

The parallel schedule lets independent events overlap. To know which are independent, every
step holds a set of **resources**: ions, sites, optical channels, transport channels,
classical bits, and the controller that saves a guard's condition (QCCDCert's
`Parallel.instructionResources`):

| step | resources |
|---|---|
| `transport` | for every move: its ion, the path's source, destination, channel and every site it passes |
| `pulse ... via k` | its ions, the site and optical channel of `k`, the controller if guarded |
| `broadcast ... via b` | its ions, the site and channel of every member's calibration, `b`'s channel |
| `readout q -> c via a` | `q`, `c`, the site and channel of `a`, the controller if guarded |
| `pump q via a` | `q`, the site and channel of `a`, the controller if guarded |
| `latch` | the controller and the bits it reads |
| `idle n` | none, but it is **global**: it separates everything before it from everything after |

(A transport on a `mandatory` channel also holds the source of every path with its channel and
waveform, because its legality depends on the ions there; no example here has one, and the
miniature leaves it out.) Two events **conflict** if one is global or they share a resource.
-/

inductive Resource where
  | ion (n : Nat) | site (n : Nat) | optical (n : Nat) | transport (n : Nat) | bit (n : Nat)
  | controller
  deriving DecidableEq, Repr

def guardRes (g : Bool) : List Resource := if g then [.controller] else []

def calRes (d : Device) (k : Nat) : List Resource :=
  match d.calibrations[k]? with
  | some c => [.site c.site, .optical c.channel]
  | none => []

def instructionResources (d : Device) : HardwareInstruction → List Resource
  | .transport moves => moves.flatMap fun m =>
      match d.paths[m.path]? with
      | some p => [.ion m.ion, .site p.src, .site p.dst, .transport p.channel] ++ p.via.map .site
      | none => [.ion m.ion]
  | .pulse _ ions g k => ions.map .ion ++ calRes d k ++ guardRes g
  | .broadcast b members g ks =>
      members.map (fun m => .ion m.2) ++ ks.flatMap (calRes d) ++
        ((d.broadcasts[b]?).map fun p => [.optical p.channel]).getD [] ++ guardRes g
  | .readout q c g a => [.ion q, .bit c] ++
      ((d.readouts[a]?).map fun r => [.site r.site, .optical r.channel]).getD [] ++ guardRes g
  | .pump q g p => [.ion q] ++
      ((d.pumps[p]?).map fun r => [.site r.site, .optical r.channel]).getD [] ++ guardRes g
  | .latch cs _ => .controller :: cs.map .bit
  | .barrier qs g => qs.map .ion ++ guardRes g
  | .idle _ => []

structure Event where
  resources : List Resource
  duration : Nat
  global : Bool

def instructionEvent (d : Device) (h : HardwareInstruction) : Event :=
  ⟨instructionResources d h, instructionDuration d h, h matches .idle _⟩

def Conflict (a b : Event) : Prop :=
  a.global = true ∨ b.global = true ∨ ∃ r, r ∈ a.resources ∧ r ∈ b.resources

/-!
## 3. The exclusive schedule

The scheduler keeps, for every resource, the time it becomes free, a **floor** before which
nothing may start, and the **horizon**, the latest finish so far. It takes the events in program
order and starts each as early as everything it holds is free (`ready`); a global event starts
at the horizon and raises the floor (QCCDCert's `Parallel.step`). It keeps the instruction
sequence; it only assigns start times.
-/

structure State where
  avail : Resource → Nat
  floor : Nat
  horizon : Nat

def initialState : State := ⟨fun _ => 0, 0, 0⟩

def ready (s : State) : List Resource → Nat
  | [] => s.floor
  | r :: rest => max (s.avail r) (ready s rest)

def step (s : State) (e : Event) : Nat × State :=
  if e.global then
    (s.horizon, ⟨s.avail, s.horizon + e.duration, s.horizon + e.duration⟩)
  else
    let t := ready s e.resources
    (t, ⟨fun r => if r ∈ e.resources then t + e.duration else s.avail r, s.floor,
      max s.horizon (t + e.duration)⟩)

def run : State → List Event → List (Event × Nat)
  | _, [] => []
  | s, e :: es => (e, (step s e).1) :: run (step s e).2 es

def final : State → List Event → State
  | s, [] => s
  | s, e :: es => final (step s e).2 es

def timeline (d : Device) (code : List HardwareInstruction) : List (Event × Nat) :=
  run initialState (code.map (instructionEvent d))

def starts (d : Device) (code : List HardwareInstruction) : List Nat :=
  (timeline d code).map Prod.snd

def finish (d : Device) (code : List HardwareInstruction) : Nat :=
  (final initialState (code.map (instructionEvent d))).horizon

#eval starts primer ex1.instructions

/-!
In Example 1 every pulse holds `G` and optical channel 7, the waits are global, and so the
parallel schedule is the sequential one: starts `0, 2, 3, 4, 5, 7, 11, 13, 15`, finish 18, as
the compiler reported.

#### Exercise 2 (★) `ex1_parallel_is_sequential`
-/

theorem ex1_parallel_is_sequential :
    starts primer ex1.instructions = [0, 2, 3, 4, 5, 7, 11, 13, 15] ∧
      finish primer ex1.instructions = 18 := by
  decide +kernel

/-!
## 4. Proved once: exclusion and the bound

Two facts hold for every device and every program, so QCCDCert proves them once and no
compilation checks them. **Exclusion** (`Parallel.timeline_exclusive`): two steps that conflict
never overlap, and the earlier one in program order finishes first. **The bound**
(`Parallel.finish_le_sequential`): the parallel finish is at most the sum of the durations.

The proof of exclusion keeps an invariant, `Covers`: once an event is scheduled, the state
remembers its end in the horizon, in the floor if it was global, and in the free time of each
of its resources. A later event that conflicts with it must wait for one of those, so it starts
after the end.
-/

def State.Bounded (s : State) : Prop := s.floor ≤ s.horizon ∧ ∀ r, s.avail r ≤ s.horizon

theorem ready_le {s : State} (b : s.Bounded) : ∀ rs : List Resource, ready s rs ≤ s.horizon
  | [] => b.1
  | r :: rest => by
      have := b.2 r
      have := ready_le b rest
      simp only [ready]; omega

theorem floor_le_ready (s : State) : ∀ rs : List Resource, s.floor ≤ ready s rs
  | [] => Nat.le_refl _
  | r :: rest => by have := floor_le_ready s rest; simp only [ready]; omega

theorem avail_le_ready (s : State) {r : Resource} :
    ∀ {rs : List Resource}, r ∈ rs → s.avail r ≤ ready s rs
  | _ :: _, .head _ => by simp only [ready]; omega
  | _ :: _, .tail _ m => by have := avail_le_ready s m; simp only [ready]; omega

theorem step_bounded {s : State} (b : s.Bounded) (e : Event) :
    (step s e).2.Bounded ∧ (step s e).2.horizon ≤ s.horizon + e.duration := by
  have hr := ready_le b e.resources
  unfold step
  split
  · exact ⟨⟨Nat.le_refl _, fun r => by have := b.2 r; simp only; omega⟩, Nat.le_refl _⟩
  · refine ⟨⟨?_, fun r => ?_⟩, ?_⟩
    · have := b.1; simp only; omega
    · simp only; split
      · omega
      · have := b.2 r; omega
    · simp only; omega

def Covers (s : State) (x : Event × Nat) : Prop :=
  x.2 + x.1.duration ≤ s.horizon ∧ (x.1.global = true → x.2 + x.1.duration ≤ s.floor) ∧
    ∀ r ∈ x.1.resources, x.2 + x.1.duration ≤ max (s.avail r) s.floor

theorem covers_start {s : State} {x : Event × Nat} (c : Covers s x) (e : Event)
    (conflict : Conflict x.1 e) : x.2 + x.1.duration ≤ (step s e).1 := by
  unfold step
  split
  · exact c.1
  · rename_i ng
    simp only
    rcases conflict with g | g | ⟨r, ra, rb⟩
    · exact Nat.le_trans (c.2.1 g) (floor_le_ready s _)
    · exact absurd g ng
    · have := c.2.2 r ra
      have := avail_le_ready s rb
      have := floor_le_ready s e.resources
      omega

theorem covers_step {s : State} (b : s.Bounded) {x : Event × Nat} (c : Covers s x) (e : Event) :
    Covers (step s e).2 x := by
  have hr := ready_le b e.resources
  obtain ⟨c1, c2, c3⟩ := c
  unfold step
  split
  · refine ⟨by simp only; omega, fun g => by have := c2 g; have := b.1; simp only; omega,
      fun r m => by have := c3 r m; have := b.1; simp only; omega⟩
  · refine ⟨by simp only; omega, fun g => by have := c2 g; simp only; omega, fun r m => ?_⟩
    have := c3 r m
    simp only
    split
    · rename_i inE
      have := avail_le_ready s inE
      have := floor_le_ready s e.resources
      omega
    · omega

theorem covers_new {s : State} (e : Event) : Covers (step s e).2 (e, (step s e).1) := by
  unfold step
  split
  · refine ⟨by simp only; omega, fun _ => by simp only; omega, fun r _ => by simp only; omega⟩
  · rename_i g
    refine ⟨by simp only; omega, fun g' => absurd g' g, fun r m => ?_⟩
    simp only [if_pos m]; omega

theorem run_after : ∀ (es : List Event) (s : State) (x : Event × Nat), s.Bounded → Covers s x →
    ∀ y ∈ run s es, Conflict x.1 y.1 → x.2 + x.1.duration ≤ y.2
  | [], _, _, _, _, y, m, _ => by simp [run] at m
  | e :: es, s, x, b, c, y, m, conflict => by
      simp only [run, List.mem_cons] at m
      rcases m with rfl | m
      · exact covers_start c e conflict
      · exact run_after es _ x (step_bounded b e).1 (covers_step b c e) y m conflict

theorem run_exclusive : ∀ (es : List Event) (s : State), s.Bounded →
    (run s es).Pairwise fun x y => Conflict x.1 y.1 → x.2 + x.1.duration ≤ y.2
  | [], _, _ => .nil
  | e :: es, s, b => by
      simp only [run]
      exact .cons (fun y m conflict =>
          run_after es _ _ (step_bounded b e).1 (covers_new e) y m conflict)
        (run_exclusive es _ (step_bounded b e).1)

/-- **Exclusion** (`Parallel.timeline_exclusive`). -/
theorem timeline_exclusive (d : Device) (code : List HardwareInstruction) :
    (timeline d code).Pairwise fun x y => Conflict x.1 y.1 → x.2 + x.1.duration ≤ y.2 :=
  run_exclusive _ _ ⟨Nat.le_refl _, fun _ => Nat.le_refl _⟩

/-!
For the bound, `step_bounded` says that scheduling one event moves the horizon by at most its
duration (the start is never later than the horizon, because nothing is free later than that).

#### Exercise 3 (★★★) `final_le`
By induction on the list of events (the statement is already for every state): the final horizon is at most the
first horizon plus the sum of the durations. Use `step_bounded` in the step, then `simp only
[final, List.map_cons, List.sum_cons]` and `omega`.
-/

theorem final_le (es : List Event) : ∀ (s : State), s.Bounded →
    (final s es).horizon ≤ s.horizon + (es.map Event.duration).sum := by
  induction es with
  | nil => intro s _; simp [final]
  | cons e es ih =>
      intro s b
      have ⟨b', h'⟩ := step_bounded b e
      have := ih _ b'
      simp only [final, List.map_cons, List.sum_cons]; omega

/-- **The bound** (`Parallel.finish_le_sequential`). -/
theorem finish_le_sequential (d : Device) (code : List HardwareInstruction) :
    finish d code ≤ (code.map (instructionDuration d)).sum := by
  have := final_le (code.map (instructionEvent d)) initialState
    ⟨Nat.le_refl _, fun _ => Nat.le_refl _⟩
  simpa [finish, initialState, instructionEvent, Function.comp_def] using this

/-!
QCCDCert also proves that the reported finish **is** the latest end of a step
(`ReportedTime.finish_latest_end`), so the bound is the whole of the first equation of Time.

## 5. Example 3: a line of four traps

Example 3 ([Compilation page](../compilation/#ex3)) runs on four traps in a line that share one
transport channel with two waveforms, one per direction; each trap has its own laser channel:

```
hardware Line4; site C0[2]; site C1[2]; site C2[2]; site C3[2];
path p0:C0->C1 channel 0 waveform 0 duration 2;   // motion +x
path p1:C1->C2 channel 0 waveform 0 duration 2;   // motion +x
path p2:C2->C3 channel 0 waveform 0 duration 2;   // motion +x
path p3:C1->C0 channel 0 waveform 1 duration 2;   // motion -x
path p4:C2->C1 channel 0 waveform 1 duration 2;   // motion -x
path p5:C3->C2 channel 0 waveform 1 duration 2;   // motion -x
share channel 0 optional;
optical channel 10, 11, 12, 13;
calibration c0:R(pi/2,pi/2) @C0 channel 10 duration 2;
calibration c1:MS(pi/2) @C0 channel 10 duration 4;
calibration c2:R(-pi/2,0) @C0 channel 10 duration 2;
calibration c3:R(-pi/2,pi/2) @C0 channel 10 duration 2;
broadcast b0:R(-pi/2,0) sites C0 channel 10 duration 2 limit 2;
acquire a0 @C0 channel 10 duration 3;
pump p0 @C0 channel 10 duration 4;
// the same at C1, C2, C3: c4..c15, b1..b3, a1..a3, p1..p3
controller duration 1;
```

The rules place one ion per trap and allow one waveform per channel per event:

```
assert init: forall s:Sites. (t = 0 and delta = 0) => N-(s) = 1;
assert cap:  forall s:Sites. Ntr(s) <= cap(s);
assert wave:  forall c:Channels. W(c) <= 1;
```

and the source is three CNOTs and two measurements:

```
OPENQASM 2.0;
qreg q[4];
creg c[2];
CX q[0],q[1];
CX q[2],q[3];
CX q[0],q[3];
measure q[0] -> c[0];
measure q[3] -> c[1];
```

Stage 9 produces one sequence of 18 events, 44 ticks one after another:

```
init q0@C0,q1@C1,q2@C2,q3@C3;
pulse R(pi/2,pi/2) q0 @C0 via c0;      // t=0, d=2
transport q0:C0->C1[p0];               // t=2, d=2
pulse MS(pi/2) q0,q1 @C1 via c5;       // t=4, d=4
broadcast R(-pi/2,0) q0,q1 @C1 via b1; // t=8, d=2
pulse R(-pi/2,pi/2) q0 @C1 via c7;     // t=10, d=2
pulse R(pi/2,pi/2) q2 @C2 via c8;      // t=12, d=2
transport q2:C2->C3[p2];               // t=14, d=2
pulse MS(pi/2) q2,q3 @C3 via c13;      // t=16, d=4
broadcast R(-pi/2,0) q2,q3 @C3 via b3; // t=20, d=2
pulse R(-pi/2,pi/2) q2 @C3 via c15;    // t=22, d=2
pulse R(pi/2,pi/2) q0 @C1 via c4;      // t=24, d=2
transport q0:C1->C2[p1];               // t=26, d=2
transport q3:C3->C2[p5];               // t=28, d=2
pulse MS(pi/2) q0,q3 @C2 via c9;       // t=30, d=4
broadcast R(-pi/2,0) q0,q3 @C2 via b2; // t=34, d=2
pulse R(-pi/2,pi/2) q0 @C2 via c11;    // t=36, d=2
readout q0 -> c0 @C2 via a2;           // t=38, d=3
readout q3 -> c1 @C2 via a2;           // t=41, d=3
```
-/

def calAt (i : Nat) : List Calibration :=
  [⟨.r half half, i, 10 + i, 2⟩, ⟨.ms half, i, 10 + i, 4⟩, ⟨.r mhalf e0, i, 10 + i, 2⟩,
   ⟨.r mhalf half, i, 10 + i, 2⟩]

def line4 : Device where
  name := "Line4"
  sites := [⟨"C0", 2⟩, ⟨"C1", 2⟩, ⟨"C2", 2⟩, ⟨"C3", 2⟩]
  paths := [⟨0, [], 1, 0, 0, 2⟩, ⟨1, [], 2, 0, 0, 2⟩, ⟨2, [], 3, 0, 0, 2⟩,
            ⟨1, [], 0, 0, 1, 2⟩, ⟨2, [], 1, 0, 1, 2⟩, ⟨3, [], 2, 0, 1, 2⟩]
  shared := [(0, false)]
  calibrations := calAt 0 ++ calAt 1 ++ calAt 2 ++ calAt 3
  broadcasts := [0, 1, 2, 3].map fun i => ⟨.r mhalf e0, [i], 10 + i, 2, 2⟩
  readouts := [0, 1, 2, 3].map fun i => ⟨i, 10 + i, 3⟩
  pumps := [0, 1, 2, 3].map fun i => ⟨i, 10 + i, 4⟩
  controller := some 1

/-- The last three events of a CNOT in trap `site`: MS, the broadcast, the last rotation. -/
def cxLine (a b site : Nat) : List HardwareInstruction :=
  [.pulse (.ms half) [a, b] false (4 * site + 1),
   .broadcast site [(.r mhalf e0, a), (.r mhalf e0, b)] false [4 * site + 2, 4 * site + 2],
   .pulse (.r mhalf half) [a] false (4 * site + 3)]

def ex3 : Execution where
  initial := [0, 1, 2, 3]
  instructions :=
    [.pulse (.r half half) [0] false 0, .transport [⟨0, 0⟩]] ++ cxLine 0 1 1 ++
    [.pulse (.r half half) [2] false 8, .transport [⟨2, 2⟩]] ++ cxLine 2 3 3 ++
    [.pulse (.r half half) [0] false 4, .transport [⟨0, 1⟩], .transport [⟨3, 5⟩]] ++
      cxLine 0 3 2 ++
    [.readout 0 0 false 2, .readout 3 1 false 2]

def ex3Program : Program :=
  [⟨"init", .staticOccupancy, .all "s" .sites (.implies (.and (.eq (.atom .start) (.constant 0))
      (.eq (.atom .duration) (.constant 0)))
      (.eq (.observe (.occupancy .before) (.var "s")) (.constant 1)))⟩,
   ⟨"cap", .staticOccupancy, capRule⟩,
   ⟨"wave", .transportControl,
      .all "c" .channels (.le (.observe .channelWaves (.var "c")) (.constant 1))⟩]

def ex3Rules : RuleSet := lowerProgram line4 ex3Program

/-!
It is a legal run (lesson I2), from `q0@C0, q1@C1, q2@C2, q3@C3` at tick 0 to
`q0@C2, q1@C1, q2@C3, q3@C2` at tick 44:
-/

theorem ex3_run : Run line4 ex3Rules 4 2 [0, 1, 2, 3] 0 ex3.instructions [2, 1, 3, 2] 44 :=
  run_of_check (by decide +kernel) (by decide +kernel) (by decide +kernel)

#eval starts line4 ex3.instructions

/-!
With every resource exclusive, the work in `C0`, `C1` and in `C2`, `C3` overlaps: the pulses
on `q0` and `q2` both start at tick 0. But the transport of `q2` holds channel 0, which the
transport of `q0` holds from tick 2 to 4, so it waits until tick 4.

#### Exercise 4 (★) `ex3_exclusive`
In the exclusive schedule, the transport of `q2` (event 7) starts at tick 4, and the whole
program finishes at tick 32.
-/

theorem ex3_exclusive :
    (starts line4 ex3.instructions)[6]? = some 4 ∧ finish line4 ex3.instructions = 32 := by
  decide +kernel

/-!
## 6. Broadcast transport: a channel is played, not held

On a broadcast channel one waveform moves every ion on its paths at once: the transports of
`q0` (C0 to C1) and `q2` (C2 to C3) are the same motion, +x, waveform 0. Holding channel 0 like
a trap would forbid them to share it; QCCDCert instead records the channel's **activations**:
an activation is one waveform the channel plays from a start to an end. A transport may start
when every activation on its channel has ended, begins only after it ends, or is the same
waveform over the same interval (QCCDCert's `Shared.choose`). In the broadcast schedule a
shared channel leaves the resources and becomes a **slot**: the channel with its key, the
waveform and the duration.
-/

structure Play where
  channel : Nat
  key : Nat × Nat
  first : Nat
  last : Nat
  deriving Repr

structure SharedEvent where
  resources : List Resource
  slots : List (Nat × (Nat × Nat))
  duration : Nat
  global : Bool

def sharedEvent (d : Device) (h : HardwareInstruction) : SharedEvent :=
  let base := instructionEvent d h
  let slots := h.moves.filterMap fun m =>
    match d.paths[m.path]? with
    | some p => if isShared d p.channel then some (p.channel, (p.waveform, base.duration)) else none
    | none => none
  ⟨base.resources.filter (fun r => match r with
      | .transport c => !isShared d c
      | _ => true), slots.eraseDups, base.duration, base.global⟩

structure SharedState where
  avail : Resource → Nat
  floor : Nat
  horizon : Nat
  plays : List Play

/-- An activation leaves room for the event at `t`: it is over, or starts after the event, or
it is the same play. -/
def clear (e : SharedEvent) (t : Nat) (l : Play) : Bool :=
  e.slots.all fun slot => slot.1 != l.channel || decide (l.last ≤ t) ||
    decide (t + e.duration ≤ l.first) ||
    (slot.2 == l.key && l.first == t && l.last == t + e.duration)

def minOf (x : Nat) : List Nat → Nat
  | [] => x
  | y :: ys => min y (minOf x ys)

/-- The earliest start at or after `ready` that leaves room in every relevant activation; the
candidates are `ready` and the ends of the activations. -/
def choose (s : SharedState) (e : SharedEvent) : Nat :=
  let r := ready ⟨s.avail, s.floor, s.horizon⟩ e.resources
  let rel := s.plays.filter fun l => decide (r < l.last)
  let f := maxOf (r :: rel.map Play.last)
  let cands := (r :: rel.flatMap fun l => [l.first, l.last]).filter fun t =>
    decide (r ≤ t) && rel.all (clear e t)
  minOf f cands

def sharedStep (s : SharedState) (e : SharedEvent) : Nat × SharedState :=
  let t := if e.global then s.horizon else choose s e
  let plays := s.plays ++ e.slots.map fun slot => ⟨slot.1, slot.2, t, t + e.duration⟩
  if e.global then
    (t, ⟨s.avail, s.horizon + e.duration, s.horizon + e.duration, plays⟩)
  else
    (t, ⟨fun r => if r ∈ e.resources then t + e.duration else s.avail r, s.floor,
      max s.horizon (t + e.duration), plays⟩)

def sharedRun : SharedState → List SharedEvent → List Nat
  | _, [] => []
  | s, e :: es => (sharedStep s e).1 :: sharedRun (sharedStep s e).2 es

def sharedFinal : SharedState → List SharedEvent → Nat
  | s, [] => s.horizon
  | s, e :: es => sharedFinal (sharedStep s e).2 es

def sharedStart : SharedState := ⟨fun _ => 0, 0, 0, []⟩

#eval sharedRun sharedStart (ex3.instructions.map (sharedEvent line4))

/-!
These are the start times the compiler reported for Example 3,
`0, 2, 4, 8, 10, 0, 2, 4, 8, 10, 12, 14, 16, 18, 22, 24, 26, 29`: the two +x transports start
together at tick 2, on one play of waveform 0. The two later transports go in opposite
directions, need two waveforms, and follow one another (`q3`'s also waits for `C2`, which
`q0`'s transport holds). The result: 32 ticks instead of 44.

#### Exercise 5 (★) `ex3_one_activation`
The transports of `q0` and `q2` (events 2 and 7) both start at tick 2, and the broadcast
schedule finishes at 32, while the steps one after another take 44.
-/

theorem ex3_one_activation :
    (sharedRun sharedStart (ex3.instructions.map (sharedEvent line4)))[1]? = some 2 ∧
      (sharedRun sharedStart (ex3.instructions.map (sharedEvent line4)))[6]? = some 2 ∧
      sharedFinal sharedStart (ex3.instructions.map (sharedEvent line4)) = 32 ∧
      (ex3.instructions.map (instructionDuration line4)).sum = 44 := by
  decide +kernel

/-!
Is that safe? The transport of `q2` now runs before the gate and the return of `q0`, which
precede it in the code. A name alone is bookkeeping, so QCCDCert checks the device too:
`Uniform(H)` says that paths sharing a waveform on a shared channel have the same sequence of
hops and the same duration (here `p0`, `p1`, `p2`: one hop +x, 2 ticks), so all ions a channel
moves together make the same motion in the same time (`Broadcast.execution_same_motion`). And
it proves for the broadcast schedule what section 4 proved for the exclusive one
(`Shared.timeline_exclusive`): steps that share an exclusive resource do not overlap, and two
steps on one broadcast channel are apart in time or play the same waveform from the same start
for the same duration. Every event is then checked again, on its frame, at its new start time;
if a rule fails there, the compiler reports the sequential timing instead.

## 7. Why overlapping is still the program

Running the target code in the order of its start times must give the same result as program
order. QCCDCert proves that two hardware instructions that hold no common resource act in
either order with the same outcome branches, from the matrices of the primitives
(`Independence.hardware_comm`), and that the timeline in time order is the program order up to
swaps of such neighbours (`TimeOrder.time_order`), so it yields the result of the source
(`TimeOrder.time_order_sound`). Its consequence for bookkeeping, `TimeOrder.resource_order`:
every ion, site, channel and classical bit sees its events in program order. Here is the step
that makes it work.
-/

/-- The events that hold resource `r`, in order. -/
def trace (r : Resource) : List Event → List Event
  | [] => []
  | e :: es => if r ∈ e.resources then e :: trace r es else trace r es

/-!
#### Exercise 6 (★★★) `trace_swap`
Swapping two neighbours that share no resource changes no resource's trace. Induction on the
prefix. With an empty prefix, `by_cases` on whether `r` is held by `a`; if it is, `indep` says
`b` does not hold it; then `simp [trace, ...]`.
-/

theorem trace_swap (r : Resource) (a b : Event) (post : List Event)
    (indep : ∀ x ∈ a.resources, x ∉ b.resources) (pre : List Event) :
    trace r (pre ++ a :: b :: post) = trace r (pre ++ b :: a :: post) := by
  induction pre with
  | nil =>
      by_cases ha : r ∈ a.resources
      · have hb : r ∉ b.resources := indep r ha
        simp [trace, ha, hb]
      · by_cases hb : r ∈ b.resources <;> simp [trace, ha, hb]
  | cons e pre ih =>
      simp only [List.cons_append, trace]
      rw [ih]

/-!
## 8. Chain order

Stages 1 to 9 track which trap holds each ion. A trap also **orders** its ions, and only the
ion at the end that faces a path can leave through it. The device declares, for each path, the
end it leaves its source by and the end it enters its destination by, and for each trap the
duration of one **exchange** of two ions. The last pass replays the target code on a **chain
machine** and inserts an exchange wherever the departing ion is not at that end. The machine
accepts three operations, each only under its condition (`Chain.check`):

* `exchange(s, a, b)`, if `a ≠ b` are both in the chain of `s`: swap them;
* `depart(s, end, q)`, if `q` is at that end of `s`'s chain: take it out, into transit;
* `arrive(s, end, q)`, if `q` is in transit and `s` has room: attach it at that end.
-/

inductive End where
  | left | right
  deriving DecidableEq, Repr

structure Ports where
  enter : List End
  leave : List End
  exchangeDuration : List Nat

inductive Step where
  | instr (h : HardwareInstruction)
  | exch (site a b : Nat)
  deriving Repr

structure ChainState where
  chains : List (List Nat)
  transit : List Nat
  deriving DecidableEq, Repr

def endOf : End → List Nat → Option Nat
  | .left, l => l.head?
  | .right, l => l.getLast?

def swapIn (a b : Nat) (l : List Nat) : List Nat :=
  l.map fun x => if x == a then b else if x == b then a else x

def setAt (s : Nat) (l : List Nat) : List (List Nat) → List (List Nat)
  | [] => []
  | c :: cs => match s with
    | 0 => l :: cs
    | n + 1 => c :: setAt n l cs

def depart (s : Nat) (side : End) (q : Nat) (χ : ChainState) : Option ChainState :=
  let c := (χ.chains[s]?).getD []
  if endOf side c == some q then some ⟨setAt s (c.erase q) χ.chains, q :: χ.transit⟩ else none

def arrive (cap : List Nat) (s : Nat) (side : End) (q : Nat) (χ : ChainState) :
    Option ChainState :=
  let c := (χ.chains[s]?).getD []
  if χ.transit.contains q && decide (c.length < (cap[s]?).getD 0) then
    some ⟨setAt s (match side with | .left => q :: c | .right => c ++ [q]) χ.chains,
      χ.transit.erase q⟩
  else none

def exchange (s a b : Nat) (χ : ChainState) : Option ChainState :=
  let c := (χ.chains[s]?).getD []
  if a != b && c.contains a && c.contains b then some ⟨setAt s (swapIn a b c) χ.chains, χ.transit⟩
  else none

def moveOps (d : Device) (ports : Ports) (cap : List Nat) (m : Move) (χ : ChainState) :
    Option ChainState :=
  match d.paths[m.path]?, ports.leave[m.path]?, ports.enter[m.path]? with
  | some p, some lv, some en =>
      if p.src == p.dst then none else (depart p.src lv m.ion χ).bind (arrive cap p.dst en m.ion)
  | _, _, _ => none

def stepOps (d : Device) (ports : Ports) (cap : List Nat) : Step → ChainState → Option ChainState
  | .exch s a b, χ => exchange s a b χ
  | .instr (.transport moves), χ =>
      moves.foldl (fun acc m => acc.bind (moveOps d ports cap m)) (some χ)
  | .instr _, χ => some χ

def Chain.check (d : Device) (ports : Ports) (cap : List Nat) :
    ChainState → List Step → Option ChainState
  | χ, [] => some χ
  | χ, e :: es => (stepOps d ports cap e χ).bind fun χ' => Chain.check d ports cap χ' es

def Chain.erase : List Step → List HardwareInstruction
  | [] => []
  | .instr h :: rest => h :: Chain.erase rest
  | .exch .. :: rest => Chain.erase rest

/-!
On `Line4`, the +x paths leave by the right end and enter by the left; the -x paths the other
way; an exchange takes 12 ticks. `q0` enters `C1` on the left, so `C1` holds `q0, q1`; to leave
`C1` to the right `q0` must first exchange with `q1`. Likewise `q2` enters `C3` on the left, and
`q3` must exchange with it before it leaves to the left. The compiler's timeline has these two
exchanges:

```
pulse R(pi/2,pi/2) q0 @C1 via c4;      // t=12, d=2
exchange q0,q1 @C1;                    // t=14, d=12
transport q0:C1->C2[p1];               // t=26, d=2
exchange q3,q2 @C3;                    // t=12, d=12
transport q3:C3->C2[p5];               // t=28, d=2
```
-/

def line4Ports : Ports :=
  ⟨[.left, .left, .left, .right, .right, .right], [.right, .right, .right, .left, .left, .left],
   [12, 12, 12, 12]⟩

def ex3Steps : List Step :=
  (ex3.instructions.take 11).map .instr ++
  [.exch 1 0 1, .instr (.transport [⟨0, 1⟩]), .exch 3 3 2, .instr (.transport [⟨3, 5⟩])] ++
  (ex3.instructions.drop 13).map .instr

def chains0 : ChainState := ⟨[[0], [1], [2], [3]], []⟩

/-!
#### Exercise 7 (★) `ex3_needs_exchanges`
Without the exchanges, the chain machine rejects the target code.
-/

theorem ex3_needs_exchanges :
    Chain.check line4 line4Ports [2, 2, 2, 2] chains0 (ex3.instructions.map .instr) = none := by
  decide +kernel

/-!
#### Exercise 8 (★) `ex3_chains`
With them, it accepts, and ends with `C1` holding `q1`, `C2` holding `q0, q3` and `C3`
holding `q2`: exactly the ions the target code places there (`Chain.check_agrees`: an accepted
replay keeps in every chain the ions of the final placement).
-/

theorem ex3_chains :
    Chain.check line4 line4Ports [2, 2, 2, 2] chains0 ex3Steps = some ⟨[[], [1], [0, 3], [2]], []⟩ := by
  decide +kernel

/-!
An exchange reorders ions inside a trap and applies no instruction, so erasing the exchanges
gives the target code back: that is `erase(e₁..eₘ) = K`, the first equation of Meaning.

#### Exercise 9 (★) `ex3_erase`
-/

theorem ex3_erase : Chain.erase ex3Steps = ex3.instructions := by
  decide +kernel

/-!
An exchange holds its two ions and its trap for its duration. Scheduled with the rest, the two
exchanges overlap in time in different traps, and the timeline finishes at tick 44, against 68
one after another:
-/

def stepShared (d : Device) (ports : Ports) : Step → SharedEvent
  | .instr h => sharedEvent d h
  | .exch s a b => ⟨[.ion a, .ion b, .site s], [], (ports.exchangeDuration[s]?).getD 0, false⟩

#eval sharedRun sharedStart (ex3Steps.map (stepShared line4 line4Ports))

/-!
#### Exercise 10 (★) `ex3_timeline`
The compiler's chain timeline for Example 3: the exchange in `C1` starts at 14, the one in `C3`
at 12, the timeline finishes at 44, and the 20 steps one after another take 68.
-/

theorem ex3_timeline :
    (sharedRun sharedStart (ex3Steps.map (stepShared line4 line4Ports))) =
        [0, 2, 4, 8, 10, 0, 2, 4, 8, 10, 12, 14, 26, 12, 28, 30, 34, 36, 38, 41] ∧
      sharedFinal sharedStart (ex3Steps.map (stepShared line4 line4Ports)) = 44 ∧
      ((ex3Steps.map (stepShared line4 line4Ports)).map SharedEvent.duration).sum = 68 := by
  decide +kernel

/-!
## 9. Time, in one sentence

Every event is charged the duration the device declares for it; the reported finish is the
latest end of a step, never more than the sum of the durations (Example 3: 32 ticks without
chain order, 44 with it, against 44 and 68 one after another); steps that share a resource never
overlap; a broadcast channel plays one waveform at a time; only the ion at a trap's end leaves
it. Lesson I5 puts this together with Meaning and Legality into `verified_compilation`.
-/
