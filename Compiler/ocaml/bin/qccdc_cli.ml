(* `qccdc` -- the compiler front end.

   At C0 it does exactly two things, and neither of them compiles anything.  That is the
   point: before any pass exists, prove that OCaml can read a real hardware program and a
   real architecture and hand them back unchanged.  An interface validated against the
   397 184 / 8 808 oracle costs an afternoon; an interface assumed and found wrong in
   month three costs the milestone. *)

let usage () =
  prerr_endline
    {|qccdc -- the QCCD compiler

  qccdc arch <expanded.json>
      read an expanded architecture and report its structure and routing regime

  qccdc roundtrip <in.tsir.json> -o <out.tsir.json>
      read a TSIR program, validate its shape, write it back

  qccdc parse <in.qasm> [-o <out.dag.json>]
      parse OpenQASM 2.0, flatten it, build the dependency DAG

  qccdc decompose <in.qasm>
      decompose every gate into native pulses and report the cost

  qccdc traps <expanded.json>
      build the trap graph (what one machine cycle can move an ion between)

  qccdc compile <in.qasm> --arch <expanded.json> -o <out-prefix>
      the whole pipeline: place, route, decompose, emit TSIR + certificate

  qccdc route-instances <in.qasm> --arch <expanded.json> -o <instances.json>
      dump every routing sub-problem the heuristic router solved, with its makespan,
      for the SAT oracle to solve optimally on the same graph

  qccdc compile ... --no-rotate
      refuse rather than falling back to rigid rotation, so that a measurement of the
      individual-ion router measures the individual-ion router

  qccdc rotate <in.qasm> --arch <expanded.json> -o <out-prefix>
      compile by rotating the loop rigidly: one template moves every ion at once.
      For a conveyor device and a bipartite circuit (a syndrome-extraction round).

  qccdc rotate ... --placement <layout.json>      (also honoured by `compile`'s fallback)
      start the riders in the loop slots, and park the docked qubits at the docks, that
      the file names: {"loop": {"<qubit>": slot}, "docks": {"<qubit>": "<dock site>"}}.
      A code-aware placer decides the layout; this pass still certifies the program.

  qccdc rotate ... --dock-all
      dock every rider standing beside a dock in every batch, gate only the contacts:
      what a machine whose spurs share one waveform with no per-site switch must do.

  qccdc racetrack <in.qasm> --arch <expanded.json> -o <out-prefix> [--init-placement <json>]
      compile on a race track -- one closed loop with its gate zones ON it and no docks
      (Quantinuum H2): rigid shifts of the whole loop, swaps in the capacity-2 zones, and
      up to one gate per gate zone per round.  Also `compile`'s last fallback, after the
      general router and rigid rotation have both declined (not under --no-rotate).

  qccdc pulses-selftest
      differential-test the pulse table against the unitaries it claims to implement

`arch` and `roundtrip` are C0: they establish the OCaml/Python interface.
`parse` is C1: the front end, differential-tested against qiskit's circuit_to_dag.|};
  exit 2

let arg_after flag argv =
  let rec go = function
    | a :: b :: _ when a = flag -> Some b
    | _ :: rest -> go rest
    | [] -> None
  in
  go argv

(* `--placement <json>`: a layout decided upstream (see `Rotate_pipeline.placement`).
   Read here, once, so both the `rotate` command and `compile`'s rotation fallback see
   the same file; a malformed one is a usage error, not a compile result. *)
let placement_arg rest =
  match arg_after "--placement" rest with
  | None -> None
  | Some path -> (
    try Some (Qccdc.Rotate_pipeline.placement_of_file path) with
    | Invalid_argument m | Sys_error m | Yojson.Json_error m ->
      Printf.eprintf "--placement %s: %s\n" path m;
      exit 2)

let cmd_arch path =
  let a = Qccdc.Arch.load path in
  print_endline (Qccdc.Arch.describe a);
  (* the structural self-check runs on every load, not behind a flag: a reader that
     silently misparsed the graph is worse than one that refuses to. *)
  (match Qccdc.Arch.check_structure a with
  | [] -> ()
  | errs ->
    prerr_endline "  STRUCTURE ERRORS:";
    List.iter (fun e -> prerr_endline ("    " ^ e)) (List.filteri (fun i _ -> i < 6) errs);
    Printf.eprintf "    (%d total)\n" (List.length errs);
    exit 3);
  List.iter
    (fun (c : Qccdc.Arch.simd_class) ->
      Printf.printf "  class %-12s type=%-6s orbit=%-6s delta=%-4s entails=[%s]\n" c.cid
        c.ctype
        (Option.value c.orbit ~default:"-")
        (match c.delta with Some d -> string_of_int d | None -> "-")
        (String.concat "," c.entails))
    a.classes;
  List.iter
    (fun (l : Qccdc.Arch.loop) ->
      Printf.printf "  loop  %-6s %d nodes, closed=%b, %d corners\n" l.lid
        (List.length l.nodes) l.closed (List.length l.corners))
    a.loops;
  0

let cmd_roundtrip inp out =
  let p = Qccdc.Tsir.load inp in
  (match Qccdc.Tsir.validate p with
  | [] -> ()
  | errs ->
    prerr_endline "SHAPE ERRORS:";
    List.iter (fun e -> prerr_endline ("  " ^ e)) errs;
    exit 2);
  Printf.printf "%s: %d instructions, id_seq %d\n" p.name (Qccdc.Tsir.length p) p.id_seq;
  Printf.printf "  templates %s\n"
    (String.concat ", "
       (List.map (fun (k, v) -> Printf.sprintf "%s=%d" k v) (Qccdc.Tsir.templates p)));
  Qccdc.Tsir.save out p;
  Printf.printf "  -> %s\n" out;
  0

let cmd_parse inp out =
  let prog = Qccdc.Qasm.parse_file inp in
  let name = Filename.remove_extension (Filename.basename inp) in
  let c = Qccdc.Circuit.build ~name prog in
  print_endline (Qccdc.Circuit.summary c);
  (match out with
  | None -> ()
  | Some path ->
    let oc = open_out_bin path in
    Fun.protect
      ~finally:(fun () -> close_out oc)
      (fun () ->
        output_string oc (Yojson.Safe.to_string (Qccdc.Circuit.to_json c));
        output_char oc '
');
    Printf.printf "  -> %s
" path);
  0

let cmd_decompose inp =
  let prog = Qccdc.Qasm.parse_file inp in
  let name = Filename.remove_extension (Filename.basename inp) in
  let c = Qccdc.Circuit.build ~name prog in
  let beams = ref 0 and ms = ref 0 and frames = ref 0 and skipped = ref [] in
  List.iter
    (fun (o : Qccdc.Circuit.op) ->
      if List.mem o.name [ "measure"; "reset"; "barrier" ] then ()
      else
        match Qccdc.Gateset_composites.decompose_op ~gates:c.gates o.name o.params
                o.qubits with
        | d ->
          beams := !beams + Qccdc.Gateset.pulse_count d - Qccdc.Gateset.ms_count d;
          ms := !ms + Qccdc.Gateset.ms_count d;
          frames :=
            !frames
            + List.length
                (List.filter (fun p -> not (Qccdc.Gateset.is_physical p)) d.pulses)
        | exception Qccdc.Gateset.Unsupported m ->
          if not (List.mem m !skipped) then skipped := m :: !skipped)
    c.ops;
  Printf.printf "%s
" (Qccdc.Circuit.summary c);
  Printf.printf "  native: %d beams, %d MS gates, %d virtual-Z frame updates (free)
"
    !beams !ms !frames;
  Printf.printf "  physical pulses: %d
" (!beams + !ms);
  List.iter (fun m -> Printf.printf "  UNSUPPORTED: %s
" m) (List.rev !skipped);
  if !skipped = [] then 0 else 1

let cmd_selftest () =
  Printf.printf "%-28s %-12s %8s %8s
" "gate" "max error" "beams" "MS";
  print_endline (String.make 60 '-');
  let worst = ref 0.0 in
  List.iter
    (fun (r : Qccdc.Gateset_composites.report) ->
      worst := Float.max !worst r.worst;
      Printf.printf "%-28s %-12.3e %8d %8d
" r.name r.worst r.beams r.ms)
    (Qccdc.Gateset_composites.check ());
  Printf.printf "
worst error %.3e over the whole table
" !worst;
  if !worst < 1e-12 then (print_endline "PULSES PASS"; 0)
  else (print_endline "PULSES FAIL"; 1)

let cmd_traps path =
  let a = Qccdc.Arch.load path in
  let t = Qccdc.Traps.build a in
  print_endline (Qccdc.Arch.describe a);
  print_endline ("  " ^ Qccdc.Traps.describe t a);
  (* connectivity is the property the router depends on and the one a wrong `via`
     convention silently destroys, so it is measured rather than assumed *)
  let d = Qccdc.Traps.all_dists t in
  (match t.sites with
  | [] -> print_endline "  NO TRAPS"
  | s0 :: _ ->
    let row = Hashtbl.find d s0 in
    let reach = Hashtbl.length row in
    let ecc = Hashtbl.fold (fun _ v acc -> max acc v) row 0 in
    Printf.printf "  from %s: %d/%d traps reachable, eccentricity %d
" s0 reach
      (Qccdc.Traps.n_sites t) ecc;
    if reach < Qccdc.Traps.n_sites t then
      print_endline "  WARNING: the trap graph is disconnected");
  0

(* `--init-placement <json>`: `{"q0": "T0_0h", ...}` -- every ion the circuit names, at
   the trap it starts in.  Read once, like `--placement`; a malformed file is a usage
   error, not a compile result. *)
let init_placement_arg rest =
  match arg_after "--init-placement" rest with
  | None -> None
  | Some path -> (
    try
      match Yojson.Safe.from_file path with
      | `Assoc kv ->
        Some
          (List.map
             (fun (ion, v) ->
               match v with
               | `String s -> (ion, s)
               | _ -> invalid_arg (ion ^ ": site must be a string"))
             kv)
      | _ -> invalid_arg "expected an object {ion: site}"
    with Invalid_argument m | Sys_error m | Yojson.Json_error m ->
      Printf.eprintf "--init-placement %s: %s\n" path m;
      exit 2)

let cmd_compile ?init inp arch_path out =
  let a = Qccdc.Arch.load arch_path in
  (match Qccdc.Arch.check_structure a with
  | [] -> ()
  | errs ->
    prerr_endline "STRUCTURE ERRORS in the architecture:";
    List.iter (fun e -> prerr_endline ("  " ^ e)) errs;
    exit 3);
  let prog = Qccdc.Qasm.parse_file inp in
  let name = Filename.remove_extension (Filename.basename inp) in
  let c = Qccdc.Circuit.build ~name prog in
  let r =
    Qccdc.Compile.run ?init a c ~arch_path:("arch/" ^ a.name ^ ".arch.json") ~qasm_path:inp
  in
  Printf.printf "%s -> %s (%s regime)
" (Qccdc.Circuit.summary c) a.name
    (Qccdc.Arch.regime_name (Qccdc.Arch.regime a));
  Printf.printf
    "  %d layers, %d transport cycles, %d beams, %d MS, %d virtual-Z, %d instructions
"
    r.stats.layers r.stats.transport_cycles r.stats.beams r.stats.ms_gates
    r.stats.frames r.stats.instructions;
  List.iter (fun n -> Printf.printf "  - %s
" n) r.notes;
  (* the fast pre-flight: a compiler bug should surface here, not three milestones later *)
  (match Qccdc.Cert.check r.cert with
  | [] -> Printf.printf "  certificate: %d gate witnesses, self-check OK
"
            (List.length r.cert.gates)
  | fs ->
    Printf.printf "  CERTIFICATE SELF-CHECK FAILED (%d)
" (List.length fs);
    List.iteri
      (fun i (f : Qccdc.Cert.failure) ->
        if i < 8 then Printf.printf "    %s: %s
" f.where f.why)
      fs);
  (* The UNREALISED verdict is NOT printed here.  Leaving ops unrealised is a decline,
     and the caller may retry with rigid rotation -- printing the verdict before that
     retry publishes an outcome the retry can supersede, and `bridge/run_matrix.py`
     greps stdout for exactly this string.  The list is returned instead, and whoever
     decides the final answer prints it.  See `report_unrealised`. *)
  (match Qccdc.Tsir.validate r.prog with
  | [] -> ()
  | errs ->
    prerr_endline "  TSIR SHAPE ERRORS:";
    List.iter (fun e -> prerr_endline ("    " ^ e)) errs;
    exit 2);
  Qccdc.Tsir.save (out ^ ".tsir.json") r.prog;
  Qccdc.Cert.save (out ^ ".qcert.json") r.cert;
  Printf.printf "  -> %s.tsir.json  %s.qcert.json
" out out;
  ((if Qccdc.Cert.check r.cert <> [] then 1 else 0), r.cert.unrealised)

(* The verdict, in the exact words `bridge/run_matrix.py` greps for.  Printed only once
   the answer is final -- after a rotation retry has succeeded or declined. *)
let report_unrealised (ops : int list) =
  if ops <> [] then
    Printf.printf "  UNREALISED ops: %d (%s)
" (List.length ops)
      (String.concat "," (List.filteri (fun i _ -> i < 8) (List.map string_of_int ops)))

let cmd_route_instances inp arch_path out =
  let a = Qccdc.Arch.load arch_path in
  let prog = Qccdc.Qasm.parse_file inp in
  let name = Filename.remove_extension (Filename.basename inp) in
  let c = Qccdc.Circuit.build ~name prog in
  let record = ref [] in
  let r =
    Qccdc.Compile.run ~record a c ~arch_path:("arch/" ^ a.name ^ ".arch.json")
      ~qasm_path:inp
  in
  let insts = List.rev !record in
  let doc =
    `Assoc
      [ ("circuit", `String name); ("arch", `String a.name);
        ("layers", `Int r.stats.layers);
        ("instances", `List insts) ]
  in
  let oc = open_out_bin out in
  Fun.protect
    ~finally:(fun () -> close_out oc)
    (fun () ->
      output_string oc (Yojson.Safe.to_string doc);
      output_char oc '
');
  Printf.printf "%s on %s: %d routing instances -> %s
" name a.name
    (List.length insts) out;
  0

let cmd_rotate ?(sweep = Qccdc.Rotate_pipeline.Monotone) ?placement ?(dock_all = false) inp
    arch_path out =
  let a = Qccdc.Arch.load arch_path in
  let prog = Qccdc.Qasm.parse_file inp in
  let name = Filename.remove_extension (Filename.basename inp) in
  let c = Qccdc.Circuit.build ~name prog in
  let c, _ = Qccdc.Circuit.lower c in
  let p, cert, st, notes =
    Qccdc.Rotate_pipeline.run ~sweep ?placement ~dock_all a c
      ~arch_path:("arch/" ^ a.name ^ ".arch.json") ~qasm_path:inp
  in
  Printf.printf "%s -> %s (rigid rotation)
" (Qccdc.Circuit.summary c) a.name;
  List.iter (fun n -> Printf.printf "  - %s
" n) notes;
  Printf.printf "  %d instructions
" (Qccdc.Tsir.length p);
  (match Qccdc.Tsir.validate p with
  | [] -> ()
  | errs ->
    prerr_endline "  TSIR SHAPE ERRORS:";
    List.iter (fun e -> prerr_endline ("    " ^ e)) errs;
    exit 2);
  Printf.printf "  %d contacts in %d batches, %d rotations, %d hops
" st.contacts
    st.batches st.rotations st.hops;
  (* the loop's node order for the self-check comes from the ARCHITECTURE, the same place
     `mk_qcheck_input.py` reads it for the Lean checker *)
  let loops =
    List.filter_map
      (fun (l : Qccdc.Arch.loop) ->
        if l.closed then Some (l.lid, Array.of_list l.nodes) else None)
      a.loops
  in
  (match Qccdc.Cert.check ~loops cert with
  | [] ->
    Printf.printf
      "  certificate: %d gate witnesses, %d moves, %d rotations, self-check OK
"
      (List.length cert.gates) (List.length cert.moves) (List.length cert.rotations)
  | fs ->
    Printf.printf "  CERTIFICATE SELF-CHECK FAILED (%d)
" (List.length fs);
    List.iteri
      (fun i (f : Qccdc.Cert.failure) ->
        if i < 6 then Printf.printf "    %s: %s
" f.where f.why)
      fs);
  Qccdc.Tsir.save (out ^ ".tsir.json") p;
  Qccdc.Cert.save (out ^ ".qcert.json") cert;
  Printf.printf "  -> %s.tsir.json  %s.qcert.json
" out out;
  if Qccdc.Cert.check ~loops cert <> [] then 1 else 0

(* The race-track pass (`Racetrack`): a closed loop with no docks.  Nothing is written
   unless it succeeds, so a decline leaves whatever a caller wrote before it untouched. *)
let cmd_racetrack ?(strict = false) ?init inp arch_path out =
  let a = Qccdc.Arch.load arch_path in
  let prog = Qccdc.Qasm.parse_file inp in
  let name = Filename.remove_extension (Filename.basename inp) in
  let c = Qccdc.Circuit.build ~name prog in
  let p, cert, _, notes =
    Qccdc.Racetrack.run ?init a c ~arch_path:("arch/" ^ a.name ^ ".arch.json") ~qasm_path:inp
  in
  (match Qccdc.Tsir.validate p with
  | [] -> ()
  | errs ->
    prerr_endline "  TSIR SHAPE ERRORS:";
    List.iter (fun e -> prerr_endline ("    " ^ e)) errs;
    raise (Qccdc.Racetrack.Not_applicable "the programme it built is malformed"));
  let loops =
    List.filter_map
      (fun (l : Qccdc.Arch.loop) ->
        if l.closed then Some (l.lid, Array.of_list l.nodes) else None)
      a.loops
  in
  let fails = Qccdc.Cert.check ~loops cert in
  (* as a fallback it must not replace the caller's answer with a programme its own
     certificate refutes *)
  if strict && fails <> [] then
    raise
      (Qccdc.Racetrack.Not_applicable
         (Printf.sprintf "its certificate fails the self-check (%s)"
            (match fails with f :: _ -> f.where ^ ": " ^ f.why | [] -> "")));
  Printf.printf "%s -> %s (race track)\n" (Qccdc.Circuit.summary c) a.name;
  List.iter (fun n -> Printf.printf "  - %s\n" n) notes;
  Printf.printf "  %d instructions\n" (Qccdc.Tsir.length p);
  (match fails with
  | [] ->
    Printf.printf
      "  certificate: %d gate witnesses, %d moves, %d rotations, self-check OK\n"
      (List.length cert.gates) (List.length cert.moves) (List.length cert.rotations)
  | fs ->
    Printf.printf "  CERTIFICATE SELF-CHECK FAILED (%d)\n" (List.length fs);
    List.iteri
      (fun i (f : Qccdc.Cert.failure) ->
        if i < 6 then Printf.printf "    %s: %s\n" f.where f.why)
      fs);
  Qccdc.Tsir.save (out ^ ".tsir.json") p;
  Qccdc.Cert.save (out ^ ".qcert.json") cert;
  Printf.printf "  -> %s.tsir.json  %s.qcert.json\n" out out;
  if fails <> [] then 1 else 0

(* `compile`'s last resort: the race track, once rotation has declined.  [k] is what the
   caller does if this declines too -- exactly what it did before the pass existed. *)
let try_racetrack ?init inp arch_path out (k : unit -> 'a) : 'a =
  prerr_endline "  trying the race track: rigid shifts of the whole loop, swaps in its zones";
  match cmd_racetrack ~strict:true ?init inp arch_path out with
  | code -> exit code
  | exception Qccdc.Racetrack.Not_applicable r ->
    Printf.eprintf "  the race track does not apply either: %s\n" r;
    k ()
  | exception e ->
    (* a fault in the newest pass must not cost the caller the answer it had *)
    Printf.eprintf "  the race track failed (%s); the answer above stands\n"
      (Printexc.to_string e);
    k ()

let () =
  let argv = Array.to_list Sys.argv |> List.tl in
  match argv with
  | "rotate" :: inp :: rest -> (
    match (arg_after "--arch" rest, arg_after "-o" rest) with
    | Some ap, Some out -> (
      let sweep =
        match arg_after "--sweep" rest with
        | Some "greedy" -> Qccdc.Rotate_pipeline.Greedy
        | _ -> Qccdc.Rotate_pipeline.Monotone
      in
      let placement = placement_arg rest in
      let dock_all = List.mem "--dock-all" rest in
      try exit (cmd_rotate ~sweep ?placement ~dock_all inp ap out) with
      | Qccdc.Rotate_pipeline.Not_applicable m ->
        Printf.eprintf "%s: rotation does not apply: %s
" inp m; exit 5
      | Qccdc.Qasm.Error m -> Printf.eprintf "%s: parse error: %s
" inp m; exit 2)
    | _ -> usage ())
  | "racetrack" :: inp :: rest -> (
    match (arg_after "--arch" rest, arg_after "-o" rest) with
    | Some ap, Some out -> (
      try exit (cmd_racetrack ?init:(init_placement_arg rest) inp ap out) with
      | Qccdc.Racetrack.Not_applicable m ->
        Printf.eprintf "%s: the race track does not apply: %s\n" inp m;
        exit 5
      | Qccdc.Qasm.Error m -> Printf.eprintf "%s: parse error: %s\n" inp m; exit 2
      | Qccdc.Circuit.Error m -> Printf.eprintf "%s: %s\n" inp m; exit 2)
    | _ -> usage ())
  | "route-instances" :: inp :: rest -> (
    match (arg_after "--arch" rest, arg_after "-o" rest) with
    | Some ap, Some out -> (
      try exit (cmd_route_instances inp ap out) with
      | Qccdc.Route.Unroutable m ->
        Printf.eprintf "%s: unroutable: %s
" inp m; exit 4)
    | _ -> usage ())
  | "compile" :: inp :: rest -> (
    match (arg_after "--arch" rest, arg_after "-o" rest) with
    | Some arch_path, Some out -> (
      try
        let code, unrealised = cmd_compile ?init:(init_placement_arg rest) inp arch_path out in
        (* A PARTIAL PLACEMENT IS A DECLINE TOO.  The general router signals "I cannot do
           this" in two different ways: it raises `Unroutable`, or it returns having left
           some ops unrealised.  The rotation fallback below used to hang off the
           exception only, so a device with MORE room could compile WORSE than one with
           less -- raising `ring144_24v`'s trap capacity to 8 stopped the router raising,
           so rotation was never tried and 79 of 864 contacts stayed unrealised on a
           circuit that compiles completely at capacity 2 (`Codesign/findings/q03`).
           That made capacity a non-monotone axis for a reason that was control flow
           rather than physics. Retrying here can only add programs that compile. *)
        if unrealised <> [] && not (List.mem "--no-rotate" rest) then begin
          Printf.eprintf "%s: %d ops unrealised by the general router
" inp
            (List.length unrealised);
          prerr_endline "  trying rigid rotation, which does not need a free slot to move into";
          try exit (cmd_rotate ?placement:(placement_arg rest) ~dock_all:(List.mem "--dock-all" rest) inp arch_path out) with
          | Qccdc.Rotate_pipeline.Not_applicable r ->
            Printf.eprintf "  rotation does not apply either: %s
" r;
            try_racetrack ?init:(init_placement_arg rest) inp arch_path out (fun () ->
                (* the partial program from the compile above is still on disk, and this is
                   now the final answer, so publish the verdict the caller greps for *)
                report_unrealised unrealised;
                exit code)
        end;
        report_unrealised unrealised;
        exit code
      with
      | Qccdc.Qasm.Error m -> Printf.eprintf "%s: parse error: %s
" inp m; exit 2
      | Qccdc.Circuit.Error m -> Printf.eprintf "%s: %s
" inp m; exit 2
      | Qccdc.Route.Unroutable m -> (
        (* The individual-ion router runs out at about 46% occupancy on a closed loop
           (PLAN.md 11.9).  Rigid rotation has no such limit but a narrower domain -- it
           preserves the riders' cyclic order, so it only serves a bipartite circuit on a
           conveyor.  Trying it only AFTER the general router has declined is deliberate:
           it can only add programs that compile, never change one that already did. *)
        Printf.eprintf "%s: unroutable: %s
" inp m;
        (* `--no-rotate` is not a convenience.  `bridge/c7_occupancy.py` measures where
           the INDIVIDUAL-ION router runs out, and with the fallback in place it measured
           the pair of them and reported the answer as the individual router's -- a
           silently wrong number in a document whose whole point is that number. *)
        if List.mem "--no-rotate" rest then exit 4;
        prerr_endline "  trying rigid rotation, which does not need a free slot to move into";
        try exit (cmd_rotate ?placement:(placement_arg rest) ~dock_all:(List.mem "--dock-all" rest) inp arch_path out) with
        | Qccdc.Rotate_pipeline.Not_applicable r ->
          Printf.eprintf "  rotation does not apply either: %s
" r;
          try_racetrack ?init:(init_placement_arg rest) inp arch_path out (fun () -> exit 4))
      | Qccdc.Place.Too_many_qubits m ->
        Printf.eprintf "%s: %s
" inp m; exit 4)
    | _ -> usage ())
  | "traps" :: path :: _ -> exit (cmd_traps path)
  | "decompose" :: inp :: _ -> (
    try exit (cmd_decompose inp) with
    | Qccdc.Qasm.Error m ->
      Printf.eprintf "%s: parse error: %s
" inp m;
      exit 2)
  | "pulses-selftest" :: _ -> exit (cmd_selftest ())
  | "parse" :: inp :: rest -> (
    try exit (cmd_parse inp (arg_after "-o" rest)) with
    | Qccdc.Qasm.Error m ->
      Printf.eprintf "%s: parse error: %s
" inp m;
      exit 2
    | Qccdc.Circuit.Error m ->
      Printf.eprintf "%s: %s
" inp m;
      exit 2)
  | "arch" :: path :: _ -> exit (cmd_arch path)
  | "roundtrip" :: inp :: rest -> (
    match arg_after "-o" rest with
    | Some out -> exit (cmd_roundtrip inp out)
    | None -> usage ())
  | _ -> usage ()
