(* Pass 3 -- routing: get the operands of every gate into one trap, legally.

   This is prioritised planning with a space-time reservation table: ions are routed one
   at a time, in priority order, each by A* over `(trap, cycle)` states, and each
   reserving the resources its path consumes so later ions plan around it.  Waiting in
   place is an action, which is what makes it complete enough to be useful and what keeps
   it deadlock-free within a layer.

   {1 The reservation table IS the rule set}

   The point of listing the constraints here rather than emitting moves and hoping is that
   every one of them corresponds to a rule the verifier will independently re-check:

     occupancy of a trap   <= its capacity                                        R1
     transits of a junction in one cycle <= 1                                     R2
     participants on a segment in one cycle <= its capacity                       R3
     one movement class per cycle                                                 R4
     all moves along one named loop in one cycle share a signed delta             R4d
     never (u->v) together with (v->u) on one segment                             R5
     an ion participates at most once per cycle                                   R8

   R4d is the one that makes this hardware different from ordinary multi-agent
   pathfinding, and it is also the one the verifier is *silent* about on a loop-free
   broadcast device (`Compiler/PLAN.md` §6).  The router therefore enforces it from the
   declared control plane rather than inheriting it, and says so.

   {1 Layers}

   Gates are executed in DAG layers.  Within a layer the reservation table is built fresh:
   ions not involved are static obstacles, involved ions are routed, and each holds its
   destination from arrival to the end of the layer.  That gives up cross-layer
   pipelining -- which is exactly what `Compiler/PLAN.md` C4 and C5 exist to recover, and
   what the SAT oracle will measure this against. *)

exception Unroutable of string

type move = { ion : string; src : string; dst : string; via : string list }
type cycle = { moves : move list }

type layer_plan = {
  cycles : cycle list;
  arrivals : (string * string) list;  (* ion, where it ended up *)
  (* The number of TIME SLOTS the reservation table used, before a slot carrying several
     waveforms was split into one instruction each.  `cycles` is what the machine runs;
     `slots` is the MAPF makespan, and it is the only one of the two the SAT oracle is
     solving the same problem as -- reporting the split count as the heuristic's makespan
     would turn a uniformity split into an apparent optimality gap. *)
  slots : int;
}

(* ------------------------------------------------------------------ loop actions *)

(* The action signature R4d judges: moving along a named path is one conveyor
   instruction, `"L0:+1"`, and one channel can only carry one of them per cycle.  A hop
   whose endpoints are not adjacent on a common loop has no signature and no verdict --
   which is exactly why the router must not treat "no verdict" as "no constraint". *)
type action = { loop : string; delta : int }

let loop_index (a : Arch.t) =
  let tbl = Hashtbl.create 8 in
  List.iter
    (fun (l : Arch.loop) ->
      let idx = Hashtbl.create (List.length l.nodes) in
      List.iteri (fun i n -> Hashtbl.replace idx n i) l.nodes;
      Hashtbl.replace tbl l.lid (idx, List.length l.nodes, l.closed))
    a.loops;
  tbl

let action_of (li : (string, (string, int) Hashtbl.t * int * bool) Hashtbl.t) src dst :
    action option =
  Hashtbl.fold
    (fun lid (idx, n, closed) acc ->
      match acc with
      | Some _ -> acc
      | None -> (
        match (Hashtbl.find_opt idx src, Hashtbl.find_opt idx dst) with
        | Some i, Some j ->
          let raw = j - i in
          let d = if closed then ((raw + n + (n / 2)) mod n) - (n / 2) else raw in
          if abs d = 1 then Some { loop = lid; delta = d } else None
        | _ -> None))
    li None

(* ------------------------------------------- R22: one cycle is one waveform

   Under broadcast control a cycle IS a waveform, played at every site at once, so the
   participants of one `simd` instruction must all be doing the same thing.  `qccd/verify`
   states that as R22 and decides it with `motion_signature`; what follows is the same
   function written here, and the router splits a time slot into one instruction per
   distinct signature so that the compiler and the verifier agree by construction rather
   than by luck.

   The signature is per HOP, not per move: a grid move crosses two segments through a
   junction and the verifier labels both of them.  Keep the three cases and the order
   they are tried in identical to the Python -- along a named loop, across a spur, else
   the lab-frame axis -- because a signature that differs in a single label is a cycle
   the verifier splits and the compiler did not. *)

let sig_eps = 1e-6

