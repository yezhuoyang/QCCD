(* The conveyor pipeline: compile by rotating the loop, not by walking ions along it.

   Applies when the device rotates (`Conveyor.detect`) and the circuit's interaction graph
   is bipartite with one side small enough to sit at the docks.  Both conditions are real
   constraints rather than conveniences:

   * rigid rotation preserves the cyclic ORDER of the ions on the loop, so two ions riding
     it can never meet -- every gate must have one operand at a dock;
   * a dock holds one ion for the whole program, so there must be at least as many docks
     as there are qubits on that side.

   A syndrome-extraction round satisfies both exactly: data qubits ride the loop, ancillas
   sit at the docks, and every check is a contact between the two.  That is the shape
   `ring144_24v` was built for, and the reason BB[[144,12,12]] fits on it at all.

   {1 The emitted shape}

     rotate (one instruction, whatever the distance)
     dock    every data ion whose partner is waiting at the dock beside it
     gate    all of those contacts together
     undock

   Rotations happen only with the docks empty.  A docked ion is off the loop and does not
   move with it, so rotating underneath one would change which slot it came back to. *)

exception Not_applicable of string

type plan = {
  contacts : int;
  rotations : int;
  hops : int;
  batches : int;
}

(* ------------------------------------------------------------------ the partition *)

(* Two-colour the interaction graph.  Returns (dock side, loop side) with the dock side
   the smaller, or raises if the circuit cannot be split -- which is the honest answer for
   a circuit whose qubits all have to meet each other. *)
let bipartition (c : Circuit.t) : int list * int list =
  let colour = Array.make c.n_qubits (-1) in
  let adj = Array.make c.n_qubits [] in
  List.iter
    (fun (o : Circuit.op) ->
      match o.qubits with
      | [ a; b ] ->
        adj.(a) <- b :: adj.(a);
        adj.(b) <- a :: adj.(b)
      | _ -> ())
    c.ops;
  for s = 0 to c.n_qubits - 1 do
    if colour.(s) < 0 then begin
      colour.(s) <- 0;
      let q = Queue.create () in
      Queue.add s q;
      while not (Queue.is_empty q) do
        let u = Queue.pop q in
        List.iter
          (fun v ->
            if colour.(v) < 0 then begin
              colour.(v) <- 1 - colour.(u);
              Queue.add v q
            end
            else if colour.(v) = colour.(u) then
              raise
                (Not_applicable
                   (Printf.sprintf
                      "the interaction graph is not bipartite (q%d and q%d are on the \
                       same side and interact); two ions riding a loop can never meet"
                      u v)))
          adj.(u)
      done
    end
  done;
  let side k = List.filter (fun q -> colour.(q) = k) (List.init c.n_qubits (fun i -> i)) in
  let a = side 0 and b = side 1 in
  if List.length a <= List.length b then (a, b) else (b, a)

(* ------------------------------------------------------------------ the pass *)

type ctx = {
  cv : Conveyor.t;
  (* a loop ion's FIXED slot in the loop's own order; its site is that slot plus the
     current offset, which is what makes rotation a single number *)
  slot_of : (string, int) Hashtbl.t;
  dock_of : (string, Conveyor.dock) Hashtbl.t;  (* dock ions, parked for the program *)
  mutable offset : int;
}

let site_of (x : ctx) (ion : string) : string =
  match Hashtbl.find_opt x.dock_of ion with
  | Some d -> d.site
  | None -> Conveyor.site_at x.cv (Hashtbl.find x.slot_of ion + x.offset)

(* The rotation that brings `ion` to the rail slot beside `d`. *)
let delta_to (x : ctx) (ion : string) (d : Conveyor.dock) : int =
  let slot = Hashtbl.find x.slot_of ion in
  Conveyor.shortest x.cv ((slot + x.offset) mod x.cv.n) d.rail_idx

(* How to pick the next rotation.

   `Monotone` always turns the same way, to the nearest offset ahead that has work.  One
   revolution then costs `n` hops and visits every offset, so a whole round costs a few
   turns -- which is what the shipped deck schedule does, and why its hop count is 2 672
   rather than tens of thousands.

   `Greedy` jumps to whichever offset serves the most contacts.  It makes bigger batches
   and pays for them in hops, and hops are not free: every one heats the ion, and heating
   is what degrades the next gate (`docs/PLAN.md` §0.3).  Monotone is the default for that
   reason, not because it looks tidier. *)
