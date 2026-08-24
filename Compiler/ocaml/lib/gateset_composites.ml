(* Composite gates, custom-gate inlining, and the differential self-test.

   Everything here reduces to [Gateset.decompose_u3] and [Gateset.decompose_cx], so its
   correctness rests on the two Lean theorems PLUS a circuit identity.  The circuit
   identities are standard -- but "standard" is exactly how a wrong constant survives, so
   every one is checked numerically against the gate's *defining* unitary by [check], at
   its real width rather than on a two-qubit stand-in.

   The Lean coverage is therefore precisely: the `u3` family and `CX`.  The composites are
   numerically verified and reduce to those two.  `Compiler/PLAN.md` C2 records that
   distinction rather than blurring it. *)

open Gateset

let seq (ds : decomposed list) : decomposed =
  {
    pulses = List.concat_map (fun d -> d.pulses) ds;
    phase = List.fold_left (fun a d -> a +. d.phase) 0.0 ds;
  }

let u1 lam q = decompose_u3 ~qubit:q (0.0, 0.0, lam)

(* `rz` as a BUILDING BLOCK, with its true global phase.

   OpenQASM's `rz` is `u1`, i.e. `diag (1, e^{iθ})`, which is the rotation
   `diag (e^{-iθ/2}, e^{iθ/2})` only up to the global phase `e^{-iθ/2}`.  Standing alone
   that difference is unobservable and `u3_of_gate` rightly ignores it.  Inside a
   conjugation it is NOT unobservable: `CX · rz(θ) · CX` spreads the phase unevenly
   across the basis and it becomes relative.

   That is precisely the bug `check` caught on `rzz` -- an error of 2.0, which is a wrong
   operator, not a rounding difference.  So composites use this, never `u1`. *)
let rz t q = { (u1 t q) with phase = -.t /. 2.0 }
let named n q = decompose_u3 ~qubit:q (Option.get (u3_of_gate n []))
let cxg c t = decompose_cx ~control:c ~target:t

(* Toffoli: six CX and a handful of T gates.  Worth having rather than refusing -- a
   reversible-arithmetic circuit is nothing but Toffolis. *)
let ccx a b c =
  seq
    [
      named "h" c; cxg b c; named "tdg" c; cxg a c; named "t" c; cxg b c;
      named "tdg" c; cxg a c; named "t" b; named "t" c; named "h" c;
      cxg a b; named "t" a; named "tdg" b; cxg a b;
    ]