type sigctx = {
  sarch : Arch.t;
  seg_by_id : (string, Arch.segment) Hashtbl.t;
  loop_seq : (string, string array) Hashtbl.t;
  on_loop : (string, unit) Hashtbl.t;
  (* (src, dst) -> signature.  `hop_free` asks for this inside the A* inner loop, and a
     directed hop's `via` is fixed by `Traps.build` (one hop per ordered pair), so the
     answer never changes and is worth keeping.  Recomputing it per expanded state turned
     a two-minute compile into a twenty-minute one. *)
  hop_sig : (string * string, string) Hashtbl.t;
}

let sigctx (a : Arch.t) : sigctx =
  let seg_by_id = Hashtbl.create (List.length a.segments) in
  List.iter (fun (s : Arch.segment) -> Hashtbl.replace seg_by_id s.sid s) a.segments;
  let loop_seq = Hashtbl.create 8 in
  let on_loop = Hashtbl.create 64 in
  List.iter
    (fun (l : Arch.loop) ->
      Hashtbl.replace loop_seq l.lid (Array.of_list l.nodes);
      List.iter (fun n -> Hashtbl.replace on_loop n ()) l.nodes)
    a.loops;
  { sarch = a; seg_by_id; loop_seq; on_loop; hop_sig = Hashtbl.create 1024 }

(* `stationary_chain` declares `control.model = "direct"` -- every electrode is its own
   channel, so a cycle may legitimately ask two ions to do different things and R22 does
   not apply.  Splitting there would inflate the schedule to satisfy a rule that is not
   being checked. *)
let control_model (a : Arch.t) =
  match Arch.mem "control" a.raw with
  | Some c -> Arch.str_or "model" "simd_classes" c
  | None -> "simd_classes"

let index_in (seq : string array) (x : string) =
  let r = ref None in
  Array.iteri (fun i n -> if !r = None && n = x then r := Some i) seq;
  !r

let hop_label (c : sigctx) (seg : Arch.segment) src dst =
  let along =
    match seg.loop with
    | None -> None
    | Some lid -> (
      match Hashtbl.find_opt c.loop_seq lid with
      | None -> None
      | Some seq -> (
        match (index_in seq src, index_in seq dst) with
        | Some i, Some j ->
          let k = Array.length seq in
          let d = (((j - i) mod k) + k) mod k in
          let d = if d <= k / 2 then d else d - k in
          Some (Printf.sprintf "%s:%+d" lid d)
        | _ -> None))
  in
  match along with
  | Some l -> l
  | None -> (
    let spur =
      if seg.loop <> None then None
      else
        let s_on = Hashtbl.mem c.on_loop src and d_on = Hashtbl.mem c.on_loop dst in
        if s_on = d_on then None
        else Some (if s_on then "spur:inward" else "spur:outward")
    in
    match spur with
    | Some l -> l
    | None ->
      let p id =
        match Arch.node c.sarch id with Some (n : Arch.node) -> n.pos | None -> (0.0, 0.0)
      in
      let ax, ay = p src and bx, by = p dst in
      let dx = bx -. ax and dy = by -. ay in
      let sx = if dx > sig_eps then "+x" else if dx < -.sig_eps then "-x" else "" in
      let sy = if dy > sig_eps then "+y" else if dy < -.sig_eps then "-y" else "" in
      if sx = "" && sy = "" then "0" else sx ^ sy)

(* The waveform one move needs: the instruction's class, then a label per segment the ion
   crosses, in the order it crosses them.  `via` is ordered from the source, which is how
   `qccd/verify/replay.py` walks it, so the two label the same hops in the same order. *)
let via_signature (c : sigctx) ~(cls : string) ~(src : string) ~(via : string list) =
  let at = ref src in
  let labels =
    List.map
      (fun sid ->
        match Hashtbl.find_opt c.seg_by_id sid with
        | None -> "?"
        | Some (s : Arch.segment) ->
          let nxt = if s.a = !at then s.b else s.a in
          let l = hop_label c s !at nxt in
          at := nxt;
          l)
      via
  in
  Printf.sprintf "%s %s" cls (String.concat "," labels)

(* The class the router emits.  Named once so the signature the reservation table locks
   and the signature `split_slot` groups by cannot drift apart. *)
let shuttle_cls = "shuttle"

