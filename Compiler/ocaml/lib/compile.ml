(* The pipeline: a circuit and an architecture in, a TSIR program and a certificate out.

   Layer by layer:

     1. which ops are ready            (ASAP over the dependency DAG)
     2. where each must happen         (a gate-capable trap, near both operands)
     3. how the ions get there         (`Route`, prioritised space-time A-star)
     4. what the machine actually does (`Gateset`, the Lean-proved pulse table)

   {1 Why gates are emitted as pulses, not as CX}

   The existing TSIR programs say `gate: "CX"` and let the cost model price it as one
   `ms_gate`.  That is right for an imported artifact and wrong for a compiler: a CX is
   one MS gate *and four single-qubit beams*, and calling it one entangling gate
   undercounts its duration.  So this emits the native sequence -- which is what
   `Compiler/PLAN.md` set out to do, and which is only trustworthy because C2 proved the
   sequence equals the gate.

   {1 Why rounds}

   R12 allows one gate per trap per cycle and puts no bound on gates in *different*
   traps.  Since every gate in a layer is placed in a distinct trap, taking the k-th
   pulse of every gate as one cycle is legal by construction, and it is what makes N
   simultaneous single-qubit gates cost one cycle rather than N. *)

exception Cannot of string

(* A round whose deferrals or evictions could not be routed: nothing of it was emitted, and
   the layer runs it again as the single pass did (`run_once`). *)
exception Round_gave_up of string

type policy = { horizon : int; spam : bool }

let default_policy = { horizon = 0 (* 0 = derive from the device *); spam = true }

type stats = {
  layers : int;
  transport_cycles : int;
  beams : int;
  ms_gates : int;
  frames : int;
  instructions : int;
}

type result = {
  prog : Tsir.t;
  cert : Cert.t;
  stats : stats;
  notes : string list;
}

(* ------------------------------------------------------------------ layering *)

(* ASAP: an op sits one layer after the latest op it shares a wire with.  This is the
   dependency DAG of C1, read directly -- no separate scheduler, and no chance of the two
   disagreeing about what depends on what.

   A `barrier` is a fence, not a step: it does nothing, so it takes no layer of its own.
   It sits in the layer of the latest op before it on any of its wires, and every wire it
   names resumes after that layer -- so nothing after it can run before anything ahead of
   it, which is all a barrier asks.  Giving it a layer of its own (as when it was an op
   like any other) would put an empty layer between two gate layers, and the undock
   lookahead, which looks one layer ahead, would then see the barrier's 32 qubits as
   "about to be used" and leave every operand standing in a gate zone. *)
let layer_of (c : Circuit.t) : int array =
  let n = List.length c.ops in
  let out = Array.make n 0 in
  let last = Hashtbl.create 64 in
  List.iter
    (fun (o : Circuit.op) ->
      let ws = Circuit.wires_of c o in
      if o.name = "barrier" then begin
        let l =
          List.fold_left
            (fun acc w -> max acc (try Hashtbl.find last w with Not_found -> -1))
            (-1) ws
        in
        out.(o.index) <- max l 0;
        if l >= 0 then List.iter (fun w -> Hashtbl.replace last w l) ws
      end
      else begin
        let l =
          List.fold_left
            (fun acc w ->
              max acc (1 + (try Hashtbl.find last w with Not_found -> -1)))
            0 ws
        in
        out.(o.index) <- l;
        List.iter (fun w -> Hashtbl.replace last w l) ws
      end)
    c.ops;
  out

(* ------------------------------------------------------------------ targets *)

let can_gate (a : Arch.t) s =
  match Arch.node a s with Some n -> n.can_gate | None -> false

let can_spam (a : Arch.t) s =
  match Arch.node a s with Some n -> n.can_spam | None -> false

let capacity = Arch.eff_capacity

(* How many ions are sitting in each trap right now.
   After a gate its operands stay where they are -- which is what the hardware does, and
   which means popular traps FILL UP.  On a GHZ chain every gate shares a qubit with the
   last, so a chooser that ignores occupancy keeps picking the same trap until it holds
   `capacity` ions and the next gate cannot get its operands in.  That is not a rare
   corner: it is what made ghz6 unroutable on a 72-trap ring with 288 slots free. *)
let occupancy (pos : (string, string) Hashtbl.t) : (string, int) Hashtbl.t =
  let occ = Hashtbl.create 64 in
  Hashtbl.iter
    (fun _ site ->
      Hashtbl.replace occ site (1 + (try Hashtbl.find occ site with Not_found -> 0)))
    pos;
  occ

let occ_of occ s = try Hashtbl.find occ s with Not_found -> 0

(* The meeting trap for a two-qubit gate: gate-capable, closest to the pair, and with
   room for both once the ions already parked there are counted.  Claimed traps are
   excluded so that every gate in a layer lands somewhere different, which is what makes
   the round-based emission R12-legal. *)
let meet ?(usable = fun (_ : string) -> true) (a : Arch.t) (t : Traps.t) (d : Traps.dists)
    ~(claimed : (string, unit) Hashtbl.t)
    ~(occ : (string, int) Hashtbl.t) ~(here : string list) ~(sa : string) ~(sb : string) :
    string option =
  let score m =
    match (Traps.dist d sa m, Traps.dist d sb m) with
    | Some x, Some y -> Some (x + y)
    | _ -> None
  in
  List.fold_left
    (fun best m ->
      (* the two operands do not count against themselves if they are already there *)
      let mine = List.length (List.filter (fun s -> s = m) here) in
      let room = capacity a m - (occ_of occ m - mine) in
      if Hashtbl.mem claimed m || (not (can_gate a m)) || room < 2 || not (usable m) then best
      else
        match (score m, best) with
        | None, _ -> best
        | Some s, None -> Some (m, s)
        | Some s, Some (_, bs) when s < bs -> Some (m, s)
        | _ -> best)
    None t.sites
  |> Option.map fst

(* The nearest trap with a given capability, for a one-qubit gate or a measurement.  On a
   grid every trap can gate and this is the identity; on the shipped ring only the 24
   docks can, so every single-qubit gate costs a round trip -- a real property of that
   device, and one this pass surfaces rather than hides. *)
