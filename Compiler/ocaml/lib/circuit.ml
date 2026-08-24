(* The flattened circuit and its dependency DAG.

   Two jobs, both of which have to agree exactly with `qiskit.converters.circuit_to_dag`,
   because that is the oracle C1 is differential-tested against.

   {b Flattening.}  A register argument broadcasts: `h q;` on a width-3 register is three
   ops, and `cx q, r;` pairs them index by index.  Bits are numbered in declaration
   order, registers in the order their `qreg`/`creg` statements appear -- the same
   numbering qiskit gives them, which is what makes a bit index a shared vocabulary
   between the two implementations rather than a private convention.

   {b The DAG.}  A dependency edge joins consecutive ops on a wire.  Note what this
   means: the DAG is *generated* by the per-wire op orderings, so two DAGs are equal iff
   their per-wire sequences are equal.  The comparison therefore reports both -- the
   per-wire sequences (which localise a disagreement to a wire and a position) and the
   derived edge set (which is what a reader expects to see).  Comparing only edges would
   pass on two circuits that differ by a transitive edge; comparing only sequences would
   be right but unrecognisable. *)

open Qasm_ast

exception Error of string

let fail fmt = Printf.ksprintf (fun s -> raise (Error s)) fmt

type op = {
  index : int;              (* position in program order; the DAG node identity *)
  name : string;
  params : float list;
  qubits : int list;        (* flattened bit indices *)
  clbits : int list;
  cond : (string * int) option;  (* `if (c == v)` *)
  src_line : int;
}

