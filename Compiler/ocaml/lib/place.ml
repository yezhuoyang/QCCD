(* Pass 1 -- placement: which trap each logical qubit starts in.

   The objective is total routing distance: every hop an ion takes costs a machine cycle
   and heats it, and heating is what degrades the *next* gate (`docs/PLAN.md` §0.3).  So a
   placement that puts interacting qubits far apart is not merely slow, it is less
   accurate, which is why this pass exists at all rather than assigning qubit `i` to site
   `i`.

   The heuristic is greedy weighted insertion:

     1. weight every qubit pair by how many two-qubit gates act on it;
     2. seed with the qubit of greatest total weight, at the most central trap;
     3. repeatedly place the unplaced qubit most strongly tied to what is already placed,
        in whichever free trap minimises the weighted distance to its placed partners.

   That is O(qubits × traps × placed) -- microseconds at these sizes -- and it beats the
   identity order badly on any circuit with locality.  `Compiler/PLAN.md` C5 replaces it
   with a spectral seed plus annealing, and keeps this as the baseline to beat. *)

type t = {
  ion : string array;                      (* qubit index -> ion name *)
  site : (string, string) Hashtbl.t;       (* ion -> its initial trap *)
  notes : string list;
}

let ion_name q = Printf.sprintf "q%d" q

(* ------------------------------------------------------------------ interaction *)

(* Symmetric pair weights over the two-qubit gates.  One-qubit gates constrain nothing
   about *pairs*, so they are absent -- but they do force a visit to a gate-capable trap,
   which the router handles and the placer deliberately ignores: optimising placement for
   one-qubit gates on a device where every trap can gate would be optimising nothing. *)
let interaction (c : Circuit.t) : (int * int, int) Hashtbl.t =
  let w = Hashtbl.create 64 in
  List.iter
    (fun (o : Circuit.op) ->
      match o.qubits with
      | [ a; b ] ->
        let k = if a < b then (a, b) else (b, a) in
        Hashtbl.replace w k (1 + (try Hashtbl.find w k with Not_found -> 0))
      | _ -> ())
    c.ops;
  w

let weight w a b =
  let k = if a < b then (a, b) else (b, a) in
  try Hashtbl.find w k with Not_found -> 0

(* ------------------------------------------------------------------ centrality *)

(* The trap whose greatest distance to any other trap is smallest.  Seeding there rather
   than at "the first trap in the document" matters on a long device: on `chain72` the
   two choices differ by 35 hops of eccentricity. *)
let most_central (t : Traps.t) (d : Traps.dists) (candidates : string list) : string option
    =
  let score s =
    match Hashtbl.find_opt d s with
    | None -> max_int
    | Some row -> Hashtbl.fold (fun _ v acc -> max acc v) row 0
  in
  ignore t;
  List.fold_left
    (fun best s ->
      match best with
      | None -> Some (s, score s)
      | Some (_, bs) when score s < bs -> Some (s, score s)
      | keep -> keep)
    None candidates
  |> Option.map fst

(* ------------------------------------------------------------------ spectral

   The continuous relaxation.  Placement is a quadratic assignment problem -- minimise
   sum over pairs of w(a,b) * dist(site(a), site(b)) -- and QAP is hard, so the greedy
   above commits to one qubit at a time and never reconsiders.  The relaxation is what
   lets every qubit be placed at once: the Fiedler vector (the eigenvector of the second
   smallest eigenvalue of the interaction Laplacian) is the one-dimensional embedding
   that minimises sum w(a,b) * (x_a - x_b)^2, so ordering qubits by it puts strongly
   interacting ones adjacent BEFORE any of them has a trap.

   Ordering the device the same way and zipping the two is a genuinely different starting
   point from greedy insertion, and on a device whose traps form a line or a ring it is
   close to optimal by construction.

   Power iteration in the orthogonal complement of the constant vector: no eigensolver,
   no dependency, and at these sizes (<= 144 qubits) a few hundred iterations is
   microseconds. *)

