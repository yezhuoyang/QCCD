import QCCDC.Cert.Sound

/-!
# Devices are graphs

*Part E, lesson E1. You need Parts A-D of this tutorial: sites, junctions, rails, shuttles,
gates and the rules, as the studio shows them. You do not need to know Lean; this lesson
teaches the Lean it uses.*

In the studio a device is a drawing: traps on rails, rails meeting at junctions. Underneath the
drawing is a document, a list of **nodes** and a list of **segments** (the rails), and every
tool on this site works from that document: the studio, the rules, the compiler, and, through
facts read out of it, the proved checker of Part F. This lesson writes three of the site's
devices as Lean data and computes from them what the rules and the checker read off a device:
who is next to whom, how many rails meet at a node, which nodes are junctions, and which moves
the machine allows.

Part E goes on the same way: programmes as data (E2), the rules as statements Lean can decide
(E3), and rule sets (E4). Everything below runs in this page. Edit any of it.
-/

/-!
## 1. A device document

Here is one node, one segment and one zone type of the device `chain4`, from its document
(`chain4.arch.json`; `...` marks fields left out):

```
{ "id": "C1", "pos": [1.0, 0.0], "kind": "site", "capacity": 2, "zone_type": "trap", ..., "degree": 2, ... }
{ "id": "E0", "ends": ["C0", "C1"], "length": 1.0, "capacity": 1, ... }
"zone_types": { "trap": { "capacity": 2, "gate": true, "spam": true, "cool": true, ... } }
```

A node's `kind` is `site` (a trap, where ions rest) or `junction`. Its `capacity` is how many
ions it holds; a junction's entry gives none, which the platform reads as 0. Whether a site
can gate is said by its zone type: `trap` has `"gate": true`. We keep four facts per node and
two per segment.

An `inductive` type lists its values; `Kind` has exactly two. `deriving DecidableEq, Repr`
asks Lean to write two things for us: a way to compare two kinds, and a way to print one.
-/

inductive Kind where
  | site
  | junction
  deriving DecidableEq, Repr

/-!
A `structure` is a record: named fields, each with its type. `String` is text, `Nat` a whole
number 0, 1, 2, ..., `Bool` is `true` or `false`.
-/

structure Node where
  id : String
  kind : Kind
  capacity : Nat
  gate : Bool
  deriving Repr

/-!
A segment joins two nodes. `String × String` is the type of a **pair** of strings, written
`("C0", "C1")`; `s.ends.1` and `s.ends.2` are its two halves. `List Node` is a list of nodes,
written `[a, b, c]`.
-/

structure Segment where
  id : String
  ends : String × String
  deriving Repr

structure Device where
  name : String
  nodes : List Node
  segments : List Segment
  deriving Repr

/-!
## 2. Three devices

`def` gives a name to a value. `⟨"C0", .site, 2, true⟩` builds a `Node` from its fields in
order (id, kind, capacity, gate); `.site` is short for `Kind.site`, since Lean knows the type
it wants there. `chain4` is four traps in a row, `C0 - C1 - C2 - C3`, each holding two ions and
able to gate, joined by three segments.
-/

def chain4 : Device :=
  { name := "chain4",
    nodes := [⟨"C0", .site, 2, true⟩, ⟨"C1", .site, 2, true⟩,
              ⟨"C2", .site, 2, true⟩, ⟨"C3", .site, 2, true⟩],
    segments := [⟨"E0", ("C0", "C1")⟩, ⟨"E1", ("C1", "C2")⟩, ⟨"E2", ("C2", "C3")⟩] }

/-!
`grid2x3` is a lattice: six nodes its document calls junctions, `J0_0` to `J1_2`, and seven
traps, each on a rail between two of them (`T0_1h` lies between `J0_1` and `J1_1`). Each trap
has two segments, `.a` and `.b`, one to each of its junctions. The lists are in the document's
order.
-/

