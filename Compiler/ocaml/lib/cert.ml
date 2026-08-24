(* The compilation certificate -- the evidence R10 is discharged against.

   `qccd/verify/__init__.py` lists R10, "the compiled program implements the input
   circuit", as UNCHECKABLE, for one stated reason: "needs symbolic permutation +
   Pauli-frame tracking against a QASM DAG".  This file is that missing document.

   {1 What a certificate must NOT be}

   It must not be a summary the compiler writes about itself.  The whole discipline
   (`Compiler/PLAN.md` §2, and `qccd/ir/import_deck.py` before it) is that a checker
   *recomputes* rather than reads: ion positions are derived by replaying the move list
   from the initial placement, and any position the certificate also claims is
   cross-checked, never believed.  Totals can agree by coincidence; a few hundred
   recomputed positions cannot.

   So the certificate carries only things a checker can independently verify:

     map      logical qubit -> ion            (a bijection, checkable)
     init     ion -> initial trap             (the replay's starting state)
     moves    every hop, with its cycle        (replayable)
     gates    circuit op -> the TSIR instruction that realises it, and where
     hashes   which circuit and which architecture this is a certificate ABOUT

   The hashes matter more than they look.  A certificate with no binding is a certificate
   for whatever you point it at. *)

type json = Yojson.Safe.t

type gate_witness = {
  dag : int;                 (* index of the op in the circuit's program order *)
  instr : int;               (* the TSIR instruction id that realises it *)
  cycle : int;               (* the machine cycle it happens in *)
  site : string;             (* the trap it happens in *)
  operands : string list;    (* the ions, in circuit operand order *)
  pulses : string list;      (* the native sequence, from `Gateset` *)
}

(* A rigid rotation of a named loop: every ion on it advances by `delta`.

   Carried as ONE entry rather than expanded into a move per ion per unit hop.  Expanding
   is more uniform -- the checker then needs no rotation case -- but a BB round expands to
   113 472 moves, and a checker whose position lookup is a fold over the move list becomes
   quadratic and never finishes.  The loop's node order travels from the ARCHITECTURE, not
   from the compiler, so this adds a case to the checker without adding anything the
   compiler can lie about. *)
type rotation_witness = { rcycle : int; rloop : string; rdelta : int }

type move_witness = {
  cycle : int;
  ion : string;
  src : string;
  dst : string;
  via : string list;
}

(* The circuit, carried inside the certificate.

   Not for convenience: the checker must DERIVE the dependency order rather than be told
   it.  A certificate that merely asserted "these gates are correctly ordered" would be
   the compiler grading its own homework.  With the op list present, the checker rebuilds
   the wire precedence itself and checks the witnesses against it. *)
type circuit_op = {
  oi : int;
  oname : string;
  oqubits : int list;
  (* the angles too: without them a `cu1(pi/4)` in the certificate is indistinguishable
     from a `cu1(pi)`, and the unitary check has no defining matrix to compare against *)
  oparams : float list;
  (* the line of the QASM file this op was written on, so a debugger can put the
     hardware instruction and the source statement side by side *)
  osrc : int;
}

type t = {
  version : int;
  circuit_ops : circuit_op list;
  circuit_sha256 : string;
  arch_sha256 : string;
  circuit_name : string;
  arch_name : string;
  n_qubits : int;
  map_ : (int * string) list;        (* qubit index -> ion *)
  init : (string * string) list;     (* ion -> trap *)
  moves : move_witness list;
  rotations : rotation_witness list;
  gates : gate_witness list;
  unrealised : int list;             (* circuit ops with NO witness, named not hidden *)
  claims : (string * json) list;
}

(* ------------------------------------------------------------------ hashing
 *
 * A tiny FNV-1a over the file bytes.  It is NOT a cryptographic binding and does not
 * pretend to be: it exists so a certificate cannot be silently applied to a different
 * circuit, and `bridge/check_cert.py` recomputes the real SHA-256 on the Python side
 * where `hashlib` is available.  Naming it `sha256` would be a lie, so the field says
 * what it is. *)
let fnv1a (s : string) : string =
  let h = ref 0xcbf29ce484222325L in
  String.iter
    (fun c ->
      h := Int64.logxor !h (Int64.of_int (Char.code c));
      h := Int64.mul !h 0x100000001b3L)
    s;
  Printf.sprintf "%016Lx" !h

let hash_file path =
  try
    let ic = open_in_bin path in
    Fun.protect
      ~finally:(fun () -> close_in ic)
      (fun () -> fnv1a (really_input_string ic (in_channel_length ic)))
  with _ -> ""

(* ------------------------------------------------------------------ json *)

let to_json (c : t) : json =
  `Assoc
    [
      ("version", `Int c.version);
      ("circuit", `String c.circuit_name);
      ("arch", `String c.arch_name);
      ("circuit_fnv1a", `String c.circuit_sha256);
      ("arch_fnv1a", `String c.arch_sha256);
      ("n_qubits", `Int c.n_qubits);
      ( "circuit_ops",
        `List
          (List.map
             (fun o ->
               `Assoc
                 [
                   ("i", `Int o.oi);
                   ("name", `String o.oname);
                   ("qubits", `List (List.map (fun q -> `Int q) o.oqubits));
                   ("params", `List (List.map (fun x -> `Float x) o.oparams));
                   ("line", `Int o.osrc);
                 ])
             c.circuit_ops) );
      ( "map",
        `Assoc (List.map (fun (q, i) -> (string_of_int q, `String i)) c.map_) );
      ("init", `Assoc (List.map (fun (i, s) -> (i, `String s)) c.init));
      ( "moves",
        `List
          (List.map
             (fun (m : move_witness) ->
               `Assoc
                 [
                   ("t", `Int m.cycle);
                   ("ion", `String m.ion);
                   ("from", `String m.src);
                   ("to", `String m.dst);
                   ("via", `List (List.map (fun v -> `String v) m.via));
                 ])
             c.moves) );
      ( "gates",
        `List
          (List.map
             (fun (g : gate_witness) ->
               `Assoc
                 [
                   ("dag", `Int g.dag);
                   ("instr", `Int g.instr);
                   ("t", `Int g.cycle);
                   ("site", `String g.site);
                   ("ions", `List (List.map (fun i -> `String i) g.operands));
                   ("pulses", `List (List.map (fun p -> `String p) g.pulses));
                 ])
             c.gates) );
      ( "rotations",
        `List
          (List.map
             (fun (r : rotation_witness) ->
               `Assoc
                 [ ("t", `Int r.rcycle); ("loop", `String r.rloop);
                   ("delta", `Int r.rdelta) ])
             c.rotations) );
      ("unrealised", `List (List.map (fun i -> `Int i) c.unrealised));
      ("claims", `Assoc c.claims);
    ]

let save path (c : t) =
  let oc = open_out_bin path in
  Fun.protect
    ~finally:(fun () -> close_out oc)
    (fun () ->
      output_string oc (Yojson.Safe.to_string (to_json c));
      output_char oc '\n')

(* ------------------------------------------------------------------ pre-flight
 *
 * The FAST checker.  It is deliberately not the trusted one -- `Compiler/PLAN.md` D3 puts
 * the proved Lean checker at C6 and reports R10 as `partial` until then -- but it runs on
 * every compile, so a compiler bug surfaces at the moment it is introduced rather than
 * when someone eventually runs the slow path.
 *
 * It recomputes positions from `init` + `moves` and confirms each gate's operands really
 * were co-located at the trap the witness names.  It never reads a claimed position. *)

type failure = { where : string; why : string }

(* A rotation is replayed the way the hardware performs it: every ion standing ON the loop
   advances, and every ion that is not -- a docked one -- stays put.  The node order comes
   from the ARCHITECTURE (`~loops`), never from the certificate, so a compiler cannot
   rotate its ions to wherever it needs them by describing a loop of its own. *)
let apply_rotation (nodes : string array) (delta : int)
    (pos : (string, string) Hashtbl.t) : unit =
  let n = Array.length nodes in
  if n > 0 then begin
    let slot = Hashtbl.create n in
    Array.iteri (fun i s -> Hashtbl.replace slot s i) nodes;
    Hashtbl.iter
      (fun ion site ->
        match Hashtbl.find_opt slot site with
        | None -> ()
        | Some i -> Hashtbl.replace pos ion nodes.(((i + delta) mod n + n) mod n))
      (Hashtbl.copy pos)
  end

let check ?(loops : (string * string array) list = []) (c : t) : failure list =
  let out = ref [] in
  let bad where why = out := { where; why } :: !out in

  (* the map must be a bijection: two qubits sharing an ion is the single most damaging
     thing a placer can do and the cheapest to detect *)
  let seen = Hashtbl.create 64 in
  List.iter
    (fun (q, ion) ->
      if Hashtbl.mem seen ion then
        bad (Printf.sprintf "map[%d]" q) (Printf.sprintf "ion %s is already taken" ion);
      Hashtbl.replace seen ion q)
    c.map_;
  if List.length c.map_ <> c.n_qubits then
    bad "map"
      (Printf.sprintf "covers %d of %d qubits" (List.length c.map_) c.n_qubits);

  (* replay the moves; the certificate's own positions are never consulted *)
  let pos = Hashtbl.create 64 in
  List.iter (fun (ion, site) -> Hashtbl.replace pos ion site) c.init;
  (* `cycle` names a field of BOTH witness records, and `move_witness` is declared last,
     so every lambda over gates needs its type written out.  Without the annotations the
     compiler quietly reads a gate witness as a move witness. *)
  let by_cycle = Hashtbl.create 64 in
  List.iter
    (fun (m : move_witness) ->
      Hashtbl.replace by_cycle m.cycle
        (m :: (try Hashtbl.find by_cycle m.cycle with Not_found -> [])))
    c.moves;
  let gates_at = Hashtbl.create 64 in
  List.iter
    (fun (g : gate_witness) ->
      Hashtbl.replace gates_at g.cycle
        (g :: (try Hashtbl.find gates_at g.cycle with Not_found -> [])))
    c.gates;
  let rots_at = Hashtbl.create 64 in
  List.iter
    (fun (r : rotation_witness) ->
      Hashtbl.replace rots_at r.rcycle
        (r :: (try Hashtbl.find rots_at r.rcycle with Not_found -> [])))
    c.rotations;

  (* `cycle` is a field of both witness records and `move_witness` is declared later, so
     the annotations are load-bearing, not decoration *)
  let last =
    List.fold_left (fun a (m : move_witness) -> max a m.cycle) 0 c.moves
    |> (fun a -> List.fold_left (fun b (g : gate_witness) -> max b g.cycle) a c.gates)
    |> fun a ->
    List.fold_left (fun b (r : rotation_witness) -> max b (r.rcycle + abs r.rdelta)) a
      c.rotations
  in
  for t = 0 to last do
    (* gates first: a gate at cycle t sees the positions BEFORE t's moves, which is the
       same convention the replay uses (`pos_before`) *)
    List.iter
      (fun (g : gate_witness) ->
        List.iter
          (fun ion ->
            match Hashtbl.find_opt pos ion with
            | None -> bad (Printf.sprintf "gate dag=%d" g.dag) (ion ^ " is unplaced")
            | Some s when s <> g.site ->
              bad
                (Printf.sprintf "gate dag=%d" g.dag)
                (Printf.sprintf "%s is at %s, but the witness says the gate is at %s" ion
                   s g.site)
            | Some _ -> ())
          g.operands)
      (try Hashtbl.find gates_at t with Not_found -> []);
    List.iter
      (fun (m : move_witness) ->
        (match Hashtbl.find_opt pos m.ion with
        | Some s when s = m.src -> ()
        | Some s ->
          bad
            (Printf.sprintf "move t=%d %s" t m.ion)
            (Printf.sprintf "departs %s but is at %s" m.src s)
        | None -> bad (Printf.sprintf "move t=%d %s" t m.ion) "moves an unplaced ion");
        Hashtbl.replace pos m.ion m.dst)
      (try Hashtbl.find by_cycle t with Not_found -> []);
    List.iter
      (fun (r : rotation_witness) ->
        match List.assoc_opt r.rloop loops with
        | Some nodes -> apply_rotation nodes r.rdelta pos
        | None ->
          bad
            (Printf.sprintf "rotation t=%d" r.rcycle)
            (Printf.sprintf "loop %s is not one the architecture declares" r.rloop))
      (try Hashtbl.find rots_at t with Not_found -> [])
  done;
  List.rev !out