let nearest ?(usable = fun (_ : string) -> true) (a : Arch.t) (t : Traps.t) (d : Traps.dists)
    ~(claimed : (string, unit) Hashtbl.t)
    ~(occ : (string, int) Hashtbl.t) ~(from : string) ~(cap : Arch.t -> string -> bool) :
    string option =
  if cap a from && usable from && not (Hashtbl.mem claimed from) then Some from
  else
    List.fold_left
      (fun best m ->
        let room = capacity a m - occ_of occ m in
        if Hashtbl.mem claimed m || (not (cap a m)) || room < 1 || not (usable m) then best
        else
          match (Traps.dist d from m, best) with
          | None, _ -> best
          | Some s, None -> Some (m, s)
          | Some s, Some (_, bs) when s < bs -> Some (m, s)
          | _ -> best)
      None t.sites
    |> Option.map fst

(* ------------------------------------------------------------------ junctions are crossed

   A SITE WHERE THREE OR MORE RAILS MEET IS A JUNCTION (R18), whatever the drawing calls
   it, and an ion parked on one blocks every route through it.  R2 lets one ion stand
   there, so the router used to treat it as a trap like any other: on a hexagon lattice
   whose corners were declared as sites, ions waited on the corners with empty traps on
   every side (reported 2026-09-29).  A bare junction never had the problem -- it is not
   a vertex of the trap graph, so a move crosses it in one cycle.

   R23 now says it: no ion ends a cycle on a junction while the traps off the junctions
   have room for every ion.  So when they do, the junction sites are left out of the trap
   graph (`Traps.build ~rest`), and nothing is placed on, gated on, or made to wait on
   one.  The test is R23's own, counted the same way -- capacity capped at the chain limit
   (`eff_capacity`) -- because a compiler that kept a wider notion of "room" than the
   verifier would emit programs the verifier refuses.  There is no falling back: a device
   that cannot run the circuit without standing on a junction cannot run it by the rules,
   and saying so is the compiler's job.

   A device with no room off its junctions compiles as before, and R23 does not apply to
   it.  The answer is the predicate `Traps.build` wants, or `None` for "no restriction". *)
let junction_rest (a : Arch.t) ~(n_qubits : int) : (string -> bool) option =
  let is_j s = match Arch.node a s with Some n -> n.is_junction | None -> false in
  let sites =
    List.filter
      (fun s -> match Arch.node a s with Some n -> n.kind = "site" | None -> false)
      a.node_order
  in
  let js, others = List.partition is_j sites in
  let room = List.fold_left (fun acc s -> acc + capacity a s) 0 others in
  if js = [] || room < n_qubits then None else Some (fun s -> not (is_j s))

(* ------------------------------------------------------------------ the driver *)

(* `init`: a starting placement decided upstream, as (ion, site) pairs -- the general
   router's counterpart of `qccdc rotate --placement`.  The placement pass optimises
   interaction distance with no knowledge of the code; a BB code is translation-invariant
   on a torus, and a layout that keeps that structure (`Codesign/scripts/bb_torus.py`)
   is something the pass cannot find and the study needs to measure.  Every ion the
   circuit names must be given a site that exists; nothing else is checked here, because
   the router says loudly enough when a layout does not work. *)
let placement_of_init (a : Arch.t) (c : Circuit.t) (init : (string * string) list) : Place.t =
  let site = Hashtbl.create 256 in
  List.iter
    (fun (ion, s) ->
      (match Arch.node a s with
      | Some _ -> ()
      | None -> invalid_arg (Printf.sprintf "--init-placement: %s -> unknown site %s" ion s));
      Hashtbl.replace site ion s)
    init;
  let ion = Array.init c.Circuit.n_qubits Place.ion_name in
  Array.iter
    (fun i ->
      if not (Hashtbl.mem site i) then
        invalid_arg (Printf.sprintf "--init-placement: no site for %s" i))
    ion;
  { Place.ion; site; notes = [ Printf.sprintf "placement: %d ions placed by --init-placement" (List.length init) ] }

