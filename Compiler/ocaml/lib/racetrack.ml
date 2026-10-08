(* Compiling on a race track: one closed loop, its gate zones ON the loop, and no docks.

   Quantinuum's H2 (Moses et al., PRX 13, 041052, 2023; `Reproduce/moses2023`) is such a
   machine: one RF null closed on itself, four gate zones and their auxiliary zones on the
   bottom straight, four sorting zones on the top one, and two conveyors of forty wells that
   three tied signals drive with no per-site switch.  Neither existing pass can compile on
   it.  The general router moves ions one route at a time, and on a single lane that is full
   past a third (32 qubits, 37 places an ion may stop) the ion that must reach a gate zone
   finds every route blocked by ions at rest.  The rotate pipeline turns the loop rigidly,
   but it gates at DOCKS: the loop carries the riders and every contact is a rider beside a
   parked partner.  On H2 both operands of every gate ride the loop, and a rigid rotation
   never changes the order of two riders, so they could never meet.

   {1 What H2 does, and what this pass does}

   H2's own compiler sorts the qubits with "a parallel bubble sort routine that allows qubits
   to move in both directions around the device", runs the gates "on each batch of ions,
   starting with the qubits already in the DG zones", and moves on with a batch shift
   (Sec. II.E).  This pass is that, in this language's rules:

     * ROTATE.  A rigid shift of every ion on the loop -- one `loop_shift` template, one
       class (`batch_shift_*`, the loop's own ±1 classes), one direction, one waveform -- so
       R4 (every loaded well on the tied conveyor channel moves, the same way), R11 (one
       direction per loop per cycle) and R22 (one signature) hold by construction.  It is
       the only way an ion ever enters, leaves or crosses a tied conveyor.  A shift is legal
       only with every ion alone in its site (R3: one ion per rail per cycle; R1: a
       capacity-1 well cannot take a pair), so between steps every ion is ALONE: the
       "rest state" this pass keeps.

     * REORDER LOCALLY.  Order changes only where two ions may share a site: a capacity-2
       zone whose electrodes are independently driven (the auxiliary, DG and UG zones).
       Two neighbours swap there in two cycles -- one steps in beside the other, then the
       other steps out the far side (H2's physical swap: a combine, then a split) -- and an
       ion beside an empty site just steps into it.  Every such cycle moves one ion, along
       the loop, one site: one class, one direction.  Nothing on a tied site ever moves
       alone (R4), because both sites of a swap must be independently driven.

     * GATE.  A round takes up to four ready gates (as many as the gate zones allow in one
       uniform step), gathers their eight ions into one contiguous block in pair order --
       each ion carried along the sequence of ions and empty sites by swaps, the loop
       rotated whenever the swap it needs is not inside a sorting window -- rotates the
       block onto the gate row, and merges every pair into its gate zone in ONE cycle (all
       movers step the same way).  The gates' pulses run zone-parallel, one pulse per zone
       per instruction (R12), and every MS of the round is ONE instruction: that is a
       round, in the sense of H2's Table I.  One cycle splits the pairs again.

   Single-qubit gates and read-outs happen in a gate zone too.  The ones before a qubit's
   next two-qubit gate run in the zone just before its MS; the ones after it run there right
   after (and the read-out, where the zone can measure).  Anything else -- a read-out behind
   a barrier, a gate on a qubit no pair touches -- is SERVICED: the loop turns until the ion
   stands on a capable site.

   {1 Which gates, and in what order}

   A barrier is a fence (`Compile.layer_of`): nothing after it on its wires runs before
   everything ahead of it has.  A two-qubit gate is eligible when everything before it on
   both its qubits is done, or is a single-qubit gate that can run in the zone first.  A
   round takes the eligible gates on the longest remaining chains of two-qubit gates (list
   scheduling, highest level first), and breaks a tie by which pair is cheaper to gather.

   {1 What is certified}

   Exactly what the program does: every swap and step is a move witness one site long (an
   ordered pair of neighbouring sites, which `mk_qcheck_input.py` derives from the segment
   list), every shift a rotation witness whose node order the checker reads from the
   architecture, every gate a witness at its zone.  Nothing here asks the checker for
   anything new.

   {1 Where it applies}

   A device that IS one closed loop: every node a site on it, every rail joining two
   neighbours on it.  Anything else -- a spur, a junction, a second loop -- is declined, and
   the caller's answer stands. *)

exception Not_applicable of string

let na fmt = Printf.ksprintf (fun s -> raise (Not_applicable s)) fmt

type stats = {
  rounds : int;          (* MS instructions: two-qubit gate rounds *)
  gates2 : int;          (* two-qubit gates realised *)
  rotations : int;       (* rotation instructions *)
  rot_hops : int;        (* unit shifts they total *)
  local_moves : int;     (* one-ion steps (swaps count two) *)
  swaps : int;
  services : int;        (* shifts made only to bring an ion to a capable site *)
}

(* ------------------------------------------------------------------ the device *)

type dev = {
  a : Arch.t;
  lid : string;
  nodes : string array;       (* the loop, in its declared order *)
  n : int;
  cap : int array;            (* `Arch.eff_capacity` *)
  free : bool array;          (* driven independently: not tied to other sites (R4) *)
  gate : bool array;
  spam : bool array;
  seg : string array;         (* seg.(i) joins nodes.(i) and nodes.(i+1) *)
  fwd_cls : string;           (* the class of a +1 shift along the loop *)
  bwd_cls : string;
}

let md n x = ((x mod n) + n) mod n

(* the signed shift of least magnitude that takes `from_` to `to_` on a loop of `n` *)
let shortest n from_ to_ =
  let d = md n (to_ - from_) in
  if d <= n - d then d else d - n

let detect (a : Arch.t) : dev =
  let closed = List.filter (fun (l : Arch.loop) -> l.closed) a.loops in
  let l =
    match closed with
    | [] -> na "the device has no closed loop"
    | [ l ] -> l
    | ls -> na "the device has %d closed loops; this pass drives exactly one" (List.length ls)
  in
  let nodes = Array.of_list l.nodes in
  let n = Array.length nodes in
  if n < 4 then na "loop %s has %d nodes" l.lid n;
  let idx = Hashtbl.create n in
  Array.iteri
    (fun i s ->
      if Hashtbl.mem idx s then na "loop %s visits %s twice" l.lid s;
      Hashtbl.replace idx s i)
    nodes;
  List.iter
    (fun id ->
      match Arch.node a id with
      | Some nd when nd.kind <> "site" ->
        na "node %s is a %s: the device is not a single loop of sites" id nd.kind
      | _ ->
        if not (Hashtbl.mem idx id) then
          na "site %s is off loop %s (a dock or a spur): the device is not a single loop" id
            l.lid)
    a.node_order;
  if List.length a.node_order <> n then na "the device is not exactly loop %s" l.lid;
  let seg = Array.make n "" in
  List.iter
    (fun (s : Arch.segment) ->
      match (Hashtbl.find_opt idx s.a, Hashtbl.find_opt idx s.b) with
      | Some i, Some j when md n (j - i) = 1 && seg.(i) = "" -> seg.(i) <- s.sid
      | Some i, Some j when md n (i - j) = 1 && seg.(j) = "" -> seg.(j) <- s.sid
      | _ -> na "rail %s does not join two neighbours of loop %s" s.sid l.lid)
    a.segments;
  Array.iteri
    (fun i s -> if s = "" then na "no rail joins %s and %s" nodes.(i) nodes.(md n (i + 1)))
    seg;
  List.iter
    (fun (s : Arch.segment) ->
      if s.seg_capacity < 1 then na "rail %s carries no ion" s.sid)
    a.segments;
  let tied = Route.tied_of a in
  let cap = Array.map (fun s -> Arch.eff_capacity a s) nodes in
  if Array.exists (fun c -> c < 1) cap then na "a site of loop %s holds no ion" l.lid;
  let nd s = match Arch.node a s with Some x -> x | None -> assert false in
  {
    a;
    lid = l.lid;
    nodes;
    n;
    cap;
    free = Array.map (fun s -> not (Hashtbl.mem tied s)) nodes;
    gate = Array.map (fun s -> (nd s).can_gate) nodes;
    spam = Array.map (fun s -> (nd s).can_spam) nodes;
    seg;
    fwd_cls =
      Conveyor.class_named a ~orbit:l.lid ~delta:(Some 1) ~direction:None ~fallback:"shuttle";
    bwd_cls =
      Conveyor.class_named a ~orbit:l.lid ~delta:(Some (-1)) ~direction:None
        ~fallback:"shuttle";
  }

(* Two neighbours, `s` and `s+dir`, can exchange their ions in place: both sites are
   independently driven (a tied site's ion never moves alone, R4) and one of them can hold
   both ions for the cycle in between (R1).  A property of the SITES alone, so a rotation
   can be chosen to bring a swap into such a window before its ions are looked at. *)
let can_swap (d : dev) s dir =
  let s2 = md d.n (s + dir) in
  d.free.(s) && d.free.(s2) && (d.cap.(s) >= 2 || d.cap.(s2) >= 2)

(* how many swaps in a row an ion standing at `s` can make in direction `dir` *)
let run_len (d : dev) s dir =
  let rec go k cur = if k >= d.n || not (can_swap d cur dir) then k else go (k + 1) (md d.n (cur + dir)) in
  go 0 s

(* The ion at `mover` can step `dir` into a gate site and share it with the ion there. *)
let merge_ok (d : dev) mover dir =
  let h = md d.n (mover + dir) in
  d.free.(mover) && d.free.(h) && d.gate.(h) && d.cap.(h) >= 2

(* A gate row for `k` pairs: 2k neighbouring sites from `b`, pair j on (b+2j, b+2j+1), the
   mover of each pair stepping `dir` into the gate site beside it -- the same way for every
   pair, so the merge is one cycle with one waveform. *)
let layout_ok (d : dev) b dir k =
  2 * k <= d.n
  && List.for_all
       (fun j -> merge_ok d (md d.n (if dir > 0 then b + (2 * j) else b + (2 * j) + 1)) dir)
       (List.init k (fun j -> j))

let max_pairs (d : dev) =
  let best = ref 0 in
  for b = 0 to d.n - 1 do
    List.iter
      (fun dir ->
        let k = ref (!best + 1) in
        while layout_ok d b dir !k do
          best := !k;
          incr k
        done)
      [ 1; -1 ]
  done;
  !best

(* ------------------------------------------------------------------ the circuit *)

type kind = Barrier | One | Two | Meas | Reset

type item =
  | IBeam of int * float * float * int        (* qubit, theta, phi, op *)
  | IFrame of int * float * int               (* qubit, lambda, op *)
  | IMs of int * int * float * int            (* qubits a b, theta, op *)
  | IMeas of int * int                        (* qubit, op *)
  | IReset of int * int

let item_op = function
  | IBeam (_, _, _, o) | IFrame (_, _, o) | IMs (_, _, _, o) | IMeas (_, o) | IReset (_, o) -> o

let run ?init (a : Arch.t) (c : Circuit.t) ~(arch_path : string) ~(qasm_path : string) :
    Tsir.t * Cert.t * stats * string list =
  let d = detect a in
  let n = d.n in
  let k_max = max_pairs d in
  if k_max = 0 then
    na "no gate site on loop %s that an ion beside it can step into to share" d.lid;
  let c, n_lowered = Circuit.lower c in
  let nq = c.n_qubits in
  if nq > n then
    na "%d qubits but loop %s has %d sites, and a rigid shift needs every ion alone in its site"
      nq d.lid n;
  let ops = Array.of_list c.ops in
  let nops = Array.length ops in
  let kind =
    Array.map
      (fun (o : Circuit.op) ->
        if o.cond <> None then na "op %d (%s) is classically conditioned" o.index o.name;
        match (o.name, o.qubits) with
        | "barrier", _ -> Barrier
        | "measure", [ _ ] -> Meas
        | "reset", [ _ ] -> Reset
        | ("measure" | "reset"), _ -> na "op %d: %s on %d qubits" o.index o.name (List.length o.qubits)
        | _, [ _ ] -> One
        | _, [ _; _ ] -> Two
        | _, qs -> na "op %d (%s) acts on %d qubits" o.index o.name (List.length qs))
      ops
  in
  (* every pulse sequence up front: a gate this pass cannot decompose is a refusal now, not
     a hole in the programme later *)
  let pulses =
    Array.mapi
      (fun i (o : Circuit.op) ->
        match kind.(i) with
        | One | Two -> (
          match
            Gateset_composites.decompose_op ~gates:c.gates o.name o.params
              (List.mapi (fun k _ -> k) o.qubits)
          with
          | dc ->
            if dc.pulses = [] then na "op %d (%s) has no pulses to witness it" i o.name;
            dc.pulses
          | exception Gateset.Unsupported m -> na "op %d (%s): %s" i o.name m)
        | _ -> [])
      ops
  in
  if Array.exists (fun k -> k = Meas || k = Reset) kind && not (Array.exists (fun x -> x) d.spam)
  then na "the circuit reads out and no site of loop %s can" d.lid;
  if Array.exists (fun k -> k = One) kind && not (Array.exists (fun x -> x) d.gate) then
    na "no site of loop %s can gate" d.lid;

  (* ---- the DAG: per-wire order, as `Circuit.wire_sequences` builds it ---- *)
  let wires = Array.map (fun o -> Circuit.wires_of c o) ops in
  let preds = Array.make nops [] in
  let succs = Array.make nops [] in
  (* previous / next op on each QUBIT wire of an op *)
  let prev_q : (int * int, int) Hashtbl.t = Hashtbl.create (2 * nops) in
  let next_q : (int * int, int) Hashtbl.t = Hashtbl.create (2 * nops) in
  let last = Hashtbl.create 64 in
  Array.iteri
    (fun i ws ->
      List.iter
        (fun w ->
          (match Hashtbl.find_opt last w with
          | Some p ->
            if not (List.mem p preds.(i)) then preds.(i) <- p :: preds.(i);
            if not (List.mem i succs.(p)) then succs.(p) <- i :: succs.(p);
            if w < nq then begin
              Hashtbl.replace prev_q (i, w) p;
              Hashtbl.replace next_q (p, w) i
            end
          | None -> ());
          Hashtbl.replace last w i)
        ws)
    wires;
  (* the level of an op: the most two-qubit gates on any chain from it to the end *)
  let level = Array.make nops 0 in
  for i = nops - 1 downto 0 do
    let below = List.fold_left (fun acc s -> max acc level.(s)) 0 succs.(i) in
    level.(i) <- below + (if kind.(i) = Two then 1 else 0)
  done;

  let done_ = Array.make nops false in
  let remaining = ref nops in
  let is_ready i = List.for_all (fun p -> done_.(p)) preds.(i) in
  (* first op on each qubit wire not yet done *)
  let wire_ops = Array.make nq [] in
  Array.iteri
    (fun i (o : Circuit.op) -> List.iter (fun q -> wire_ops.(q) <- i :: wire_ops.(q)) o.qubits)
    ops;
  let wire_ops = Array.map (fun l -> Array.of_list (List.rev l)) wire_ops in
  let front = Array.make nq 0 in
  let front_op q =
    let w = wire_ops.(q) in
    while front.(q) < Array.length w && done_.(w.(front.(q))) do
      front.(q) <- front.(q) + 1
    done;
    if front.(q) < Array.length w then Some w.(front.(q)) else None
  in

  (* ---- state ---- *)
  let pos = Array.make nq 0 in            (* qubit -> site index *)
  let at = Array.make n [] in             (* site -> qubits there *)
  let ion q = Place.ion_name q in
  let placement_note =
    match init with
    | Some init ->
      let seen = Hashtbl.create nq in
      List.iter
        (fun (name, site) ->
          let q =
            match
              if String.length name > 1 && name.[0] = 'q' then
                int_of_string_opt (String.sub name 1 (String.length name - 1))
              else None
            with
            | Some q when q >= 0 && q < nq && ion q = name -> q
            | _ -> na "--init-placement names %s, which is not an ion of this circuit" name
          in
          let s =
            match
              Array.to_list (Array.mapi (fun i x -> (i, x)) d.nodes)
              |> List.find_opt (fun (_, x) -> x = site)
            with
            | Some (i, _) -> i
            | None -> na "--init-placement puts %s at %s, which is not on loop %s" name site d.lid
          in
          if at.(s) <> [] then
            na "--init-placement puts two ions at %s; a rigid shift needs every ion alone" site;
          Hashtbl.replace seen q ();
          pos.(q) <- s;
          at.(s) <- [ q ])
        init;
      if Hashtbl.length seen <> nq then na "--init-placement does not place every ion";
      Printf.sprintf "placement: %d ions placed by --init-placement" nq
    | None ->
      (* Every ion alone, side by side, in the order the two-qubit gates first meet them --
         partners next to each other -- starting where a full gate row starts, so the first
         round has least to gather. *)
      let order = ref [] and seen = Hashtbl.create nq in
      let take q = if not (Hashtbl.mem seen q) then (Hashtbl.replace seen q (); order := q :: !order) in
      Array.iteri (fun i (o : Circuit.op) -> if kind.(i) = Two then List.iter take o.qubits) ops;
      for q = 0 to nq - 1 do take q done;
      let start =
        let rec find b = if b >= n then 0 else if layout_ok d b 1 k_max then b else find (b + 1) in
        find 0
      in
      List.iteri
        (fun k q ->
          let s = md n (start + k) in
          pos.(q) <- s;
          at.(s) <- [ q ])
        (List.rev !order);
      Printf.sprintf "placement: %d ions side by side from %s, in the order the gates meet them"
        nq d.nodes.(start)
  in

  (* ---- emission ---- *)
  let instrs = ref [] and nid = ref 0 and cyc = ref 1 in
  let fresh () = let i = !nid in incr nid; i in
  (* instructions are collected newest first: appending to a list of ten thousand is
     quadratic, and an MB circuit is ten thousand *)
  let add (i : Tsir.instr) = instrs := i :: !instrs in
  let op_meta (ids : int list) : (string * Yojson.Safe.t) list =
    match List.sort_uniq compare ids with
    | [] -> []
    | l -> [ ("op", `List (List.map (fun i -> `Int i) l)) ]
  in
  let blank =
    Tsir.{ ityp = ""; id = 0; cls = None; mode = None; template = None; participants = [];
           holds = []; gate = None; arity = None; params = []; pairs = []; ions = []; sites = [];
           broadcast = false; placement = []; quanta = []; t0 = None; t1 = None; cost = None;
           steps = None; quanta_delta = None; operating_point = None; meta = [] }
  in
  let cert_moves = ref [] and cert_rots = ref [] and cert_gates = ref [] in
  let n_rounds = ref 0 and n_g2 = ref 0 and n_rot = ref 0 and n_hops = ref 0 in
  let n_moves = ref 0 and n_swaps = ref 0 and n_service = ref 0 in

  let placement = List.init nq (fun q -> (ion q, d.nodes.(pos.(q)))) in
  add
    Tsir.{ blank with ityp = "init"; id = fresh (); placement;
                      quanta = List.map (fun (i, _) -> (i, `Float 0.0)) placement;
                      meta = [ ("compiler", `String "qccdc/racetrack"); ("circuit", `String c.name);
                               ("arch", `String a.name); ("loop", `String d.lid) ] };
  (* state preparation begins with Doppler cooling, as in `Compile` *)
  add Tsir.{ blank with ityp = "cool"; id = fresh (); broadcast = true;
                        meta = [ ("kind", `String "state_prep") ] };

  let resting () = Array.for_all (fun l -> List.length l <= 1) at in

  (* One transport cycle: every ion in `ms` steps one site, all the same way. *)
  let step_all ~(what : string) ~(ops_for : int list) (ms : (int * int) list) (dir : int) =
    if ms <> [] then begin
      let parts =
        List.map
          (fun (q, s) ->
            let s2 = md n (s + dir) in
            let sg = if dir > 0 then d.seg.(s) else d.seg.(s2) in
            (q, s, s2, sg))
          ms
      in
      List.iter
        (fun (q, s, s2, sg) ->
          cert_moves :=
            Cert.{ cycle = !cyc; ion = ion q; src = d.nodes.(s); dst = d.nodes.(s2); via = [ sg ] }
            :: !cert_moves)
        parts;
      List.iter (fun (q, s, _, _) -> at.(s) <- List.filter (fun x -> x <> q) at.(s)) parts;
      List.iter
        (fun (q, _, s2, _) ->
          at.(s2) <- at.(s2) @ [ q ];
          pos.(q) <- s2)
        parts;
      Array.iteri
        (fun s l ->
          if List.length l > d.cap.(s) then
            failwith (Printf.sprintf "racetrack: %s over capacity" d.nodes.(s)))
        at;
      add
        Tsir.{ blank with ityp = "simd"; id = fresh (); cls = Some Route.shuttle_cls;
                          mode = Some "inter";
                          participants =
                            List.map
                              (fun (q, s, s2, sg) ->
                                Tsir.{ ion = ion q; src = d.nodes.(s); dst = d.nodes.(s2); via = [ sg ] })
                              parts;
                          meta = [ ("kind", `String what) ] @ op_meta ops_for };
      n_moves := !n_moves + List.length parts;
      incr cyc
    end
  in

  (* One rigid shift of the whole loop by `delta` (one instruction, |delta| cycles). *)
  let rotate ?(why = "rotate") ?(ops_for = []) delta =
    if delta <> 0 then begin
      if not (resting ()) then failwith "racetrack: a shift with two ions in one site";
      cert_rots := Cert.{ rcycle = !cyc; rloop = d.lid; rdelta = delta } :: !cert_rots;
      cyc := !cyc + abs delta;
      add
        Tsir.{ blank with ityp = "simd"; id = fresh ();
                          cls = Some (if delta > 0 then d.fwd_cls else d.bwd_cls);
                          mode = Some "inter";
                          template =
                            Some (`Assoc [ ("kind", `String "loop_shift"); ("loop", `String d.lid);
                                           ("delta", `Int delta) ]);
                          holds = d.lid :: Array.to_list d.seg;
                          meta = [ ("kind", `String why) ] @ op_meta ops_for };
      let old = Array.copy at in
      for s = 0 to n - 1 do
        at.(md n (s + delta)) <- old.(s)
      done;
      Array.iteri (fun s l -> List.iter (fun q -> pos.(q) <- s) l) at;
      incr n_rot;
      n_hops := !n_hops + abs delta
    end
  in

  (* ---- pulses: one item per zone per instruction ---- *)
  let run_zones (zones : (int * item list) list) =
    let zs = Array.of_list (List.map (fun (s, l) -> (s, ref l)) zones) in
    let left = Hashtbl.create 32 in
    Array.iter
      (fun (_, l) -> List.iter (fun it -> let o = item_op it in
                                Hashtbl.replace left o (1 + try Hashtbl.find left o with Not_found -> 0)) !l)
      zs;
    let where = Hashtbl.create 32 in
    Array.iter (fun (s, l) -> List.iter (fun it -> Hashtbl.replace where (item_op it) s) !l) zs;
    let tag = function IFrame _ -> 0 | IBeam _ -> 1 | IMeas _ -> 2 | IReset _ -> 3 | IMs _ -> 4 in
    let continue = ref true in
    while !continue do
      let heads =
        Array.to_list zs |> List.filter_map (fun (s, l) -> match !l with it :: _ -> Some (s, l, it) | [] -> None)
      in
      if heads = [] then continue := false
      else begin
        let counts = Array.make 5 0 in
        List.iter (fun (_, _, it) -> counts.(tag it) <- counts.(tag it) + 1) heads;
        let pick =
          let best = ref (-1) in
          for t = 0 to 3 do
            if counts.(t) > 0 && (!best < 0 || counts.(t) > counts.(!best)) then best := t
          done;
          if !best < 0 then 4 else !best
        in
        let these = List.filter (fun (_, _, it) -> tag it = pick) heads in
        List.iter (fun (_, l, _) -> l := List.tl !l) these;
        let id = fresh () in
        let sites = List.sort_uniq compare (List.map (fun (s, _, _) -> d.nodes.(s)) these) in
        let opl = List.map (fun (_, _, it) -> item_op it) these in
        (match pick with
        | 0 ->
          add Tsir.{ blank with ityp = "gate"; id; gate = Some "VZ"; arity = Some 1; mode = Some "intra";
                                ions = List.map (fun (_, _, it) -> match it with IFrame (q, _, _) -> ion q | _ -> assert false) these;
                                params = List.map (fun (_, _, it) -> match it with IFrame (_, l, _) -> [ l ] | _ -> assert false) these;
                                sites;
                                meta = [ ("kind", `String "virtual_z"); ("note", `String "frame update: no laser, no duration") ] @ op_meta opl }
        | 1 ->
          add Tsir.{ blank with ityp = "gate"; id; gate = Some "R"; arity = Some 1; mode = Some "intra";
                                ions = List.map (fun (_, _, it) -> match it with IBeam (q, _, _, _) -> ion q | _ -> assert false) these;
                                params = List.map (fun (_, _, it) -> match it with IBeam (_, t, p, _) -> [ t; p ] | _ -> assert false) these;
                                sites; meta = [ ("kind", `String "beam") ] @ op_meta opl }
        | 2 ->
          add Tsir.{ blank with ityp = "measure"; id;
                                ions = List.map (fun (_, _, it) -> match it with IMeas (q, _) -> ion q | _ -> assert false) these;
                                meta = [ ("kind", `String "readout") ] @ op_meta opl }
        | 3 ->
          add Tsir.{ blank with ityp = "reset"; id;
                                ions = List.map (fun (_, _, it) -> match it with IReset (q, _) -> ion q | _ -> assert false) these;
                                meta = [ ("kind", `String "reset") ] @ op_meta opl }
        | _ ->
          add Tsir.{ blank with ityp = "gate"; id; gate = Some "MS"; mode = Some "intra";
                                pairs = List.map (fun (_, _, it) -> match it with IMs (x, y, _, _) -> (ion x, ion y) | _ -> assert false) these;
                                params = List.map (fun (_, _, it) -> match it with IMs (_, _, t, _) -> [ t ] | _ -> assert false) these;
                                sites; meta = [ ("kind", `String "ms") ] @ op_meta opl };
          incr n_rounds);
        (* an op is witnessed by the instruction that carries its LAST pulse *)
        List.iter
          (fun o ->
            let k = Hashtbl.find left o - 1 in
            Hashtbl.replace left o k;
            if k = 0 then begin
              (match kind.(o) with
              | One | Two ->
                let s = Hashtbl.find where o in
                cert_gates :=
                  Cert.{ dag = o; instr = id; cycle = !cyc; site = d.nodes.(s);
                         operands = List.map ion ops.(o).qubits;
                         pulses =
                           List.map
                             (fun (p : Gateset.pulse) ->
                               let nm k = ion (List.nth ops.(o).qubits k) in
                               match p with
                               | Beam { theta; phi; qubit } -> Printf.sprintf "R(%.6g,%.6g)@%s" theta phi (nm qubit)
                               | Ms { theta; a = x; b = y } -> Printf.sprintf "MS(%.6g)@%s,%s" theta (nm x) (nm y)
                               | Frame { lam; qubit } -> Printf.sprintf "VZ(%.6g)@%s" lam (nm qubit))
                             pulses.(o) }
                  :: !cert_gates
              | _ -> ());
              done_.(o) <- true;
              decr remaining
            end)
          (List.sort_uniq compare opl);
        incr cyc
      end
    done
  in
  (* the items of one op, in time order, on the circuit's qubits *)
  let items_of o =
    match kind.(o) with
    | Meas -> [ IMeas (List.hd ops.(o).qubits, o) ]
    | Reset -> [ IReset (List.hd ops.(o).qubits, o) ]
    | Barrier -> []
    | One | Two ->
      let qs = ops.(o).qubits in
      let q k = List.nth qs k in
      List.map
        (fun (p : Gateset.pulse) ->
          match p with
          | Beam { theta; phi; qubit } -> IBeam (q qubit, theta, phi, o)
          | Frame { lam; qubit } -> IFrame (q qubit, lam, o)
          | Ms { theta; a = x; b = y } -> IMs (q x, q y, theta, o))
        pulses.(o)
  in
  (* What can run at `site` on qubit `q` now, in order: its next ops while they are one-qubit
     gates or read-outs the site supports, each ready once the one before it has run.  A
     read-out also waits on its classical bit, which must already be written. *)
  let chain ?(planned = []) q site =
    let rec go o acc =
      match o with
      | None -> List.rev acc
      | Some o ->
        let ok_site =
          match kind.(o) with
          | One -> d.gate.(site)
          | Meas | Reset -> d.spam.(site)
          | _ -> false
        in
        let ready =
          List.for_all (fun p -> done_.(p) || List.mem p acc || List.mem p planned) preds.(o)
        in
        if ok_site && ready then go (Hashtbl.find_opt next_q (o, q)) (o :: acc) else List.rev acc
    in
    (* past the ops already planned on this wire (a zone's own gate and what precedes it) *)
    let rec skip = function
      | Some o when List.mem o planned -> skip (Hashtbl.find_opt next_q (o, q))
      | x -> x
    in
    go (skip (front_op q)) []
  in

  (* ---- barriers ---- *)
  let settle () =
    let again = ref true in
    while !again do
      again := false;
      for q = 0 to nq - 1 do
        match front_op q with
        | Some o when kind.(o) = Barrier && is_ready o ->
          done_.(o) <- true;
          decr remaining;
          again := true;
          add Tsir.{ blank with ityp = "barrier"; id = fresh (); meta = op_meta [ o ] }
        | _ -> ()
      done
    done;
    (* a barrier on no qubit at all (classical only) is ready at once *)
    Array.iteri
      (fun i k ->
        if k = Barrier && (not done_.(i)) && ops.(i).qubits = [] && is_ready i then begin
          done_.(i) <- true;
          decr remaining
        end)
      kind
  in

  (* ---- serving one-qubit ops where the ions stand ---- *)
  let serve_here () =
    let zones =
      List.filter_map
        (fun s ->
          match at.(s) with
          | [ q ] -> (
            match chain q s with
            | [] -> None
            | os -> Some (s, List.concat_map items_of os))
          | _ -> None)
        (List.init n (fun s -> s))
    in
    if zones <> [] then run_zones zones;
    zones <> []
  in

  (* ---- eligibility ---- *)
  (* A two-qubit gate whose qubits have nothing left before it but one-qubit gates: those
     run in its zone first.  Returns the gate with its pending ops per qubit. *)
  let pre_chain q g =
    let rec back o acc =
      match o with
      | None -> Some acc
      | Some o when done_.(o) -> Some acc
      | Some o when kind.(o) = One -> back (Hashtbl.find_opt prev_q (o, q)) (o :: acc)
      | Some _ -> None
    in
    back (Hashtbl.find_opt prev_q (g, q)) []
  in
  let eligible () =
    let seen = Hashtbl.create 32 in
    let out = ref [] in
    for q = 0 to nq - 1 do
      (* the first op on q that is not a pending one-qubit gate *)
      let w = wire_ops.(q) in
      ignore (front_op q);
      let k = ref front.(q) in
      while !k < Array.length w && kind.(w.(!k)) = One do incr k done;
      if !k < Array.length w then begin
        let g = w.(!k) in
        if kind.(g) = Two && not (Hashtbl.mem seen g) then begin
          Hashtbl.replace seen g ();
          match ops.(g).qubits with
          | [ qa; qb ] -> (
            match (pre_chain qa g, pre_chain qb g) with
            | Some pa, Some pb
              when List.for_all (fun p -> done_.(p) || List.mem p pa || List.mem p pb) preds.(g) ->
              out := (g, pa, pb) :: !out
            | _ -> ())
          | _ -> ()
        end
      end
    done;
    List.rev !out
  in

  (* ---- gathering ---- *)
  (* The loop as a cyclic sequence of tokens (an ion or an empty site).  A swap exchanges
     two neighbouring tokens; a rotation renames every position at once and changes no
     order.  So a gather is planned on the sequence, and the plan holds whatever shifts
     carrying it out needs. *)
  let weight tok = if tok >= 0 then 2 else 1 in   (* cycles: a swap is two, a step one *)
  let seq_now () = Array.map (function [ q ] -> q | [] -> -1 | _ -> failwith "racetrack: not resting") at in
  let where_in seq x =
    let r = ref (-1) in
    Array.iteri (fun i t -> if t = x then r := i) seq;
    !r
  in
  (* x at p passes `cnt` tokens going `dir`: the cost, and the sequence after *)
  let pass_cost seq p dir cnt =
    let c = ref 0 in
    for i = 1 to cnt do c := !c + weight seq.(md n (p + (i * dir))) done;
    !c
  in
  let apply seq p dir cnt =
    let x = seq.(p) in
    for i = 1 to cnt do
      seq.(md n (p + ((i - 1) * dir))) <- seq.(md n (p + (i * dir)))
    done;
    seq.(md n (p + (cnt * dir))) <- x
  in
  (* the four ways to bring x to one end of the block [l..r]; each (cost, dir, cnt, l', r') *)
  let options seq l r x ~right =
    let p = where_in seq x in
    let mk dir cnt l' r' = (pass_cost seq p dir cnt, dir, cnt, md n l', md n r') in
    if right then
      [ mk (-1) (md n (p - r - 1)) l (r + 1);        (* from beyond the right end *)
        mk 1 (md n (r - p)) (l - 1) r ]                (* through the block from the left *)
    else
      [ mk 1 (md n (l - 1 - p)) (l - 1) r;            (* from beyond the left end *)
        mk (-1) (md n (p - l)) l (r + 1) ]             (* through the block from the right *)
  in
  let best_of l = List.fold_left (fun b x -> match b with None -> Some x | Some (c, _, _, _, _) -> let (c2, _, _, _, _) = x in if c2 < c then Some x else b) None l |> Option.get in
  (* add the ions `xs` one after another at one end: cost, drags, block *)
  let add_seq seq l r xs ~right =
    List.fold_left
      (fun (cost, drags, l, r) x ->
        let c, dir, cnt, l', r' = best_of (options seq l r x ~right) in
        let p = where_in seq x in
        apply seq p dir cnt;
        (cost + c, (x, dir, cnt) :: drags, l', r'))
      (0, [], l, r) xs
  in
  (* the block as a list of pairs (left ion, right ion), left to right *)
  let plan_gather (pairs : (int * int) list) =
    let base = seq_now () in
    let best = ref None in
    List.iteri
      (fun si (u0, v0) ->
        List.iter
          (fun (f, s) ->
            let seq = Array.copy base in
            let p = where_in seq f in
            (* the seed: f where it stands, its partner brought to whichever side is cheaper *)
            let cr, _, _, _ = add_seq (Array.copy seq) p p [ s ] ~right:true in
            let cl, _, _, _ = add_seq (Array.copy seq) p p [ s ] ~right:false in
            let right = cr <= cl in
            let c1, dr1, l1, r1 = add_seq seq p p [ s ] ~right in
            let order = ref (if right then [ (f, s) ] else [ (s, f) ]) in
            let cost = ref c1 and drags = ref dr1 and l = ref l1 and r = ref r1 in
            let rest = ref (List.filteri (fun j _ -> j <> si) pairs) in
            while !rest <> [] do
              let cands =
                List.concat_map
                  (fun (u, v) ->
                    List.concat_map
                      (fun right ->
                        List.map
                          (fun xs ->
                            let c, _, _, _ = add_seq (Array.copy seq) !l !r xs ~right in
                            (c, (u, v), xs, right))
                          [ [ u; v ]; [ v; u ] ])
                      [ true; false ])
                  !rest
              in
              let c, (u, v), xs, right =
                List.fold_left (fun (bc, _, _, _ as b) (cc, _, _, _ as x) -> if cc < bc then x else b)
                  (List.hd cands) (List.tl cands)
              in
              let _, dr, l', r' = add_seq seq !l !r xs ~right in
              cost := !cost + c;
              drags := dr @ !drags;
              l := l';
              r := r';
              (match xs with
              | [ x; y ] -> order := if right then !order @ [ (x, y) ] else (y, x) :: !order
              | _ -> assert false);
              rest := List.filter (fun pr -> pr <> (u, v)) !rest
            done;
            match !best with
            | Some (bc, _, _) when bc <= !cost -> ()
            | _ -> best := Some (!cost, List.rev !drags, !order))
          [ (u0, v0); (v0, u0) ])
      pairs;
    match !best with Some (_, dr, order) -> (dr, order) | None -> ([], [])
  in

  (* Carry x past `cnt` tokens going `dir`, one swap at a time, shifting the loop whenever
     the swap it needs is not inside a sorting window. *)
  let max_run = Array.init 2 (fun k -> let dir = if k = 0 then 1 else -1 in
                                 let m = ref 0 in for s = 0 to n - 1 do m := max !m (run_len d s dir) done; !m) in
  let drag x dir cnt ops_for =
    let left = ref cnt in
    while !left > 0 do
      let s = pos.(x) in
      if not (can_swap d s dir) then begin
        let want = min !left max_run.(if dir > 0 then 0 else 1) in
        let best = ref None in
        for s' = 0 to n - 1 do
          let rl = run_len d s' dir in
          if rl >= want then begin
            let dl = shortest n s s' in
            match !best with
            | Some (bd, _) when abs bd <= abs dl -> ()
            | _ -> best := Some (dl, s')
          end
        done;
        match !best with
        | Some (dl, _) -> rotate ~ops_for dl
        | None -> failwith "racetrack: no sorting window"
      end;
      let s = pos.(x) in
      let s2 = md n (s + dir) in
      (match at.(s2) with
      | [] -> step_all ~what:"sort" ~ops_for [ (x, s) ] dir
      | [ y ] ->
        incr n_swaps;
        if d.cap.(s2) >= 2 then begin
          step_all ~what:"sort" ~ops_for [ (x, s) ] dir;
          step_all ~what:"sort" ~ops_for [ (y, s2) ] (-dir)
        end
        else begin
          step_all ~what:"sort" ~ops_for [ (y, s2) ] (-dir);
          step_all ~what:"sort" ~ops_for [ (x, s) ] dir
        end
      | _ -> failwith "racetrack: swap into a full site");
      decr left
    done
  in

  (* ---- one round ---- *)
  let layouts k =
    List.concat_map
      (fun b -> List.filter_map (fun dir -> if layout_ok d b dir k then Some (b, dir) else None) [ 1; -1 ])
      (List.init n (fun b -> b))
  in
  let round (sel : (int * int list * int list) list) =
    let gs = List.map (fun (g, _, _) -> g) sel in
    let pairs = List.map (fun (g, _, _) -> match ops.(g).qubits with [ x; y ] -> (x, y) | _ -> assert false) sel in
    let drags, order = plan_gather pairs in
    List.iter (fun (x, dir, cnt) -> drag x dir cnt gs) drags;
    let k = List.length order in
    let first = fst (List.hd order) in
    let l = pos.(first) in
    let b, dir =
      List.fold_left
        (fun (bb, bd) (b, dir) -> if abs (shortest n l b) < abs (shortest n l bb) then (b, dir) else (bb, bd))
        (List.hd (layouts k)) (layouts k)
    in
    rotate ~why:"align" ~ops_for:gs (shortest n l b);
    (* the block must stand where the plan says: pair j on (b+2j, b+2j+1) *)
    List.iteri
      (fun j (x, y) ->
        if pos.(x) <> md n (b + (2 * j)) || pos.(y) <> md n (b + (2 * j) + 1) then
          failwith "racetrack: the gathered block is not where it was planned")
      order;
    let movers = List.mapi (fun j (x, y) -> if dir > 0 then (x, md n (b + (2 * j))) else (y, md n (b + (2 * j) + 1))) order in
    step_all ~what:"merge" ~ops_for:gs movers dir;
    let zone_of g =
      match ops.(g).qubits with
      | [ qa; _ ] -> pos.(qa)
      | _ -> assert false
    in
    let zones =
      List.map
        (fun (g, pa, pb) ->
          let site = zone_of g in
          let qa, qb = match ops.(g).qubits with [ x; y ] -> (x, y) | _ -> assert false in
          let planned = pa @ pb @ [ g ] in
          let post_a = chain ~planned qa site in
          let post_b = chain ~planned:(planned @ post_a) qb site in
          let all = pa @ pb @ [ g ] @ post_a @ post_b in
          (site, List.concat_map items_of all))
        sel
    in
    run_zones zones;
    n_g2 := !n_g2 + List.length sel;
    step_all ~what:"split" ~ops_for:gs (List.map (fun (q, s) -> (q, md n (s + dir))) movers) (-dir)
  in

  (* ---- choosing a round ---- *)
  let select (e : (int * int list * int list) list) =
    let dist (g, _, _) =
      match ops.(g).qubits with
      | [ qa; qb ] ->
        let seq = seq_now () in
        let p = pos.(qa) in
        let r = md n (pos.(qb) - p) in
        min (pass_cost seq p 1 (max 0 (r - 1))) (pass_cost seq p (-1) (max 0 (n - r - 1)))
      | _ -> 0
    in
    let scored = List.map (fun x -> let (g, _, _) = x in (-level.(g), dist x, g, x)) e in
    let sorted = List.sort compare scored in
    List.filteri (fun i _ -> i < k_max) sorted |> List.map (fun (_, _, _, x) -> x)
  in

  (* ---- servicing: shift until an ion with a pending one-qubit op stands where it can run *)
  let service () =
    let want =
      List.filter_map
        (fun q ->
          match front_op q with
          | Some o when (kind.(o) = One || kind.(o) = Meas || kind.(o) = Reset) && is_ready o -> Some (q, o)
          | _ -> None)
        (List.init nq (fun q -> q))
    in
    if want = [] then false
    else begin
      let fits o s = match kind.(o) with One -> d.gate.(s) | _ -> d.spam.(s) in
      let best = ref None in
      for delta = -(n / 2) to n / 2 do
        if delta <> 0 then begin
          let served = List.length (List.filter (fun (q, o) -> fits o (md n (pos.(q) + delta))) want) in
          if served > 0 then begin
            (* cycles per ion served, then more served, then the shorter shift *)
            let key = (float_of_int (abs delta) /. float_of_int served, -served, abs delta) in
            match !best with
            | Some (bk, _) when bk <= key -> ()
            | _ -> best := Some (key, delta)
          end
        end
      done;
      match !best with
      | None -> na "no shift brings a waiting ion to a site that can serve it"
      | Some (_, delta) ->
        incr n_service;
        rotate ~why:"service" ~ops_for:(List.map snd want) delta;
        true
    end
  in

  (* ---- the schedule ---- *)
  let guard = ref (8 * (nops + 4)) in
  while !remaining > 0 do
    decr guard;
    if !guard < 0 then na "the schedule stopped making progress";
    settle ();
    if !remaining > 0 then begin
      let served = serve_here () in
      settle ();
      if !remaining > 0 then begin
        match eligible () with
        | [] -> if not (service () || served) then na "deadlock: nothing is ready and nothing can be served"
        | e -> round (select e)
      end
    end
  done;
  if not (resting ()) then failwith "racetrack: ended with two ions in one site";

  let prog =
    Tsir.{ name = c.name; arch_spec = arch_path; instructions = List.rev !instrs; metrics = [];
           prog_meta = []; id_seq = !nid }
  in
  let cert =
    Cert.
      {
        version = 1;
        circuit_sha256 = Cert.hash_file qasm_path;
        arch_sha256 = Cert.hash_file arch_path;
        circuit_name = c.name;
        arch_name = a.name;
        n_qubits = nq;
        circuit_ops =
          List.map
            (fun (o : Circuit.op) ->
              { oi = o.index; oname = o.name; oqubits = o.qubits; oparams = o.params; osrc = o.src_line })
            c.ops;
        map_ = List.init nq (fun q -> (q, ion q));
        init = placement;
        moves = List.rev !cert_moves;
        rotations = List.rev !cert_rots;
        gates = List.rev !cert_gates;
        unrealised = [];
        claims =
          [ ("cycles", `Int !cyc); ("rounds", `Int !n_rounds); ("rotations", `Int !n_rot);
            ("rotation_hops", `Int !n_hops); ("moves", `Int !n_moves); ("swaps", `Int !n_swaps) ];
      }
  in
  let st =
    { rounds = !n_rounds; gates2 = !n_g2; rotations = !n_rot; rot_hops = !n_hops;
      local_moves = !n_moves; swaps = !n_swaps; services = !n_service }
  in
  let notes =
    [ Printf.sprintf "race track: loop %s of %d sites, up to %d gates per round, classes %s/%s + %s"
        d.lid n k_max d.fwd_cls d.bwd_cls Route.shuttle_cls;
      placement_note ]
    @ (if n_lowered > 0 then [ Printf.sprintf "lowered %d multi-qubit gate(s) to 1- and 2-qubit gates" n_lowered ] else [])
    @ [ Printf.sprintf "%d two-qubit gates in %d rounds; %d shifts totalling %d hops (%d to serve one-qubit ops), %d one-ion steps (%d swaps)"
          !n_g2 !n_rounds !n_rot !n_hops !n_service !n_moves !n_swaps ]
  in
  (prog, cert, st, notes)
