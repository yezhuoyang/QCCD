import QCCDC.Cert.Sound

/-!
# Floorplans: designing the machine, and what its traffic costs

*Part J, lesson J3. You need lesson J1 (places and their doors). This is the lesson where you
design a machine: where its places stand, and the roads between them.*

A **floorplan** says where every place of a machine stands and how roads and wires join them.
On the site it is a text file you write yourself -- the town every example runs on is one,
and Shor's algorithm names its own (`floorplan "shor"`):

```
floorplan shor_d3 "A machine for Shor's algorithm (surface code, d = 3)"
region memory
place mem1      mem_d3  at 31 10
...
junction j0_4 at 31 0
road j0_4.s -- mem1.in as door_mem1_in
road j0_3.e -- j0_4.w as street0_j0_3_j0_4
wires auto bus 88
```

Nine rules (F1-F9) judge a floorplan before any program runs on it. Lesson J1 did F1. This
lesson does the two a designer meets first -- **F6**, every place can send a bundle to every
other, and **F5**, a bundle can wait in front of every door -- and then the number a designer
optimises: **traffic**, how far the ions travel.
-/

/-!
## 1. A floorplan as a graph

A road joins two doors and has a length in sites. A door is a place's (`depot.out`, `mem.in`)
or one of a junction's four arms (`n e s w`); a bundle may cross a junction from any arm to any
other. A place is an end, never a thoroughfare: nothing passes *through* it.

For Lean, every door gets a number -- the doors of places are 1 to 8, arm `k` of junction `j` is
`100 + 4j + k` -- because numbers compare in a step, and names would have to be compared letter
by letter. The names stay beside them, for reading.
-/

def depotOut : Nat := 1
def depotIn : Nat := 2
def prepIn : Nat := 3
def prepOut : Nat := 4
def memIn : Nat := 5
def memOut : Nat := 6
def postIn : Nat := 7
def postOut : Nat := 8

/-- Arm `k` of junction `j`: `k` is 0 for n, 1 for e, 2 for s, 3 for w. -/
def arm (j k : Nat) : Nat := 100 + 4 * j + k
def N : Nat := 0
def E : Nat := 1
def S : Nat := 2
def W : Nat := 3

structure Road where
  id : Nat
  name : String            -- for reading only
  a : Nat
  b : Nat
  length : Nat
deriving Repr

structure Floor where
  junctions : Nat          -- junctions 0 .. junctions - 1
  roads : List Road
deriving Repr

/-- The doors one step from `d`: across a road, or to another arm of the same junction. -/
def next (f : Floor) (d : Nat) : List Nat :=
  (f.roads.filterMap fun r => if r.a == d then some r.b else if r.b == d then some r.a else none)
  ++ (if d ≥ 100 then ((List.range 4).map (fun k => 100 + 4 * ((d - 100) / 4) + k)).filter (· != d)
      else [])

/-!
A **route** from door `a` to door `b` is the list of doors a bundle passes after `a`, each one
step from the one before, the last of them `b`. Checking a route is easy -- one step at a time --
and that is how this lesson proves that a bundle *can* get somewhere: by showing the way.
-/

/-- `leads f a b ds`: from door `a`, the doors `ds` are a route that ends at `b`. -/
def leads (f : Floor) (a b : Nat) : List Nat → Bool
  | [] => a == b
  | d :: ds => (next f a).contains d && leads f d b ds

/-!
A small machine: a depot, a preparation place, a memory and a readout, side by side below one
street. Every door takes a road of 9 sites up to the `s` arm of its own junction; the junctions
are joined left to right by the street.
-/

def doorRoads : List Road :=
  [ ⟨0, "door_depot_out", arm 0 S, depotOut, 9⟩, ⟨1, "door_depot_in", arm 1 S, depotIn, 9⟩,
    ⟨2, "door_prep_in", arm 2 S, prepIn, 9⟩, ⟨3, "door_prep_out", arm 3 S, prepOut, 9⟩,
    ⟨4, "door_mem_in", arm 4 S, memIn, 9⟩, ⟨5, "door_mem_out", arm 5 S, memOut, 9⟩,
    ⟨6, "door_post_in", arm 6 S, postIn, 9⟩, ⟨7, "door_post_out", arm 7 S, postOut, 9⟩ ]

def street : List Road :=
  [ ⟨10, "s01", arm 0 E, arm 1 W, 7⟩, ⟨11, "s12", arm 1 E, arm 2 W, 5⟩,
    ⟨12, "s23", arm 2 E, arm 3 W, 10⟩, ⟨13, "s34", arm 3 E, arm 4 W, 5⟩,
    ⟨14, "s45", arm 4 E, arm 5 W, 10⟩, ⟨15, "s56", arm 5 E, arm 6 W, 5⟩,
    ⟨16, "s67", arm 6 E, arm 7 W, 8⟩ ]

def corridor : Floor := { junctions := 8, roads := doorRoads ++ street }

