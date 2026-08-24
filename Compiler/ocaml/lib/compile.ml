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
   disagreeing about what depends on what. *)
let layer_of (c : Circuit.t) : int array =
  let n = List.length c.ops in
  let out = Array.make n 0 in
  let last = Hashtbl.create 64 in
  List.iter
    (fun (o : Circuit.op) ->
      let ws = Circuit.wires_of c o in
      let l =
        List.fold_left
          (fun acc w ->
            max acc (1 + (try Hashtbl.find last w with Not_found -> -1)))
          0 ws
      in
      out.(o.index) <- l;
      List.iter (fun w -> Hashtbl.replace last w l) ws)
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
let meet (a : Arch.t) (t : Traps.t) (d : Traps.dists) ~(claimed : (string, unit) Hashtbl.t)
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
      if Hashtbl.mem claimed m || (not (can_gate a m)) || room < 2 then best
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
let nearest (a : Arch.t) (t : Traps.t) (d : Traps.dists) ~(claimed : (string, unit) Hashtbl.t)
    ~(occ : (string, int) Hashtbl.t) ~(from : string) ~(cap : Arch.t -> string -> bool) :
    string option =
  if cap a from && not (Hashtbl.mem claimed from) then Some from
  else
    List.fold_left
      (fun best m ->
        let room = capacity a m - occ_of occ m in
        if Hashtbl.mem claimed m || (not (cap a m)) || room < 1 then best
        else
          match (Traps.dist d from m, best) with
          | None, _ -> best
          | Some s, None -> Some (m, s)
          | Some s, Some (_, bs) when s < bs -> Some (m, s)
          | _ -> best)
      None t.sites
    |> Option.map fst

(* ------------------------------------------------------------------ the driver *)