type t = {
  name : string;
  qregs : (string * int * int) list;  (* name, offset, width *)
  cregs : (string * int * int) list;
  n_qubits : int;
  n_clbits : int;
  ops : op list;
  gates : (string * gate_def) list;  (* declarations, kept for C2's decomposition *)
}

(* ------------------------------------------------------------------ registers *)

let find_reg regs name =
  match List.find_opt (fun (n, _, _) -> n = name) regs with
  | Some (_, off, w) -> (off, w)
  | None -> fail "unknown register %S" name

let resolve regs = function
  | Index (r, i) ->
    let off, w = find_reg regs r in
    if i < 0 || i >= w then fail "index %s[%d] is out of range (width %d)" r i w;
    `Bit (off + i)
  | Whole r ->
    let off, w = find_reg regs r in
    `Reg (off, w)

(* OpenQASM 2.0's broadcast rule: every whole-register argument must have the same
   width, single bits are repeated, and the op is issued once per index. *)
let broadcast (resolved : [ `Bit of int | `Reg of int * int ] list) : int list list =
  let widths =
    List.filter_map (function `Reg (_, w) -> Some w | `Bit _ -> None) resolved
  in
  match widths with
  | [] -> [ List.map (function `Bit b -> b | `Reg _ -> assert false) resolved ]
  | w :: rest ->
    if List.exists (fun x -> x <> w) rest then
      fail "registers of differing widths in one statement (%s)"
        (String.concat ", " (List.map string_of_int (w :: rest)));
    List.init w (fun k ->
        List.map (function `Bit b -> b | `Reg (off, _) -> off + k) resolved)

(* ------------------------------------------------------------------ building *)

let build ?(name = "circuit") (p : program) : t =
  let qregs = ref [] and cregs = ref [] in
  let nq = ref 0 and nc = ref 0 in
  let gates = ref [] in
  let ops = ref [] in
  let counter = ref 0 in

  let emit ?cond ~line name params qubits clbits =
    let o =
      { index = !counter; name; params; qubits; clbits; cond; src_line = line }
    in
    incr counter;
    ops := o :: !ops
  in

  let eval_params ps =
    List.map
      (fun e ->
        try eval e
        with Unbound v ->
          fail "parameter %S is unbound outside a gate body" v)
      ps
  in

  let rec do_stmt ?cond s =
    match s with
    | Qreg (n, w) ->
      if List.exists (fun (m, _, _) -> m = n) !qregs then
        fail "qreg %S declared twice" n;
      qregs := !qregs @ [ (n, !nq, w) ];
      nq := !nq + w
    | Creg (n, w) ->
      if List.exists (fun (m, _, _) -> m = n) !cregs then
        fail "creg %S declared twice" n;
      cregs := !cregs @ [ (n, !nc, w) ];
      nc := !nc + w
    | GateDecl g -> gates := !gates @ [ (g.gname, g) ]
    | Opaque (n, _, _) -> fail "opaque gate %S has no definition to compile" n
    | Apply c ->
      let resolved = List.map (resolve !qregs) c.cargs in
      let params = eval_params c.cparams in
      List.iter
        (fun qs -> emit ?cond ~line:c.cline c.cname params qs [])
        (broadcast resolved)
    | Measure (q, cl, ln) ->
      let rq = resolve !qregs q and rc = resolve !cregs cl in
      (match (rq, rc) with
      | `Bit a, `Bit b -> emit ?cond ~line:ln "measure" [] [ a ] [ b ]
      | `Reg (qo, qw), `Reg (co, cw) ->
        if qw <> cw then
          fail "measure: qreg width %d does not match creg width %d" qw cw;
        for k = 0 to qw - 1 do
          emit ?cond ~line:ln "measure" [] [ qo + k ] [ co + k ]
        done
      | _ -> fail "measure: cannot pair a whole register with a single bit")
    | Reset (q, ln) -> (
      match resolve !qregs q with
      | `Bit a -> emit ?cond ~line:ln "reset" [] [ a ] []
      | `Reg (off, w) ->
        for k = 0 to w - 1 do
          emit ?cond ~line:ln "reset" [] [ off + k ] []
        done)
    | Barrier (args, ln) ->
      (* one barrier node over every qubit named, not one per qubit: that is how
         `circuit_to_dag` models it, and the difference is visible in the DAG. *)
      let qs =
        List.concat_map
          (fun a ->
            match resolve !qregs a with
            | `Bit b -> [ b ]
            | `Reg (off, w) -> List.init w (fun k -> off + k))
          args
      in
      emit ?cond ~line:ln "barrier" [] qs []
    | If (creg, v, inner, _) ->
      let _ = find_reg !cregs creg in
      do_stmt ~cond:(creg, v) inner
  in
  List.iter (fun s -> do_stmt s) p.stmts;
  {
    name;
    qregs = !qregs;
    cregs = !cregs;
    n_qubits = !nq;
    n_clbits = !nc;
    ops = List.rev !ops;
    gates = !gates;
  }

(* ------------------------------------------------------------------ the DAG *)

(* A wire is a qubit or a clbit.  Clbits are offset by `n_qubits` so one integer
   addresses either, which keeps the per-wire tables flat. *)
let wires_of c (o : op) : int list =
  let cl =
    match o.cond with
    | None -> o.clbits
    | Some (r, _) ->
      (* a conditional op reads every bit of the register it tests, so it is ordered
         against everything that writes any of them *)
      let off, w = find_reg c.cregs r in
      o.clbits @ List.init w (fun k -> off + k)
  in
  o.qubits @ List.map (fun b -> c.n_qubits + b) cl

(* Per-wire op sequences.  This IS the DAG: the edge set is exactly the set of
   consecutive pairs in these lists. *)
let wire_sequences c : (int * int list) list =
  let tbl = Hashtbl.create (c.n_qubits + c.n_clbits) in
  List.iter
    (fun o ->
      List.iter
        (fun w ->
          Hashtbl.replace tbl w (o.index :: (try Hashtbl.find tbl w with Not_found -> [])))
        (wires_of c o))
    c.ops;
  List.init
    (c.n_qubits + c.n_clbits)
    (fun w -> (w, List.rev (try Hashtbl.find tbl w with Not_found -> [])))

let edges c : (int * int) list =
  let seen = Hashtbl.create 256 in
  let out = ref [] in
  List.iter
    (fun (_, seq) ->
      let rec go = function
        | a :: (b :: _ as rest) ->
          if not (Hashtbl.mem seen (a, b)) then begin
            Hashtbl.replace seen (a, b) ();
            out := (a, b) :: !out
          end;
          go rest
        | _ -> ()
      in
      go seq)
    (wire_sequences c);
  List.sort compare !out

(* ------------------------------------------------------------------ analysis *)

let two_qubit_ops c = List.filter (fun o -> List.length o.qubits = 2) c.ops

(* Is every op in the Clifford fragment?  D1 makes this the gate: inside it, R10 is
   discharged by stabilizer simulation over the whole program; outside it, the compiler
   falls back to the symbolic route and reports R10 `partial`.

   A rotation is Clifford only at a multiple of pi/2, so this is an angle test, and the
   tolerance is what makes `pi/2` and a float literal the same angle. *)
let is_right_angle ?(eps = 1e-9) theta =
  let k = theta /. (Float.pi /. 2.0) in
  Float.abs (k -. Float.round k) < eps

let non_clifford c =
  (* the annotation is load-bearing: `name` is a field of both `op` and `t`, and `t` is
     declared later, so without it `o.name` resolves to the wrong record *)
  List.filter
    (fun (o : op) ->
      if List.mem o.name [ "measure"; "reset"; "barrier" ] then false
      else if List.mem o.name clifford_no_param then false
      else if List.mem o.name clifford_at_right_angles then
        not (List.for_all is_right_angle o.params)
      else true)
    c.ops

let summary c =
  let n2 = List.length (two_qubit_ops c) in
  let nnc = List.length (non_clifford c) in
  Printf.sprintf
    "%s: %d qubits, %d clbits, %d ops (%d two-qubit), %d edges, %s"
    c.name c.n_qubits c.n_clbits (List.length c.ops) n2
    (List.length (edges c))
    (if nnc = 0 then "Clifford" else Printf.sprintf "%d non-Clifford ops" nnc)

(* ------------------------------------------------------------------ canonical JSON

   The interchange format for the differential test.  Both this and the qiskit oracle
   emit it, and `bridge/diff_dag.py` compares them.  Floats are printed with %.12g: a
   parameter that agrees to 12 significant figures is the same angle, and printing full
   precision would make the two sides disagree on the last bit of `pi/2` for no reason. *)
let num f =
  if Float.is_integer f && Float.abs f < 1e15 then `Int (int_of_float f)
  else `Float (float_of_string (Printf.sprintf "%.12g" f))

let to_json c : Yojson.Safe.t =
  `Assoc
    [
      ("name", `String c.name);
      ("n_qubits", `Int c.n_qubits);
      ("n_clbits", `Int c.n_clbits);
      ( "qregs",
        `List
          (List.map
             (fun (n, o, w) ->
               `Assoc [ ("name", `String n); ("offset", `Int o); ("width", `Int w) ])
             c.qregs) );
      ( "cregs",
        `List
          (List.map
             (fun (n, o, w) ->
               `Assoc [ ("name", `String n); ("offset", `Int o); ("width", `Int w) ])
             c.cregs) );
      ( "ops",
        `List
          (List.map
             (fun o ->
               `Assoc
                 ([
                    ("i", `Int o.index);
                    ("name", `String o.name);
                    ("qubits", `List (List.map (fun q -> `Int q) o.qubits));
                    ("clbits", `List (List.map (fun b -> `Int b) o.clbits));
                    ("params", `List (List.map num o.params));
                  ]
                 @
                 match o.cond with
                 | None -> []
                 | Some (r, v) -> [ ("cond", `List [ `String r; `Int v ]) ]))
             c.ops) );
      ( "wires",
        `List
          (List.map
             (fun (w, seq) ->
               `Assoc
                 [ ("wire", `Int w); ("ops", `List (List.map (fun i -> `Int i) seq)) ])
             (wire_sequences c)) );
      ("edges", `List (List.map (fun (a, b) -> `List [ `Int a; `Int b ]) (edges c)));
    ]

(* ------------------------------------------------------------------ lowering

   Rewrite the circuit so every op acts on at most two qubits.

   The router meets a PAIR of ions in one trap; three ions in one trap at once is a
   different problem, and most shipped devices cannot host it at all.  So multi-qubit
   gates are expanded here, before placement, using `Gateset_composites.lower`.

   The DAG is rebuilt from the lowered ops, which is what makes the expansion invisible
   to everything downstream.  What is NOT invisible is the semantics: R10's stabilizer
   half compares the emitted pulses against the ORIGINAL circuit, so a wrong lowering is
   caught rather than trusted. *)
let lower (c : t) : t * int =
  let out = ref [] in
  let n = ref 0 in
  let expanded = ref 0 in
  List.iter
    (fun (o : op) ->
      if List.length o.qubits <= 2 then begin
        out := { o with index = !n } :: !out;
        incr n
      end
      else begin
        incr expanded;
        List.iter
          (fun (l : Gateset_composites.lowered) ->
            out :=
              {
                index = !n;
                name = l.lname;
                params = l.lparams;
                qubits = l.lqubits;
                clbits = [];
                cond = o.cond;
                src_line = o.src_line;
              }
              :: !out;
            incr n)
          (Gateset_composites.lower o.name o.params o.qubits)
      end)
    c.ops;
  ({ c with ops = List.rev !out }, !expanded)