let rec decompose_named ~(gates : (string * Qasm_ast.gate_def) list) (name : string)
    (params : float list) (qubits : int list) : decomposed =
  match (name, qubits, params) with
  (* A single-qubit call is a builtin `u3` if the table knows it -- but if not, it may
     still be a user-declared gate, so this branch must fall THROUGH to inlining rather
     than refusing.  Matching on arity first and giving up inside the branch is what made
     `gate rot(theta) a { ... }` unreachable. *)
  | _, [ q ], _ when u3_of_gate name params <> None ->
    decompose_u3 ~qubit:q (Option.get (u3_of_gate name params))
  | "cx", [ c; t ], _ -> cxg c t
  | "cz", [ c; t ], _ -> seq [ named "h" t; cxg c t; named "h" t ]
  | "cy", [ c; t ], _ -> seq [ named "sdg" t; cxg c t; named "s" t ]
  | "ch", [ c; t ], _ ->
    seq
      [ named "s" t; named "h" t; named "t" t; cxg c t; named "tdg" t; named "h" t;
        named "sdg" t ]
  | "swap", [ a; b ], _ -> seq [ cxg a b; cxg b a; cxg a b ]
  | "crz", [ c; t ], [ th ] ->
    seq [ rz (th /. 2.0) t; cxg c t; rz (-.th /. 2.0) t; cxg c t ]
  | ("cu1" | "cp"), [ c; t ], [ l ] ->
    seq [ u1 (l /. 2.0) c; cxg c t; u1 (-.l /. 2.0) t; cxg c t; u1 (l /. 2.0) t ]
  | "rzz", [ a; b ], [ th ] -> seq [ cxg a b; rz th b; cxg a b ]
  | "rxx", [ a; b ], [ th ] ->
    (* the MS gate IS an XX rotation, so this is a native, not a composite *)
    { pulses = [ Ms { theta = th; a; b } ]; phase = 0.0 }
  | "ccx", [ a; b; c ], _ -> ccx a b c
  | "cswap", [ a; b; c ], _ -> seq [ cxg c b; ccx a b c; cxg c b ]
  | _, _, _ -> (
    (* A user-declared `gate`: inline its body.
       The DAG deliberately keeps a custom gate as ONE node, to match `circuit_to_dag`.
       Inlining happens HERE, at decomposition time, where it cannot affect what the
       front end is compared against. *)
    match List.assoc_opt name gates with
    | None ->
      raise (Unsupported (Printf.sprintf "%s on %d qubit(s)" name (List.length qubits)))
    | Some (g : Qasm_ast.gate_def) ->
      if List.length g.gqargs <> List.length qubits then
        raise (Unsupported (Printf.sprintf "%s: wrong arity" name));
      let env = List.combine g.gparams params in
      let qmap = List.combine g.gqargs qubits in
      seq
        (List.map
           (fun (c : Qasm_ast.call) ->
             let ps = List.map (fun e -> Qasm_ast.eval ~env e) c.cparams in
             let qs =
               List.map
                 (function
                   | Qasm_ast.Whole f -> (
                     match List.assoc_opt f qmap with
                     | Some q -> q
                     | None ->
                       raise (Unsupported (Printf.sprintf "%s: unbound %S" name f)))
                   | Qasm_ast.Index _ ->
                     raise (Unsupported (Printf.sprintf "%s: indexed arg in body" name)))
                 c.cargs
             in
             decompose_named ~gates c.cname ps qs)
           g.gbody))

let decompose_op ?(gates = []) name params qubits =
  decompose_named ~gates name params qubits

(* ------------------------------------------------------------------ matrices
 *
 * Matrices exist ONLY here, for checking.  The compiler proper never builds one.
 * Everything is n-qubit general so a gate added above is automatically checked at its
 * real width. *)

type mat = cx array array

let dim n = 1 lsl n

let mmul (a : mat) (b : mat) : mat =
  let n = Array.length a in
  Array.init n (fun i ->
      Array.init n (fun j ->
          let s = ref c0 in
          for k = 0 to n - 1 do
            s := !s +: (a.(i).(k) *: b.(k).(j))
          done;
          !s))

let eye n : mat = Array.init n (fun i -> Array.init n (fun j -> if i = j then c1 else c0))
let scale (z : cx) (m : mat) : mat = Array.map (Array.map (fun x -> z *: x)) m
let expi (t : float) : cx = ci (cos t) (sin t)

(* big-endian: qubit 0 is the most significant bit, matching `QCCDC.kron` in Lean *)
let bit n q x = (x lsr (n - 1 - q)) land 1
let clear n q x = x land lnot (1 lsl (n - 1 - q))

let embed1 n q (m : mat) : mat =
  Array.init (dim n) (fun i ->
      Array.init (dim n) (fun j ->
          if clear n q i <> clear n q j then c0 else m.(bit n q i).(bit n q j)))

let embed2 n a b (m : mat) : mat =
  Array.init (dim n) (fun i ->
      Array.init (dim n) (fun j ->
          if clear n b (clear n a i) <> clear n b (clear n a j) then c0
          else m.((2 * bit n a i) + bit n b i).((2 * bit n a j) + bit n b j)))

let mat_R theta phi : mat =
  let c = ci (cos (theta /. 2.0)) 0.0 and s = ci (sin (theta /. 2.0)) 0.0 in
  [| [| c; Complex.neg cI *: expi (-.phi) *: s |];
     [| Complex.neg cI *: expi phi *: s; c |] |]

let mat_VZ lam : mat = [| [| c1; c0 |]; [| c0; expi lam |] |]

let mat_MS theta : mat =
  let c = ci (cos (theta /. 2.0)) 0.0 and s = ci (sin (theta /. 2.0)) 0.0 in
  let m = scale c (eye 4) in
  let k = Complex.neg cI *: s in
  for i = 0 to 3 do
    m.(i).(3 - i) <- m.(i).(3 - i) +: k
  done;
  m