let run_once ?(policy = default_policy) ?(record : Yojson.Safe.t list ref option)
    ?(variant = 0) (a : Arch.t) (c : Circuit.t) ~(arch_path : string)
    ~(qasm_path : string) : result =
  (* Lower first.  The router meets a PAIR of ions in one trap; three ions in one trap is
     a different problem most shipped devices cannot host at all, so a Toffoli becomes six
     CX before placement ever sees it.  Everything downstream -- placement, routing, the
     certificate's op list -- is about the LOWERED circuit; R10's stabilizer half still
     compares the emitted pulses against the ORIGINAL, so the lowering is checked rather
     than trusted. *)
  let c, n_lowered = Circuit.lower c in
  let t = Traps.build a in
  let d = Traps.all_dists t in
  let pl = Place.run ~variant a t d c in
  let notes = ref (List.rev pl.notes) in
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

  Array.iteri
    (fun layer_i (ops : Circuit.op list) ->
      let claimed = Hashtbl.create 16 in
      let occ = occupancy pos in
      (* 1. where each op must happen *)
      let sited =
        List.filter_map
          (fun (o : Circuit.op) ->
            match (o.name, o.qubits) with
            | "barrier", _ -> Some (o, None)
            | ("measure" | "reset"), [ q ] ->
              (* SPAM does NOT compete with gates for traps.  R12 bounds one *gate* per
                 trap per cycle; a measurement is a different instruction in a different
                 cycle, and several ions in one trap can be read out together.  Excluding
                 claimed traps here pushed every readout to a further and further trap and
                 cost `cyclone_base` 151 transport cycles where 19 suffice -- an eightfold
                 penalty for a constraint that does not exist. *)
              let ion = ion_of q in
              let from = Hashtbl.find pos ion in
              let unclaimed = Hashtbl.create 1 in
              (match nearest a t d ~claimed:unclaimed ~occ ~from ~cap:can_spam with
              | Some s -> Some (o, Some (s, [ ion ]))
              | None ->
                unrealised := o.index :: !unrealised;
                None)
            | _, [ q ] ->
              let ion = ion_of q in
              let from = Hashtbl.find pos ion in
              (match nearest a t d ~claimed ~occ ~from ~cap:can_gate with
              | Some s ->
                Hashtbl.replace claimed s ();
                Some (o, Some (s, [ ion ]))
              | None ->
                unrealised := o.index :: !unrealised;
                None)
            | _, [ qa; qb ] ->
              let ia = ion_of qa and ib = ion_of qb in
              let sa = Hashtbl.find pos ia and sb = Hashtbl.find pos ib in
              (match meet a t d ~claimed ~occ ~here:[ sa; sb ] ~sa ~sb with
              | Some s ->
                Hashtbl.replace claimed s ();
                Some (o, Some (s, [ ia; ib ]))
              | None ->
                unrealised := o.index :: !unrealised;
                None)
            | _, qs ->
              (* three-qubit gates would need a trap that holds three ions AND a
                 decomposition into pairs that are all co-located; refusing loudly beats
                 emitting something that looks fine and is not *)
              ignore qs;
              unrealised := o.index :: !unrealised;
              None)
          ops
      in

      (* 2. route everything that has to move *)
      let needed =
        List.concat_map
          (fun (_, site) ->
            match site with
            | None -> []
            | Some (s, ions) -> List.map (fun i -> (i, s)) ions)
          sited
      in
      let targets = needed |> List.filter (fun (i, s) -> Hashtbl.find pos i <> s) in
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
      if targets <> [] then begin
        let before = Hashtbl.copy pos in
        let plan = Route.plan_layer a t d ~pos ~targets ~horizon in
        (* record the sub-problem AND what the heuristic achieved on it, so C4 can measure
           the gap against an optimal solver on the very same instance *)
        (match record with
        | None -> ()
        | Some acc ->
          acc :=
            Route.instance_json a t ~pos:before ~targets ~horizon
              ~heuristic:(List.length plan.cycles)
            :: !acc);
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
                    [ ("kind", `String "route") ]
                    @ op_meta
                        (List.concat_map
                           (fun (m : Route.move) ->
                             try Hashtbl.find ion_ops m.ion with Not_found -> [])
                           cy.moves);
                };
            incr n_transport;
            incr cyc)
          plan.cycles
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
                let qmap = List.mapi (fun i ion -> (ion, i)) ions in
                ignore qmap;
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
         double the transport for nothing. *)
      let next_layer_ions =
        if layer_i + 1 < Array.length by_layer then
          List.concat_map
            (fun (o : Circuit.op) -> List.map ion_of o.qubits)
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
               && Hashtbl.find pos i <> Hashtbl.find pl.site i)
        |> List.map (fun i -> (i, Hashtbl.find pl.site i))
      in
      if returning <> [] then begin
        match Route.plan_layer a t d ~pos ~targets:returning ~horizon with
        | plan ->
          List.iter
            (fun (cy : Route.cycle) ->
              List.iter
                (fun (m : Route.move) ->
                  cert_moves :=
                    Cert.
                      { cycle = !cyc; ion = m.ion; src = m.src; dst = m.dst; via = m.via }
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
                    (* the layer's ops again: this transport is not travelling TOWARDS
                       them -- they have happened -- it is clearing the gate zone after
                       them, and `qccd.ir.source_map` sorts the two apart by position *)
                    meta =
                      [ ("kind", `String "undock") ]
                      @ op_meta
                          (List.concat_map
                             (fun (m : Route.move) ->
                               try Hashtbl.find ion_ops m.ion with Not_found -> [])
                             cy.moves);
                  };
              incr n_transport;
              incr cyc)
            plan.cycles
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
      end)
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
   `clifford12` on `ladder_2x72` compiling after the hill-climb was added. *)
let run ?(policy = default_policy) ?(record : Yojson.Safe.t list ref option) (a : Arch.t)
    (c : Circuit.t) ~(arch_path : string) ~(qasm_path : string) : result =
  let rec attempt v last =
    if v > 2 then match last with Some e -> raise e | None -> assert false
    else
      match run_once ~policy ?record ~variant:v a c ~arch_path ~qasm_path with
      | r -> r
      | exception (Route.Unroutable _ as e) ->
        (match record with Some acc -> acc := [] | None -> ());
        attempt (v + 1) (Some e)
  in
  attempt 0 None