/-- Every out door and every in door of another place. -/
def outsAndIns : List (Nat × Nat) :=
  [ (depotOut, prepIn), (depotOut, memIn), (depotOut, postIn),
    (prepOut, depotIn), (prepOut, memIn), (prepOut, postIn),
    (memOut, depotIn), (memOut, prepIn), (memOut, postIn),
    (postOut, depotIn), (postOut, prepIn), (postOut, memIn) ]

/-- F6: from every out door there is a route to every in door of another place. -/
def F6 (f : Floor) : Prop := ∀ p ∈ outsAndIns, ∃ ds, leads f p.1 p.2 ds = true

/-!
On the corridor every door hangs off a junction of its own -- door `d` off junction `d - 1` --
so the way from one door to another is always the same shape: down the door road to the `s`
arm, across to `e` (or `w`), along the street, across to `s`, up the door road. `way` writes it
down; `F6` then needs only that each of these routes really is one.
-/

/-- Along the street from junction `i` to junction `j`, arm by arm. -/
def along (i j : Nat) : List Nat :=
  if i ≤ j then (List.range (j - i)).flatMap (fun k => [arm (i + k) E, arm (i + k + 1) W])
  else (List.range (i - j)).flatMap (fun k => [arm (i - k) W, arm (i - k - 1) E])

/-- The way on the corridor from door `a` to door `b`. -/
def way (a b : Nat) : List Nat := [arm (a - 1) S] ++ along (a - 1) (b - 1) ++ [arm (b - 1) S, b]

#eval way prepOut memIn
#eval leads corridor prepOut memIn (way prepOut memIn)

/-- Routes shown for every pair are F6. -/
theorem f6_by_way (f : Floor) (h : outsAndIns.all (fun p => leads f p.1 p.2 (way p.1 p.2)) = true) :
    F6 f := by
  intro p hp
  exact ⟨way p.1 p.2, by simp only [List.all_eq_true] at h; exact h p hp⟩

/-!
#### Exercise 1 (★) `corridor_f6`
Prove that the corridor satisfies F6. (`f6_by_way`, and let the kernel check the twelve routes.)
-/

theorem corridor_f6 : F6 corridor := by
  exact f6_by_way corridor (by decide +kernel)

/-!
Showing that a bundle *cannot* get somewhere needs a different witness: a **closed region**, a
set of doors no step leaves. A bundle that starts inside one stays inside it, however long its
route -- so a door outside it is out of reach.
-/

/-- No step from a door of `S` leaves `S`. -/
def closed (f : Floor) (S : List Nat) : Bool :=
  S.all fun d => (next f d).all fun d' => S.contains d'

/-!
#### Exercise 2 (★★★) `trapped`
Prove it: a route that starts in a closed region ends in it. (Induction on the route; `simp`
turns `contains` and `all` into `∈` and `∀`.)
-/

theorem trapped (f : Floor) (S : List Nat) (hS : closed f S = true) :
    ∀ (ds : List Nat) (a b : Nat), S.contains a = true → leads f a b ds = true →
      S.contains b = true := by
  intro ds
  induction ds with
  | nil =>
    intro a b ha h
    simp only [leads, beq_iff_eq] at h
    exact h ▸ ha
  | cons d ds ih =>
    intro a b ha h
    simp only [leads, Bool.and_eq_true] at h
    apply ih d b _ h.2
    simp only [closed, List.all_eq_true] at hS
    simp only [List.contains_iff_mem] at hS ha h ⊢
    exact hS a ha d h.1

/-!
#### Exercise 3 (★★) `a_missing_road`
Take away the street road between `j3` and `j4`. Prove that F6 then fails: the doors left of the
gap -- the depot's and `prep`'s, and every arm of `j0` to `j3` -- are a closed region, and the
memory's door is not in it.
-/

def cut : Floor := { corridor with roads := doorRoads ++ street.filter (·.id != 13) }

def leftSide : List Nat := [depotOut, depotIn, prepIn, prepOut] ++ (List.range 16).map (· + 100)

theorem a_missing_road : ¬ F6 cut := by
  intro h
  obtain ⟨ds, hds⟩ := h (prepOut, memIn) (by decide)
  have := trapped cut leftSide (by decide +kernel) ds prepOut memIn (by decide +kernel) hds
  revert this
  decide +kernel

/-!
#### Exercise 4 (★★) `what_the_cut_leaves`
In `cut`, a block prepared at `prep` can still reach the depot, but no route takes it to the
memory. Prove both.
-/

theorem what_the_cut_leaves :
    (∃ ds, leads cut prepOut depotIn ds = true) ∧ ∀ ds, leads cut prepOut memIn ds = false := by
  refine ⟨⟨way prepOut depotIn, by decide +kernel⟩, fun ds => ?_⟩
  cases h : leads cut prepOut memIn ds
  · rfl
  · have := trapped cut leftSide (by decide +kernel) ds prepOut memIn (by decide +kernel) h
    revert this
    decide +kernel

/-!
## 2. F5: room to wait

A bundle that reaches a door while the place is busy waits on the road in front of it. That
road must hold the whole bundle -- nine ions for a block -- or the ions would spill back into
the junction behind them (G4, G14 on the schedule). F5 asks it of the floorplan, for every road
that ends at a door of a place.
-/