let mat_u3 theta phi lam : mat =
  let c = ci (cos (theta /. 2.0)) 0.0 and s = ci (sin (theta /. 2.0)) 0.0 in
  [| [| c; Complex.neg (expi lam *: s) |];
     [| expi phi *: s; expi (phi +. lam) *: c |] |]

(** Replay a pulse sequence into an `n`-qubit unitary, global phase included. *)
let replay n (d : decomposed) : mat =
  let acc =
    List.fold_left
      (fun acc p ->
        let m =
          match p with
          | Frame { lam; qubit } -> embed1 n qubit (mat_VZ lam)
          | Beam { theta; phi; qubit } -> embed1 n qubit (mat_R theta phi)
          | Ms { theta; a; b } -> embed2 n a b (mat_MS theta)
        in
        mmul m acc)
      (eye (dim n)) d.pulses
  in
  scale (expi d.phase) acc

let max_diff (a : mat) (b : mat) =
  let worst = ref 0.0 in
  Array.iteri
    (fun i row ->
      Array.iteri
        (fun j x -> worst := Float.max !worst (Complex.norm (x -: b.(i).(j))))
        row)
    a;
  !worst

(* ------------------------------------------------------------------ targets *)

let controlled (u : mat) : mat =
  let m = eye 4 in
  m.(2).(2) <- u.(0).(0);
  m.(2).(3) <- u.(0).(1);
  m.(3).(2) <- u.(1).(0);
  m.(3).(3) <- u.(1).(1);
  m

let mat_of_named name params : mat option =
  match u3_of_gate name params with
  | Some (t, p, l) -> Some (mat_u3 t p l)
  | None -> None

let target n name params : mat option =
  let g nm = Option.get (mat_of_named nm []) in
  match (name, params) with
  | "cx", _ -> Some (embed2 n 0 1 (controlled (g "x")))
  | "cz", _ -> Some (embed2 n 0 1 (controlled (g "z")))
  | "cy", _ -> Some (embed2 n 0 1 (controlled (g "y")))
  | "ch", _ -> Some (embed2 n 0 1 (controlled (g "h")))
  | "swap", _ ->
    let m = eye 4 in
    m.(1).(1) <- c0;
    m.(2).(2) <- c0;
    m.(1).(2) <- c1;
    m.(2).(1) <- c1;
    Some (embed2 n 0 1 m)
  | "crz", [ th ] ->
    let m = eye 4 in
    m.(2).(2) <- expi (-.th /. 2.0);
    m.(3).(3) <- expi (th /. 2.0);
    Some (embed2 n 0 1 m)
  | ("cu1" | "cp"), [ l ] ->
    let m = eye 4 in
    m.(3).(3) <- expi l;
    Some (embed2 n 0 1 m)
  | "rzz", [ th ] ->
    let m = eye 4 in
    m.(0).(0) <- expi (-.th /. 2.0);
    m.(1).(1) <- expi (th /. 2.0);
    m.(2).(2) <- expi (th /. 2.0);
    m.(3).(3) <- expi (-.th /. 2.0);
    Some (embed2 n 0 1 m)
  | "rxx", [ th ] -> Some (embed2 n 0 1 (mat_MS th))
  | "ccx", _ ->
    let m = eye 8 in
    m.(6).(6) <- c0;
    m.(7).(7) <- c0;
    m.(6).(7) <- c1;
    m.(7).(6) <- c1;
    Some m
  | "cswap", _ ->
    let m = eye 8 in
    m.(5).(5) <- c0;
    m.(6).(6) <- c0;
    m.(5).(6) <- c1;
    m.(6).(5) <- c1;
    Some m
  | _ -> mat_of_named name params

type report = { name : string; worst : float; beams : int; ms : int }

(* The comparison is EXACT, not up-to-phase: the global phase is part of the claim, so
   `replay` applies it and any phase error shows up as an error. *)
