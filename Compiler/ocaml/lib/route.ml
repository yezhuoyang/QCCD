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

(* ------------------------------------------------------------------ reservations *)

type resv = {
  occ : (string * int, int) Hashtbl.t;
  junc : (string * int, int) Hashtbl.t;
  seg : (string * int, int) Hashtbl.t;
  edge : (string * string * int, unit) Hashtbl.t;
  act : (string * int, int) Hashtbl.t;
  horizon : int;
}

let bump tbl k n =
  Hashtbl.replace tbl k (n + try Hashtbl.find tbl k with Not_found -> 0)

let count tbl k = try Hashtbl.find tbl k with Not_found -> 0

let fresh horizon =
  {
    occ = Hashtbl.create 256;
    junc = Hashtbl.create 256;
    seg = Hashtbl.create 256;
    edge = Hashtbl.create 256;
    act = Hashtbl.create 64;
    horizon;
  }

(* Can `ion` sit at `site` at time `t`?  Capacity is the R1 bound. *)
let site_free (a : Arch.t) (r : resv) site t =
  count r.occ (site, t) < Arch.eff_capacity a site

(* Can `ion` take `hop` departing at time `t` (arriving at `t+1`)? *)
let hop_free (a : Arch.t) (li : _) (r : resv) src (h : Traps.hop) t =
  site_free a r h.dst (t + 1)
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

let reserve_hop (li : _) (r : resv) src (h : Traps.hop) t =
  bump r.occ (h.dst, t + 1) 1;
  List.iter (fun j -> bump r.junc (j, t) 1) h.junctions;
  List.iter (fun s -> bump r.seg (s, t) 1) h.via;
  Hashtbl.replace r.edge (src, h.dst, t) ();
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

(* Returns the hops PAIRED WITH THE CYCLE THEY DEPART IN, and the arrival time.
   Returning a bare hop list would throw away every wait the reservation table just
   computed, and the caller would re-index the hops by list position -- which is exactly
   the bug that let two ions cross one segment in opposite directions in one cycle, in a
   router whose whole point is that it cannot. *)
let plan_one (a : Arch.t) (t : Traps.t) (d : Traps.dists) (li : _) (r : resv)
    ~(ion : string) ~(src : string) ~(goal : string) :
    ((int * Traps.hop) list * int) option =
  ignore ion;
  let h_of s = match Traps.dist d s goal with Some k -> k | None -> 1_000_000 in
  if h_of src >= 1_000_000 then None
  else begin
    let nodes = ref [| |] in
    let push s = nodes := Array.append !nodes [| s |] in
    push { at = src; t = 0; hop = None; parent = -1 };
    let seen = Hashtbl.create 256 in
    Hashtbl.replace seen (src, 0) ();
    (* a plain priority queue over f = t + h; the state space is (traps × horizon), so a
       list-based frontier is fine at these sizes and avoids a dependency *)
    let frontier = ref [ (h_of src, 0) ] in
    let answer = ref None in
    while !answer = None && !frontier <> [] do
      let (_, i) =
        List.fold_left
          (fun best (f, i) ->
            match best with
            | (bf, _) when f < bf -> (f, i)
            | keep -> keep)
          (max_int, -1) !frontier
      in
      frontier := List.filter (fun (_, j) -> j <> i) !frontier;
      let cur = (!nodes).(i) in
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
          frontier := (cur.t + 1 + h_of cur.at, Array.length !nodes - 1) :: !frontier
        end;
        (* hop *)
        List.iter
          (fun (h : Traps.hop) ->
            if
              (not (Hashtbl.mem seen (h.dst, cur.t + 1)))
              && hop_free a li r cur.at h cur.t
            then begin
              Hashtbl.replace seen (h.dst, cur.t + 1) ();
              push { at = h.dst; t = cur.t + 1; hop = Some h; parent = i };
              frontier := (cur.t + 1 + h_of h.dst, Array.length !nodes - 1) :: !frontier
            end)
          (Traps.neighbours t cur.at)
      end
    done;
    match !answer with
    | None ->
      if Sys.getenv_opt "QCCDC_DEBUG" <> None then begin
        Printf.eprintf "  [route] %s %s->%s: explored %d states, horizon %d
" ion src
          goal (Array.length !nodes) r.horizon;
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
        let n = (!nodes).(j) in
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
            reserve_hop li r !cur h t0;
            cur := h.dst)
        steps;
      let arrive = (!nodes).(i).t in
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
    : layer_plan =
  let li = loop_index a in
  let r = fresh horizon in
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
        match plan_one a t d li r ~ion ~src ~goal with
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
  let out =
    Array.to_list cycles
    |> List.filter (fun m -> m <> [])
    |> List.map (fun m -> { moves = List.rev m })
  in
  (* Replay the plan and check occupancy against R1 before handing it back.
     The reservation table is meant to guarantee this, but a table and the plan derived
     from it are two different objects, and only one of them is what the machine runs.
     Checking the plan itself is what turns a reservation bug from a rule failure three
     stages downstream into an exception here, naming the trap. *)
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
    out;
  { cycles = out; arrivals }

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
   and the rest of the orders are not attempted. *)
let plan_layer (a : Arch.t) (t : Traps.t) (d : Traps.dists) ~(pos : (string, string) Hashtbl.t)
    ~(targets : (string * string) list) ~(horizon : int) : layer_plan =
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
         match plan_with a t d ~pos ~targets ~horizon ~ordered with
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
