import QCCDC.Cert.Sound

/-!
# Composition: a program of proved gates is the program it says

*Part J, lesson J5. You need lesson J4 (a gate is a program of places, with a contract), and
lesson I2 helps (correct by construction). Here you prove the theorem that lets the site check
a fault-tolerant program in parts.*

Shor's algorithm for 15, compiled for the site's machine, is 157 instructions after its gates
expand -- 15 magic states, 29 π/4 gadgets, 44 lattice surgeries -- and dozens of measured bits
decide what runs. No simulation of the whole thing branch by branch would ever finish. The
site signs it off in parts instead -- the compositional sign-off:

1. every op the schedule runs is a verified place op (G9);
2. every gate the program calls is its contract, in every branch (lesson J4 did `t` and `s`);
3. the floorplan and the schedule obey every machine rule (lesson J2);
4. the program, **every call counted as its contract**, computes what it should.

The design page of Shor's algorithm shows these parts, each ticked, in its Verification panel
(top right):

{{gadget algorithms/shor15}}

Step 4 simulates a few logical qubits instead of hundreds of ions. This lesson proves the
theorem that makes it sound: if every gate's body computes its contract, then running a program
whose calls are expanded into bodies is the same as running it with each call replaced by its
contract -- and the same holds for guarded calls.

We prove it for any kind of state `σ`, with each instruction a function on states. (The site's
logical semantics also branches on measurements; the argument is the same, branch by branch.)
-/

/-!
## 1. Programs, gates, expansion
-/

/-- An instruction: a primitive (a function on states), a call of a gate, or a guarded call. -/
inductive Ins (σ : Type) where
  | prim : (σ → σ) → Ins σ
  | call : Nat → Ins σ
  | guarded : Bool → Nat → Ins σ

/-- Run a list of primitive functions, first to last. -/
def runAll {σ : Type} : List (σ → σ) → σ → σ
  | [], s => s
  | f :: fs, s => runAll fs (f s)

/-- Expand a program: every call becomes its gate's body (a list of primitives); a guarded
call becomes its body with every instruction guarded. -/
def expand {σ : Type} (body : Nat → List (σ → σ)) : List (Ins σ) → List (σ → σ)
  | [] => []
  | .prim f :: p => f :: expand body p
  | .call g :: p => body g ++ expand body p
  | .guarded c g :: p => (body g).map (fun f => if c then f else id) ++ expand body p

/-- Run a program with every call replaced by its contract. -/
def runContracts {σ : Type} (contract : Nat → σ → σ) : List (Ins σ) → σ → σ
  | [], s => s
  | .prim f :: p, s => runContracts contract p (f s)
  | .call g :: p, s => runContracts contract p (contract g s)
  | .guarded c g :: p, s => runContracts contract p (if c then contract g s else s)

/-!
A small example to see the definitions run: the state is a number, gate 0 is "add three" written
as three "add one"s, and its contract is `(· + 3)`.
-/

def body0 : Nat → List (Nat → Nat)
  | 0 => [(· + 1), (· + 1), (· + 1)]
  | _ => []

def contract0 : Nat → Nat → Nat
  | 0 => (· + 3)
  | _ => id

def prog0 : List (Ins Nat) := [.prim (· * 2), .call 0, .guarded false 0, .guarded true 0]

#eval runAll (expand body0 prog0) 5
#eval runContracts contract0 prog0 5

/-!
#### Exercise 1 (★) `example_agrees`
Prove that on `prog0`, from 5, the expanded program and the program with contracts agree, and
give the number.
-/

theorem example_agrees :
    runAll (expand body0 prog0) 5 = 16 ∧ runContracts contract0 prog0 5 = 16 := by
  decide

/-!
## 2. The pieces

Running two lists one after the other is running their concatenation.
-/

/-!
#### Exercise 2 (★★) `runAll_append`
Prove it, by induction on the first list.
-/

theorem runAll_append {σ : Type} (p q : List (σ → σ)) (s : σ) :
    runAll (p ++ q) s = runAll q (runAll p s) := by
  induction p generalizing s with
  | nil => rfl
  | cons f p ih => exact ih (f s)

