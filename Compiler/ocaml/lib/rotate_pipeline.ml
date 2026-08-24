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

let run ?(sweep = Monotone) (a : Arch.t) (c : Circuit.t) ~(arch_path : string)
    ~(qasm_path : string) : Tsir.t * Cert.t * plan * string list =
  let cv =
    match Conveyor.detect a with
    | Some cv -> cv
    | None -> raise (Not_applicable "the device has no closed loop with docks")
  in
  let dock_side, loop_side = bipartition c in
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
  List.iteri (fun k q -> Hashtbl.replace x.dock_of (ion q) cv.docks.(k)) dock_side;
  (* Spread the loop ions evenly rather than packing them: the loop has more slots than
     qubits on a real round, and spacing them keeps consecutive contacts from needing a
     full turn between them. *)
  let stride = max 1 (cv.n / max 1 (List.length loop_side)) in
  List.iteri (fun k q -> Hashtbl.replace x.slot_of (ion q) (k * stride mod cv.n)) loop_side;

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
  let contact (pairs4 : (string * Conveyor.dock * string * int * bool) list) =
    if pairs4 <> [] then begin
      let batch_ops = List.map (fun (_, _, _, dag, _) -> dag) pairs4 in
      let dock_moves dir =
        List.map
          (fun (rider, (d : Conveyor.dock), _, _, _) ->
            if dir then Tsir.{ ion = rider; src = d.rail; dst = d.site; via = [ d.spur ] }
            else Tsir.{ ion = rider; src = d.site; dst = d.rail; via = [ d.spur ] })
          pairs4
      in
      add Tsir.{ blank with ityp = "simd"; id = fresh (); cls = Some cv.dock_cls;
                 mode = Some "inter"; participants = dock_moves true;
                 meta = [ ("kind", `String "dock") ] @ op_meta batch_ops };
      List.iter
        (fun (rider, (d : Conveyor.dock), _, _, _) ->
          cert_moves :=
            Cert.{ cycle = !cyc; ion = rider; src = d.rail; dst = d.site; via = [ d.spur ] }
            :: !cert_moves)
        pairs4;
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
        (fun (rider, (d : Conveyor.dock), _, _, _) ->
          cert_moves :=
            Cert.{ cycle = !cyc; ion = rider; src = d.site; dst = d.rail; via = [ d.spur ] }
            :: !cert_moves)
        pairs4;
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
  let emit_1q (o : Circuit.op) (i : string) =
    let d = Gateset_composites.decompose_op ~gates:c.gates o.name o.params [ 0 ] in
    let start = !cyc in
    List.iter
      (fun (p : Gateset.pulse) ->
        match p with
        | Gateset.Beam { theta; phi; _ } ->
          add Tsir.{ blank with ityp = "gate"; id = fresh (); gate = Some "R";
                     arity = Some 1; mode = Some "intra"; ions = [ i ];
                     params = [ [ theta; phi ] ];
                     meta = [ ("kind", `String "beam") ] @ op_meta [ o.index ] };
          incr cyc
        | Gateset.Frame { lam; _ } ->
          add Tsir.{ blank with ityp = "gate"; id = fresh (); gate = Some "VZ";
                     arity = Some 1; mode = Some "intra"; ions = [ i ];
                     params = [ [ lam ] ];
                     meta = [ ("kind", `String "virtual_z") ] @ op_meta [ o.index ] };
          incr cyc
        | Gateset.Ms _ ->
          raise (Not_applicable "a one-qubit gate decomposed to an entangler"))
      d.pulses;
    (match Hashtbl.find_opt x.dock_of i with
    | Some (dk : Conveyor.dock) ->
      cert_gates :=
        Cert.{ dag = o.index;
               instr =
                 (try Hashtbl.find last_instr o.index
                  with Not_found -> !prog.id_seq - 1);
               cycle = max start (!cyc - 1);
               site = dk.site; operands = [ i ]; pulses = [] }
        :: !cert_gates
    | None -> raise (Not_applicable "a one-qubit gate on an ion that is not docked"))
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
    let progressed = ref false in
    for i = 0 to n_ops - 1 do
      if (not finished.(i)) && not (blocked i) then begin
        let (o : Circuit.op) = ops.(i) in
        match (o.name, o.qubits) with
        | ("measure" | "reset"), [ q ] when Hashtbl.mem x.dock_of (ion q) ->
          add Tsir.{ blank with ityp = o.name; id = fresh (); ions = [ ion q ];
                     meta = [ ("kind", `String o.name) ] @ op_meta [ o.index ] };
          incr cyc;
          finished.(i) <- true; decr remaining; progressed := true
        | "barrier", _ ->
          finished.(i) <- true; decr remaining; progressed := true
        | _, [ q ] when Hashtbl.mem x.dock_of (ion q) ->
          emit_1q o (ion q);
          finished.(i) <- true; decr remaining; progressed := true
        | _, [ q ] ->
          raise
            (Not_applicable
               (Printf.sprintf
                  "%s acts on q%d, which rides the loop; only docked ions can gate" o.name
                  q))
        | _, [ _; _ ] -> ()
        | _ -> raise (Not_applicable (Printf.sprintf "%s has an unsupported arity" o.name))
      end
    done;
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
      contact batch
    end
  done;

  notes :=
    [ Conveyor.describe cv;
      Printf.sprintf "%d qubits at docks, %d riding the loop (stride %d)"
        (List.length dock_side) (List.length loop_side) stride;
      Printf.sprintf "%d rotations totalling %d hops, %d contacts in %d batches"
        !n_rot !n_hops !n_contacts !n_batches ];
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