let fiedler (n : int) (w : (int * int, int) Hashtbl.t) : float array =
  if n <= 2 then Array.init n float_of_int
  else begin
    let deg = Array.make n 0.0 in
    Hashtbl.iter
      (fun (a, b) c ->
        deg.(a) <- deg.(a) +. float_of_int c;
        deg.(b) <- deg.(b) +. float_of_int c)
      w;
    let dmax = Array.fold_left max 1.0 deg in
    (* Iterate on (dmax*I - L), whose LARGEST eigenvector in the complement of the
       constant vector is L's smallest non-trivial one.  Shifting like this is what turns
       "find the second smallest" into a plain power iteration. *)
    let v = Array.init n (fun i -> sin (float_of_int (i * 7 + 1))) in
    let centre a =
      let m = Array.fold_left ( +. ) 0.0 a /. float_of_int n in
      Array.iteri (fun i x -> a.(i) <- x -. m) a
    in
    let norm a =
      let s = sqrt (Array.fold_left (fun acc x -> acc +. (x *. x)) 0.0 a) in
      if s > 1e-12 then Array.iteri (fun i x -> a.(i) <- x /. s) a
    in
    centre v;
    norm v;
    let cur = ref v in
    for _ = 1 to 400 do
      let nxt = Array.make n 0.0 in
      (* (dmax*I - L)v = (dmax - deg_i) v_i + sum_j w_ij v_j *)
      Array.iteri (fun i x -> nxt.(i) <- (dmax -. deg.(i)) *. x) !cur;
      Hashtbl.iter
        (fun (a, b) c ->
          let cf = float_of_int c in
          nxt.(a) <- nxt.(a) +. (cf *. (!cur).(b));
          nxt.(b) <- nxt.(b) +. (cf *. (!cur).(a)))
        w;
      centre nxt;
      norm nxt;
      cur := nxt
    done;
    !cur
  end

(* The device's own one-dimensional order: a breadth-first walk from the most central
   trap.  On a line or a ring this is the natural order; on a grid it is a reasonable
   space-filling one, and the hill-climb afterwards is what covers the difference. *)
let device_order (t : Traps.t) (usable : string list) (seed : string) : string list =
  let ok = Hashtbl.create (List.length usable) in
  List.iter (fun s -> Hashtbl.replace ok s ()) usable;
  let seen = Hashtbl.create 64 in
  let out = ref [] in
  let q = Queue.create () in
  Queue.add seed q;
  Hashtbl.replace seen seed ();
  while not (Queue.is_empty q) do
    let u = Queue.pop q in
    if Hashtbl.mem ok u then out := u :: !out;
    List.iter
      (fun (h : Traps.hop) ->
        if not (Hashtbl.mem seen h.dst) then begin
          Hashtbl.replace seen h.dst ();
          Queue.add h.dst q
        end)
      (Traps.neighbours t u)
  done;
  let visited = List.rev !out in
  visited @ List.filter (fun s -> not (Hashtbl.mem seen s)) usable

(* ------------------------------------------------------------------ the pass *)

exception Too_many_qubits of string

(* `variant` picks among the ranked candidates: 0 is the best by the objective, 1 and 2
   are the fallbacks.  The objective is interaction DISTANCE, which knows nothing about
   whether the router can then get the ions where they need to go -- and a placement can
   be shorter on paper and put an ion somewhere that makes a later layer unroutable.  So
   the compiler asks for the next candidate rather than giving up, which is why this takes
   an index at all. *)