let check ?(trials = 10000) ?(seed = 20260823) () : report list =
  Random.init seed;
  let out = ref [] in
  let add name worst d =
    out := { name; worst; beams = beam_count d; ms = ms_count d } :: !out
  in

  let worst = ref 0.0 in
  let last = ref (decompose_u3 ~qubit:0 (0.0, 0.0, 0.0)) in
  for _ = 1 to trials do
    let r () = (Random.float 8.0 -. 4.0) *. pi in
    let t = r () and p = r () and l = r () in
    let d = decompose_u3 ~qubit:0 (t, p, l) in
    last := d;
    worst := Float.max !worst (max_diff (replay 1 d) (mat_u3 t p l))
  done;
  add (Printf.sprintf "u3 (%d random)" trials) !worst !last;

  List.iter
    (fun name ->
      let d = decompose_named ~gates:[] name [] [ 0 ] in
      add name (max_diff (replay 1 d) (Option.get (target 1 name []))) d)
    [ "id"; "x"; "y"; "z"; "h"; "s"; "sdg"; "t"; "tdg"; "sx"; "sxdg" ];

  List.iter
    (fun name ->
      let d = decompose_named ~gates:[] name [] [ 0; 1 ] in
      add name (max_diff (replay 2 d) (Option.get (target 2 name []))) d)
    [ "cx"; "cz"; "cy"; "ch"; "swap" ];

  List.iter
    (fun name ->
      let worst = ref 0.0 and last = ref (cxg 0 1) in
      for _ = 1 to 200 do
        let th = (Random.float 8.0 -. 4.0) *. pi in
        let d = decompose_named ~gates:[] name [ th ] [ 0; 1 ] in
        last := d;
        worst :=
          Float.max !worst (max_diff (replay 2 d) (Option.get (target 2 name [ th ])))
      done;
      add (name ^ " (200 angles)") !worst !last)
    [ "crz"; "cu1"; "cp"; "rzz"; "rxx" ];

  List.iter
    (fun name ->
      let d = decompose_named ~gates:[] name [] [ 0; 1; 2 ] in
      add name (max_diff (replay 3 d) (Option.get (target 3 name []))) d)
    [ "ccx"; "cswap" ];

  List.rev !out

(* ------------------------------------------------------------------ lowering
 *
 * Rewrite an operation into ones the ROUTER can host: at most two qubits each.
 *
 * A three-qubit gate needs three ions in one trap simultaneously, which is a different
 * (and much harder) routing problem than a pair meeting.  Every shipped architecture
 * would have to be asked for it, and most cannot supply it.  So a Toffoli is lowered to
 * six CX and some single-qubit gates BEFORE placement -- the standard identity, and the
 * one `ccx` above already uses at the pulse level.
 *
 * The correctness of the lowering is not asserted here.  It is checked end to end: R10's
 * stabilizer half composes the EMITTED pulses and compares them against the ORIGINAL
 * circuit, so a wrong lowering shows up as a tableau mismatch rather than as a claim
 * nobody tests. *)

type lowered = { lname : string; lparams : float list; lqubits : int list }

let rec lower (name : string) (params : float list) (qubits : int list) : lowered list =
  match (name, qubits, params) with
  | _, ([] | [ _ ] | [ _; _ ]), _ -> [ { lname = name; lparams = params; lqubits = qubits } ]
  | "ccx", [ a; b; c ], _ ->
    let g n qs = { lname = n; lparams = []; lqubits = qs } in
    [ g "h" [ c ]; g "cx" [ b; c ]; g "tdg" [ c ]; g "cx" [ a; c ]; g "t" [ c ];
      g "cx" [ b; c ]; g "tdg" [ c ]; g "cx" [ a; c ]; g "t" [ b ]; g "t" [ c ];
      g "h" [ c ]; g "cx" [ a; b ]; g "t" [ a ]; g "tdg" [ b ]; g "cx" [ a; b ] ]
  | "cswap", [ a; b; c ], _ ->
    let g n qs = { lname = n; lparams = []; lqubits = qs } in
    (g "cx" [ c; b ] :: lower "ccx" [] [ a; b; c ]) @ [ g "cx" [ c; b ] ]
  | _, _, _ ->
    raise
      (Unsupported
         (Printf.sprintf "%s on %d qubits: no lowering to 1- and 2-qubit gates" name
            (List.length qubits)))
