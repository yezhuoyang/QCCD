import QCCDC.Cert.Sound

/-!
# The whole theorem, and what an extension must keep

*Part I, lesson I5. You need lessons I1 to I4 (restated, unseen, at the top of this page).*

This lesson puts the three clauses together into QCCDCert's theorem
([Compilation page](../compilation/#proof)):

```
compile(H, R, P, κ) ⇓ (K, T)   ⟹   Meaning ∧ Legality ∧ Time
```

In QCCDCert it is one Lean declaration, `verified_compilation`. Its hypotheses say only what
three procedures returned, and none says that the result is correct:

```
theorem verified_compilation {device : Device} {assertions : Assertions.Program}
    {rules : RuleSet} (elaborated : Assertions.elaborate device assertions = .ok rules)
    ... (_returned : compileQASM device rules bundle root config = .ok compiled)
    ... (reported : ReportedFor compiled ports capacity chains broadcast) (wiring : Broadcast.Wiring) :
    Meaning compiled broadcast reported ∧
      Legality assertions wiring compiled broadcast reported ∧
      Time compiled broadcast reported
```

The theorem adds no proof of its own: every component is a theorem of the passes, the ones
lessons I2 to I4 restated. Here you build a miniature compiler from those passes and prove the
same statement for it, then see what is proved once, what is decided in each compilation, what
is assumed, and what a compiler that extends QCCDCert must do to keep the theorem.
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
def initRule : Formula :=
  .implies (.and (.eq (.atom .start) (.constant 0)) (.eq (.atom .duration) (.constant 0)))
    (.eq (.observe (.occupancy .before) (.id 0)) (.constant 1))
def capRule : Formula :=
  .all "s" .sites (.le (.observe (.occupancy .during) (.var "s")) (.observe .capacity (.var "s")))
def relRule : Formula :=
  .implies (.le (.constant 1) (.atom .pulseCount)) (.le (.constant 5) (.atom .start))
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
theorem elaborate_correct {d : Device} {program : Program} {rules : RuleSet}
    (success : elaborate d program = .ok rules) (F : Frame) :
    RulesHold rules F ↔ ProgramHolds d program F := by
  unfold elaborate at success
  split at success
  · contradiction
  · cases success
    exact lowerProgram_correct _ _ _
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
def ex1Rules : RuleSet := lowerProgram primer ex1Program
theorem ex1_elaborates : elaborate primer ex1Program = .ok ex1Rules := rfl
def ex2Program : Program :=
  [⟨"init", .staticOccupancy, .implies (.and (.eq (.atom .start) (.constant 0))
      (.eq (.atom .duration) (.constant 0)))
      (.eq (.observe (.occupancy .before) (.id 0)) (.constant 2))⟩,
   ⟨"cap", .staticOccupancy, capRule⟩,
   ⟨"traffic", .transportControl, .all "p" .paths (.le (.observe .pathUse (.var "p")) (.constant 1))⟩]
def oneRule : Declaration := ⟨"one", .operationTiming, .le (.atom .pulseCount) (.constant 1)⟩
def ex2RulesB : RuleSet := lowerProgram tee (ex2Program ++ [oneRule])
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
inductive EffectInstruction where
  | u (theta phi lam : Expr) (q : Nat) (guarded : Bool)
  | cx (q q' : Nat) (guarded : Bool)
  | measure (q c : Nat) (guarded : Bool)
  | reset (q : Nat) (guarded : Bool)
  | latch (bits : List Nat) (value : Nat)
  | barrier (qs : List Nat) (guarded : Bool)
  deriving DecidableEq, Repr
def ex1Effects : List EffectInstruction := [.cx 0 1 false, .measure 1 0 false]
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
def eligible (d : Device) : PulseInstruction → List Nat
  | .pulse w _ _ => (List.range d.calibrations.length).filter fun k =>
      ((d.calibrations[k]?).map Calibration.wave) == some w
  | .measure .. => List.range d.readouts.length
  | .reset .. => List.range d.pumps.length
  | _ => []
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
theorem routes_project {d : Device} {qubits bits : Nat} {pos : Placement}
    {is : List PulseInstruction} {A : List HardwareInstruction}
    (routes : Routes d qubits bits pos is A) : hardwareProject A = is := by
  induction routes with
  | done => rfl
  | move moves _ _ ih => simpa [hardwareProject, HardwareInstruction.project] using ih
  | emit h real _ _ ih =>
      simp only [hardwareProject, List.flatMap_cons] at ih ⊢
      rw [real.2, ih]; rfl
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
theorem quantum_refines {σ : Type} (act : PulseInstruction → σ → σ)
    (code : List HardwareInstruction) (s : σ) :
    interpretHardwareCode act code s = interpretPulseCode act (hardwareProject code) s := by
  induction code generalizing s with
  | nil => rfl
  | cons h rest ih =>
      simp only [interpretHardwareCode, hardwareProject, List.flatMap_cons] at ih ⊢
      rw [ih, interpretPulseCode_append]
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
theorem meaning_chain {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement}
    {effects : List EffectInstruction} {A A' K : List HardwareInstruction}
    (routes : Routes d qubits bits pos (selectProgram effects) A)
    (packs : Packs d qubits bits pos A A') (times : Times d rules qubits bits pos 0 A' K) :
    hardwareProject K = selectProgram effects := by
  rw [times_project times, packs_project packs, routes_project routes]
theorem same_result {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos : Placement}
    {effects : List EffectInstruction} {A A' K : List HardwareInstruction}
    (routes : Routes d qubits bits pos (selectProgram effects) A)
    (packs : Packs d qubits bits pos A A') (times : Times d rules qubits bits pos 0 A' K)
    {σ : Type} (act : PulseInstruction → σ → σ) (s : σ) :
    interpretHardwareCode act K s = interpretPulseCode act (selectProgram effects) s := by
  rw [quantum_refines, meaning_chain routes packs times]
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
theorem timeline_exclusive (d : Device) (code : List HardwareInstruction) :
    (timeline d code).Pairwise fun x y => Conflict x.1 y.1 → x.2 + x.1.duration ≤ y.2 :=
  run_exclusive _ _ ⟨Nat.le_refl _, fun _ => Nat.le_refl _⟩
theorem final_le (es : List Event) : ∀ (s : State), s.Bounded →
    (final s es).horizon ≤ s.horizon + (es.map Event.duration).sum := by
  induction es with
  | nil => intro s _; simp [final]
  | cons e es ih =>
      intro s b
      have ⟨b', h'⟩ := step_bounded b e
      have := ih _ b'
      simp only [final, List.map_cons, List.sum_cons]; omega
theorem finish_le_sequential (d : Device) (code : List HardwareInstruction) :
    finish d code ≤ (code.map (instructionDuration d)).sum := by
  have := final_le (code.map (instructionEvent d)) initialState
    ⟨Nat.le_refl _, fun _ => Nat.le_refl _⟩
  simpa [finish, initialState, instructionEvent, Function.comp_def] using this

/-!
## 1. The clauses, in full

* **Meaning.** `erase(e₁..eₘ) = K`: the steps are the target code with exchanges inserted
  (lesson I4). `proj(K) = native(P)`: deleting transports and waits and unpacking broadcasts
  gives the native program of stages 1-4; nothing is dropped, merged or reordered (lesson I3).
  For every angle assignment and input state, running `K` in the order of the start times of
  `T` yields the observable result of the source.
* **Legality.** Every frame `Fᵢ` passes the core checks and every rule of `R` as the designer
  wrote it, at its reported start time (lessons I1, I2); the chain machine accepts the steps and
  ends in chains that agree with the final placement; steps that share a resource do not
  overlap; two steps on one broadcast channel are apart in time or form one activation; and if
  the device's wiring is uniform, the moves of one transport on a broadcast channel make one
  motion (lesson I4).
* **Time.** `t_end = maxᵢ(tᵢ + δᵢ) ≤ Σᵢ δᵢ`, and every pulse is charged the duration of a
  declared calibration at the trap that holds its ions (lesson I4).

## 2. A compiler made of checked stages

QCCDCert's stages 7 to 9 search. Each returns its output together with a record of the facts
the proof needs (`Routed`, `Packed`, `Timed`), and QCCDCert proves that an output has a
derivation of the stage's judgment exactly when it has the record (`Rules.routes_iff`,
`Rules.packs_iff`, `Rules.times_iff`). So how a search chooses has no effect on the proof, and a
proposal from outside (an external router, a SAT seed) is admitted only if it passes the same
check.

The miniature makes that literal. Its "search configuration" `κ` simply proposes the routed,
packed and timed programs and the initial placement; the compiler decides the judgments of
lesson I3 on them, with deciders proved sound. The decider for routing:
-/

def realB (h : HardwareInstruction) (i : PulseInstruction) : Bool :=
  (h matches .pulse .. || h matches .readout .. || h matches .pump .. || h matches .latch .. ||
    h matches .barrier ..) && h.project == [i]

def routesB (d : Device) (qubits bits : Nat) :
    Placement → List PulseInstruction → List HardwareInstruction → Bool
  | _, [], [] => true
  | pos, is, .transport moves :: A =>
      coreLegal d qubits bits pos (.transport moves) &&
        routesB d qubits bits (nextPlacement d pos (.transport moves)) is A
  | pos, i :: is, h :: A => realB h i && coreLegal d qubits bits pos h && routesB d qubits bits pos is A
  | _, _, _ => false

/-!
#### Exercise 1 (★★★) `routesB_sound`
Whatever the decider accepts has a derivation (R-Done, R-Move, R-Emit). Induction on the routed
program. For a transport, split the `&&` and use `Routes.move`. For any other instruction, `cases`
on the native program; in the `cons` case, split the `&&` (with `beq_iff_eq`), and the kind half
of `Real` follows by `simp_all` from the first conjunct.
-/

theorem routesB_sound {d : Device} {qubits bits : Nat} :
    ∀ {A : List HardwareInstruction} {pos : Placement} {is : List PulseInstruction},
      routesB d qubits bits pos is A = true → Routes d qubits bits pos is A := by
  intro A
  induction A with
  | nil =>
      intro pos is ok
      cases is with
      | nil => exact .done pos
      | cons i is => simp [routesB] at ok
  | cons h A ih =>
      intro pos is ok
      cases h with
      | transport moves =>
          simp only [routesB, Bool.and_eq_true] at ok
          exact .move moves ok.1 (ih ok.2)
      | _ =>
          cases is with
          | nil => simp [routesB] at ok
          | cons i is =>
              simp only [routesB, realB, Bool.and_eq_true, beq_iff_eq] at ok
              have kind := ok.1.1.1
              exact .emit _ ⟨by simp_all, ok.1.1.2⟩ ok.1.2 (ih ok.2)

/-!
The deciders for broadcast formation and time assignment have the same shape. A broadcast in
the packed program must be preceded, in the routed one, by exactly its member pulses; an `idle`
in the timed program is a T-Wait, any other instruction a T-Emit of the next packed instruction.
-/

def packsB (d : Device) (qubits bits : Nat) :
    Placement → List HardwareInstruction → List HardwareInstruction → Bool
  | _, [], [] => true
  | pos, A, .broadcast b members g ks :: A' =>
      coreLegal d qubits bits pos (.broadcast b members g ks) &&
        A.take (membersAsPulses g members ks).length == membersAsPulses g members ks &&
        packsB d qubits bits pos (A.drop (membersAsPulses g members ks).length) A'
  | pos, h :: A, h' :: A' =>
      h'.plain && h == h' && coreLegal d qubits bits pos h &&
        packsB d qubits bits (nextPlacement d pos h) A A'
  | _, _, _ => false

theorem packsB_sound {d : Device} {qubits bits : Nat} :
    ∀ {A' : List HardwareInstruction} {pos : Placement} {A : List HardwareInstruction},
      packsB d qubits bits pos A A' = true → Packs d qubits bits pos A A' := by
  intro A'
  induction A' with
  | nil =>
      intro pos A ok
      cases A with
      | nil => exact .done pos
      | cons h A => simp [packsB] at ok
  | cons h' A' ih =>
      intro pos A ok
      cases h' with
      | broadcast b members g ks =>
          simp only [packsB, Bool.and_eq_true, beq_iff_eq] at ok
          have split := List.take_append_drop (membersAsPulses g members ks).length A
          rw [ok.1.2] at split
          rw [← split]
          exact .join b members g ks ok.1.1 (ih ok.2)
      | _ =>
          cases A with
          | nil => simp [packsB] at ok
          | cons h A =>
              simp only [packsB, Bool.and_eq_true, beq_iff_eq] at ok
              obtain ⟨⟨⟨plain, rfl⟩, legal⟩, rest⟩ := ok
              exact .keep _ plain legal (ih rest)

def timesB (d : Device) (rules : RuleSet) (qubits bits : Nat) :
    Placement → Nat → List HardwareInstruction → List HardwareInstruction → Bool
  | _, _, [], [] => true
  | pos, t, A, .idle n :: K =>
      legal d rules qubits bits pos t (.idle n) && timesB d rules qubits bits pos (t + n) A K
  | pos, t, h :: A, h' :: K =>
      h == h' && legal d rules qubits bits pos t h &&
        timesB d rules qubits bits (nextPlacement d pos h) (t + instructionDuration d h) A K
  | _, _, _, _ => false

theorem timesB_sound {d : Device} {rules : RuleSet} {qubits bits : Nat} :
    ∀ {K : List HardwareInstruction} {pos : Placement} {t : Nat} {A : List HardwareInstruction},
      timesB d rules qubits bits pos t A K = true → Times d rules qubits bits pos t A K := by
  intro K
  induction K with
  | nil =>
      intro pos t A ok
      cases A with
      | nil => exact .done pos t
      | cons h A => simp [timesB] at ok
  | cons h' K ih =>
      intro pos t A ok
      cases h' with
      | idle n =>
          simp only [timesB, Bool.and_eq_true] at ok
          exact .wait n (legal_correct.mp ok.1) (ih ok.2)
      | _ =>
          cases A with
          | nil => simp [timesB] at ok
          | cons h A =>
              simp only [timesB, Bool.and_eq_true, beq_iff_eq] at ok
              obtain ⟨⟨rfl, lg⟩, rest⟩ := ok
              exact .emit _ rfl (legal_correct.mp lg) (ih rest)

/-!
Binding (stage 5) needs a control for every native instruction; placement (stage 6) needs
`InitialLegal`. After stage 9 the compiler reports a timeline: it computes the parallel start
times (lesson I4), **rechecks every frame at its new start time** (`atStarts`), and reports the
parallel timing if that holds and the sequential one otherwise. (The miniature reports the
exclusive schedule and no chain order; QCCDCert also inserts the exchanges, replays them on the
chain machine and can schedule broadcast channels by activation.)
-/

def bindable (d : Device) : PulseInstruction → Bool
  | .latch .. => d.controller.isSome
  | .barrier .. => true
  | i => !(eligible d i).isEmpty

structure Search where
  initial : Placement
  routed : List HardwareInstruction
  packed : List HardwareInstruction
  timed : List HardwareInstruction

structure Compiled where
  rules : RuleSet
  native : List PulseInstruction
  execution : Execution
  parallel : Bool
  starts : List Nat
  finish : Nat

def sequentialStarts (d : Device) : Nat → List HardwareInstruction → List Nat
  | _, [] => []
  | t, h :: rest => t :: sequentialStarts d (t + instructionDuration d h) rest

/-- Every instruction is `Legal` at its reported start, where the ones before it left the ions. -/
def atStarts (d : Device) (rules : RuleSet) (qubits bits : Nat) :
    Placement → List HardwareInstruction → List Nat → Bool
  | _, [], [] => true
  | pos, h :: rest, t :: ts => legal d rules qubits bits pos t h &&
      atStarts d rules qubits bits (nextPlacement d pos h) rest ts
  | _, _, _ => false

def checks (d : Device) (rules : RuleSet) (effects : List EffectInstruction) (qubits bits : Nat)
    (κ : Search) : Bool :=
  (selectProgram effects).all (bindable d) &&
  placementValid d qubits κ.initial && rulesOK rules (initialFrame d κ.initial) &&
  routesB d qubits bits κ.initial (selectProgram effects) κ.routed &&
  packsB d qubits bits κ.initial κ.routed κ.packed &&
  timesB d rules qubits bits κ.initial 0 κ.packed κ.timed

def report (d : Device) (rules : RuleSet) (effects : List EffectInstruction) (qubits bits : Nat)
    (κ : Search) : Compiled :=
  let par := atStarts d rules qubits bits κ.initial κ.timed (starts d κ.timed)
  ⟨rules, selectProgram effects, ⟨κ.initial, κ.timed⟩, par,
    if par then starts d κ.timed else sequentialStarts d 0 κ.timed,
    if par then finish d κ.timed else finalTime d 0 κ.timed⟩

/-- The compiler: stage 4 is a function; stages 5 to 9 are decided on what the search proposes. -/
def compile (d : Device) (rules : RuleSet) (effects : List EffectInstruction) (qubits bits : Nat)
    (κ : Search) : Except String Compiled :=
  if checks d rules effects qubits bits κ then .ok (report d rules effects qubits bits κ)
  else .error "no compilation for this search"

/-!
## 3. The three clauses, in Lean

The miniature's clauses keep QCCDCert's shape, minus what the miniature does not model (the
source semantics before stage 4, chain order and broadcast activations, which lesson I4
computed on Example 3):
-/

def Meaning (effects : List EffectInstruction) (c : Compiled) : Prop :=
  c.native = selectProgram effects ∧
  hardwareProject c.execution.instructions = c.native ∧
  ∀ {σ : Type} (act : PulseInstruction → σ → σ) (s : σ),
    interpretHardwareCode act c.execution.instructions s = interpretPulseCode act c.native s

def Legality (d : Device) (program : Program) (qubits bits : Nat) (c : Compiled) : Prop :=
  InitialLegal d c.rules qubits c.execution.initial ∧
  Run d c.rules qubits bits c.execution.initial 0 c.execution.instructions
    (finalPlacement d c.execution.initial c.execution.instructions)
    (finalTime d 0 c.execution.instructions) ∧
  atStarts d c.rules qubits bits c.execution.initial c.execution.instructions c.starts = true ∧
  (∀ F : Frame, RulesHold c.rules F ↔ ProgramHolds d program F) ∧
  (c.parallel = true → c.starts = starts d c.execution.instructions ∧
    (timeline d c.execution.instructions).Pairwise
      fun x y => Conflict x.1 y.1 → x.2 + x.1.duration ≤ y.2)

def Time (d : Device) (c : Compiled) : Prop :=
  c.finish ≤ (c.execution.instructions.map (instructionDuration d)).sum ∧
  ∀ (front : List HardwareInstruction) (w : Wave) (ions : List Nat) (g : Bool) (k : Nat)
    (back : List HardwareInstruction),
    c.execution.instructions = front ++ .pulse w ions g k :: back →
    ∃ cal : Calibration, d.calibrations[k]? = some cal ∧
      instructionDuration d (.pulse w ions g k) = cal.duration ∧
      ∀ q ∈ ions, (finalPlacement d c.execution.initial front)[q]? = some cal.site

/-!
Four small lemmas. The sequential finish is the sum of the durations:

#### Exercise 2 (★★) `finalTime_eq_sum`
Induction on the code; the statement is already for every start time.
-/

theorem finalTime_eq_sum (d : Device) (code : List HardwareInstruction) :
    ∀ t, finalTime d t code = t + (code.map (instructionDuration d)).sum := by
  induction code with
  | nil => intro t; simp [finalTime]
  | cons h rest ih => intro t; simp only [finalTime, ih, List.map_cons, List.sum_cons]; omega

/-!
A legal run is legal at its sequential start times:

#### Exercise 3 (★★) `run_atStarts`
Induction on the run; in the step, split the `&&` and use `legal_correct.mpr`.
-/

theorem run_atStarts {d : Device} {rules : RuleSet} {qubits bits : Nat} {pos final : Placement}
    {t finish : Nat} {code : List HardwareInstruction}
    (run : Run d rules qubits bits pos t code final finish) :
    atStarts d rules qubits bits pos code (sequentialStarts d t code) = true := by
  induction run with
  | nil => rfl
  | cons head _ ih =>
      simp only [atStarts, sequentialStarts, Bool.and_eq_true]
      exact ⟨legal_correct.mpr head, ih⟩

/-!
Every instruction of a run is legal where the instructions before it left the ions, and a
core-legal pulse is charged its calibration at the site that holds its ions:
-/

theorem run_split {d : Device} {rules : RuleSet} {qubits bits : Nat} :
    ∀ {front : List HardwareInstruction} {pos final : Placement} {t finish : Nat}
      {h : HardwareInstruction} {back : List HardwareInstruction},
      Run d rules qubits bits pos t (front ++ h :: back) final finish →
      ∃ t', Legal d rules qubits bits (finalPlacement d pos front) t' h := by
  intro front
  induction front with
  | nil => intro pos final t finish h back run; cases run with | cons head _ => exact ⟨_, head⟩
  | cons x rest ih =>
      intro pos final t finish h back run
      cases run with | cons _ tail => exact ih tail

theorem pulse_charged {d : Device} {qubits bits : Nat} {pos : Placement} {w : Wave}
    {ions : List Nat} {g : Bool} {k : Nat} (legal : CoreLegal d qubits bits pos (.pulse w ions g k)) :
    ∃ cal : Calibration, d.calibrations[k]? = some cal ∧
      instructionDuration d (.pulse w ions g k) = cal.duration ∧
      ∀ q ∈ ions, pos[q]? = some cal.site := by
  simp only [CoreLegal, coreLegal, activeCoreLegal, pulseLegal, Bool.and_eq_true] at legal
  obtain ⟨⟨_, hm⟩, _⟩ := legal
  split at hm
  · rename_i cal hk
    simp only [Bool.and_eq_true, List.all_eq_true, beq_iff_eq] at hm
    exact ⟨cal, hk, by simp [instructionDuration, hk], hm.2⟩
  · simp at hm

/-!
## 4. The theorem

For every device, rule program, effect program and search proposal: if elaboration returns the
rules and the compiler returns a result, the result has the meaning of the source, it is legal,
and its reported time is the time of the declared model. The proof only assembles: the deciders'
soundness gives the three derivations of lesson I3; `meaning_chain` and `same_result` give
Meaning; `times_run`, `elaborate_correct` and the timeline's recheck give Legality, with
`timeline_exclusive` (lesson I4) for exclusion; `finish_le_sequential` and `pulse_charged` give
Time.
-/

/-- **Verified compilation** (a miniature of QCCDCert's `verified_compilation`). -/
theorem verified_compilation {d : Device} {program : Program} {rules : RuleSet}
    (elaborated : elaborate d program = .ok rules) {effects : List EffectInstruction}
    {qubits bits : Nat} {κ : Search} {c : Compiled}
    (returned : compile d rules effects qubits bits κ = .ok c) :
    Meaning effects c ∧ Legality d program qubits bits c ∧ Time d c := by
  unfold compile at returned
  split at returned
  · rename_i ok
    cases returned
    simp only [checks, Bool.and_eq_true] at ok
    obtain ⟨⟨⟨⟨⟨_, valid⟩, initial⟩, routed⟩, packed⟩, timed⟩ := ok
    have routes := routesB_sound routed
    have packs := packsB_sound packed
    have times := timesB_sound timed
    have run := times_run times
    refine ⟨⟨rfl, meaning_chain routes packs times, fun act s => same_result routes packs times act s⟩,
      ⟨⟨valid, (rulesOK_correct _ _).mp initial⟩, run, ?_, elaborate_correct elaborated, ?_⟩, ?_, ?_⟩
    · simp only [report]
      split
      · assumption
      · exact run_atStarts run
    · simp only [report]
      intro par
      refine ⟨?_, timeline_exclusive d κ.timed⟩
      simp [par]
    · simp only [report]
      split
      · exact finish_le_sequential d κ.timed
      · rw [finalTime_eq_sum]; omega
    · intro front w ions g k back eq
      simp only [report] at eq
      obtain ⟨t', legal⟩ := run_split (eq ▸ run)
      exact pulse_charged legal.1
  · cases returned

/-!
## 5. On the paper's examples

Example 1, with the programs of lesson I3 as the search's proposals:
-/

def κ1 : Search := ⟨[0, 2], ex1Routed, ex1Packed, ex1.instructions⟩

/-!
#### Exercise 4 (★) `ex1_compiles`
Every stage accepts.
-/

theorem ex1_compiles : checks primer ex1Rules ex1Effects 2 1 κ1 = true := by
  decide +kernel

#eval (report primer ex1Rules ex1Effects 2 1 κ1).starts
#eval (report primer ex1Rules ex1Effects 2 1 κ1).finish

/-!
#### Exercise 5 (★★) `ex1_guarantees`
So the compiler returns a result, and the theorem gives its three clauses. First show what
`compile` returns (`unfold compile`, then `rw [if_pos ex1_compiles]`), then apply
`verified_compilation` with `ex1_elaborates` (lesson I2).
-/

theorem ex1_guarantees :
    ∃ c, compile primer ex1Rules ex1Effects 2 1 κ1 = .ok c ∧
      Meaning ex1Effects c ∧ Legality primer ex1Program 2 1 c ∧ Time primer c := by
  have h : compile primer ex1Rules ex1Effects 2 1 κ1 = .ok (report primer ex1Rules ex1Effects 2 1 κ1) := by
    unfold compile; rw [if_pos ex1_compiles]
  exact ⟨_, h, verified_compilation ex1_elaborates h⟩

/-!
A search that proposes something wrong gets no result, and so no theorem is needed about it.
Dropping the three waits breaks the rule `rel` (lesson I2):

#### Exercise 6 (★) `no_waits_rejected`
-/

theorem no_waits_rejected :
    checks primer ex1Rules ex1Effects 2 1 ⟨[0, 2], ex1Routed, ex1Packed, ex1Packed⟩ = false := by
  decide +kernel

/-!
Swapping the two `R(-π/2,0)` pulses of the CNOT gives a routed program that is still legal,
event by event, and even computes the same thing (the two pulses act on different ions). Routing
rejects it all the same: the compiler never reorders the native program, so `proj(K)` is
`native(P)` exactly, not up to a commutation.

#### Exercise 7 (★) `reordered_rejected`
-/

def ex1Swapped : List HardwareInstruction :=
  [.transport [⟨0, 0⟩],
   .pulse (.r half half) [0] false 3, .pulse (.ms half) [0, 1] false 4,
   .pulse (.r mhalf e0) [1] false 5, .pulse (.r mhalf e0) [0] false 5,
   .pulse (.r mhalf half) [0] false 6, .readout 1 0 false 0]

theorem reordered_rejected :
    coreRun primer 2 1 [0, 2] ex1Swapped = true ∧
      routesB primer 2 1 [0, 2] (selectProgram ex1Effects) ex1Swapped = false := by
  decide +kernel

/-!
Example 2 under rule program B (lesson I2): a different device, different rules, guarded code,
and the same theorem. The search proposes the code the compiler returned: no broadcast (rule
`one` forbids it), and no waits.
-/

def ex2Effects : List EffectInstruction :=
  [.cx 0 2 false, .cx 1 2 false, .measure 2 0 false, .latch [0] 1, .reset 2 true]

def ex2ProgramB : Program := ex2Program ++ [oneRule]

def κ2B : Search := ⟨[0, 0, 2], ex2B.instructions, ex2B.instructions, ex2B.instructions⟩

theorem ex2B_elaborates : elaborate tee ex2ProgramB = .ok ex2RulesB := rfl

/-!
#### Exercise 8 (★★) `ex2B_guarantees`
Nothing in the compiler or the theorem names the rule `one`: the proof of Exercise 5 works
again.
-/

theorem ex2B_guarantees :
    ∃ c, compile tee ex2RulesB ex2Effects 3 1 κ2B = .ok c ∧
      Meaning ex2Effects c ∧ Legality tee ex2ProgramB 3 1 c ∧ Time tee c := by
  have ok : checks tee ex2RulesB ex2Effects 3 1 κ2B = true := by decide +kernel
  have h : compile tee ex2RulesB ex2Effects 3 1 κ2B = .ok (report tee ex2RulesB ex2Effects 3 1 κ2B) := by
    unfold compile; rw [if_pos ok]
  exact ⟨_, h, verified_compilation ex2B_elaborates h⟩

/-!
## 6. What is proved, what is decided, what is assumed

| | |
|---|---|
| **Proved once**, by Lean's kernel | stages 1 to 9 for every program, device and rule set; rule elaboration; the parallel schedule; that events on disjoint resources commute; what the chain machine accepts |
| **Decided in each compilation**, by a proved procedure inside the compiler | the legality of every transport, including proposed ones; the rules on every frame; the rules again at the parallel start times; the chain replay |
| **Assumed** | the device performs each declared event in its declared time (the primitive-realization assumption); Lean's compiler and runtime |

No compilation needs a simulation or a proof certificate, and the listings the compiler prints
are displays, outside the theorem. QCCDCert's proofs use no axiom beyond Lean's three standard
ones (`propext`, `Classical.choice`, `Quot.sound`), the same three the grader of these
exercises allows. The theorem is about successful compilations: the compiler may fail (a
control is missing, a search bound is reached), and a failure returns no target code.

## 7. Extending the compiler

The compiler of this platform extends QCCDCert. An extension may add features; it keeps the
hardware syntax, the assertion syntax, the passes and the theorem, and every addition comes with
what keeps `verified_compilation` true:

* **A new rule** needs nothing. Rules are inputs, `H` and `R` are ordinary arguments of the
  theorem, and `elaborate_correct` holds for every formula. A new rule can make a compilation
  fail; it cannot make a returned one wrong (Exercise 8).
* **A new observation** a rule can read (a new count on the frame) is a new constructor of
  `NatExpr` with its case of `evalNat`; `evalRule_correct` and `Formula.lower_correct` go through
  by the same induction, with one more case.
* **A new kind of hardware declaration or instruction** needs everything an instruction has
  here: its effect on placement and clock (`nextPlacement`, `instructionDuration`), its frame
  observations, its core check (a case of `ActiveCoreLegal`), its projection to native
  instructions, its resources, and the two semantic lemmas: that a core-legal instance acts as
  its projection (`hardwareInstruction_refines`), and that it commutes with events on disjoint
  resources (`Independence.hardware_comm`).
* **A better search** (a new router, placer, packer, an external solver) needs no new proof if
  it outputs derivations of the existing judgments, `Rules.Routes`, `Rules.Packs`,
  `Rules.Times`: its proposals are decided like `κ` above (Exercise 7 is a proposal the routing
  judgment refuses).
* **A new pass or intermediate language** needs its own equation between the meanings of its
  input and output, and must keep the legality invariant, so that the chain of equations of
  lesson I3 still composes.
* **A new schedule or timing** needs exclusion and `finish = latest end ≤ Σ δ` proved once, as
  lesson I4 did, or every frame rechecked at the new start times with the sequential timing as
  the fallback.

That is the whole of Part I: one compiler, written down exactly, with its inputs, its passes and
its proof. The [Compilation page](../compilation/#proof) draws the same proof on the three
examples.
-/