let run ?(variant = 0) (a : Arch.t) (t : Traps.t) (d : Traps.dists) (c : Circuit.t) : t =
  (* A trap can hold more than one ion, but starting two qubits in one trap only makes
     the router's life harder for no gain, so placement is one ion per trap.

     Junction traps are excluded where possible, and the reason is not tidiness.  A
     degree->=3 node holds at most ONE ion (R2), and on the shipped ring every route from
     the rail to a dock passes through one.  Park a qubit there and it is a chokepoint no
     other ion can cross for the whole layer -- the router cannot ask a parked ion to step
     aside, so the instance becomes unroutable rather than slow.  That is exactly what
     made ghz8 fail on `ring144_24v` while the same circuit compiled on a bare grid. *)
  let notes_pre = ref [] in
  let capacity s = Arch.eff_capacity a s in
  let open_trap s = capacity s >= 1 in
  let not_choke s =
    match Arch.node a s with Some n -> not n.is_junction | None -> false
  in
  (* Prefer traps that can GATE.  On `cyclone_dual_loop` the two loops are disconnected
     and only one of them can gate at all, so a placer that ignores capability puts every
     qubit on the wrong loop and the compiler reports every op unrealised -- correctly,
     and uselessly.  Tiering by capability first fixes that without changing any device
     where the distinction does not exist. *)
  let can_gate s = match Arch.node a s with Some n -> n.can_gate | None -> false in
  let tiers =
    [
      List.filter (fun s -> open_trap s && not_choke s && can_gate s) t.sites;
      List.filter (fun s -> open_trap s && not_choke s) t.sites;
      List.filter (fun s -> open_trap s && can_gate s) t.sites;
      List.filter open_trap t.sites;
    ]
  in
  (* Choose the tier by CAPACITY, not by trap count.  `ring144_24v` has 144 non-junction
     traps of capacity 2 -- 288 slots -- and a 168-qubit round fits them comfortably.
     Counting traps instead would find 144 < 168, fall through to the tier that includes
     the 24 degree-3 dock slots, park ions on every chokepoint, and report the circuit
     unroutable on a device that can hold it twice over. *)
  let tier_capacity l = List.fold_left (fun acc s -> acc + capacity s) 0 l in
  let usable =
    match List.find_opt (fun l -> tier_capacity l >= c.n_qubits) tiers with
    | Some l -> l
    | None -> List.filter open_trap t.sites
  in
  (* How many ions may START in one trap.

     One, whenever there are enough traps -- two ions in a trap only makes the router's
     life harder.  But `ring144_24v` has 168 traps and a BB[[144,12,12]] round needs 168
     qubits, so one-per-trap fills the device and NOTHING can move: every hop needs a free
     slot at its destination.  The traps have capacity 2, so the room exists; refusing to
     use it would report "unroutable" for a circuit the device can hold. *)
  let per_trap =
    if List.length usable >= c.n_qubits then 1
    else max 1 (List.fold_left (fun acc s -> max acc (capacity s)) 1 usable)
  in
  if per_trap > 1 then
    notes_pre := Printf.sprintf "device is tight: up to %d ions per trap at t=0" per_trap
                 :: !notes_pre;
  if tier_capacity usable < c.n_qubits then
    raise
      (Too_many_qubits
         (Printf.sprintf "%s has %d usable traps (%d ion slots) but the circuit needs %d"
            a.name (List.length usable) (tier_capacity usable) c.n_qubits));

  let w = interaction c in
  let deg q =
    List.fold_left (fun acc r -> acc + weight w q r) 0
      (List.init c.n_qubits (fun i -> i))
  in
  let placed : (int, string) Hashtbl.t = Hashtbl.create c.n_qubits in
  let used : (string, int) Hashtbl.t = Hashtbl.create c.n_qubits in
  let count s = try Hashtbl.find used s with Not_found -> 0 in
  let take s = Hashtbl.replace used s (count s + 1) in
  let room s = count s < min per_trap (capacity s) in
  let free () = List.filter room usable in

  let notes = ref !notes_pre in
  if c.n_qubits > 0 then begin
    (* seed *)
    let seed_q =
      List.init c.n_qubits (fun i -> i)
      |> List.fold_left (fun best q -> if deg q > deg best then q else best) 0
    in
    let seed_site =
      match most_central t d usable with Some s -> s | None -> List.hd usable
    in
    Hashtbl.replace placed seed_q seed_site;
    take seed_site;
    notes := Printf.sprintf "seeded q%d (weight %d) at %s" seed_q (deg seed_q) seed_site
             :: !notes;

    (* grow *)
    for _ = 2 to c.n_qubits do
      (* the unplaced qubit most strongly tied to what is already placed; ties break on
         index so the result is deterministic *)
      let best_q = ref None in
      for q = c.n_qubits - 1 downto 0 do
        if not (Hashtbl.mem placed q) then begin
          let tie =
            Hashtbl.fold (fun p _ acc -> acc + weight w q p) placed 0
          in
          match !best_q with
          | Some (_, bt) when bt >= tie && bt > 0 -> ()
          | Some (_, bt) when bt = tie -> best_q := Some (q, tie)
          | _ -> best_q := Some (q, tie)
        end
      done;
      match !best_q with
      | None -> ()
      | Some (q, _) ->
        (* the free trap minimising weighted distance to this qubit's placed partners *)
        let cost s =
          Hashtbl.fold
            (fun p ps acc ->
              let ww = weight w q p in
              if ww = 0 then acc
              else
                match Traps.dist d s ps with
                | Some k -> acc + (ww * k)
                | None -> acc + 1_000_000 (* a different component: effectively barred *))
            placed 0
        in
        let cand = free () in
        let pick =
          List.fold_left
            (fun best s ->
              match best with
              | None -> Some (s, cost s)
              | Some (_, bc) when cost s < bc -> Some (s, cost s)
              | keep -> keep)
            None cand
          |> Option.map fst
        in
        let s = match pick with Some s -> s | None -> List.hd cand in
        Hashtbl.replace placed q s;
        take s
    done
  end;

  (* --- the objective, in one place, so every candidate is scored the same way --- *)
  let objective (assign : (int, string) Hashtbl.t) =
    Hashtbl.fold
      (fun (x, y) n acc ->
        match
          ( Hashtbl.find_opt assign x,
            Hashtbl.find_opt assign y )
        with
        | Some sx, Some sy -> (
          match Traps.dist d sx sy with Some k -> acc + (n * k) | None -> acc + 1_000_000)
        | _ -> acc)
      w 0
  in

  (* --- the spectral candidate ------------------------------------------------ *)
  let spectral =
    let f = fiedler c.n_qubits w in
    let qorder =
      List.init c.n_qubits (fun i -> i)
      |> List.sort (fun i j -> compare f.(i) f.(j))
    in
    let seed = match most_central t d usable with Some s -> s | None -> List.hd usable in
    let sorder = device_order t usable seed in
    let assign = Hashtbl.create c.n_qubits in
    let slots =
      List.concat_map
        (fun s -> List.init (min per_trap (capacity s)) (fun _ -> s))
        sorder
    in
    List.iteri
      (fun k q -> if k < List.length slots then Hashtbl.replace assign q (List.nth slots k))
      qorder;
    assign
  in

  (* --- select on the TRUE objective, then hill-climb --------------------------
     Selecting rather than assuming is the same discipline `qccd/compile/place.py`
     already applies: a relaxation optimises a surrogate, so which candidate actually
     wins is a question, not a prediction. *)
  let candidates = [ ("greedy", placed); ("spectral", spectral) ] in
  let scored = List.map (fun (n, a) -> (n, a, objective a)) candidates in
  let best_name, best, best_score =
    List.fold_left
      (fun (bn, ba, bs) (n, a, sc) -> if sc < bs then (n, a, sc) else (bn, ba, bs))
      (List.hd scored |> fun (n, a, sc) -> (n, a, sc))
      scored
  in
  notes :=
    ("candidates: "
    ^ String.concat ", " (List.map (fun (n, _, sc) -> Printf.sprintf "%s %d" n sc) scored)
    ^ Printf.sprintf " -> %s" best_name)
    :: !notes;

  (* pairwise swaps on the true objective: the relaxation is one-dimensional and the
     device is not, so a few hundred exchanges recover most of what that costs *)
  let cur = Hashtbl.copy best in
  let score = ref best_score in
  let improved = ref true in
  let rounds = ref 0 in
  while !improved && !rounds < 40 do
    improved := false;
    incr rounds;
    for x = 0 to c.n_qubits - 1 do
      for y = x + 1 to c.n_qubits - 1 do
        match (Hashtbl.find_opt cur x, Hashtbl.find_opt cur y) with
        | Some sx, Some sy ->
          Hashtbl.replace cur x sy;
          Hashtbl.replace cur y sx;
          let sc = objective cur in
          if sc < !score then begin
            score := sc;
            improved := true
          end
          else begin
            Hashtbl.replace cur x sx;
            Hashtbl.replace cur y sy
          end
        | _ -> ()
      done
    done
  done;
  if !score < best_score then
    notes :=
      Printf.sprintf "hill-climb %d -> %d in %d rounds" best_score !score !rounds
      :: !notes;

  (* ranked: the hill-climbed winner, then the raw candidates as fallbacks *)
  let ranked = [ cur; placed; spectral ] in
  let chosen = List.nth ranked (min variant (List.length ranked - 1)) in
  if variant > 0 then
    notes := Printf.sprintf "placement variant %d (a fallback)" variant :: !notes;

  let site = Hashtbl.create c.n_qubits in
  let ion = Array.init c.n_qubits ion_name in
  for q = 0 to c.n_qubits - 1 do
    Hashtbl.replace site ion.(q) (Hashtbl.find chosen q)
  done;
  notes :=
    Printf.sprintf "weighted interaction distance %d over %d distinct pairs"
      (objective chosen) (Hashtbl.length w)
    :: !notes;
  { ion; site; notes = List.rev !notes }

let site_of (p : t) (ion : string) = Hashtbl.find_opt p.site ion