def isDoorRoad (r : Road) : Bool := r.a < 100 || r.b < 100

def f5 (f : Floor) (width : Nat) : Bool := f.roads.all fun r => !isDoorRoad r || r.length ≥ width

/-!
#### Exercise 5 (★) `corridor_f5`
Prove that every door road of the corridor holds a bundle of nine.
-/

theorem corridor_f5 : f5 corridor 9 = true := by
  decide +kernel

/-!
#### Exercise 6 (★) `a_short_door`
Move junction `j4` down so its road to `mem.in` is only 4 sites, and F5 fails. Prove it.
-/

def shortDoor : Floor :=
  { corridor with roads := doorRoads.map (fun r => if r.id == 4 then { r with length := 4 } else r) ++ street }

theorem a_short_door : f5 shortDoor 9 = false := by
  decide +kernel

/-!
## 3. Traffic

Every bundle a program moves travels a route of roads, and costs **ion-sites**: its ions times
the sites it crosses. Summed over a program, that is its **traffic**, the number the design page
shows in its Traffic panel and the floorplan optimiser reduces. A route here is the list of roads a bundle takes, by number.
-/

/-- The sum of a list of numbers, one at a time (so a proof can take it apart). -/
def total : List Nat → Nat
  | [] => 0
  | x :: xs => x + total xs

def lengthOf (f : Floor) (id : Nat) : Nat :=
  total ((f.roads.filter (·.id == id)).map (·.length))

def tripCost (f : Floor) (ions : Nat) (route : List Nat) : Nat :=
  ions * total (route.map (lengthOf f))

/-!
A block is prepared, rests in the memory, and is read out: three trips of nine ions -- from the
depot to `prep` (its door road, `s01`, `s12`, `prep`'s door road), from `prep` to `mem`, from
`mem` to `post`.
-/

def trips : List (List Nat) := [[0, 10, 11, 2], [3, 13, 4], [5, 15, 6]]

def traffic (f : Floor) (ts : List (List Nat)) : Nat :=
  total (ts.map (tripCost f 9))

#eval traffic corridor trips

/-!
#### Exercise 7 (★) `corridor_traffic`
Compute the traffic of the three trips on the corridor and prove the number.
-/

theorem corridor_traffic : traffic corridor trips = 684 := by
  decide +kernel

/-!
A designer can do better without moving a single place: shorten the street between the doors
the program uses most. Here the street segments between `prep` and the memory, and between the
memory and the readout, are pulled in to 3 sites.
-/

def tight : Floor :=
  { corridor with roads := doorRoads ++ street.map (fun r =>
      if r.id == 13 || r.id == 15 then { r with length := 3 } else r) }

/-!
#### Exercise 8 (★★) `tight_is_better`
Prove that `tight` has less traffic for these trips, and that it still satisfies F5 and F6.
-/

theorem tight_is_better :
    traffic tight trips < traffic corridor trips ∧ f5 tight 9 = true ∧ F6 tight := by
  exact ⟨by decide +kernel, by decide +kernel, f6_by_way tight (by decide +kernel)⟩

/-!
#### Exercise 9 (★★★) `longer_road`
In general, a longer road never makes a trip cheaper. Prove the step everything rests on:
lengthening the road with a given number by one site never lowers `lengthOf` for that number,
for any list of roads. (Induction on the list; `omega` finishes the arithmetic.)
-/

def bump (id : Nat) (r : Road) : Road :=
  if r.id == id then { r with length := r.length + 1 } else r

theorem longer_road (rs : List Road) (id : Nat) :
    lengthOf { junctions := 0, roads := rs } id ≤
      lengthOf { junctions := 0, roads := rs.map (bump id) } id := by
  unfold lengthOf
  induction rs with
  | nil => simp [total]
  | cons r rs ih =>
    by_cases h : r.id = id
    · simp [bump, h, List.filter_cons, total] at ih ⊢
      omega
    · simp [bump, h, List.filter_cons, total] at ih ⊢
      omega

/-!
## What the site does with this

Shor's algorithm on the floorplan `shor`, on the design page: its **Floorplan** button shows the
text the machine was built from, and its **Traffic** panel the number this lesson is about.

{{gadget algorithms/shor15}}

The floorplan rules F1-F9 are evaluated on every floorplan the design page loads, by the rule
language of Part E, with words for footprints, crossings and reachability: there `reachable`
is decided by a search over the roads, and the routes and closed regions of this lesson are
what that search finds. Traffic is measured on the real schedule: Shor's algorithm on `shor`
moves 313,727 ion-sites. The optimiser searched 150 rearrangements of that floorplan's places,
scheduling each, and found one (`shor_traffic`) that moves 199,580 -- 36% less -- while
every rule still holds and the compiled program signs off the same. Its makespan hardly
changed (11.16 s to 11.04 s): Shor's critical path is lattice surgery, not travel, so a faster
Shor needs more bridges, which is the next design decision, not the layout.
-/