(* The signature of one directed trap-to-trap hop, memoised. *)
let hop_signature (c : sigctx) (src : string) (h : Traps.hop) =
  match Hashtbl.find_opt c.hop_sig (src, h.dst) with
  | Some s -> s
  | None ->
    let s = via_signature c ~cls:shuttle_cls ~src ~via:h.via in
    Hashtbl.replace c.hop_sig (src, h.dst) s;
    s

let move_signature (c : sigctx) ~(cls : string) (m : move) =
  let via =
    match m.via with
    | [] -> (
      match
        List.find_opt
          (fun (s : Arch.segment) ->
            (s.a = m.src && s.b = m.dst) || (s.b = m.src && s.a = m.dst))
          c.sarch.segments
      with
      | Some (s : Arch.segment) -> [ s.sid ]
      | None -> [])
    | v -> v
  in
  via_signature c ~cls ~src:m.src ~via

(* Split one time slot into uniform cycles -- the safety net, not the mechanism.

   The reservation table now locks one waveform per cycle during the search, so every
   slot this sees should already hold exactly one signature and this should be the
   identity.  It stays because a signature computed two ways is a signature that can
   drift, and because a slot that somehow arrives mixed is better emitted as several
   legal cycles than as one illegal one.

   If it ever does have work to do: a slot with k waveforms is k machine cycles, and the
   order between them is not free -- a group that arrives where another group is still
   standing needs that group to leave first, or the intermediate state overflows the
   trap.  So the groups are offered in signature order and the first one that fits the
   capacities AS THEY STAND is committed, which is vacate-before-arrive exactly when
   capacity is what binds.  A cyclic dependency (a rotation of full traps, whichever
   direction goes first overfills the other) has no legal order at all; that residue is
   what made the post-hoc pass fail R1 on two lattice entries, and it is the reason the
   signature moved into the reservation table. *)
let split_slot (a : Arch.t) (c : sigctx) ~(cls : string) ~(occ : (string, int) Hashtbl.t)
    (ms : move list) : move list list =
  let groups =
    List.fold_left
      (fun acc m ->
        let s = move_signature c ~cls m in
        match List.assoc_opt s acc with
        | Some r ->
          r := m :: !r;
          acc
        | None -> acc @ [ (s, ref [ m ]) ])
      [] ms
    |> List.map (fun (s, r) -> (s, List.rev !r))
    |> List.sort (fun (x, _) (y, _) -> compare x y)
  in
  let get t k = try Hashtbl.find t k with Not_found -> 0 in
  let apply t g =
    List.iter
      (fun (m : move) ->
        Hashtbl.replace t m.src (get t m.src - 1);
        Hashtbl.replace t m.dst (get t m.dst + 1))
      g
  in
  let fits g =
    let scratch = Hashtbl.copy occ in
    apply scratch g;
    List.for_all (fun (m : move) -> get scratch m.dst <= Arch.eff_capacity a m.dst) g
  in
  let rec go remaining acc =
    match remaining with
    | [] -> List.rev acc
    | _ -> (
      match List.find_opt (fun (_, g) -> fits g) remaining with
      | Some (s, g) ->
        apply occ g;
        go (List.filter (fun (s2, _) -> s2 <> s) remaining) (g :: acc)
      | None ->
        (* no order avoids the overlap; emit the rest in signature order *)
        List.iter (fun (_, g) -> apply occ g) remaining;
        List.rev_append acc (List.map snd remaining))
  in
  (* The net catching something means the table and this disagree about what one
     waveform is, which is a bug in one of them and not a thing to fix silently. *)
  (match groups with
  | _ :: _ :: _ when Sys.getenv_opt "QCCDC_DEBUG" <> None ->
    Printf.eprintf "  [route] slot needs %d waveforms after the reservation lock: %s
"
      (List.length groups)
      (String.concat " | " (List.map fst groups))
  | _ -> ());
  match groups with
  | [] -> []
  | [ (_, g) ] ->
    apply occ g;
    [ g ]
  | _ -> go groups []

(* ------------------------------------------------------------------ reservations *)

type resv = {
  occ : (string * int, int) Hashtbl.t;
  junc : (string * int, int) Hashtbl.t;
  seg : (string * int, int) Hashtbl.t;
  edge : (string * string * int, unit) Hashtbl.t;
  act : (string * int, int) Hashtbl.t;
  (* R22, reserved rather than repaired: cycle t -> the one waveform committed to it.
     Splitting a finished plan can only serialise what the router already decided, and on
     a full lattice the groups of a rotation are a dependency cycle -- whichever goes
     first overfills a trap, so no serialisation is legal and R1 fails.  Holding the
     signature in the table instead makes the slot uniform DURING the search: an ion
     whose hop plays a different waveform is simply not free to move this cycle, and A*
     does what it already does with any other blocked cycle, which is wait. *)
  wave : (int, string) Hashtbl.t;
  (* false on a `control.model = "direct"` device, where R22 does not apply and locking
     the slot would cost cycles to satisfy a rule nobody checks *)
  uniform : bool;
  horizon : int;
}