def grid2x3 : Device :=
  { name := "grid2x3",
    nodes := [⟨"J0_0", .junction, 0, false⟩, ⟨"J0_1", .junction, 0, false⟩,
              ⟨"J0_2", .junction, 0, false⟩, ⟨"J1_0", .junction, 0, false⟩,
              ⟨"J1_1", .junction, 0, false⟩, ⟨"J1_2", .junction, 0, false⟩,
              ⟨"T0_0h", .site, 2, true⟩, ⟨"T0_1h", .site, 2, true⟩, ⟨"T0_2h", .site, 2, true⟩,
              ⟨"T0_0v", .site, 2, true⟩, ⟨"T0_1v", .site, 2, true⟩,
              ⟨"T1_0v", .site, 2, true⟩, ⟨"T1_1v", .site, 2, true⟩],
    segments := [⟨"T0_0h.a", ("J0_0", "T0_0h")⟩, ⟨"T0_0h.b", ("T0_0h", "J1_0")⟩,
                 ⟨"T0_1h.a", ("J0_1", "T0_1h")⟩, ⟨"T0_1h.b", ("T0_1h", "J1_1")⟩,
                 ⟨"T0_2h.a", ("J0_2", "T0_2h")⟩, ⟨"T0_2h.b", ("T0_2h", "J1_2")⟩,
                 ⟨"T0_0v.a", ("J0_0", "T0_0v")⟩, ⟨"T0_0v.b", ("T0_0v", "J0_1")⟩,
                 ⟨"T0_1v.a", ("J0_1", "T0_1v")⟩, ⟨"T0_1v.b", ("T0_1v", "J0_2")⟩,
                 ⟨"T1_0v.a", ("J1_0", "T1_0v")⟩, ⟨"T1_0v.b", ("T1_0v", "J1_1")⟩,
                 ⟨"T1_1v.a", ("J1_1", "T1_1v")⟩, ⟨"T1_1v.b", ("T1_1v", "J1_2")⟩] }

/-!
`ring6d` is a closed loop of six sites, `S0` to `S5`, that cannot gate (their zone type is
`data`, with `"gate": false`), and two docks that can: `A0` off `S0` and `A3` off `S3`.
-/

def ring6d : Device :=
  { name := "ring6d",
    nodes := [⟨"S0", .site, 2, false⟩, ⟨"S1", .site, 2, false⟩, ⟨"S2", .site, 2, false⟩,
              ⟨"S3", .site, 2, false⟩, ⟨"S4", .site, 2, false⟩, ⟨"S5", .site, 2, false⟩,
              ⟨"A0", .site, 2, true⟩, ⟨"A3", .site, 2, true⟩],
    segments := [⟨"E0", ("S0", "S1")⟩, ⟨"E1", ("S1", "S2")⟩, ⟨"E2", ("S2", "S3")⟩,
                 ⟨"E3", ("S3", "S4")⟩, ⟨"E4", ("S4", "S5")⟩, ⟨"E5", ("S5", "S0")⟩,
                 ⟨"V0", ("S0", "A0")⟩, ⟨"V3", ("S3", "A3")⟩] }

/-!
A function is a `def` with parameters. `fun n => ...` is a function written in place;
`filter` keeps the elements of a list for which it says `true`, and `map` applies it to every
element. `==` compares two values and answers a `Bool`. So `sites d` is the names of the
nodes of `d` whose kind is `site`, in the document's order.
-/

def sites (d : Device) : List String :=
  (d.nodes.filter fun n => n.kind == .site).map fun n => n.id

/-!
`#eval` runs an expression and shows the result. It proves nothing; it computes.
-/

#eval sites chain4
#eval sites grid2x3