/-!
#### Exercise 3 (★★) `guard_off`
A body whose every instruction is switched off does nothing. Prove it for any list.
-/

theorem guard_off {σ : Type} (fs : List (σ → σ)) (s : σ) :
    runAll (fs.map (fun f => if false then f else id)) s = s := by
  induction fs generalizing s with
  | nil => rfl
  | cons f fs ih => exact ih s

/-!
#### Exercise 4 (★★) `guard_on`
And a body whose every instruction is switched on is the body.
-/

theorem guard_on {σ : Type} (fs : List (σ → σ)) (s : σ) :
    runAll (fs.map (fun f => if true then f else id)) s = runAll fs s := by
  induction fs generalizing s with
  | nil => rfl
  | cons f fs ih => exact ih (f s)

/-!
## 3. The theorem

If every gate's body computes its contract -- `runAll (body g) s = contract g s` for every gate
and every state, which is what the contract checker establishes for each gate, branch by branch
-- then expanding a program and running it is running it with contracts.
-/

/-!
#### Exercise 5 (★★★) `calls_are_contracts`
Prove it, by induction on the program. (For a call, `runAll_append` splits the expanded run in
two; for a guarded call, split on the guard and use `guard_on` or `guard_off`.)
-/

theorem calls_are_contracts {σ : Type} (body : Nat → List (σ → σ)) (contract : Nat → σ → σ)
    (h : ∀ g s, runAll (body g) s = contract g s) (p : List (Ins σ)) (s : σ) :
    runAll (expand body p) s = runContracts contract p s := by
  induction p generalizing s with
  | nil => rfl
  | cons i p ih =>
    cases i with
    | prim f => exact ih (f s)
    | call g =>
      simp only [expand, runContracts]
      rw [runAll_append, h]
      exact ih _
    | guarded c g =>
      simp only [expand, runContracts]
      rw [runAll_append]
      cases c with
      | false => rw [guard_off]; exact ih _
      | true => rw [guard_on, h]; exact ih _

/-!
## 4. The hypothesis is not decoration

A gate whose body is wrong on a single state breaks the conclusion. Gate 0 below claims to add
three but adds three only below 10 (a "bug" that a test from small states would miss).
-/

def badBody : Nat → List (Nat → Nat)
  | 0 => [fun n => if n < 10 then n + 3 else n]
  | _ => []

/-!
#### Exercise 6 (★★) `a_wrong_gate_shows`
Prove that the expanded program and the program with contracts disagree for `prog0`, from 20.
-/

theorem a_wrong_gate_shows :
    runAll (expand badBody prog0) 20 ≠ runContracts contract0 prog0 20 := by
  decide

/-!
#### Exercise 7 (★★★) `the_contract_check_is_needed`
And in general: if the conclusion of `calls_are_contracts` holds for *every* program, then
every body computes its contract. (Use the one-instruction program `[.call g]`.)
-/

theorem the_contract_check_is_needed {σ : Type} (body : Nat → List (σ → σ))
    (contract : Nat → σ → σ)
    (h : ∀ p s, runAll (expand body p) s = runContracts contract p s) :
    ∀ g s, runAll (body g) s = contract g s := by
  intro g s
  have := h [.call g] s
  simpa [expand, runContracts, runAll_append, runAll] using this

/-!
## What the site checks, and how it fits

`calls_are_contracts` is why step 4 of the sign-off may count a call as its contract: the
contract checker proves the hypothesis gate by gate (lesson J4 did it for `t` and `s` by hand),
and a gate whose body calls other gates is itself checked with those calls as contracts --
induction on the order the gates are defined, the same argument one level up. Exercise 7 is
why the contract checker cannot be skipped. For Shor the semantics step returns, exactly, the
probabilities of the three phase bits: `m1 = 0`, and `(m2, m3)` uniform -- lesson J6.

The other half -- that the *hardware* runs each call's body as written -- is not about logic:
it is the machine rules (lesson J2) holding on the schedule, every op a verified place op, and
every call expanded into its own body with fresh names. That is the same division as the proved
checker's in Part F: logic once, checked; the machine, checked every time.
-/