let run_once ?(policy = default_policy) ?(record : Yojson.Safe.t list ref option)
    ?(variant = 0) ?(eager = true) ?init ?rest (a : Arch.t) (c : Circuit.t) ~(arch_path : string)
    ~(qasm_path : string) : result =
  (* Lower first.  The router meets a PAIR of ions in one trap; three ions in one trap is
     a different problem most shipped devices cannot host at all, so a Toffoli becomes six
     CX before placement ever sees it.  Everything downstream -- placement, routing, the
     certificate's op list -- is about the LOWERED circuit; R10's stabilizer half still
     compares the emitted pulses against the ORIGINAL, so the lowering is checked rather
     than trusted. *)
  let c, n_lowered = Circuit.lower c in
  let t = Traps.build ?rest a in
  let d = Traps.all_dists t in
  let pl =
    match init with
    | Some init -> placement_of_init a c init
    | None -> Place.run ~variant a t d c
  in
  let notes = ref (List.rev pl.notes) in
  (match rest with
  | Some rest ->
    let n =
      List.length
        (List.filter
           (fun s ->
             (match Arch.node a s with Some n -> n.kind = "site" | None -> false)
             && not (rest s))
           a.node_order)
    in
    notes :=
      Printf.sprintf
        "junctions: %d site(s) where three or more rails meet are crossed, never rested on"
        n
      :: !notes
  | None -> ());
  if n_lowered > 0 then
    notes :=
      Printf.sprintf "lowered %d multi-qubit gate(s) to 1- and 2-qubit gates" n_lowered
      :: !notes;

  let horizon =
    if policy.horizon > 0 then policy.horizon
    else
      (* generous: the router may need to detour around parked ions, and a horizon that
         is merely "the diameter" makes a solvable instance look unroutable *)
      let ecc =
        match t.sites with
        | [] -> 1
        | s0 :: _ -> (
          match Hashtbl.find_opt d s0 with
          | None -> 1
          | Some row -> Hashtbl.fold (fun _ v acc -> max acc v) row 1)
      in
      (2 * ecc) + 8
  in

  let prog = ref (Tsir.
                    {
                      name = c.name;
                      arch_spec = arch_path;
                      instructions = [];
                      metrics = [];
                      prog_meta = [];
                      id_seq = 0;
                    }) in
  (* Which circuit operation is this instruction for?

     The certificate answers that for one instruction per gate -- the one the witness
     names -- and a `cx` is seven pulses, so four fifths of a compiled program has no
     answer there. A debugger wants one for every instruction, including the transport,
     so the compiler stamps it as it emits: `meta.op` is the list of circuit ops an
     instruction serves. `bridge/animate.py` cross-checks the gate instructions against
     the certificate's own witnesses before drawing anything, so the stamp cannot drift
     from the claim the Lean checker actually decided. *)
  let op_meta (ids : int list) : (string * Yojson.Safe.t) list =
    match List.sort_uniq compare ids with
    | [] -> []
    | l -> [ ("op", `List (List.map (fun i -> `Int i) l)) ]
  in
  (* The instruction a gate witness names ought to be one that actually performs the
     operation.  It used to be `id_seq - 1` -- the last instruction of the whole LAYER --
     which nothing read, so nothing noticed; the moment the animation joined on it, a
     witness for op 5 pointed at a pulse belonging to ops 11, 22 and 32.  Recording the
     last instruction stamped with each op keeps the field honest by construction. *)
  let last_instr : (int, int) Hashtbl.t = Hashtbl.create 64 in
  let note_instr (i : Tsir.instr) =
    match List.assoc_opt "op" i.meta with
    | Some (`List l) ->
      List.iter (function `Int oi -> Hashtbl.replace last_instr oi i.id | _ -> ()) l
    | _ -> ()
  in
  let add (instr : Tsir.instr) =
    note_instr instr;
    let p = !prog in
    prog := { p with instructions = p.instructions @ [ instr ] }
  in
  let fresh_id () =
    let n, p = Tsir.next_id !prog in
    prog := p;
    n
  in
  let blank =
    Tsir.
      {
        ityp = "";
        id = 0;
        cls = None;
        mode = None;
        template = None;
        participants = [];
        holds = [];
        gate = None;
        arity = None;
        params = [];
        pairs = [];
        ions = [];
        sites = [];
        broadcast = false;
        placement = [];
        quanta = [];
        t0 = None;
        t1 = None;
        cost = None;
        steps = None;
        quanta_delta = None;
        operating_point = None;
        meta = [];
      }
  in

  (* --- init --------------------------------------------------------------- *)
  let pos : (string, string) Hashtbl.t = Hashtbl.create c.n_qubits in
  Array.iter
    (fun ion -> Hashtbl.replace pos ion (Hashtbl.find pl.site ion))
    pl.ion;
  let placement = Array.to_list (Array.map (fun i -> (i, Hashtbl.find pos i)) pl.ion) in
  add
    Tsir.
      {
        blank with
        ityp = "init";
        id = fresh_id ();
        placement;
        quanta = List.map (fun (i, _) -> (i, `Float 0.0)) placement;
        meta =
          [
            ("compiler", `String "qccdc");
            ("circuit", `String c.name);
            ("arch", `String a.name);
            ("regime", `String (Arch.regime_name (Arch.regime a)));
          ];
      };

  (* State preparation begins with Doppler cooling.  This is not a device to make R7c
     pass -- it is what a trapped-ion experiment actually does before it starts, and R7c
     ("cooling is mandatory") is the platform saying so.  A program that gates for
     milliseconds having never cooled is not a program the hardware would run.
     Additional cooling that R7 demands mid-program is inserted afterwards by
     `qccd/compile/cooling.py`, which is the pass that provably converges. *)
  add
    Tsir.
      {
        blank with
        ityp = "cool";
        id = fresh_id ();
        broadcast = true;
        meta = [ ("kind", `String "state_prep") ];
      };
  (* --- state for the certificate ------------------------------------------ *)
  let cyc = ref 1 in  (* the state-prep cool is cycle 0 *)
  let cert_moves = ref [] in
  let cert_gates = ref [] in
  let unrealised = ref [] in
  let n_beams = ref 0 and n_ms = ref 0 and n_frames = ref 0 and n_transport = ref 0 in

  let ion_of q = pl.ion.(q) in

  (* --- per layer ----------------------------------------------------------- *)
  let lay = layer_of c in
  let n_layers = Array.fold_left max (-1) lay + 1 in
  let by_layer = Array.make (max n_layers 0) [] in
  List.iter (fun (o : Circuit.op) -> by_layer.(lay.(o.index)) <- o :: by_layer.(lay.(o.index))) c.ops;
  Array.iteri (fun i l -> by_layer.(i) <- List.rev l) by_layer;

  (* Where an ion goes back to after a gate: its placement, until it is moved out of a
     gate zone to make room (`evict_for`) or off a tied conveyor (below).  Each of those
     gives it a new home, so that undocking does not carry it straight back into the
     place it was just cleared from. *)
  let home : (string, string) Hashtbl.t = Hashtbl.copy pl.site in
  (* R4's tied sites (`Arch.tied_classes`): nothing is placed, gated, read out or parked
     on one, and the router crosses them without stopping (`Route.plan_one`).  Empty on
     every device whose channels have per-site switches, and then `usable` is always
     true and none of the choices below changes. *)
  let tied = Route.tied_of a in
  let usable s = not (Hashtbl.mem tied s) in

  (* One routed plan, emitted: one instruction and one certificate cycle per cycle. *)
  let emit_plan ~(kind : string) ~(ops_of : string -> int list) (plan : Route.layer_plan) =
    List.iter
      (fun (cy : Route.cycle) ->
        List.iter
          (fun (m : Route.move) ->
            cert_moves :=
              Cert.{ cycle = !cyc; ion = m.ion; src = m.src; dst = m.dst; via = m.via }
              :: !cert_moves;
            Hashtbl.replace pos m.ion m.dst)
          cy.moves;
        add
          Tsir.
            {
              blank with
              ityp = "simd";
              id = fresh_id ();
              cls = Some "shuttle";
              mode = Some "inter";
              participants =
                List.map
                  (fun (m : Route.move) ->
                    Tsir.{ ion = m.ion; src = m.src; dst = m.dst; via = m.via })
                  cy.moves;
              meta =
                [ ("kind", `String kind) ]
                @ op_meta (List.concat_map (fun (m : Route.move) -> ops_of m.ion) cy.moves);
            };
        incr n_transport;
        incr cyc)
      plan.cycles
  in

  (* --- a tied conveyor is emptied before anything else -----------------------

     An ion that STARTS on a tied site -- an `--init-placement` can put it there -- cannot
     be moved out alone: R4 makes every loaded site of its channel move with it.  That is
     what the router did on H2, pulling one ion out of the conveyor while another, on the
     same three signals, stayed (2026-10-07).  So every such ion leaves together, before
     the first layer, each to the nearest untied site with room, in one plan in which no
     ion may wait on a tied site: every cycle moves every ion still on one, along the
     path, with one delta -- the batch shift H2 itself uses.  If there is no room for them
     all off the tied sites, they stay, and their class is closed to every later plan
     (`Route.plan_with`) rather than half-moved. *)
  if Hashtbl.length tied > 0 then begin
    let inside =
      List.filter (fun i -> Hashtbl.mem tied (Hashtbl.find pos i)) (Array.to_list pl.ion)
    in
    if inside <> [] then begin
      let load = occupancy pos in
      let refuge from =
        List.fold_left
          (fun best s ->
            if Hashtbl.mem tied s || occ_of load s >= capacity a s then best
            else
              match (Traps.dist d from s, best) with
              | None, _ -> best
              | Some k, None -> Some (s, k)
              | Some k, Some (_, bk) when k < bk -> Some (s, k)
              | _ -> best)
          None t.sites
      in
      (* the ion nearest a free slot first, one at a time, so that no two of them count
         the same slot *)
      let rec assign left acc =
        match left with
        | [] -> Some (List.rev acc)
        | _ -> (
          let scored =
            List.filter_map
              (fun i -> Option.map (fun (s, k) -> (i, s, k)) (refuge (Hashtbl.find pos i)))
              left
          in
          match scored with
          | [] -> None
          | _ when List.length scored < List.length left -> None
          | first :: _ ->
            let i, s, _ =
              List.fold_left
                (fun ((_, _, bk) as b) ((_, _, k) as x) -> if k < bk then x else b)
                first scored
            in
            Hashtbl.replace load s (occ_of load s + 1);
            assign (List.filter (fun j -> j <> i) left) ((i, s) :: acc))
      in
      match assign inside [] with
      | None ->
        notes :=
          Printf.sprintf
            "tied sites: %d ion(s) start on them and there is no room for them all off them; %s"
            (List.length inside)
            "they stay, and no plan moves anything across their channel's sites"
          :: !notes
      | Some targets -> (
        match Route.plan_layer a t d ~pos ~targets ~horizon with
        | plan ->
          emit_plan ~kind:"evacuate" ~ops_of:(fun _ -> []) plan;
          List.iter (fun (i, s) -> Hashtbl.replace home i s) targets;
          notes :=
            Printf.sprintf
              "tied sites: %d ion(s) placed on them left together in %d cycle(s), %s"
              (List.length targets) (List.length plan.cycles)
              "every loaded site of the channel moving in every cycle (R4)"
            :: !notes
        | exception Route.Unroutable m ->
          notes := ("tied sites: could not empty them: " ^ m) :: !notes)
    end
  end;

  (* --- one round of a layer ---------------------------------------------------

     A layer is every op whose inputs are ready; its ops share no qubit.  It used to run
     in one go -- each op given a trap of its own, and an op that found none left
     UNREALISED -- which failed two ways on Quantinuum's H2, four gate zones (2026-10-07):

       - more ops than free gate zones.  The fifth of five independent gates found every
         zone claimed and was dropped.  H2 runs such a layer in batches of four (Moses et
         al., Sec. II.E), and so does this now: an op that finds no trap THIS round is
         deferred to the next round, which starts after this round's gates have run and
         their operands have undocked.
       - gate zones holding idle ions.  A gate found no zone with room for two and was
         dropped, where moving one idle ion aside would have made the room.  Now such ions
         are EVICTED -- each to the nearest site with room, one that is not itself a gate
         or readout zone where there is one -- and the gate takes the zone this round.

     Both run only where the single pass found nothing, so a layer that compiled before
     compiles to the same instructions.  What this round moves is counted (`proj`), so an
     eviction never sends an ion where there is no room.  A round whose new choices
     cannot be routed is run again as the single pass was (`eager = false`): the new
     paths only ever add a program, they never lose one.  A round that places nothing
     ends the layer, and what is left of it is unrealised. *)
  let round ~(eager : bool) (layer_i : int) (round_i : int) (ops : Circuit.op list) :
      Circuit.op list =
    let claimed = Hashtbl.create 16 in
    let occ = occupancy pos in
    (* this round's moves so far, per site.  Read only by the paths the single pass never
       took -- eviction and the settling below -- so no layer that compiled can see them *)
    let arrive = Hashtbl.create 16 and depart = Hashtbl.create 16 in
    let proj s = occ_of occ s + occ_of arrive s - occ_of depart s in
    let bump tbl s k = Hashtbl.replace tbl s (occ_of tbl s + k) in
    let goes : (string, string) Hashtbl.t = Hashtbl.create 16 in
    let send i s =
      let p = Hashtbl.find pos i in
      Hashtbl.replace goes i s;
      if p <> s then begin
        bump depart p 1;
        bump arrive s 1
      end
    in
    let unsend i =
      match Hashtbl.find_opt goes i with
      | None -> ()
      | Some s ->
        Hashtbl.remove goes i;
        let p = Hashtbl.find pos i in
        if p <> s then begin
          bump depart p (-1);
          bump arrive s (-1)
        end
    in
    (* the ions this round's ops act on: never evicted, they have somewhere to be *)
    let busy = Hashtbl.create 32 in
    List.iter
      (fun (o : Circuit.op) ->
        if o.name <> "barrier" then
          List.iter (fun q -> Hashtbl.replace busy (ion_of q) ()) o.qubits)
      ops;
    let evictions = ref [] in
    let home_before = Hashtbl.copy home in
    (* where an evicted ion goes: the nearest site with room, preferring one that is no
       gate or readout zone, so that it does not stand in the next gate's way *)
    let refuge ~(from : string) ~(taken : (string, int) Hashtbl.t) : string option =
      let pick ok =
        List.fold_left
          (fun best s ->
            if (not (ok s)) || proj s + occ_of taken s >= capacity a s then best
            else
              match (Traps.dist d from s, best) with
              | None, _ -> best
              | Some k, None -> Some (s, k)
              | Some k, Some (_, bk) when k < bk -> Some (s, k)
              | _ -> best)
          None t.sites
        |> Option.map fst
      in
      let base s = s <> from && usable s && not (Hashtbl.mem claimed s) in
      match pick (fun s -> base s && (not (can_gate a s)) && not (can_spam a s)) with
      | Some s -> Some s
      | None -> pick base
    in
    (* A site with capability `cap` for `ions`, counting what this round already moves,
       and evicting idle ions from it when that is what it takes: the nearest, an eviction
       counted as one more hop.  `None` when no site can be made to fit. *)
    let evict_for ~(cap : Arch.t -> string -> bool) ~(use_claims : bool) (ions : string list)
        : string option =
      if not eager then None
      else begin
        let here = List.map (fun i -> Hashtbl.find pos i) ions in
        let k = List.length ions in
        let cands =
          List.filter_map
            (fun m ->
              if (not (cap a m)) || (not (usable m)) || (use_claims && Hashtbl.mem claimed m)
              then None
              else
                let ds = List.map (fun s -> Traps.dist d s m) here in
                if List.exists Option.is_none ds then None
                else
                  let mine = List.length (List.filter (fun s -> s = m) here) in
                  let need = k - (capacity a m - (proj m - mine)) in
                  let idle =
                    List.filter
                      (fun i ->
                        Hashtbl.find pos i = m
                        && (not (Hashtbl.mem busy i))
                        && not (Hashtbl.mem goes i))
                      (Array.to_list pl.ion)
                  in
                  if need > List.length idle then None
                  else
                    Some
                      ( m,
                        List.fold_left (fun acc x -> acc + Option.get x) (max need 0) ds,
                        List.filteri (fun j _ -> j < need) idle ))
            t.sites
          |> List.stable_sort (fun (_, x, _) (_, y, _) -> compare x y)
        in
        let rec first = function
          | [] -> None
          | (m, _, out) :: rest ->
            let taken = Hashtbl.create 4 in
            let dests =
              List.map
                (fun e ->
                  match refuge ~from:m ~taken with
                  | Some s ->
                    Hashtbl.replace taken s (occ_of taken s + 1);
                    Some (e, s)
                  | None -> None)
                out
            in
            if List.exists Option.is_none dests then first rest
            else begin
              List.iter
                (fun x ->
                  let e, s = Option.get x in
                  send e s;
                  evictions := (e, s) :: !evictions;
                  Hashtbl.replace home e s)
                dests;
              Some m
            end
        in
        first cands
      end
    in

    (* 1. where each op must happen *)
    let deferred = ref [] in
    let later (o : Circuit.op) =
      if eager then deferred := o :: !deferred else unrealised := o.index :: !unrealised
    in
    let sited_rev = ref [] in
    List.iter
      (fun (o : Circuit.op) ->
        let site_at s ions =
          List.iter (fun i -> send i s) ions;
          sited_rev := (o, Some (s, ions)) :: !sited_rev
        in
        let claim s = Hashtbl.replace claimed s () in
        match (o.name, o.qubits) with
        | "barrier", _ -> sited_rev := (o, None) :: !sited_rev
        | ("measure" | "reset"), [ q ] -> (
          (* SPAM does NOT compete with gates for traps.  R12 bounds one *gate* per
             trap per cycle; a measurement is a different instruction in a different
             cycle, and several ions in one trap can be read out together.  Excluding
             claimed traps here pushed every readout to a further and further trap and
             cost `cyclone_base` 151 transport cycles where 19 suffice -- an eightfold
             penalty for a constraint that does not exist. *)
          let ion = ion_of q in
          let from = Hashtbl.find pos ion in
          let unclaimed = Hashtbl.create 1 in
          match nearest ~usable a t d ~claimed:unclaimed ~occ ~from ~cap:can_spam with
          | Some s -> site_at s [ ion ]
          | None -> (
            match evict_for ~cap:can_spam ~use_claims:false [ ion ] with
            | Some s -> site_at s [ ion ]
            | None -> later o))
        | _, [ q ] -> (
          let ion = ion_of q in
          let from = Hashtbl.find pos ion in
          match nearest ~usable a t d ~claimed ~occ ~from ~cap:can_gate with
          | Some s ->
            claim s;
            site_at s [ ion ]
          | None -> (
            match evict_for ~cap:can_gate ~use_claims:true [ ion ] with
            | Some s ->
              claim s;
              site_at s [ ion ]
            | None -> later o))
        | _, [ qa; qb ] -> (
          let ia = ion_of qa and ib = ion_of qb in
          let sa = Hashtbl.find pos ia and sb = Hashtbl.find pos ib in
          match meet ~usable a t d ~claimed ~occ ~here:[ sa; sb ] ~sa ~sb with
          | Some s ->
            claim s;
            site_at s [ ia; ib ]
          | None -> (
            match evict_for ~cap:can_gate ~use_claims:true [ ia; ib ] with
            | Some s ->
              claim s;
              site_at s [ ia; ib ]
            | None -> later o))
        | _, qs ->
          (* three-qubit gates would need a trap that holds three ions AND a
             decomposition into pairs that are all co-located; refusing loudly beats
             emitting something that looks fine and is not *)
          ignore qs;
          unrealised := o.index :: !unrealised)
      ops;

    (* Settle.  The single pass reads each op's room from the occupancy at the start of
       the round, so two read-outs can pick the same last slot -- 32 measurements on
       H2's eight readout slots all chose the nearest zone.  Such a round never routed
       (the last ion in cannot park), so this changes no layer that compiled: the last op
       bringing an ion to an overfull site is placed again, counting this round's moves,
       or deferred to the next round. *)
    let settled = ref false in
    let guard = ref (4 * (List.length ops + 1)) in
    let is_spam (o : Circuit.op) = o.name = "measure" || o.name = "reset" in
    let rec settle () =
      let over =
        Hashtbl.fold
          (fun s n acc -> if n > 0 && proj s > capacity a s then s :: acc else acc)
          arrive []
        |> List.sort compare
      in
      match over with
      | [] -> ()
      | _ when not eager -> ()
      | _ when !guard <= 0 -> ()
      | s :: _ -> (
        decr guard;
        match
          List.find_opt
            (fun ((_ : Circuit.op), site) ->
              match site with
              | Some (s2, ions) -> s2 = s && List.exists (fun i -> Hashtbl.find pos i <> s) ions
              | None -> false)
            !sited_rev
        with
        | None -> ()
        | Some ((o, site) as x) ->
          settled := true;
          sited_rev := List.filter (fun y -> y != x) !sited_rev;
          let ions = match site with Some (_, ions) -> ions | None -> [] in
          List.iter unsend ions;
          (match site with
          | Some (s2, _) when not (is_spam o) -> Hashtbl.remove claimed s2
          | _ -> ());
          let again =
            if is_spam o then evict_for ~cap:can_spam ~use_claims:false ions
            else evict_for ~cap:can_gate ~use_claims:true ions
          in
          (match again with
          | Some s3 ->
            if not (is_spam o) then Hashtbl.replace claimed s3 ();
            List.iter (fun i -> send i s3) ions;
            sited_rev := (o, Some (s3, ions)) :: !sited_rev
          | None -> deferred := o :: !deferred);
          settle ())
    in
    settle ();
    let sited =
      List.stable_sort
        (fun ((x : Circuit.op), _) ((y : Circuit.op), _) -> compare x.index y.index)
        (List.rev !sited_rev)
    in
    let deferred =
      List.sort (fun (x : Circuit.op) (y : Circuit.op) -> compare x.index y.index) !deferred
    in
    (* only these can fail where the single pass would not have *)
    let novel = round_i > 0 || !evictions <> [] || !settled in

    (* 2. route everything that has to move *)
    let needed =
      List.concat_map
        (fun (_, site) ->
          match site with
          | None -> []
          | Some (s, ions) -> List.map (fun i -> (i, s)) ions)
        sited
    in
    let targets =
      (needed |> List.filter (fun (i, s) -> Hashtbl.find pos i <> s)) @ List.rev !evictions
    in
    (* ion -> the ops of THIS layer it is being brought together for, so a transport
       cycle can say which statements it is serving rather than just "route" *)
    let ion_ops = Hashtbl.create 32 in
    List.iter
      (fun ((o : Circuit.op), site) ->
        match site with
        | None -> ()
        | Some (_, ions) ->
          List.iter
            (fun i ->
              Hashtbl.replace ion_ops i
                (o.index :: (try Hashtbl.find ion_ops i with Not_found -> [])))
            ions)
      sited;
    let ops_of i = try Hashtbl.find ion_ops i with Not_found -> [] in
    if targets <> [] then begin
      let before = Hashtbl.copy pos in
      match Route.plan_layer a t d ~pos ~targets ~horizon with
      | plan ->
        (* record the sub-problem AND what the heuristic achieved on it, so C4 can measure
           the gap against an optimal solver on the very same instance.

           `plan.slots`, not `List.length plan.cycles`: the oracle minimises the MAPF
           makespan, and since R22 one slot may be emitted as several instructions (one
           per waveform).  Reporting the emitted count would score a uniformity split as
           an optimality gap against a solver that was never asked about uniformity. *)
        (match record with
        | None -> ()
        | Some acc ->
          acc :=
            Route.instance_json a t ~pos:before ~targets ~horizon ~heuristic:plan.slots
            :: !acc);
        (* one `Route.cycle` is one waveform (R22), so it is one instruction, one machine
           cycle and one `cert_moves` cycle stamp -- the split the router did upstream
           needs nothing here beyond emitting what it hands over *)
        emit_plan ~kind:"route" ~ops_of plan
      | exception (Route.Unroutable m as e) ->
        if Sys.getenv_opt "QCCDC_DEBUG" <> None then begin
          let occ_now = occupancy pos in
          Printf.eprintf "  [round] layer %d round %d eager=%b: %s\n    at:" layer_i round_i
            eager m;
          List.iter
            (fun s ->
              let here =
                Hashtbl.fold (fun i s2 acc -> if s2 = s then i :: acc else acc) pos []
              in
              if occ_of occ_now s > 0 then
                Printf.eprintf " %s=[%s]" s (String.concat "," (List.sort compare here)))
            t.sites;
          Printf.eprintf "\n    targets: %s\n"
            (String.concat " " (List.map (fun (i, s) -> i ^ "->" ^ s) targets))
        end;
        if not (eager && novel) then raise e;
        (* nothing was emitted: give the homes back and let the caller run this round as
           the single pass did *)
        Hashtbl.reset home;
        Hashtbl.iter (fun k v -> Hashtbl.replace home k v) home_before;
        raise (Round_gave_up m)
    end;

    (* 3. the pulses *)
    let decomposed =
      List.filter_map
        (fun ((o : Circuit.op), site) ->
          match site with
          | None -> None
          | Some (s, ions) -> (
            match o.name with
            | "barrier" | "measure" | "reset" -> Some (o, s, ions, [])
            | _ -> (
              match
                Gateset_composites.decompose_op ~gates:c.gates o.name o.params
                  (List.mapi (fun i _ -> i) ions)
              with
              | dc ->
                let idx_to_ion k = List.nth ions k in
                let ps =
                  List.map
                    (fun (p : Gateset.pulse) ->
                      match p with
                      | Frame { lam; qubit } ->
                        Gateset.Frame { lam; qubit = 0 } |> fun _ ->
                        `Frame (lam, idx_to_ion qubit)
                      | Beam { theta; phi; qubit } ->
                        `Beam (theta, phi, idx_to_ion qubit)
                      | Ms { theta; a = x; b = y } ->
                        `Ms (theta, idx_to_ion x, idx_to_ion y))
                    dc.pulses
                in
                Some (o, s, ions, ps)
              | exception Gateset.Unsupported m ->
                notes := Printf.sprintf "op %d (%s): %s" o.index o.name m :: !notes;
                unrealised := o.index :: !unrealised;
                None)))
        sited
    in

    (* barriers cost nothing and only order things *)
    if List.exists (fun ((o : Circuit.op), _, _, _) -> o.name = "barrier") decomposed
    then add Tsir.{ blank with ityp = "barrier"; id = fresh_id () };

    (* A frame update has no DURATION, but it is not nothing: `VZ` is a real Clifford
       operation, and a Z frame does not commute through the MS entangler.  Dropping it
       from the emitted program leaves a program that does not implement the circuit --
       which is exactly what `bridge/check_cert.py` caught as a tableau mismatch, and
       precisely the obligation O3 exists for.  So frames are EMITTED, as zero-duration
       `VZ` instructions, and only their cost is free. *)
    let phys =
      List.map
        (fun (o, s, ions, ps) ->
          ( o,
            s,
            ions,
            ps,
            List.length (List.filter (function `Frame _ -> true | _ -> false) ps) ))
        decomposed
    in
    List.iter (fun (_, _, _, _, nf) -> n_frames := !n_frames + nf) phys;

    let rounds =
      List.fold_left (fun acc (_, _, _, ps, _) -> max acc (List.length ps)) 0 phys
    in
    for k = 0 to rounds - 1 do
      let beams = ref [] and mss = ref [] and vzs = ref [] in
      List.iter
        (fun ((o : Circuit.op), s, _, ps, _) ->
          if k < List.length ps then
            match List.nth ps k with
            | `Beam (th, ph, ion) -> beams := (ion, s, o.index, [ th; ph ]) :: !beams
            | `Ms (th, x, y) -> mss := ((x, y), s, o.index, [ th ]) :: !mss
            | `Frame (lam, ion) -> vzs := (ion, s, o.index, [ lam ]) :: !vzs)
        phys;
      if !vzs <> [] then
        add
          Tsir.
            {
              blank with
              ityp = "gate";
              id = fresh_id ();
              gate = Some "VZ";
              arity = Some 1;
              mode = Some "intra";
              ions = List.rev_map (fun (i, _, _, _) -> i) !vzs;
              params = List.rev_map (fun (_, _, _, p) -> p) !vzs;
              sites =
                List.sort_uniq compare (List.rev_map (fun (_, s, _, _) -> s) !vzs);
              meta =
                [ ("kind", `String "virtual_z"); ("round", `Int k);
                  ("note", `String "frame update: no laser, no duration") ]
                @ op_meta (List.rev_map (fun (_, _, oi, _) -> oi) !vzs);
            };
      if !beams <> [] then begin
        let id = fresh_id () in
        add
          Tsir.
            {
              blank with
              ityp = "gate";
              id;
              gate = Some "R";
              arity = Some 1;
              mode = Some "intra";
              ions = List.rev_map (fun (i, _, _, _) -> i) !beams;
              params = List.rev_map (fun (_, _, _, p) -> p) !beams;
              sites =
                List.sort_uniq compare (List.rev_map (fun (_, s, _, _) -> s) !beams);
              meta = [ ("kind", `String "beam"); ("round", `Int k) ]
                @ op_meta (List.rev_map (fun (_, _, oi, _) -> oi) !beams);
            };
        n_beams := !n_beams + List.length !beams
      end;
      if !mss <> [] then begin
        let id = fresh_id () in
        add
          Tsir.
            {
              blank with
              ityp = "gate";
              id;
              gate = Some "MS";
              mode = Some "intra";
              pairs = List.rev_map (fun (p, _, _, _) -> p) !mss;
              params = List.rev_map (fun (_, _, _, p) -> p) !mss;
              sites =
                List.sort_uniq compare (List.rev_map (fun (_, s, _, _) -> s) !mss);
              meta = [ ("kind", `String "ms"); ("round", `Int k) ]
                @ op_meta (List.rev_map (fun (_, _, oi, _) -> oi) !mss);
            };
        n_ms := !n_ms + List.length !mss
      end;
      if !beams <> [] || !mss <> [] || !vzs <> [] then incr cyc
    done;

    (* every gate op gets a witness naming where it happened *)
    List.iter
      (fun ((o : Circuit.op), s, ions, ps, _) ->
        if o.name <> "barrier" && o.name <> "measure" && o.name <> "reset" then
          cert_gates :=
            Cert.
              {
                dag = o.index;
                instr =
                  (try Hashtbl.find last_instr o.index
                   with Not_found -> !prog.id_seq - 1);
                cycle = !cyc - 1;
                site = s;
                operands = ions;
                pulses =
                  List.map
                    (function
                      | `Beam (th, ph, i) -> Printf.sprintf "R(%.6g,%.6g)@%s" th ph i
                      | `Ms (th, x, y) -> Printf.sprintf "MS(%.6g)@%s,%s" th x y
                      | `Frame (l, i) -> Printf.sprintf "VZ(%.6g)@%s" l i)
                    ps;
              }
            :: !cert_gates)
      phys;

    (* Undock: a gate trap is transient.

       An ion enters it, gates, and leaves -- which is exactly what the shipped deck
       schedule does (dock, contact, undock), and what keeps the small number of
       gate-capable traps available for the gates still to come.  Leaving operands
       parked is what made a 32-qubit circuit exhaust `ring144_24v`'s 24 docks and
       report seven gates UNREALISED.

       The lookahead matters as much as the rule: on a GHZ chain every gate shares a
       qubit with the next, so returning an ion that is about to be used again would
       double the transport for nothing.  A barrier in the next layer uses nothing. *)
    let next_layer_ions =
      if layer_i + 1 < Array.length by_layer then
        List.concat_map
          (fun (o : Circuit.op) ->
            if o.name = "barrier" then [] else List.map ion_of o.qubits)
          by_layer.(layer_i + 1)
      else []
    in
    let returning =
      List.filter_map
        (fun ((o : Circuit.op), site) ->
          match site with
          | Some (_, ions) when List.length ions = 2 -> Some ions
          | _ -> ignore o; None)
        sited
      |> List.concat
      |> List.filter (fun i ->
             (not (List.mem i next_layer_ions))
             && Hashtbl.find pos i <> Hashtbl.find home i
             && usable (Hashtbl.find home i))
      |> List.map (fun i -> (i, Hashtbl.find home i))
    in
    if returning <> [] then begin
      match Route.plan_layer a t d ~pos ~targets:returning ~horizon with
      | plan ->
        (* the layer's ops again: this transport is not travelling TOWARDS them -- they
           have happened -- it is clearing the gate zone after them, and
           `qccd.ir.source_map` sorts the two apart by position *)
        emit_plan ~kind:"undock" ~ops_of plan
      | exception Route.Unroutable m ->
        (* an ion that cannot get home is not an error: it stays where it is, and the
           next layer routes from there *)
        notes := ("undock deferred: " ^ m) :: !notes
    end;

    (* SPAM *)
    if policy.spam then begin
      let ms =
        List.filter_map
          (fun ((o : Circuit.op), _, ions, _, _) ->
            if o.name = "measure" then Some (List.hd ions, o.index) else None)
          phys
      in
      let rs =
        List.filter_map
          (fun ((o : Circuit.op), _, ions, _, _) ->
            if o.name = "reset" then Some (List.hd ions, o.index) else None)
          phys
      in
      if ms <> [] then begin
        add
          Tsir.
            {
              blank with
              ityp = "measure";
              id = fresh_id ();
              ions = List.map fst ms;
              meta =
                [ ("kind", `String "readout") ] @ op_meta (List.map snd ms);
            };
        incr cyc
      end;
      if rs <> [] then begin
        add
          Tsir.
            {
              blank with
              ityp = "reset";
              id = fresh_id ();
              ions = List.map fst rs;
              meta =
                [ ("kind", `String "reset") ] @ op_meta (List.map snd rs);
            };
        incr cyc
      end
    end;
    deferred
  in

  Array.iteri
    (fun layer_i (ops : Circuit.op list) ->
      let rec go round_i pending =
        let later =
          match round ~eager layer_i round_i pending with
          | l -> l
          | exception Round_gave_up m ->
            if round_i = 0 && eager then begin
              notes :=
                Printf.sprintf "layer %d: deferral and eviction could not be routed (%s); %s"
                  layer_i m "the layer ran as a single pass"
                :: !notes;
              round ~eager:false layer_i round_i pending
            end
            else begin
              notes :=
                Printf.sprintf "layer %d, round %d: could not be routed (%s)" layer_i
                  round_i m
                :: !notes;
              List.iter
                (fun (o : Circuit.op) ->
                  if o.name <> "barrier" then unrealised := o.index :: !unrealised)
                pending;
              []
            end
        in
        match later with
        | [] -> ()
        | _ when List.length later >= List.length pending ->
          (* this round placed nothing: no later round will do better *)
          List.iter (fun (o : Circuit.op) -> unrealised := o.index :: !unrealised) later
        | _ -> go (round_i + 1) later
      in
      go 0 ops)
    by_layer;

  let cert =
    Cert.
      {
        version = 1;
        circuit_sha256 = Cert.hash_file qasm_path;
        arch_sha256 = Cert.hash_file arch_path;
        circuit_name = c.name;
        arch_name = a.name;
        n_qubits = c.n_qubits;
        circuit_ops =
          List.map
            (fun (o : Circuit.op) ->
              Cert.{ oi = o.index; oname = o.name; oqubits = o.qubits;
                     oparams = o.params; osrc = o.src_line })
            c.ops;
        map_ = Array.to_list (Array.mapi (fun q i -> (q, i)) pl.ion);
        init = placement;
        moves = List.rev !cert_moves;
        rotations = [];
        gates = List.rev !cert_gates;
        unrealised = List.sort_uniq compare !unrealised;
        claims =
          [
            ("cycles", `Int !cyc);
            ("beams", `Int !n_beams);
            ("ms_gates", `Int !n_ms);
            ("frames", `Int !n_frames);
          ];
      }
  in
  {
    prog = !prog;
    cert;
    stats =
      {
        layers = n_layers;
        transport_cycles = !n_transport;
        beams = !n_beams;
        ms_gates = !n_ms;
        frames = !n_frames;
        instructions = List.length !prog.instructions;
      };
    notes = List.rev !notes;
  }

(* Try the placements in order until one routes.

   The placer optimises weighted interaction distance; the router has to live with the
   result.  Those are not the same objective, and a placement that is shorter on paper can
   put an ion where a later layer cannot get past it.  Falling back to the runner-up costs
   one recompile and turns "not routable" back into a program -- measured: it is what keeps
   `clifford12` on `ladder_2x72` compiling after the hill-climb was added.

   Every placement is tried with the rounds of `run_once` deferring and evicting
   (`eager`) first, and only then as the single pass it used to be.  The eager rounds act
   only where the single pass left an op unrealised, so a placement the single pass
   compiled compiles identically; but the state they leave can make a LATER layer
   unroutable, and a compile that used to end with a (refused) partial programme must not
   end with nothing.  So the new paths can only add a programme, never take one away. *)
let run ?(policy = default_policy) ?(record : Yojson.Safe.t list ref option) ?init
    (a : Arch.t) (c : Circuit.t) ~(arch_path : string) ~(qasm_path : string) : result =
  (* a placement given from outside has no runner-up: it either routes or it does not *)
  let last_variant = if init = None then 2 else 0 in
  let attempts ?rest () =
    (* Pass 0 runs the rounds, pass 1 the single pass.  The first COMPLETE programme wins.
       One that leaves ops unrealised is kept aside while the rest run; the single pass's
       first answer -- complete or not, what the compiler always returned -- is the answer
       when nothing completes, and a partial programme from pass 0 only when the single
       pass cannot route at all. *)
    let snapshot () = match record with Some acc -> Some !acc | None -> None in
    let restore s = match (record, s) with Some acc, Some l -> acc := l | _ -> () in
    let rec attempt pass v last partial =
      let eager = pass = 0 in
      if v > last_variant then
        if pass = 0 then attempt 1 0 last partial
        else
          match (partial, last) with
          | Some (r, s), _ ->
            restore s;
            r
          | None, Some e -> raise e
          | None, None -> assert false
      else begin
        (match record with Some acc -> acc := [] | None -> ());
        match run_once ~policy ?record ~variant:v ~eager ?init ?rest a c ~arch_path ~qasm_path with
        | r when r.cert.unrealised = [] || not eager -> r
        | r ->
          if Sys.getenv_opt "QCCDC_DEBUG" <> None then
            Printf.eprintf "  [compile] placement %d (rounds): %d ops unrealised\n" v
              (List.length r.cert.unrealised);
          attempt pass (v + 1) last
            (match partial with None -> Some (r, snapshot ()) | p -> p)
        | exception (Route.Unroutable m as e) ->
          if Sys.getenv_opt "QCCDC_DEBUG" <> None then
            Printf.eprintf "  [compile] placement %d (%s) unroutable: %s\n" v
              (if eager then "rounds" else "single pass") m;
          (match record with Some acc -> acc := [] | None -> ());
          attempt pass (v + 1) (Some e) partial
      end
    in
    attempt 0 0 None None
  in
  (* Junction sites are crossed, not rested on, when the device has room elsewhere
     (`junction_rest`, R23).  A placement given from outside that already stands on one is
     the caller's decision and is honoured; the verifier will say what it thinks of it. *)
  let lowered, _ = Circuit.lower c in
  let rest =
    match junction_rest a ~n_qubits:lowered.Circuit.n_qubits with
    | Some rest
      when (match init with
           | Some init -> List.for_all (fun (_, s) -> rest s) init
           | None -> true) -> Some rest
    | _ -> None
  in
  attempts ?rest ()