type sweep = Monotone | Greedy

(* An externally chosen layout: which loop slot each rider starts in, and which dock each
   docked qubit parks at.

   The stride placement below is what this pass does on its own, and it knows nothing
   about the circuit it is laying out.  A syndrome round has structure a placer can use --
   the six members of a check should sit close together on the loop so one turn serves
   them all -- and `qccd/compile/place.py` already finds such a layout for BB codes by
   annealing on check windows.  Rather than teach this pass about codes, it accepts the
   layout as data: the schedule is decided upstream, and this pass stays a compiler that
   emits a certificate for whatever it was given.  Everything downstream -- the DAG order,
   the readiness rule, the certificate, the proved checker -- is unchanged, so a placement
   can only change how many batches the round takes, never whether the program is right. *)
type placement = {
  loop_slot : (int * int) list;     (* qubit index -> slot index in the loop's order *)
  dock_site : (int * string) list;  (* qubit index -> the dock's gate site *)
}

let placement_of_json (j : Yojson.Safe.t) : placement =
  let entries name f =
    match j with
    | `Assoc kv -> (
      match List.assoc_opt name kv with
      | Some (`Assoc m) ->
        List.map
          (fun (k, v) ->
            let q = try int_of_string k with _ ->
              invalid_arg (Printf.sprintf "placement.%s: key %S is not a qubit index" name k)
            in
            (q, f name k v))
          m
      | Some _ -> invalid_arg (Printf.sprintf "placement.%s must be an object" name)
      | None -> [])
    | _ -> invalid_arg "placement must be a JSON object"
  in
  let as_int name k = function
    | `Int i -> i
    | _ -> invalid_arg (Printf.sprintf "placement.%s[%s] must be an integer slot" name k)
  in
  let as_str name k = function
    | `String s -> s
    | _ -> invalid_arg (Printf.sprintf "placement.%s[%s] must be a dock site name" name k)
  in
  { loop_slot = entries "loop" as_int; dock_site = entries "docks" as_str }

let placement_of_file path = placement_of_json (Yojson.Safe.from_file path)

