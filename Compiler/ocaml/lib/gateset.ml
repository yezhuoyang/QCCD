(* The native gate set, and the decomposition the compiler emits.

   Every formula here is the computational content of a theorem in
   [Compiler/lean/QCCDC/Pulse/Decompose.lean].  Two of them:

     u3 θ φ λ = VZ (φ+λ) · R (θ, π/2 − λ)                     [QCCDC.u3_decomp]
     CX = e^{-iπ/4} · Ry(-π/2)⊗I · Rx(-π/2)⊗Rx(-π/2)
                    · MS(π/2) · Ry(π/2)⊗I                     [QCCDC.cx_decomp]

   Neither is quoted from the literature.  The CX-from-MS identity in particular appears
   under at least three global-phase conventions, and a wrong phase is invisible in
   isolation but observable the moment the gate is controlled -- which is exactly why it
   was proved rather than remembered.

   {1 The claim that matters for cost}

   `u3_decomp` says a single-qubit gate is one frame update plus {b one physical pulse}.
   A `VZ` is not a pulse: the machine advances the phase of every later pulse on that ion
   and no laser fires, so it takes zero time.  The naive Euler (Z-X-Z) form would emit
   three pulses per gate, so believing it would triple the single-qubit cost of every
   program the compiler ever produces.  `pulse_count` below is therefore a number worth
   checking, and `qccdc decompose` prints it.

   {1 Where the rest is}

   [Gateset_composites] holds everything that reduces to these two primitives -- the
   controlled and three-qubit gates, user-declared gate inlining -- together with the
   n-qubit differential self-test that checks all of it against the defining unitaries. *)

type cx = Complex.t

let ci re im : cx = { Complex.re; im }
let c0 = ci 0.0 0.0
let c1 = ci 1.0 0.0
let cI = ci 0.0 1.0
let ( +: ) = Complex.add
let ( -: ) = Complex.sub
let ( *: ) = Complex.mul
let pi = 4.0 *. atan 1.0

(* ------------------------------------------------------------------ pulses *)

(* One entry of a compiled pulse sequence.  `Frame` carries no duration; the other two
   are the only things that cost time on this hardware. *)
type pulse =
  | Frame of { lam : float; qubit : int }             (* VZ: virtual, free *)
  | Beam of { theta : float; phi : float; qubit : int }  (* R: one physical pulse *)
  | Ms of { theta : float; a : int; b : int }         (* the entangler *)

let is_physical = function Frame _ -> false | Beam _ | Ms _ -> true

let pulse_to_string = function
  | Frame { lam; qubit } -> Printf.sprintf "VZ(%.6g)@q%d" lam qubit
  | Beam { theta; phi; qubit } -> Printf.sprintf "R(%.6g,%.6g)@q%d" theta phi qubit
  | Ms { theta; a; b } -> Printf.sprintf "MS(%.6g)@q%d,q%d" theta a b

(* A decomposed gate: its pulses in TIME order, plus the global phase the identity
   carries.  The phase is tracked rather than dropped because a gate that later becomes
   controlled needs it, and because dropping it silently is the classic way to get a
   compiler that is right on every benchmark and wrong on the one that matters. *)
type decomposed = { pulses : pulse list; phase : float }

(* ------------------------------------------------------------------ the u3 table
 *
 * Every supported single-qubit gate is a `u3`, so every one inherits `u3_decomp` and
 * costs exactly one beam.  The triples are the standard OpenQASM ones, cross-checked
 * against their target matrices by `bridge/derive_pulses.py`. *)

let u3_of_gate (name : string) (params : float list) : (float * float * float) option =
  match (name, params) with
  | "id", _ -> Some (0.0, 0.0, 0.0)
  | "x", _ -> Some (pi, 0.0, pi)
  | "y", _ -> Some (pi, pi /. 2.0, pi /. 2.0)
  | "z", _ -> Some (0.0, 0.0, pi)
  | "h", _ -> Some (pi /. 2.0, 0.0, pi)
  | "s", _ -> Some (0.0, 0.0, pi /. 2.0)
  | "sdg", _ -> Some (0.0, 0.0, -.pi /. 2.0)
  | "t", _ -> Some (0.0, 0.0, pi /. 4.0)
  | "tdg", _ -> Some (0.0, 0.0, -.pi /. 4.0)
  | "sx", _ -> Some (pi /. 2.0, -.pi /. 2.0, pi /. 2.0)
  | "sxdg", _ -> Some (pi /. 2.0, pi /. 2.0, -.pi /. 2.0)
  | ("rx" | "u3x"), [ t ] -> Some (t, -.pi /. 2.0, pi /. 2.0)
  | "ry", [ t ] -> Some (t, 0.0, 0.0)
  | "rz", [ t ] -> Some (0.0, 0.0, t)
  | ("u1" | "p"), [ l ] -> Some (0.0, 0.0, l)
  | "u2", [ p; l ] -> Some (pi /. 2.0, p, l)
  | ("u3" | "u"), [ t; p; l ] -> Some (t, p, l)
  | _ -> None

(* ------------------------------------------------------------------ decomposition *)

(* `u3_decomp`: one frame update, one beam.  Emitted in TIME order, so the beam comes
   first -- the matrix product `VZ · R` applies its rightmost factor first. *)
let decompose_u3 ~(qubit : int) (theta, phi, lam) : decomposed =
  {
    pulses =
      [
        Beam { theta; phi = (pi /. 2.0) -. lam; qubit };
        Frame { lam = phi +. lam; qubit };
      ];
    phase = 0.0;
  }

(* `cx_decomp`.  Time order is the reverse of the matrix order in the theorem. *)
let decompose_cx ~(control : int) ~(target : int) : decomposed =
  {
    pulses =
      [
        Beam { theta = pi /. 2.0; phi = pi /. 2.0; qubit = control };
        Ms { theta = pi /. 2.0; a = control; b = target };
        Beam { theta = -.pi /. 2.0; phi = 0.0; qubit = control };
        Beam { theta = -.pi /. 2.0; phi = 0.0; qubit = target };
        Beam { theta = -.pi /. 2.0; phi = pi /. 2.0; qubit = control };
      ];
    phase = -.pi /. 4.0;
  }

exception Unsupported of string

(* ------------------------------------------------------------------ counting *)

let pulse_count (d : decomposed) = List.length (List.filter is_physical d.pulses)

let ms_count (d : decomposed) =
  List.length (List.filter (function Ms _ -> true | _ -> false) d.pulses)

let beam_count (d : decomposed) = pulse_count d - ms_count d
let frame_count (d : decomposed) = List.length d.pulses - pulse_count d