let bump tbl k n =
  Hashtbl.replace tbl k (n + try Hashtbl.find tbl k with Not_found -> 0)

let count tbl k = try Hashtbl.find tbl k with Not_found -> 0

let fresh ~uniform horizon =
  {
    occ = Hashtbl.create 256;
    junc = Hashtbl.create 256;
    seg = Hashtbl.create 256;
    edge = Hashtbl.create 256;
    act = Hashtbl.create 64;
    wave = Hashtbl.create 64;
    uniform;
    horizon;
  }

(* Can `ion` sit at `site` at time `t`?  Capacity is the R1 bound. *)
let site_free (a : Arch.t) (r : resv) site t =
  count r.occ (site, t) < Arch.eff_capacity a site

(* Can `ion` take `hop` departing at time `t` (arriving at `t+1`)? *)
let hop_free (a : Arch.t) (li : _) (c : sigctx) (r : resv) src (h : Traps.hop) t =
  site_free a r h.dst (t + 1)
  && ((not r.uniform)
     ||
     match Hashtbl.find_opt r.wave t with
     | None -> true
     | Some w -> String.equal w (hop_signature c src h))
  && List.for_all (fun j -> count r.junc (j, t) < 1) h.junctions
  && List.for_all
       (fun s ->
         let cap =
           match List.find_opt (fun (sg : Arch.segment) -> sg.sid = s) a.segments with
           | Some sg -> sg.seg_capacity
           | None -> 1
         in
         count r.seg (s, t) < cap)
       h.via
  && (not (Hashtbl.mem r.edge (h.dst, src, t)))
  &&
  match action_of li src h.dst with
  | None -> true
  | Some act -> (
    match Hashtbl.find_opt r.act (act.loop, t) with
    | None -> true
    | Some d -> d = act.delta)

let reserve_hop (li : _) (c : sigctx) (r : resv) src (h : Traps.hop) t =
  bump r.occ (h.dst, t + 1) 1;
  List.iter (fun j -> bump r.junc (j, t) 1) h.junctions;
  List.iter (fun s -> bump r.seg (s, t) 1) h.via;
  Hashtbl.replace r.edge (src, h.dst, t) ();
  (* the first ion to move in cycle t chooses its waveform; every later one matches it
     or waits, so the slot is uniform by the time it is emitted *)
  if r.uniform && not (Hashtbl.mem r.wave t) then
    Hashtbl.replace r.wave t (hop_signature c src h);
  match action_of li src h.dst with
  | None -> ()
  | Some act -> Hashtbl.replace r.act (act.loop, t) act.delta

(* An ion that is not moving occupies its trap for the whole layer. *)
let reserve_static (r : resv) site from_t =
  for t = from_t to r.horizon do
    bump r.occ (site, t) 1
  done

(* ------------------------------------------------------------------ space-time A* *)

type step = { at : string; t : int; hop : Traps.hop option; parent : int }

(* The open set, ordered exactly as the list it replaces was.

   The frontier used to be a list: O(n) to find the minimum, O(n) to remove it, and the
   node store grew by `Array.append`, which is O(n) per push.  All three are fine at a
   horizon of fifty and none of them is at four hundred, and locking one waveform per
   cycle is precisely what pushes the horizon there -- a layer that has to wait for its
   waveform explores the wait states of every trap.  The key is `(f, -index)` so that the
   minimum is the same state the list scan used to pick: lowest f, and among equal f the
   most recently pushed. *)
module Open = Set.Make (struct
  type t = int * int

  let compare = compare
end)

(* Returns the hops PAIRED WITH THE CYCLE THEY DEPART IN, and the arrival time.
   Returning a bare hop list would throw away every wait the reservation table just
   computed, and the caller would re-index the hops by list position -- which is exactly
   the bug that let two ions cross one segment in opposite directions in one cycle, in a
   router whose whole point is that it cannot. *)
