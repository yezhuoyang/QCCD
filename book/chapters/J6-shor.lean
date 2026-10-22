import QCCDC.Cert.Sound

/-!
# Shor's algorithm, fault-tolerant, end to end

*Part J, lesson J6. You need lessons J1-J5. This lesson follows one program -- the order of 7
modulo 15 -- from its text to a machine, a schedule and a sign-off, and proves the arithmetic it
rests on.*

Shor factors `N` by finding the **order** of some `a`: the smallest `r` with `aʳ = 1 mod N`.
If `r` is even, `gcd(a^(r/2) - 1, N)` and `gcd(a^(r/2) + 1, N)` are factors. A quantum computer
finds `r` by **phase estimation**: the operator `U` that multiplies a register by `a` modulo `N`
has eigenphases `s/r`, and measuring a phase gives `r`.

The site's program is `shor15`, on the Logical Algorithm page. It uses **iterative phase
estimation** (Kitaev; the form Monz et al. ran on trapped ions in 2016): one control block per
bit of the phase, prepared in `|+̄⟩`, used to control `U^(2^k)`, rotated back by what the earlier
bits fix, and read in X. Three bits suffice here, because the order of 7 is 4.

```
include "standard"
floorplan "shor"
prep b0 Z
x    b0                 # the register holds 1 = |0001⟩
...
prep c3 X
cswap c3 b0 b3          # U multiplies by 7, on 1 and 4
cswap c3 b1 b2
cx   c3 b0
...
if m2: sdg c3           # the semiclassical inverse QFT
if m1: tdg c3
read c3 X -> m3
```
-/

/-!
## 1. The number theory
-/

def order (a n : Nat) : Nat := (List.range 16).find? (fun r => r > 0 && a ^ r % n == 1) |>.getD 0

#eval order 7 15

/-!
#### Exercise 1 (★) `order_of_seven`
Prove that the order of 7 modulo 15 is 4, and that `7² = 4 mod 15` (so iteration 2's
`U²` multiplies by 4).
-/

theorem order_of_seven : order 7 15 = 4 ∧ 7 ^ 2 % 15 = 4 := by
  decide

/-!
#### Exercise 2 (★) `the_factors`
Prove that `gcd(7² - 1, 15) = 3` and `gcd(7² + 1, 15) = 5`.
-/

theorem the_factors : Nat.gcd (7 ^ 2 - 1) 15 = 3 ∧ Nat.gcd (7 ^ 2 + 1) 15 = 5 := by
  decide

/-!
## 2. From three bits to the order

The phase is `0.m3 m2 m1` in binary. Iteration 1 measures the last bit of `4·(s/4)`, which is
always 0; so `m1 = 0`, and `s = 2·m3 + m2`. A reader of the bits recovers `r` as `4 / gcd(s, 4)`
-- the denominator of `s/4` in lowest terms -- and checks it.
-/

def sOf (m2 m3 : Bool) : Nat := 2 * (if m3 then 1 else 0) + (if m2 then 1 else 0)
def rOf (s : Nat) : Nat := 4 / Nat.gcd s 4
def works (r : Nat) : Bool := r > 0 && r % 2 == 0 && 7 ^ r % 15 == 1 && 7 ^ (r / 2) % 15 != 14

/-!
#### Exercise 3 (★★) `odd_s_gives_the_order`
Prove that whenever `m2 = 1` (so `s` is odd), the bits give `r = 4`, and `r = 4` works.
-/

theorem odd_s_gives_the_order : ∀ m3 : Bool, rOf (sOf true m3) = 4 ∧ works 4 = true := by
  decide

/-!
#### Exercise 4 (★★) `even_s_fails_safely`
And whenever `m2 = 0`, the `r` the bits give does not pass the check -- the run is repeated, never
trusted.
-/

theorem even_s_fails_safely : ∀ m3 : Bool, works (rOf (sOf false m3)) = false := by
  decide

/-!
The site's logical check (the contract checker's `program_outcomes`) runs the program
with every call as its contract and returns the probabilities exactly: `m1 = 0` always, and the
four values of `(m2, m3)` each with probability 1/4. So half the runs give `r = 4` directly.

## 3. The multiplier, compiled

`U` multiplies by 7 modulo 15. On four bits that is: rotate the bits right by one (multiply by
8 = 2⁻¹ modulo 15), then flip all four (`15 - x`, multiply by -1), since `7 = -8 mod 15`. A
controlled rotation is three controlled swaps -- three Toffolis, 21 T gates. But when iteration 3
runs, the register holds only 1 or 4 (iteration 2 left it there), and on those two values **two**
swaps do the rotation. The program uses two, as every experimental Shor has compiled its
multiplier for the values it acts on. The arithmetic says exactly how far that shortcut may go.
-/