/-!
The page draws any value with two fields, `arch` (a device of the site) and `src` (a programme
in the studio's language), in the studio. Lesson E2 is about programmes; here a programme only
places ions, with one `p.init` statement. `ionsAt k ns` names one ion per node, `q0`, `q1`, ...;
`initSrc` writes the statement. (`"\""` is a string holding one double quote; `++` joins
strings and lists.)
-/

structure Studio where
  arch : String
  src : String

def quote (s : String) : String := "\"" ++ s ++ "\""

def ionsAt : Nat → List String → List (String × String)
  | _, [] => []
  | k, n :: ns => ("q" ++ toString k, n) :: ionsAt (k + 1) ns

def initSrc (ions : List (String × String)) : String :=
  "p.init({" ++ ", ".intercalate (ions.map fun p => quote p.1 ++ ": " ++ quote p.2) ++ "})"

def chain4Sites : Studio := { arch := chain4.name, src := initSrc (ionsAt 0 (sites chain4)) }

#eval chain4Sites.src

/-!
Here is `chain4` with an ion in every site, computed from the definitions above: edit
`chain4` (drop a node) and the figure follows.

{{studio chain4Sites}}

A **theorem** is a statement with a proof. `rfl` ("reflexivity") proves an equation whose two
sides compute to the same thing; `∧` is "and", and `⟨p, q⟩` proves it from two proofs.

#### Exercise 1 (★) `grid_size`
Prove that `grid2x3` has 13 nodes and 14 segments.
-/

theorem grid_size : grid2x3.nodes.length = 13 ∧ grid2x3.segments.length = 14 :=
  ⟨rfl, rfl⟩

/-!
## 3. Neighbours and degree

The rails at a node `n`: every segment with `n` at one end, paired with the node at its other
end. `flatMap` maps each segment to a list (here of zero, one or two entries) and joins the
lists. A node's **neighbours** are the other ends; its **degree** is how many rails meet at it.
A rail from a node to itself would count twice, as it does in the platform's `Device.degree`
(no device here has one).
-/

def railsAt (d : Device) (n : String) : List (String × String) :=
  d.segments.flatMap fun s =>
    (if s.ends.1 == n then [(s.ends.2, s.id)] else []) ++
    (if s.ends.2 == n then [(s.ends.1, s.id)] else [])

def neighbours (d : Device) (n : String) : List String :=
  (railsAt d n).map fun r => r.1

def degree (d : Device) (n : String) : Nat :=
  (railsAt d n).length

#eval neighbours grid2x3 "J0_1"
#eval (sites chain4).map fun n => (n, degree chain4 n)

/-!
The document records a `"degree"` for every node (`C0` 1, `C1` 2, `J0_1` 3, `J0_0` 2, `S0` 3);
the values computed here agree with it, node for node, on all three devices.

For statements that compute with strings this lesson uses `decide +kernel`. It proves a
statement by having Lean's **kernel**, the small trusted core that checks every proof, compute
it to `true`. It is the strongest kind of proof by computation: the kernel is the only thing
trusted.

#### Exercise 2 (★) `c1_neighbours`
Prove that the neighbours of `C1` on `chain4` are `C0` then `C2`.
-/

theorem c1_neighbours : neighbours chain4 "C1" = ["C0", "C2"] := by
  decide +kernel

/-!
## 4. Junctions are a matter of degree (R18)

Rule R18, as the platform's rule checker states it, says: "a node is a junction only if
three or more trap axes meet at it". The rule language says it more bluntly: "Whatever a node is called,
it is a junction exactly when three or more rails meet at it." The platform's device keeps its
junctions that way, as the nodes of degree at least three, and the junction rule R2 and the
cost of crossing a junction both read that degree, never the node's `kind`.
-/

def isJunction (d : Device) (n : String) : Bool :=
  degree d n ≥ 3

def junctions (d : Device) : List String :=
  (d.nodes.map fun n => n.id).filter (isJunction d)

#eval junctions grid2x3
#eval junctions ring6d
#eval junctions chain4

/-!
Both directions of R18 show here. `grid2x3`'s document calls six nodes junctions, but at the
four corners (`J0_0`, `J0_2`, `J1_0`, `J1_2`) only two rails meet: they are bends, crossed as
plain transport. And `ring6d`'s document calls `S0` and `S3` sites, but three rails meet at
each (two of the loop and the dock's): they are junctions that are also traps.

#### Exercise 3 (★) `junctions_by_degree`
Prove which nodes are junctions on `grid2x3` and on `ring6d`.
-/

theorem junctions_by_degree :
    junctions grid2x3 = ["J0_1", "J1_1"] ∧ junctions ring6d = ["S0", "S3"] := by
  decide +kernel

/-!
The rest of this section is about every device, not one. `{ d with segments := s ::
d.segments }` is `d` with one more segment `s` in front of its list (`::` puts an element in
front of a list).

`simp` rewrites a goal with the definitions and facts you give it, and with a large set of
facts it knows, until it is solved or stuck. `omega` proves statements of linear arithmetic
about whole numbers (`a ≤ b + a`, and the like), whatever the numbers stand for.

#### Exercise 4 (★★) `degree_more`
Prove that adding a segment never lowers the degree of a node. (`simp [degree, railsAt]` turns
both degrees into sums; `omega` finishes.)
-/

theorem degree_more (d : Device) (s : Segment) (n : String) :
    degree d n ≤ degree { d with segments := s :: d.segments } n := by
  simp [degree, railsAt]
  omega

/-!
The platform's device module says why R18 is written this way: drawing a spur onto a rail makes
the rail node degree 3, and it *becomes* a junction (that is how `S0` and `S3` became
junctions on `ring6d`). Prove it, for every device.

#### Exercise 5 (★★) `spur_makes_junction`
A node of degree 2 that gains a segment at one of its ends is a junction. (`simp [isJunction,
degree, railsAt, hs] at *` and `omega`.)
-/

theorem spur_makes_junction (d : Device) (s : Segment) (n : String)
    (h2 : degree d n = 2) (hs : s.ends.1 = n) :
    isJunction { d with segments := s :: d.segments } n = true := by
  simp [isJunction, degree, railsAt, hs] at *
  omega

/-!
## 5. Hops: what the checker is told about a device

Part F's checker decides whether a compiler's account of a programme holds. It never takes the
compiler's word for what the machine can do: the moves a device allows, its **hops**, are read
out of the device document by a short Python function, `trap_hops`, in the builder of the
checker's device facts (`mk_qcheck_input`). A hop is an ordered pair of sites one machine cycle
apart. In the function's own words: an ion never rests on a junction, so a hop leaves a trap,
crosses only junctions, and lands on a trap -- where a junction is what R18 says it is, a bare
junction node or a site where three or more rails meet.

The function walks the rails out of every site. It may cross a node that is not a site (a bare
junction, or a bend like `J0_0`) or a site where three or more rails meet; it never uses a rail
twice; it crosses at most three nodes (`max_transit = 3`); and every site it reaches, other
than the start, is a hop. Here is the same walk in Lean.

A function can be defined by **pattern matching**: one line per shape of its arguments. The
first argument of `walk` is its fuel; `| 0, _, _ => []` says that with no fuel left the walk
finds nothing, and the second line handles fuel `k + 1` and calls `walk` again with `k`. Lean
accepts a function that calls itself only when it can see the calls end, as here, where the
fuel goes down by one each time. `used.contains seg` asks whether a rail was used already; `!`
is "not", `&&` "and", `||` "or".
-/

def isSite (d : Device) (n : String) : Bool :=
  d.nodes.any fun x => x.id == n && x.kind == .site

/-- The sites a walk from `src` reaches, standing at `node`, having used the rails `used`. -/
def walk (d : Device) (src : String) : Nat → String → List String → List String
  | 0, _, _ => []
  | k + 1, node, used =>
    (railsAt d node).flatMap fun r =>
      if used.contains r.2 then []
      else (if isSite d r.1 && r.1 != src then [r.1] else []) ++
           (if r.1 != src && (!isSite d r.1 || isJunction d r.1)
            then walk d src k r.1 (r.2 :: used) else [])

/-!
A site reached by two routes is one hop: `fresh` keeps the first time each name appears, which
is the order the Python function records them in. The walk starts with fuel 4: it looks along
the rails of the start and of at most three crossed nodes, as the Python does, whose walk stops
when `depth > max_transit`.
-/

def fresh (seen : List String) : List String → List String
  | [] => []
  | x :: xs => if seen.contains x then fresh seen xs else x :: fresh (x :: seen) xs

def hopsFrom (d : Device) (s : String) : List String :=
  fresh [] (walk d s 4 s [])

def hops (d : Device) : List (String × String) :=
  (sites d).flatMap fun s => (hopsFrom d s).map fun t => (s, t)

#eval hops chain4

/-!
The Bell pair that lesson F1 proves was compiled for `chain4`, and its certificate carries the
device's hops in the field `hops`. Here is that list, copied from the certificate
([bell.qcheck.json](data/bell.qcheck.json), and `bell` in [lesson F1](2.1-certificates.html)).
-/

def bellHops : List (String × String) :=
  [("C0", "C1"), ("C1", "C0"), ("C1", "C2"), ("C2", "C1"), ("C2", "C3"), ("C3", "C2")]

/-!
#### Exercise 6 (★) `chain4_hops`
Prove that the walk computes exactly the hops of the Bell certificate.
-/

theorem chain4_hops : hops chain4 = bellHops := by
  decide +kernel

/-!
On `grid2x3` every hop crosses a junction or a bend, and from `T0_1h`, which lies between the
two real junctions, there are four. The figure puts `q0` in `T0_1h` and one ion in each trap a
hop away, computed by `hopsFrom`.
-/

def gridHops : Studio :=
  { arch := grid2x3.name, src := initSrc (ionsAt 0 ("T0_1h" :: hopsFrom grid2x3 "T0_1h")) }

#eval hopsFrom grid2x3 "T0_1h"

/-!
{{studio gridHops}}

On `ring6d` the walk crosses sites: from the dock `A3` it reaches `S3` and, through `S3`,
which is a junction, `S2` and `S4`. The SWAP compiled for `ring6d` on the Compilation page
uses exactly such a hop: its first move takes `q1` from `A3` to `S4` in one cycle, through
`S3`.
-/

def ringHops : Studio :=
  { arch := ring6d.name, src := initSrc (ionsAt 0 ("A3" :: hopsFrom ring6d "A3")) }

#eval hopsFrom ring6d "A3"

/-!
{{studio ringHops}}

The published certificates of the CX on `grid2x3` and the SWAP on `ring6d` carry these hop
lists ([cx.qcheck.json](data/cx.qcheck.json) and [swap.qcheck.json](data/swap.qcheck.json)),
copied here.
-/

def cxHops : List (String × String) :=
  [("T0_0h", "T0_0v"), ("T0_0h", "T1_0v"), ("T0_1h", "T0_0v"), ("T0_1h", "T0_1v"),
   ("T0_1h", "T1_0v"), ("T0_1h", "T1_1v"), ("T0_2h", "T0_1v"), ("T0_2h", "T1_1v"),
   ("T0_0v", "T0_0h"), ("T0_0v", "T0_1h"), ("T0_0v", "T0_1v"), ("T0_1v", "T0_1h"),
   ("T0_1v", "T0_0v"), ("T0_1v", "T0_2h"), ("T1_0v", "T0_0h"), ("T1_0v", "T0_1h"),
   ("T1_0v", "T1_1v"), ("T1_1v", "T0_1h"), ("T1_1v", "T1_0v"), ("T1_1v", "T0_2h")]

def swapHops : List (String × String) :=
  [("S0", "S1"), ("S0", "S5"), ("S0", "A0"), ("S1", "S0"), ("S1", "S5"), ("S1", "A0"),
   ("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("S2", "S4"), ("S2", "A3"), ("S3", "S2"),
   ("S3", "S4"), ("S3", "A3"), ("S4", "S3"), ("S4", "S2"), ("S4", "A3"), ("S4", "S5"),
   ("S5", "S4"), ("S5", "S0"), ("S5", "S1"), ("S5", "A0"), ("A0", "S0"), ("A0", "S1"),
   ("A0", "S5"), ("A3", "S3"), ("A3", "S2"), ("A3", "S4")]

/-!
#### Exercise 7 (★) `published_hops`
Prove that the walk computes exactly the hops of both published certificates. Those lists
were written by the Python `trap_hops`, so this proves the Lean walk agrees with it on both
devices.
-/

theorem published_hops : hops grid2x3 = cxHops ∧ hops ring6d = swapHops := by
  decide +kernel

/-!
## 6. Facts about every device

The theorems so far were about one device each. These two hold for every device there will
ever be, and their proofs are about the definitions, not about any list.

`∈` is membership in a list. A hypothesis `h : m ∈ neighbours d n` is a proof you may use;
`simp only [...] at *` rewrites the goal and every hypothesis with exactly the facts listed
(`List.mem_map`, `List.mem_flatMap`: what membership in a `map` and a `flatMap` means);
`obtain ⟨a, b⟩ := h` takes a proof of "there is an `a` with `b`" apart; `by_cases h : p`
splits the proof in two, with `p` and with `¬ p` ("not p"); `<;>` runs the next tactic on every
goal the last one left; `simp_all` simplifies everything with everything.

#### Exercise 8 (★★★) `neighbours_symm`
A rail runs both ways: if `m` is a neighbour of `n`, then `n` is a neighbour of `m`. Hints:
unfold `neighbours` and `railsAt` with `simp only [neighbours, railsAt, List.mem_map,
List.mem_flatMap] at *`; `h` then says some segment `s` of `d` lists `m` as the other end of a
rail at `n`; use the same `s` (`refine ⟨(n, s.id), ⟨s, hs, ?_⟩, rfl⟩`), and finish by cases on
which end of `s` is `n`.
-/

theorem neighbours_symm (d : Device) (n m : String) (h : m ∈ neighbours d n) :
    n ∈ neighbours d m := by
  simp only [neighbours, railsAt, List.mem_map, List.mem_flatMap] at *
  obtain ⟨⟨x, sid⟩, ⟨s, hs, hx⟩, rfl⟩ := h
  refine ⟨(n, s.id), ⟨s, hs, ?_⟩, rfl⟩
  by_cases h1 : s.ends.1 = n <;> by_cases h2 : s.ends.2 = n <;> simp_all

/-!
The checker relies on every hop going from a site to a different site. That is a fact about
`walk`, for any fuel: whatever it reports is a site and is not where it started. A statement
about every amount of fuel is proved by **induction**: prove it for fuel 0, then prove it for
fuel `k + 1` assuming it for `k` (the assumption is called `ih`). `split at hx` splits a proof
on the `if` inside the hypothesis `hx`, one goal per branch.

#### Exercise 9 (★★★) `walk_sites`
Prove it. Hints: `intro fuel; induction fuel with | zero => ... | succ k ih => ...`; at fuel 0
the walk is empty (`simp [walk] at hx`); at `k + 1`, unfold one step of `walk` and
`List.mem_flatMap`, take the rail apart, and follow `split at hx` through the three `if`s: a
reported `r.1` comes with `isSite d r.1 && r.1 != src`, and a name from deeper in the walk is
`ih`'s business.
-/

theorem walk_sites (d : Device) (src : String) :
    ∀ fuel node used, ∀ x ∈ walk d src fuel node used, isSite d x = true ∧ x ≠ src := by
  intro fuel
  induction fuel with
  | zero => intro node used x hx; simp [walk] at hx
  | succ k ih =>
    intro node used x hx
    simp only [walk, List.mem_flatMap] at hx
    obtain ⟨r, _, hx⟩ := hx
    split at hx
    · simp at hx
    · simp only [List.mem_append] at hx
      rcases hx with hx | hx
      · split at hx
        · simp_all
        · simp at hx
      · split at hx
        · exact ih _ _ x hx
        · simp at hx

/-!
## 7. What comes next

A device is now a value you can compute with and prove things about. Lesson E2 does the same
for programmes: the studio's statements as Lean data, and the replay that says where every ion
is after every step. Lesson E3 then states the rules of Part R as propositions about that
replay, and has Lean decide them.
-/