let plan_one (a : Arch.t) (t : Traps.t) (d : Traps.dists) (li : _) (c : sigctx)
    (r : resv) ~(ion : string) ~(src : string) ~(goal : string) :
    ((int * Traps.hop) list * int) option =
  ignore ion;
  let h_of s = match Traps.dist d s goal with Some k -> k | None -> 1_000_000 in
  if h_of src >= 1_000_000 then None
  else begin
    let dummy = { at = ""; t = 0; hop = None; parent = -1 } in
    let store = ref (Array.make 256 dummy) in
    let n_nodes = ref 0 in
    let push s =
      let cap = Array.length !store in
      if !n_nodes = cap then begin
        let bigger = Array.make (2 * cap) dummy in
        Array.blit !store 0 bigger 0 cap;
        store := bigger
      end;
      !store.(!n_nodes) <- s;
      incr n_nodes
    in
    let nodes i = (!store).(i) in
    push { at = src; t = 0; hop = None; parent = -1 };
    let seen = Hashtbl.create 256 in
    Hashtbl.replace seen (src, 0) ();
    (* a priority queue over f = t + h; the state space is (traps × horizon) *)
    let frontier = ref (Open.singleton (h_of src, 0)) in
    let answer = ref None in
    while !answer = None && not (Open.is_empty !frontier) do
      let ((_, i) as top) = Open.min_elt !frontier in
      frontier := Open.remove top !frontier;
      let i = -i in
      let cur = nodes i in
      (* Arriving is not enough: the ion PARKS at its goal for the rest of the layer, so
         the goal must have room at every later cycle too.  Checking only the arrival
         instant lets an ion settle into a trap another ion is still going to transit --
         which is how three ions ended up in a capacity-2 trap while every individual
         check passed. *)
      let can_park () =
        let ok = ref true in
        for tt = cur.t to r.horizon do
          if not (site_free a r goal tt) then ok := false
        done;
        !ok
      in
      if cur.at = goal && can_park () then answer := Some i
      else if cur.t < r.horizon then begin
        (* wait *)
        if site_free a r cur.at (cur.t + 1) && not (Hashtbl.mem seen (cur.at, cur.t + 1))
        then begin
          Hashtbl.replace seen (cur.at, cur.t + 1) ();
          push { at = cur.at; t = cur.t + 1; hop = None; parent = i };
          frontier :=
            Open.add (cur.t + 1 + h_of cur.at, -(!n_nodes - 1)) !frontier
        end;
        (* hop *)
        List.iter
          (fun (h : Traps.hop) ->
            if
              (not (Hashtbl.mem seen (h.dst, cur.t + 1)))
              && hop_free a li c r cur.at h cur.t
            then begin
              Hashtbl.replace seen (h.dst, cur.t + 1) ();
              push { at = h.dst; t = cur.t + 1; hop = Some h; parent = i };
              frontier := Open.add (cur.t + 1 + h_of h.dst, -(!n_nodes - 1)) !frontier
            end)
          (Traps.neighbours t cur.at)
      end
    done;
    match !answer with
    | None ->
      if Sys.getenv_opt "QCCDC_DEBUG" <> None then begin
        Printf.eprintf "  [route] %s %s->%s: explored %d states, horizon %d
" ion src
          goal !n_nodes r.horizon;
        let cap = Arch.eff_capacity a goal in
        for tt = 0 to min 6 r.horizon do
          Printf.eprintf "    t=%d occ(%s)=%d/%d act=%s
" tt goal (count r.occ (goal, tt))
            cap
            (String.concat ","
               (Hashtbl.fold
                  (fun (l, t2) dd acc ->
                    if t2 = tt then Printf.sprintf "%s:%+d" l dd :: acc else acc)
                  r.act []))
        done
      end;
      None
    | Some i ->
      let rec unwind j acc =
        let n = nodes j in
        if n.parent < 0 then acc
        else unwind n.parent ((n.t - 1, n.hop) :: acc)
      in
      let steps = unwind i [] in
      (* Commit what the path consumes, walking it forward so the departure trap of each
         hop is known exactly rather than reconstructed.  Waiting reserves occupancy too:
         an ion parked mid-route still fills a slot in its trap, and forgetting that is
         how a router produces a schedule that R1 rejects. *)
      let cur = ref src in
      List.iter
        (fun (t0, hop) ->
          match hop with
          | None -> bump r.occ (!cur, t0 + 1) 1
          | Some (h : Traps.hop) ->
            reserve_hop li c r !cur h t0;
            cur := h.dst)
        steps;
      let arrive = (nodes i).t in
      (* the final hop already reserved the arrival cycle, so parking starts after it;
         reserving from `arrive` would double-count the ion in its own trap *)
      reserve_static r goal (if steps = [] then arrive else arrive + 1);
      Some
        ( List.filter_map
            (fun (t0, h) -> match h with Some hh -> Some (t0, hh) | None -> None)
            steps,
          arrive )
  end

