(* The OpenQASM 2.0 abstract syntax.

   Scope, and why it stops where it does.  The input this compiler actually receives is
   machine-generated: qiskit's QASM2 exporter, or a QEC round emitted by a code
   generator.  So the grammar is the full OpenQASM 2.0 one, but the *semantic* choices
   are made to match the oracle we are differential-tested against.

   {b User-defined gates are not inlined.}  `circuit_to_dag` keeps a custom gate as one
   instruction node, so we do too: a `gate` declaration is recorded (C2's decomposition
   pass needs its body) and a call to it is one op.  Inlining here would produce a DAG
   that is *correct* and that disagrees with the oracle on every custom gate, which would
   make the C1 acceptance test unable to distinguish a real parse bug from a modelling
   choice.

   {b Expressions are evaluated to floats at parse time.}  OpenQASM 2.0's expression
   language is closed constants only -- no symbolic parameters -- so there is nothing to
   preserve symbolically.  The one thing this must get right is that `pi/2` and
   `1.5707963267948966` are the same angle, because a decomposition table keyed on the
   angle would otherwise miss. *)

type expr =
  | Num of float
  | Pi
  | Var of string  (* a formal parameter, only inside a `gate` body *)
  | Neg of expr
  | Add of expr * expr
  | Sub of expr * expr
  | Mul of expr * expr
  | Div of expr * expr
  | Pow of expr * expr
  | Fn of string * expr  (* sin cos tan exp ln sqrt *)

(* An argument is either a single bit or a whole register.  A whole register is a
   *broadcast*: `h q;` applies h to every qubit of q, and `measure q -> c;` pairs them
   off index by index.  Flattening that is `Circuit`'s job, not the parser's. *)
type arg = Whole of string | Index of string * int

type call = { cname : string; cparams : expr list; cargs : arg list; cline : int }

type gate_def = {
  gname : string;
  gparams : string list;
  gqargs : string list;
  gbody : call list;
  gline : int;
}

type stmt =
  | Qreg of string * int
  | Creg of string * int
  | GateDecl of gate_def
  | Opaque of string * string list * string list
  | Apply of call
  | Measure of arg * arg * int
  | Reset of arg * int
  | Barrier of arg list * int
  | If of string * int * stmt * int

type program = { version : string; stmts : stmt list }

(* ------------------------------------------------------------------ evaluation *)

exception Unbound of string

let rec eval ?(env = []) (e : expr) : float =
  let ev = eval ~env in
  match e with
  | Num f -> f
  | Pi -> Float.pi
  | Var v -> ( match List.assoc_opt v env with Some f -> f | None -> raise (Unbound v))
  | Neg a -> -.ev a
  | Add (a, b) -> ev a +. ev b
  | Sub (a, b) -> ev a -. ev b
  | Mul (a, b) -> ev a *. ev b
  | Div (a, b) -> ev a /. ev b
  | Pow (a, b) -> Float.pow (ev a) (ev b)
  | Fn (f, a) -> (
    let x = ev a in
    match f with
    | "sin" -> sin x
    | "cos" -> cos x
    | "tan" -> tan x
    | "exp" -> exp x
    | "ln" -> log x
    | "sqrt" -> sqrt x
    | other -> failwith (Printf.sprintf "unknown function %S" other))

(* ------------------------------------------------------------------ the gate table

   Arity of the standard library.  A program that says `include "qelib1.inc";` uses
   these without declaring them, so the parser has to know them or it cannot tell
   `cx a, b` (legal) from `cx a` (an error worth reporting at the call site).

   Names beyond the OpenQASM 2.0 paper are the ones qiskit's exporter actually emits;
   they are here because the oracle emits them, not because the spec lists them. *)
type arity = { nparams : int; nqubits : int }

let builtins : (string * arity) list =
  [
    (* the two primitives of the language itself *)
    ("U", { nparams = 3; nqubits = 1 });
    ("CX", { nparams = 0; nqubits = 2 });
    (* qelib1.inc *)
    ("u3", { nparams = 3; nqubits = 1 });
    ("u2", { nparams = 2; nqubits = 1 });
    ("u1", { nparams = 1; nqubits = 1 });
    ("u0", { nparams = 1; nqubits = 1 });
    ("id", { nparams = 0; nqubits = 1 });
    ("x", { nparams = 0; nqubits = 1 });
    ("y", { nparams = 0; nqubits = 1 });
    ("z", { nparams = 0; nqubits = 1 });
    ("h", { nparams = 0; nqubits = 1 });
    ("s", { nparams = 0; nqubits = 1 });
    ("sdg", { nparams = 0; nqubits = 1 });
    ("t", { nparams = 0; nqubits = 1 });
    ("tdg", { nparams = 0; nqubits = 1 });
    ("rx", { nparams = 1; nqubits = 1 });
    ("ry", { nparams = 1; nqubits = 1 });
    ("rz", { nparams = 1; nqubits = 1 });
    ("cx", { nparams = 0; nqubits = 2 });
    ("cy", { nparams = 0; nqubits = 2 });
    ("cz", { nparams = 0; nqubits = 2 });
    ("ch", { nparams = 0; nqubits = 2 });
    ("crz", { nparams = 1; nqubits = 2 });
    ("cu1", { nparams = 1; nqubits = 2 });
    ("cu3", { nparams = 3; nqubits = 2 });
    ("swap", { nparams = 0; nqubits = 2 });
    ("ccx", { nparams = 0; nqubits = 3 });
    ("cswap", { nparams = 0; nqubits = 3 });
    (* emitted by qiskit, not in the 2.0 paper *)
    ("u", { nparams = 3; nqubits = 1 });
    ("p", { nparams = 1; nqubits = 1 });
    ("cp", { nparams = 1; nqubits = 2 });
    ("sx", { nparams = 0; nqubits = 1 });
    ("sxdg", { nparams = 0; nqubits = 1 });
    ("crx", { nparams = 1; nqubits = 2 });
    ("cry", { nparams = 1; nqubits = 2 });
    ("rxx", { nparams = 1; nqubits = 2 });
    ("ryy", { nparams = 1; nqubits = 2 });
    ("rzz", { nparams = 1; nqubits = 2 });
    ("rzx", { nparams = 1; nqubits = 2 });
    ("csx", { nparams = 0; nqubits = 2 });
    ("cu", { nparams = 4; nqubits = 2 });
  ]

let builtin name = List.assoc_opt name builtins

(* The Clifford fragment.  D1 makes this the compiler's verified core: a circuit inside
   it can have R10 discharged by stabilizer simulation, which checks the whole program at
   once rather than gate by gate.  Rotations are Clifford only at multiples of pi/2, so
   membership is an angle question and is decided in `Circuit`, not here. *)
let clifford_no_param =
  [ "x"; "y"; "z"; "h"; "s"; "sdg"; "sx"; "sxdg"; "id"; "cx"; "cy"; "cz"; "swap"; "CX" ]

let clifford_at_right_angles = [ "rx"; "ry"; "rz"; "p"; "u1"; "crz"; "cu1"; "cp" ]