let run ?(sweep = Monotone) ?placement ?(dock_all = false) (a : Arch.t) (c : Circuit.t)
    ~(arch_path : string) ~(qasm_path : string) : Tsir.t * Cert.t * plan * string list =
  let cv =
    match Conveyor.detect a with
    | Some cv -> cv
    | None -> raise (Not_applicable "the device has no closed loop with docks")
  in
  let dock_side, loop_side =
    let a, b = bipartition c in
    (* The placement decides WHICH side parks, when it says so.  `bipartition` returns the
       smaller side first and this pass parks that one, which is right for a ring with a
       few docks and wrong for a device with a parking site per data qubit: there the
       interesting assignment is the other one -- data parked, ancillas riding, which is
       how Cyclone moves.  A placement that names docks for every member of the larger
       side is asking for exactly that, and it is still one of the two sides, so nothing
       about the bipartition is being overridden. *)
    match placement with
    | Some (pl : placement) when pl.dock_site <> [] ->
      let named = List.sort compare (List.map fst pl.dock_site) in
      if named = List.sort compare b then (b, a) else (a, b)
    | _ -> (a, b)
  in
  let n_docks = Array.length cv.docks in
  if List.length dock_side > n_docks then
    raise
      (Not_applicable
         (Printf.sprintf "%d qubits need a dock but the device has %d"
            (List.length dock_side) n_docks));
  if List.length loop_side > cv.n then
    raise
      (Not_applicable
         (Printf.sprintf "%d qubits need a loop slot but the loop has %d"
            (List.length loop_side) cv.n));

  let ion q = Place.ion_name q in
  let x =
    { cv; slot_of = Hashtbl.create 256; dock_of = Hashtbl.create 64; offset = 0 }
  in
  let stride = max 1 (cv.n / max 1 (List.length loop_side)) in
  let layout_note =
    match placement with
    | None ->
      List.iteri (fun k q -> Hashtbl.replace x.dock_of (ion q) cv.docks.(k)) dock_side;
      (* Spread the loop ions evenly rather than packing them: the loop has more slots
         than qubits on a real round, and spacing them keeps consecutive contacts from
         needing a full turn between them. *)
      List.iteri
        (fun k q -> Hashtbl.replace x.slot_of (ion q) (k * stride mod cv.n))
        loop_side;
      Printf.sprintf "%d qubits at docks, %d riding the loop (stride %d)"
        (List.length dock_side) (List.length loop_side) stride
    | Some (pl : placement) ->
      (* The layout is checked, not believed: every docked qubit must name a dock that
         exists, every rider a slot that exists, and no two may share either.  A layout
         that puts a qubit on the wrong side of the bipartition is refused too -- the
         partition is a property of the circuit, and the layout does not get to change
         which qubits ride. *)
      let used_dock = Hashtbl.create 64 and used_slot = Hashtbl.create 256 in
      List.iter
        (fun q ->
          match List.assoc_opt q pl.dock_site with
          | None ->
            raise
              (Not_applicable
                 (Printf.sprintf "placement: q%d must be docked but names no dock" q))
          | Some site -> (
            match Conveyor.dock_for cv site with
            | None ->
              raise
                (Not_applicable
                   (Printf.sprintf "placement: q%d names %s, which is not a dock of %s"
                      q site cv.loop))
            | Some d ->
              if Hashtbl.mem used_dock site then
                raise
                  (Not_applicable
                     (Printf.sprintf "placement: dock %s is named twice" site));
              Hashtbl.replace used_dock site ();
              Hashtbl.replace x.dock_of (ion q) d))
        dock_side;
      List.iter
        (fun q ->
          match List.assoc_opt q pl.loop_slot with
          | None ->
            raise
              (Not_applicable
                 (Printf.sprintf "placement: q%d rides the loop but names no slot" q))
          | Some s ->
            if s < 0 || s >= cv.n then
              raise
                (Not_applicable
                   (Printf.sprintf "placement: q%d names slot %d of a %d-slot loop" q s
                      cv.n));
            if Hashtbl.mem used_slot s then
              raise
                (Not_applicable (Printf.sprintf "placement: slot %d is named twice" s));
            Hashtbl.replace used_slot s ();
            Hashtbl.replace x.slot_of (ion q) s)
        loop_side;
      Printf.sprintf "%d qubits at docks, %d riding the loop (external placement)"
        (List.length dock_side) (List.length loop_side)
  in

  let prog = ref Tsir.{ name = c.name; arch_spec = arch_path; instructions = [];
                        metrics = []; prog_meta = []; id_seq = 0 } in
  let fresh () = let n, p = Tsir.next_id !prog in prog := p; n in
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
  let add (i : Tsir.instr) =
    note_instr i;
    prog := { !prog with instructions = !prog.instructions @ [ i ] }
  in
  (* Which circuit operation is this instruction for?  See the note in `compile.ml`: the
     certificate names one instruction per gate, a `cx` is seven pulses, and a debugger
     wants an answer for all seven -- and for the rotation that brought the ions
     together. *)
  let op_meta (ids : int list) : (string * Yojson.Safe.t) list =
    match List.sort_uniq compare ids with
    | [] -> []
    | l -> [ ("op", `List (List.map (fun i -> `Int i) l)) ]
  in
  let blank = Tsir.{ ityp = ""; id = 0; cls = None; mode = None; template = None;
                     participants = []; holds = []; gate = None; arity = None;
                     params = []; pairs = []; ions = []; sites = []; broadcast = false;
                     placement = []; quanta = []; t0 = None; t1 = None; cost = None;
                     steps = None; quanta_delta = None; operating_point = None; meta = [] }
  in

  let placement =
    List.map (fun q -> (ion q, site_of x (ion q))) (dock_side @ loop_side)
  in
  add Tsir.{ blank with ityp = "init"; id = fresh (); placement;
             quanta = List.map (fun (i, _) -> (i, `Float 0.0)) placement;
             meta = [ ("compiler", `String "qccdc/rotate"); ("loop", `String cv.loop);
                      ("docked", `Int (List.length dock_side)) ] };
  add Tsir.{ blank with ityp = "cool"; id = fresh (); broadcast = true;
             meta = [ ("kind", `String "state_prep") ] };

  let n_rot = ref 0 and n_hops = ref 0 and n_contacts = ref 0 and n_batches = ref 0 in
  let n_extra = ref 0 in
  let notes = ref [] in
  (* The certificate expands a rotation into the individual ion movements it causes.
     Rotation is a COMPILER primitive, not a checker one: the checker replays hops and
     should not have to know that 144 of them happened together.  It costs a large move
     list and buys a checker that needs no special case. *)
  let cyc = ref 1 in
  let cert_moves = ref [] in
  let cert_rots = ref [] in
  let cert_gates = ref [] in

  let rotate ?(ops = []) delta =
    if delta <> 0 then begin
      cert_rots :=
        Cert.{ rcycle = !cyc; rloop = cv.loop; rdelta = delta } :: !cert_rots;
      cyc := !cyc + abs delta;
      add Tsir.{ blank with ityp = "simd"; id = fresh ();
                 cls = Some (if delta > 0 then cv.cw else cv.ccw);
                 mode = Some "inter";
                 template = Some (`Assoc [ ("kind", `String "loop_shift");
                                           ("loop", `String cv.loop);
                                           ("delta", `Int delta) ]);
                 holds = cv.loop :: cv.rail_segs;
                 meta = [ ("kind", `String "rotate") ] @ op_meta ops };
      x.offset <- ((x.offset + delta) mod cv.n + cv.n) mod cv.n;
      incr n_rot;
      n_hops := !n_hops + abs delta
    end
  in

  (* One contact batch: dock, run the CX pulse sequence, undock -- with no rotation in
     between, because a docked ion is off the loop and would not come back to the slot it
     left.

     The gate is the PROVED decomposition (`QCCDC.cx_decomp`), not a bare MS: MS(pi/2) is
     not a controlled-NOT, and emitting one would produce a program that passes every
     hardware rule and computes the wrong circuit.  Orientation is preserved because a
     syndrome round contains both `cx anc,data` and `cx data,anc`. *)
  (* `extra` are riders that dock and undock with the batch WITHOUT a gate: the riders
     standing beside a dock whose ancilla has no contact for them this batch.  A machine
     whose spur electrodes share one broadcast waveform with no per-site switch cannot
     leave them behind -- every spur acts or none does -- so `--dock-all` docks them too.
     They cost a split and a merge of heat each, which the cool before the gate removes,
     and no time at all: a SIMD instruction is charged at its slowest participant. *)
  let contact ?(extra : (string * Conveyor.dock) list = [])
      (pairs4 : (string * Conveyor.dock * string * int * bool) list) =
    if pairs4 <> [] then begin
      let batch_ops = List.map (fun (_, _, _, dag, _) -> dag) pairs4 in
      let riders = List.map (fun (r, d, _, _, _) -> (r, d)) pairs4 @ extra in
      n_extra := !n_extra + List.length extra;
      let dock_moves dir =
        List.map
          (fun (rider, (d : Conveyor.dock)) ->
            if dir then Tsir.{ ion = rider; src = d.rail; dst = d.site; via = [ d.spur ] }
            else Tsir.{ ion = rider; src = d.site; dst = d.rail; via = [ d.spur ] })
          riders
      in
      add Tsir.{ blank with ityp = "simd"; id = fresh (); cls = Some cv.dock_cls;
                 mode = Some "inter"; participants = dock_moves true;
                 meta = [ ("kind", `String "dock") ] @ op_meta batch_ops };
      List.iter
        (fun (rider, (d : Conveyor.dock)) ->
          cert_moves :=
            Cert.{ cycle = !cyc; ion = rider; src = d.rail; dst = d.site; via = [ d.spur ] }
            :: !cert_moves)
        riders;
      incr cyc;

      (* the CX sequence, round by round: every pair is at a different dock, so the k-th
         pulse of each can share one cycle without breaking R12 *)
      let seqs =
        List.map
          (fun (rider, (d : Conveyor.dock), partner, dag, ctrl_is_rider) ->
            let ctrl, tgt = if ctrl_is_rider then (rider, partner) else (partner, rider) in
            let dc = Gateset_composites.decompose_op ~gates:c.gates "cx" [] [ 0; 1 ] in
            let name k = if k = 0 then ctrl else tgt in
            ( d,
              dag,
              rider,
              partner,
              List.map
                (fun (p : Gateset.pulse) ->
                  match p with
                  | Gateset.Beam { theta; phi; qubit } -> `Beam (theta, phi, name qubit)
                  | Gateset.Frame { lam; qubit } -> `Frame (lam, name qubit)
                  | Gateset.Ms { theta; a = u; b = v } -> `Ms (theta, name u, name v))
                dc.pulses ))
          pairs4
      in
      let rounds =
        List.fold_left (fun acc (_, _, _, _, ps) -> max acc (List.length ps)) 0 seqs
      in
      for k = 0 to rounds - 1 do
        let beams = ref [] and mss = ref [] and vzs = ref [] in
        List.iter
          (fun ((d : Conveyor.dock), dag, _, _, ps) ->
            if k < List.length ps then
              match List.nth ps k with
              | `Beam (th, ph, i) -> beams := (i, d.site, [ th; ph ], dag) :: !beams
              | `Ms (th, u, v) -> mss := ((u, v), d.site, [ th ], dag) :: !mss
              | `Frame (l, i) -> vzs := (i, d.site, [ l ], dag) :: !vzs)
          seqs;
        let emit_batch gate arity items pairs_of =
          if items <> [] then begin
            add Tsir.{ blank with ityp = "gate"; id = fresh (); gate = Some gate; arity;
                       mode = Some "intra";
                       ions = (if arity = Some 1
                               then List.rev_map (fun (i, _, _, _) -> i) items else []);
                       pairs = pairs_of items;
                       params = List.rev_map (fun (_, _, p, _) -> p) items;
                       sites = List.sort_uniq compare
                           (List.rev_map (fun (_, s, _, _) -> s) items);
                       meta = [ ("round", `Int k) ]
                              @ op_meta (List.rev_map (fun (_, _, _, g) -> g) items) };
            incr cyc
          end
        in
        emit_batch "R" (Some 1) !beams (fun _ -> []);
        emit_batch "VZ" (Some 1) !vzs (fun _ -> []);
        if !mss <> [] then begin
          add Tsir.{ blank with ityp = "gate"; id = fresh (); gate = Some "MS";
                     mode = Some "intra";
                     pairs = List.rev_map (fun (p, _, _, _) -> p) !mss;
                     params = List.rev_map (fun (_, _, p, _) -> p) !mss;
                     sites = List.sort_uniq compare
                         (List.rev_map (fun (_, s, _, _) -> s) !mss);
                     meta = [ ("kind", `String "contact"); ("round", `Int k) ]
                            @ op_meta (List.rev_map (fun (_, _, _, g) -> g) !mss) };
          incr cyc
        end
      done;
      List.iter
        (fun ((d : Conveyor.dock), dag, rider, partner, _) ->
          cert_gates :=
            Cert.{ dag;
                   instr =
                     (try Hashtbl.find last_instr dag
                      with Not_found -> !prog.id_seq - 1);
                   cycle = !cyc - 1; site = d.site;
                   operands = [ rider; partner ]; pulses = [] }
            :: !cert_gates)
        seqs;

      add Tsir.{ blank with ityp = "simd"; id = fresh (); cls = Some cv.undock_cls;
                 mode = Some "inter"; participants = dock_moves false;
                 meta = [ ("kind", `String "undock") ] @ op_meta batch_ops };
      List.iter
        (fun (rider, (d : Conveyor.dock)) ->
          cert_moves :=
            Cert.{ cycle = !cyc; ion = rider; src = d.site; dst = d.rail; via = [ d.spur ] }
            :: !cert_moves)
        riders;
      incr cyc;
      n_contacts := !n_contacts + List.length pairs4;
      incr n_batches
    end
  in

  (* Schedule by READINESS, not by program order.

     Program order would put one rotation between every pair of consecutive contacts --
     864 rotations for 864 contacts on a BB round, because consecutive checks want
     different offsets.  What the device rewards is the opposite: rotate once, and take
     every contact that happens to be aligned at that offset.  That is the deck schedule's
     structure, and the reason its batch utilisation is a number worth reporting.

     An op is ready when every earlier op sharing one of its qubits has been emitted, so
     reordering never crosses a dependency. *)
  let ops = Array.of_list c.ops in
  let n_ops = Array.length ops in
  let finished = Array.make n_ops false in
  let remaining = ref n_ops in

  (* Do these two operations commute?

     This is where the batching comes from.  A weight-6 check is six CX gates that all
     share the same ancilla, and treating a shared qubit as a dependency serialises them --
     which leaves one contact ready per ancilla, offsets scattered over the whole loop, and
     a batch size of barely more than one.  But CX gates sharing only their CONTROL commute
     with each other, and so do CX gates sharing only their TARGET: the six can happen in
     any order, and once the scheduler knows that, a whole check is available at once.

     `cx a,b` and `cx b,a` do NOT commute, and nothing commutes past a Hadamard on a qubit
     it touches -- so the rule is stated narrowly rather than assumed generously. *)
  let commutes (u : Circuit.op) (v : Circuit.op) =
    let shared = List.exists (fun q -> List.mem q v.qubits) u.qubits in
    if not shared then true
    else
      match (u.name, u.qubits, v.name, v.qubits) with
      | "cx", [ c1; t1 ], "cx", [ c2; t2 ] -> (c1 = c2 && t1 <> t2) || (t1 = t2 && c1 <> c2)
      | _ -> false
  in

  (* per-qubit op lists, so readiness is a scan over the ops touching this one *)
  let touching = Array.make c.n_qubits [] in
  Array.iteri
    (fun i (o : Circuit.op) ->
      List.iter (fun q -> touching.(q) <- i :: touching.(q)) o.qubits)
    ops;
  Array.iteri (fun q l -> touching.(q) <- List.rev l) touching;

  let blocked i =
    let (o : Circuit.op) = ops.(i) in
    List.exists
      (fun q ->
        List.exists
          (fun j -> j < i && (not finished.(j)) && not (commutes ops.(j) o))
          touching.(q))
      o.qubits
  in

  (* A one-qubit gate on a docked ion needs no movement: the dock can gate.

     It still needs a certificate witness.  Emitting the pulses and not the witness is
     exactly the kind of gap that looks harmless -- the tableau check passes, because it
     composes from the pulses that WERE emitted -- and is caught only by asking whether
     every op of the circuit has a witness.  The proved checker asks. *)
  (* Several one-qubit gates at once, one instruction per pulse ROUND rather than one per
     pulse per op: the k-th pulse of every gate in the batch shares an instruction, as the
     CX rounds in `contact` already do.  Every ion here is docked, so the gates are at
     distinct sites and R12 is safe by construction.  Each op still gets its own witness,
     naming the (shared) instruction that performed it. *)
  let emit_singles (items : (Circuit.op * string) list) =
    if items <> [] then begin
      let seqs =
        List.map
          (fun ((o : Circuit.op), i) ->
            let d = Gateset_composites.decompose_op ~gates:c.gates o.name o.params [ 0 ] in
            let dk =
              match Hashtbl.find_opt x.dock_of i with
              | Some (dk : Conveyor.dock) -> dk
              | None -> raise (Not_applicable "a one-qubit gate on an ion that is not docked")
            in
            (o, i, dk, d.pulses))
          items
      in
      let rounds = List.fold_left (fun acc (_, _, _, ps) -> max acc (List.length ps)) 0 seqs in
      let last_cycle : (int, int) Hashtbl.t = Hashtbl.create 32 in
      for k = 0 to rounds - 1 do
        let beams = ref [] and vzs = ref [] in
        List.iter
          (fun ((o : Circuit.op), i, _, ps) ->
            if k < List.length ps then
              match List.nth ps k with
              | Gateset.Beam { theta; phi; _ } -> beams := (i, [ theta; phi ], o.index) :: !beams
              | Gateset.Frame { lam; _ } -> vzs := (i, [ lam ], o.index) :: !vzs
              | Gateset.Ms _ ->
                raise (Not_applicable "a one-qubit gate decomposed to an entangler"))
          seqs;
        let emit gate kind its =
          if its <> [] then begin
            add Tsir.{ blank with ityp = "gate"; id = fresh (); gate = Some gate;
                       arity = Some 1; mode = Some "intra";
                       ions = List.rev_map (fun (i, _, _) -> i) its;
                       params = List.rev_map (fun (_, p, _) -> p) its;
                       meta = [ ("kind", `String kind); ("round", `Int k) ]
                              @ op_meta (List.rev_map (fun (_, _, g) -> g) its) };
            List.iter (fun (_, _, g) -> Hashtbl.replace last_cycle g !cyc) its;
            incr cyc
          end
        in
        emit "R" "beam" !beams;
        emit "VZ" "virtual_z" !vzs
      done;
      List.iter
        (fun ((o : Circuit.op), i, (dk : Conveyor.dock), _) ->
          cert_gates :=
            Cert.{ dag = o.index;
                   instr =
                     (try Hashtbl.find last_instr o.index
                      with Not_found -> !prog.id_seq - 1);
                   cycle = (try Hashtbl.find last_cycle o.index with Not_found -> !cyc - 1);
                   site = dk.site; operands = [ i ]; pulses = [] }
            :: !cert_gates)
        seqs
    end
  in

  (* One readout (or one reset) instruction naming every ancilla that is ready for it.
     A measurement costs one measurement time however many ions it names -- the optics
     address them together -- so reading 144 ancillas out one instruction at a time cost
     17 ms of a 98 ms round for no reason but emission order.  Neither needs a witness:
     the checker's `needsWitness` excludes measure and reset. *)
  let emit_spam kind (items : (int * string) list) =
    if items <> [] then begin
      add Tsir.{ blank with ityp = kind; id = fresh ();
                 ions = List.map snd items;
                 meta = [ ("kind", `String kind) ] @ op_meta (List.map fst items) };
      incr cyc
    end
  in

  (* Everything ready that costs no rotation: measure, reset and one-qubit gates on
     docked ions.  Taken in PASSES.  A pass collects every such op that is ready at the
     START of the pass -- two of them never share a qubit, because ops on one ancilla are
     a chain and at most one link of a chain is ready -- and emits one instruction per
     kind, then looks again, since finishing a measure makes the reset behind it ready.
     Returns whether anything was emitted. *)
  let drain () =
    let progressed = ref false in
    let again = ref true in
    while !again do
      let ready1 =
        List.filter
          (fun i ->
            (not finished.(i))
            && (not (blocked i))
            &&
            let (o : Circuit.op) = ops.(i) in
            match o.qubits with
            | [ _ ] -> true
            | [ _; _ ] -> false
            | _ ->
              if o.name = "barrier" then true
              else
                raise
                  (Not_applicable (Printf.sprintf "%s has an unsupported arity" o.name)))
          (List.init n_ops (fun i -> i))
      in
      if ready1 = [] then again := false
      else begin
        progressed := true;
        let measures = ref [] and resets = ref [] and singles = ref [] in
        List.iter
          (fun i ->
            let (o : Circuit.op) = ops.(i) in
            (match (o.name, o.qubits) with
            | "barrier", _ -> ()
            | "measure", [ q ] when Hashtbl.mem x.dock_of (ion q) ->
              measures := (o.index, ion q) :: !measures
            | "reset", [ q ] when Hashtbl.mem x.dock_of (ion q) ->
              resets := (o.index, ion q) :: !resets
            | _, [ q ] when Hashtbl.mem x.dock_of (ion q) -> singles := (o, ion q) :: !singles
            | _, [ q ] ->
              raise
                (Not_applicable
                   (Printf.sprintf
                      "%s acts on q%d, which rides the loop; only docked ions can gate"
                      o.name q))
            | _ -> raise (Not_applicable (Printf.sprintf "%s has an unsupported arity" o.name)));
            finished.(i) <- true;
            decr remaining)
          ready1;
        emit_spam "measure" (List.rev !measures);
        emit_spam "reset" (List.rev !resets);
        emit_singles (List.rev !singles)
      end
    done;
    !progressed
  in

  let contact_of (o : Circuit.op) =
    match o.qubits with
    | [ p; q ] ->
      let ip = ion p and iq = ion q in
      let rider, partner = if Hashtbl.mem x.dock_of ip then (iq, ip) else (ip, iq) in
      let ctrl_is_rider = ion (List.nth o.qubits 0) = rider in
      ignore ctrl_is_rider;
      if Hashtbl.mem x.dock_of rider then
        raise (Not_applicable "both operands are docked: nothing can bring them together")
      else
        (* the ORIENTATION matters: `cx anc,data` and `cx data,anc` are different gates,
           and an ESM round contains both (X checks control from the ancilla, Z checks
           control from the data) *)
        Some (rider, Hashtbl.find x.dock_of partner, partner, ctrl_is_rider)
    | _ -> None
  in

  let guard = ref 0 in
  while !remaining > 0 do
    incr guard;
    if !guard > 4 * n_ops + 16 then
      raise (Not_applicable "the schedule stopped making progress");
    (* everything ready that costs no rotation, first *)
    let progressed = ref (drain ()) in
    (* then the best rotation: the offset that serves the most ready contacts *)
    let ready =
      List.filter_map
        (fun i ->
          if finished.(i) || blocked i then None
          else match contact_of ops.(i) with Some ct -> Some (i, ct) | None -> None)
        (List.init n_ops (fun i -> i))
    in
    if ready = [] then begin
      if not !progressed then raise (Not_applicable "deadlock in the rotation schedule")
    end
    else begin
      let want =
        List.map
          (fun (i, (rider, (d : Conveyor.dock), partner, orient)) ->
            let slot = Hashtbl.find x.slot_of rider in
            (((d.rail_idx - slot) mod cv.n + cv.n) mod cv.n, i, rider, d, partner, orient))
          ready
      in
      let offsets = List.sort_uniq compare (List.map (fun (o, _, _, _, _, _) -> o) want) in
      let batch_size off =
        let at = List.filter (fun (o, _, _, _, _, _) -> o = off) want in
        let docks = List.sort_uniq compare
            (List.map (fun (_, _, _, (d : Conveyor.dock), _, _) -> d.site) at) in
        let riders = List.sort_uniq compare (List.map (fun (_, _, r, _, _, _) -> r) at) in
        min (List.length docks) (List.length riders)
      in
      let forward off = ((off - x.offset) mod cv.n + cv.n) mod cv.n in
      let score off =
        match sweep with
        | Greedy -> (batch_size off, -abs (Conveyor.shortest cv x.offset off))
        | Monotone -> (-forward off, batch_size off)
      in
      let best =
        List.fold_left
          (fun acc off -> match acc with
            | None -> Some off
            | Some b -> if score off > score b then Some off else acc)
          None offsets
        |> Option.get
      in
      (* take one contact per dock and one per rider at this offset.

         Chosen BEFORE the loop turns, though it is the turn that happens first: the
         selection reads only the precomputed offsets, and knowing the batch is what lets
         the rotation instruction say which circuit statements it is travelling towards.
         A rotation with no answer to that is the single most opaque thing in a compiled
         program -- 144 ions move and the page cannot say why. *)
      let used_dock = Hashtbl.create 32 and used_rider = Hashtbl.create 32 in
      let batch = ref [] in
      List.iter
        (fun (off, i, rider, (d : Conveyor.dock), partner, orient) ->
          if off = best && (not (Hashtbl.mem used_dock d.site))
             && not (Hashtbl.mem used_rider rider)
          then begin
            Hashtbl.replace used_dock d.site ();
            Hashtbl.replace used_rider rider ();
            batch := (rider, d, partner, i, orient) :: !batch;
            finished.(i) <- true;
            decr remaining
          end)
        want;
      let batch = List.rev !batch in
      rotate
        ~ops:(List.map (fun (_, _, _, i, _) -> i) batch)
        (match sweep with
        | Greedy -> Conveyor.shortest cv x.offset best
        | Monotone -> ((best - x.offset) mod cv.n + cv.n) mod cv.n);
      (* after the turn: every rider now standing beside a dock that is not in the
         batch docks anyway on a switchless machine -- see `contact` *)
      let extra =
        if not dock_all then []
        else begin
          let in_batch = List.map (fun (r, _, _, _, _) -> r) batch in
          let at_rail = Hashtbl.create 64 in
          Hashtbl.iter (fun ion _ -> Hashtbl.replace at_rail (site_of x ion) ion) x.slot_of;
          Array.to_list cv.docks
          |> List.filter_map (fun (d : Conveyor.dock) ->
                 match Hashtbl.find_opt at_rail d.rail with
                 | Some r when not (List.mem r in_batch) -> Some (r, d)
                 | _ -> None)
        end
      in
      contact ~extra batch
    end
  done;

  notes :=
    [ Conveyor.describe cv;
      layout_note;
      Printf.sprintf "%d rotations totalling %d hops, %d contacts in %d batches"
        !n_rot !n_hops !n_contacts !n_batches ]
    @ (if dock_all
       then [ Printf.sprintf "dock-all: %d riders docked and undocked without a gate, so \
                             every spur acts in every batch" !n_extra ]
       else []);
  let cert =
    Cert.
      {
        version = 1;
        circuit_ops =
          List.map
            (fun (o : Circuit.op) ->
              { oi = o.index; oname = o.name; oqubits = o.qubits; oparams = o.params;
                osrc = o.src_line })
            c.ops;
        circuit_sha256 = Cert.hash_file qasm_path;
        arch_sha256 = "";
        circuit_name = c.name;
        arch_name = a.name;
        n_qubits = c.n_qubits;
        map_ = List.init c.n_qubits (fun q -> (q, ion q));
        init = placement;
        moves = List.rev !cert_moves;
        rotations = List.rev !cert_rots;
        gates = List.rev !cert_gates;
        unrealised = [];
        claims =
          [ ("rotations", `Int !n_rot); ("hops", `Int !n_hops);
            ("contacts", `Int !n_contacts); ("batches", `Int !n_batches) ];
      }
  in
  ( !prog, cert,
    { contacts = !n_contacts; rotations = !n_rot; hops = !n_hops; batches = !n_batches },
    !notes )