(* ------------------------------------------------------------------ a layer *)

(* `targets` gives, for each ion that must move, where it must end up.  Every other ion
   stands still and is an obstacle.  Returns the cycles, in order. *)
let plan_with (a : Arch.t) (t : Traps.t) (d : Traps.dists) ~(pos : (string, string) Hashtbl.t)
    ~(targets : (string * string) list) ~(horizon : int) ~(ordered : (string * string) list)
    ~(uniform : bool) ~(strict : bool) : layer_plan =
  let li = loop_index a in
  let ctx = sigctx a in
  let r = fresh ~uniform horizon in
  let moving = List.map fst targets in
  Hashtbl.iter
    (fun ion site -> if not (List.mem ion moving) then reserve_static r site 0)
    pos;
  (* the movers occupy their start at t=0 *)
  List.iter (fun (ion, _) -> bump r.occ (Hashtbl.find pos ion, 0) 1) targets;

  let per_ion = Hashtbl.create 16 in
  List.iter
    (fun (ion, goal) ->
      let src = Hashtbl.find pos ion in
      if src = goal then begin
        (* An operand ALREADY at the meeting trap still occupies it.  It is in `targets`,
           so the static loop skipped it, and with no path to commit it reserved nothing
           -- leaving its trap invisible and letting later ions pile in past capacity.
           That is how three ions ended up in a capacity-2 trap. *)
        reserve_static r goal 0;
        Hashtbl.replace per_ion ion ([], 0)
      end
      else
        match plan_one a t d li ctx r ~ion ~src ~goal with
        | Some (hops, arrive) ->
          Hashtbl.replace per_ion ion (hops, arrive)
        | None ->
          raise
            (Unroutable
               (Printf.sprintf "%s cannot reach %s from %s within %d cycles" ion goal src
                  horizon)))
    ordered;

  (* Lay the hops out at THEIR OWN cycle.  A hop departing at layer-local time t0 goes in
     slot t0 -- never at its position in the ion's hop list, which is a different number
     the moment the ion waits for anything. *)
  let span = Hashtbl.fold (fun _ (_, arrive) acc -> max acc arrive) per_ion 0 in
  let cycles = Array.make (max span 0) [] in
  Hashtbl.iter
    (fun ion (hops, _) ->
      let at = ref (Hashtbl.find pos ion) in
      List.iter
        (fun (t0, (h : Traps.hop)) ->
          cycles.(t0) <- { ion; src = !at; dst = h.dst; via = h.via } :: cycles.(t0);
          at := h.dst)
        hops)
    per_ion;
  let arrivals =
    Hashtbl.fold
      (fun ion (hops, _) acc ->
        let final =
          List.fold_left (fun _ (_, (h : Traps.hop)) -> h.dst) (Hashtbl.find pos ion) hops
        in
        (ion, final) :: acc)
      per_ion []
  in
  let slots =
    Array.to_list cycles |> List.map List.rev |> List.filter (fun m -> m <> [])
  in
  (* One cycle is one waveform (R22).  Whether the SPLIT runs is a property of the
     DEVICE -- only a `control.model = "direct"` machine may play two waveforms at once
     -- and not of `r.uniform`, which says merely whether the search had the lock on.
     Tying the two together is how the lock-off rung of `plan_layer` once emitted mixed
     slots and failed R22 outright. *)
  let out =
    if control_model a = "direct" then List.map (fun ms -> { moves = ms }) slots
    else begin
      let occ = Hashtbl.create 64 in
      Hashtbl.iter
        (fun _ site ->
          Hashtbl.replace occ site
            (1 + (try Hashtbl.find occ site with Not_found -> 0)))
        pos;
      let acc = ref [] in
      List.iter
        (fun ms ->
          List.iter
            (fun g -> acc := { moves = g } :: !acc)
            (split_slot a ctx ~cls:shuttle_cls ~occ ms))
        slots;
      List.rev !acc
    end
  in
  (* Replay the plan and check occupancy against R1 before handing it back.
     The reservation table is meant to guarantee this, but a table and the plan derived
     from it are two different objects, and only one of them is what the machine runs.
     Checking the plan itself is what turns a reservation bug from a rule failure three
     stages downstream into an exception here, naming the trap.

     It replays the EMITTED cycles, not the time slots.  A slot that only simultaneity
     could execute -- four groups rotating through full traps, whichever goes first
     overfills the next -- is legal as a slot and illegal as the cycles it is emitted as,
     and that residue is exactly what the post-hoc pass shipped and R1 then caught.
     Failing here instead hands the layer back to `plan_layer`, which has three more
     priority orders and a bigger horizon to try. *)
  let live = Hashtbl.copy pos in
  List.iteri
    (fun k (cy : cycle) ->
      List.iter (fun (m : move) -> Hashtbl.replace live m.ion m.dst) cy.moves;
      let occ = Hashtbl.create 32 in
      Hashtbl.iter
        (fun _ site ->
          Hashtbl.replace occ site (1 + (try Hashtbl.find occ site with Not_found -> 0)))
        live;
      Hashtbl.iter
        (fun site n ->
          let cap = Arch.eff_capacity a site in
          if n > cap then
            raise
              (Unroutable
                 (Printf.sprintf
                    "plan is illegal: after layer-cycle %d, %s holds %d ions (capacity                      %d): %s [targets: %s]"
                    k site n cap
                    (String.concat ","
                       (Hashtbl.fold
                          (fun i s2 acc -> if s2 = site then i :: acc else acc)
                          live []))
                    (String.concat ","
                       (List.map (fun (i, g) -> i ^ "->" ^ g) targets)))))
        occ)
    (if strict then out else List.map (fun ms -> { moves = ms }) slots);
  { cycles = out; arrivals; slots = List.length slots }