def bit (x i : Nat) : Bool := (x >>> i) % 2 == 1

/-- Swap bits `i` and `j` of `x`. -/
def swapBits (x i j : Nat) : Nat :=
  if bit x i == bit x j then x else x ^^^ ((1 <<< i) ||| (1 <<< j))

def mul7 (x : Nat) : Nat := 7 * x % 15
def notBits (x : Nat) : Nat := 15 - x

/-- The full rotation right by one: three swaps. -/
def rotFull (x : Nat) : Nat := swapBits (swapBits (swapBits x 0 1) 1 2) 2 3
/-- The program's shortcut: two swaps (b0 <-> b3, b1 <-> b2). -/
def rotShort (x : Nat) : Nat := swapBits (swapBits x 0 3) 1 2

#eval (rotFull 1, rotShort 1, mul7 1, notBits (rotShort 1))

/-!
#### Exercise 5 (★★) `full_multiplier`
Prove that the full circuit -- rotate with three swaps, then flip -- is multiplication by 7
modulo 15 on every value the register can hold, 1 to 14.
-/

theorem full_multiplier : ∀ x : Fin 15, x.val ≥ 1 → notBits (rotFull x.val) = mul7 x.val := by
  decide

/-!
#### Exercise 6 (★★) `short_multiplier`
Prove that the program's two-swap shortcut is multiplication by 7 on the two values iteration 3
sees, 1 and 4.
-/

theorem short_multiplier : notBits (rotShort 1) = mul7 1 ∧ notBits (rotShort 4) = mul7 4 := by
  decide

/-!
#### Exercise 7 (★★) `short_is_not_general`
And that it is not a multiplier: find the smallest value it gets wrong, and prove it is wrong
there. (It is 2.)
-/

theorem short_is_not_general : notBits (rotShort 2) ≠ mul7 2 := by
  decide

/-!
#### Exercise 8 (★★★) `iteration_two`
Iteration 2's `U²` multiplies by 4, and it runs while the register holds 1. The program does it
with two CNOTs from the control, flipping bits 0 and 2 -- so `|0001⟩` becomes `|0100⟩`. Prove that
flipping bits 0 and 2 of 1 gives `4 = 7² · 1 mod 15`, and that the same flips are *not*
multiplication by 4 on 2 (a value the register never holds when iteration 2 runs). (They happen
to be right on 4: `4 · 4 = 16 = 1 mod 15`, and flipping bits 0 and 2 of 4 gives 1.)
-/

theorem iteration_two : (1 ^^^ 0b0101) = 7 ^ 2 * 1 % 15 ∧ (2 ^^^ 0b0101) ≠ 7 ^ 2 * 2 % 15 := by
  decide

/-!
## 4. The machine, the schedule, the sign-off

The rest is what Part J built, used on this program. Here it is, all of it, on the design page
-- the floorplan, the 4,435 events, the rules and the sign-off:

{{gadget algorithms/shor15}}

* **The machine.** The floorplan `shor` places six memories (blocks rest for seconds between
  iterations, and a memory corrects what it holds), three lattice-surgery bridges (every T gate
  and every π/4 gadget is a surgery: 44 of them), one transversal-CNOT workshop, two readouts, a
  preparation place, two factories each beside a pharmacy, two depots, and the decoder and the
  classical memory. F1-F9 hold on it.
* **The program.** After `include "standard"` expands, 157 instructions: 15 magic states (two
  Toffolis of seven T each, and the conditional T† of the last iteration), 29 π/4 gadgets, 22
  transversal CNOTs, 18 readouts, and 43 guarded instructions. The refresh pass adds 32
  syndrome rounds so no block waits past its coherence budget (G8).
* **The schedule.** In parallel, on `shor`: 4,435 events and 3,668 carries in 11.16 s, the
  factories producing while the bridges work; G1-G14 hold. On `shor_traffic`, which the
  optimiser found, the same program moves 36% fewer ion-sites (lesson J3).
* **The sign-off**, in parts (lesson J5): every op a verified place op; the S̄ and √X̄ gadgets are
  their rotations in every branch; `sdg`, `h`, `t`, `tdg`, `ccx`, `cswap` are their contracts;
  every machine rule holds; and the program, calls as contracts, gives exactly `m1 = 0` and
  `(m2, m3)` uniform -- this lesson's sections 2 and 3 say why that finds 4, and the factors.

What it does not establish: logical error rates. Fault distance is counted for the places
(d = 3 on every op that corrects), but no noise is sampled through the whole run; and a d = 3
code with this many operations would not, in practice, keep Shor's answer right often enough.
The same program and machine at larger distance is the next step -- the places are built from
the code's own description, and every check here applies unchanged.
-/