(* ------------------------------------------------------------------ instances
 *
 * A routing sub-problem, serialised for the SAT oracle.
 *
 * The graph travels WITH the instance rather than being rebuilt on the other side.  That
 * is the whole point: an optimality gap measured against a solver that reconstructed the
 * trap graph slightly differently is not a gap, it is two different problems.  So the
 * hops, the capacities, the junctions each hop crosses and the R4d action signature of
 * every directed hop are all written out explicitly, and the solver reads what the router
 * actually used. *)

let instance_json (a : Arch.t) (t : Traps.t) ~(pos : (string, string) Hashtbl.t)
    ~(targets : (string * string) list) ~(horizon : int) ~(heuristic : int) : Yojson.Safe.t
    =
  let li = loop_index a in
  let hops =
    List.concat_map
      (fun src ->
        List.map
          (fun (h : Traps.hop) ->
            let act =
              match action_of li src h.dst with
              | None -> `Null
              | Some x -> `List [ `String x.loop; `Int x.delta ]
            in
            `Assoc
              [
                ("from", `String src);
                ("to", `String h.dst);
                ("via", `List (List.map (fun v -> `String v) h.via));
                ("junctions", `List (List.map (fun j -> `String j) h.junctions));
                ("action", act);
              ])
          (Traps.neighbours t src))
      t.sites
  in
  `Assoc
    [
      ("arch", `String a.name);
      ( "capacity",
        `Assoc (List.map (fun s -> (s, `Int (Arch.eff_capacity a s))) t.sites) );
      ( "segment_capacity",
        `Assoc
          (List.map (fun (sg : Arch.segment) -> (sg.sid, `Int sg.seg_capacity)) a.segments)
      );
      ("hops", `List hops);
      ("start", `Assoc (Hashtbl.fold (fun i s acc -> (i, `String s) :: acc) pos []));
      ("targets", `Assoc (List.map (fun (i, g) -> (i, `String g)) targets));
      ("horizon", `Int horizon);
      ("heuristic_makespan", `Int heuristic);
    ]

(* Priority order is not a detail on a closed loop.

   Prioritised planning routes ions one at a time, and an ion that reaches its goal PARKS
   there for the rest of the layer.  Park it on the short arc between another ion and its
   goal and that ion has to go the long way round -- on `h2_racetrack` the SAT oracle
   caught exactly this: 43 cycles where 5 suffice, because one order happened to block
   the other ion.  No single order avoids it, so several are tried and the best kept.

   The lower bound is what makes this cheap: the moment a plan matches it, it is optimal
   and the rest of the orders are not attempted.

   What is compared is the number of EMITTED cycles, after the uniformity split -- one
   waveform each -- and not the number of time slots.  Those are the cycles the machine
   runs, so an order that needs one more slot but leaves every slot uniform beats an
   order that packs four directions into three.  It also means the early exit almost
   never fires on a lattice, where a slot rarely holds one waveform; all four orders are
   tried, and the best is the one with the fewest waveforms. *)
let plan_orders (a : Arch.t) (t : Traps.t) (d : Traps.dists) ~(pos : (string, string) Hashtbl.t)
    ~(targets : (string * string) list) ~(horizon : int) ~(uniform : bool) ~(strict : bool)
    : layer_plan =
  let reach (i, g) =
    match Traps.dist d (Hashtbl.find pos i) g with Some x -> x | None -> 0
  in
  let lower = List.fold_left (fun acc tg -> max acc (reach tg)) 0 targets in
  let by cmp = List.sort cmp targets in
  let orders =
    [
      by (fun x y -> compare (reach y) (reach x));  (* furthest first *)
      by (fun x y -> compare (reach x) (reach y));  (* nearest first *)
      targets;                                      (* as the gates named them *)
      List.rev targets;
    ]
  in
  let best = ref None in
  let last_err = ref None in
  (try
     List.iter
       (fun ordered ->
         match plan_with a t d ~pos ~targets ~horizon ~ordered ~uniform ~strict with
         | p ->
           let n = List.length p.cycles in
           (match !best with
           | Some (bn, _) when bn <= n -> ()
           | _ -> best := Some (n, p));
           if n <= lower then raise Exit
         | exception Unroutable m -> last_err := Some m)
       orders
   with Exit -> ());
  match !best with
  | Some (_, p) -> p
  | None -> (
    match !last_err with
    | Some m -> raise (Unroutable m)
    | None -> raise (Unroutable "no priority order produced a plan"))

(* Three ways to plan a layer, hardest constraint first.

   Locking one waveform per cycle costs cycles -- an ion whose hop plays a different
   waveform from the one this slot already carries waits for the next -- and on a lattice
   carrying 216 ions it can cost a layer its plan outright: with the movers planned one
   at a time, the early cycles fill up with other ions' waveforms and a late ion runs out
   of horizon before it runs out of road.  That is a real limit of prioritised planning,
   not of the rule, and the answer is to fall back rather than to fail:

     1. the waveform lock on, with the horizon doubled once if the layer needs it
     2. the lock off, so the layer is scheduled by conflict as it always was, and
        `split_slot` uniformises the slots afterwards -- STRICT, so a slot whose groups
        cannot be ordered legally is rejected here and another priority order is tried
     3. the lock off and the residue tolerated, which is what this router did before R22

   Rungs 1 and 2 both guarantee R1 on the emitted cycles, because `plan_with ~strict`
   replays them.  Rung 3 exists so that a layer no arrangement can make both uniform and
   legal still produces a programme -- reported by the verifier rather than lost.

   The horizon is raised ON DEMAND rather than set high to begin with: the A* state space
   is (traps x horizon), so a horizon sized for the worst layer would be paid by every
   easy one. *)
let plan_layer (a : Arch.t) (t : Traps.t) (d : Traps.dists) ~(pos : (string, string) Hashtbl.t)
    ~(targets : (string * string) list) ~(horizon : int) : layer_plan =
  let try_rung ~uniform ~strict ~doublings =
    let rec go h tries =
      match plan_orders a t d ~pos ~targets ~horizon:h ~uniform ~strict with
      | p -> p
      | exception Unroutable m ->
        if tries <= 0 then raise (Unroutable m) else go (2 * h) (tries - 1)
    in
    go horizon doublings
  in
  let fell_back rung m =
    if Sys.getenv_opt "QCCDC_DEBUG" <> None then
      Printf.eprintf "  [route] layer falls back to rung %d (%d movers): %s
" rung
        (List.length targets) m
  in
  if control_model a = "direct" then
    try_rung ~uniform:false ~strict:false ~doublings:0
  else
    match try_rung ~uniform:true ~strict:true ~doublings:1 with
    | p -> p
    | exception Unroutable m1 -> (
      fell_back 2 m1;
      match try_rung ~uniform:false ~strict:true ~doublings:1 with
      | p -> p
      | exception Unroutable m2 ->
        fell_back 3 m2;
        try_rung ~uniform:false ~strict:false ~doublings:0)
