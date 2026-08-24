(* Lexer and recursive-descent parser for OpenQASM 2.0.

   Hand-written rather than menhir-generated, for one reason that matters here: the input
   is machine-generated, so when it fails to parse the question is always "which line of
   a 40,000-line generated file, and what did the generator emit that we did not expect".
   A hand-written parser answers that; a generated one says `Parse_error`.

   Every failure therefore carries a line number and the token that caused it. *)

open Qasm_ast

exception Error of string

let fail line fmt =
  Printf.ksprintf (fun s -> raise (Error (Printf.sprintf "line %d: %s" line s))) fmt

(* ------------------------------------------------------------------ lexer *)

type token =
  | TIdent of string
  | TInt of int
  | TReal of float
  | TStr of string
  | TSemi
  | TComma
  | TLParen
  | TRParen
  | TLBrack
  | TRBrack
  | TLBrace
  | TRBrace
  | TArrow
  | TEqEq
  | TPlus
  | TMinus
  | TStar
  | TSlash
  | TCaret
  | TEOF

let tok_str = function
  | TIdent s -> Printf.sprintf "identifier %S" s
  | TInt i -> string_of_int i
  | TReal f -> string_of_float f
  | TStr s -> Printf.sprintf "%S" s
  | TSemi -> "';'"
  | TComma -> "','"
  | TLParen -> "'('"
  | TRParen -> "')'"
  | TLBrack -> "'['"
  | TRBrack -> "']'"
  | TLBrace -> "'{'"
  | TRBrace -> "'}'"
  | TArrow -> "'->'"
  | TEqEq -> "'=='"
  | TPlus -> "'+'"
  | TMinus -> "'-'"
  | TStar -> "'*'"
  | TSlash -> "'/'"
  | TCaret -> "'^'"
  | TEOF -> "end of file"

let is_digit c = c >= '0' && c <= '9'
let is_alpha c = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c = '_'
let is_alnum c = is_alpha c || is_digit c

(* Tokens carry their line so the parser can attribute every error and so every op in
   the DAG can be traced back to the source that produced it. *)
let lex (src : string) : (token * int) array =
  let n = String.length src in
  let out = ref [] in
  let line = ref 1 in
  let i = ref 0 in
  let push t = out := (t, !line) :: !out in
  while !i < n do
    let c = src.[!i] in
    if c = '\n' then (
      incr line;
      incr i)
    else if c = ' ' || c = '\t' || c = '\r' then incr i
    else if c = '/' && !i + 1 < n && src.[!i + 1] = '/' then
      while !i < n && src.[!i] <> '\n' do
        incr i
      done
    else if c = '/' && !i + 1 < n && src.[!i + 1] = '*' then begin
      (* block comments are not in the 2.0 grammar but appear in hand-written files *)
      i := !i + 2;
      let stop = ref false in
      while (not !stop) && !i < n do
        if src.[!i] = '\n' then incr line;
        if src.[!i] = '*' && !i + 1 < n && src.[!i + 1] = '/' then (
          i := !i + 2;
          stop := true)
        else incr i
      done
    end
    else if c = '"' then begin
      let j = ref (!i + 1) in
      while !j < n && src.[!j] <> '"' do
        incr j
      done;
      if !j >= n then fail !line "unterminated string";
      push (TStr (String.sub src (!i + 1) (!j - !i - 1)));
      i := !j + 1
    end
    else if is_alpha c then begin
      let j = ref !i in
      while !j < n && is_alnum src.[!j] do
        incr j
      done;
      push (TIdent (String.sub src !i (!j - !i)));
      i := !j
    end
    else if is_digit c || (c = '.' && !i + 1 < n && is_digit src.[!i + 1]) then begin
      (* one number scanner for both forms: a token is real if it contains '.', 'e' or
         'E', integer otherwise.  Splitting them would mis-lex `2e-3`. *)
      let j = ref !i in
      let real = ref false in
      while
        !j < n
        && (is_digit src.[!j] || src.[!j] = '.'
           || ((src.[!j] = 'e' || src.[!j] = 'E') && !j > !i)
           || ((src.[!j] = '+' || src.[!j] = '-')
              && !j > !i
              && (src.[!j - 1] = 'e' || src.[!j - 1] = 'E')))
      do
        if src.[!j] = '.' || src.[!j] = 'e' || src.[!j] = 'E' then real := true;
        incr j
      done;
      let s = String.sub src !i (!j - !i) in
      push (if !real then TReal (float_of_string s) else TInt (int_of_string s));
      i := !j
    end
    else begin
      let two = if !i + 1 < n then String.sub src !i 2 else "" in
      if two = "->" then (
        push TArrow;
        i := !i + 2)
      else if two = "==" then (
        push TEqEq;
        i := !i + 2)
      else begin
        (match c with
        | ';' -> push TSemi
        | ',' -> push TComma
        | '(' -> push TLParen
        | ')' -> push TRParen
        | '[' -> push TLBrack
        | ']' -> push TRBrack
        | '{' -> push TLBrace
        | '}' -> push TRBrace
        | '+' -> push TPlus
        | '-' -> push TMinus
        | '*' -> push TStar
        | '/' -> push TSlash
        | '^' -> push TCaret
        | _ -> fail !line "unexpected character %C" c);
        incr i
      end
    end
  done;
  push TEOF;
  Array.of_list (List.rev !out)

(* ------------------------------------------------------------------ parser state *)

type state = { toks : (token * int) array; mutable pos : int }

let peek st = fst st.toks.(st.pos)
let line st = snd st.toks.(st.pos)
let advance st = st.pos <- st.pos + 1

let next st =
  let t = peek st in
  advance st;
  t

let expect st want =
  let got = peek st in
  if got <> want then fail (line st) "expected %s, got %s" (tok_str want) (tok_str got);
  advance st

let ident st =
  match next st with
  | TIdent s -> s
  | t -> fail (line st) "expected an identifier, got %s" (tok_str t)

let int_lit st =
  match next st with
  | TInt i -> i
  | t -> fail (line st) "expected an integer, got %s" (tok_str t)

let accept st want =
  if peek st = want then (
    advance st;
    true)
  else false

(* ------------------------------------------------------------------ expressions

   Precedence, loosest first: additive, then multiplicative, then unary minus, then
   `^`, then atoms.  `^` is right-associative, the usual reading of `2^3^2`. *)

let rec parse_expr st = parse_add st

and parse_add st =
  let left = ref (parse_mul st) in
  let rec go () =
    match peek st with
    | TPlus ->
      advance st;
      left := Add (!left, parse_mul st);
      go ()
    | TMinus ->
      advance st;
      left := Sub (!left, parse_mul st);
      go ()
    | _ -> ()
  in
  go ();
  !left

and parse_mul st =
  let left = ref (parse_unary st) in
  let rec go () =
    match peek st with
    | TStar ->
      advance st;
      left := Mul (!left, parse_unary st);
      go ()
    | TSlash ->
      advance st;
      left := Div (!left, parse_unary st);
      go ()
    | _ -> ()
  in
  go ();
  !left

and parse_unary st =
  match peek st with
  | TMinus ->
    advance st;
    Neg (parse_unary st)
  | TPlus ->
    advance st;
    parse_unary st
  | _ -> parse_pow st

and parse_pow st =
  let base = parse_atom st in
  if accept st TCaret then Pow (base, parse_unary st) else base

and parse_atom st =
  match next st with
  | TInt i -> Num (float_of_int i)
  | TReal f -> Num f
  | TLParen ->
    let e = parse_expr st in
    expect st TRParen;
    e
  | TIdent "pi" -> Pi
  | TIdent name when List.mem name [ "sin"; "cos"; "tan"; "exp"; "ln"; "sqrt" ] ->
    expect st TLParen;
    let e = parse_expr st in
    expect st TRParen;
    Fn (name, e)
  | TIdent v -> Var v  (* a formal parameter; only legal inside a gate body *)
  | t -> fail (line st) "expected an expression, got %s" (tok_str t)

(* ------------------------------------------------------------------ arguments *)

let parse_arg st =
  let name = ident st in
  if accept st TLBrack then begin
    let i = int_lit st in
    expect st TRBrack;
    Index (name, i)
  end
  else Whole name

let parse_arglist st =
  let first = parse_arg st in
  let rec go acc = if accept st TComma then go (parse_arg st :: acc) else List.rev acc in
  go [ first ]

let parse_params st =
  (* `h q;` has no parens at all; `rz(0.5) q;` has them; `u2 () q;` is legal and empty. *)
  if accept st TLParen then
    if accept st TRParen then []
    else begin
      let first = parse_expr st in
      let rec go acc =
        if accept st TComma then go (parse_expr st :: acc) else List.rev acc
      in
      let ps = go [ first ] in
      expect st TRParen;
      ps
    end
  else []

(* ------------------------------------------------------------------ statements *)

let check_arity ~decls ln name nparams nqubits =
  let expected =
    match builtin name with
    | Some a -> Some (a.nparams, a.nqubits)
    | None -> (
      match List.assoc_opt name decls with
      | Some (g : gate_def) -> Some (List.length g.gparams, List.length g.gqargs)
      | None -> None)
  in
  match expected with
  | None ->
    (* An undeclared gate is an error, not a warning.  qelib1's contents are known
       (Qasm_ast.builtins), so reaching here means either a typo or a gate set we do not
       model -- and silently accepting it would produce a DAG with the wrong arity. *)
    fail ln "gate %S is neither a builtin nor declared" name
  | Some (p, q) ->
    if p <> nparams then
      fail ln "gate %S takes %d parameter(s), got %d" name p nparams;
    (* a whole-register argument broadcasts, so the *count* must match even though the
       width does not resolve until flattening *)
    if q <> nqubits then fail ln "gate %S takes %d qubit(s), got %d" name q nqubits

let rec parse_stmt st ~decls : stmt =
  let ln = line st in
  match peek st with
  | TIdent "qreg" ->
    advance st;
    let name = ident st in
    expect st TLBrack;
    let n = int_lit st in
    expect st TRBrack;
    expect st TSemi;
    Qreg (name, n)
  | TIdent "creg" ->
    advance st;
    let name = ident st in
    expect st TLBrack;
    let n = int_lit st in
    expect st TRBrack;
    expect st TSemi;
    Creg (name, n)
  | TIdent "gate" ->
    advance st;
    let name = ident st in
    let params =
      if accept st TLParen then
        if accept st TRParen then []
        else begin
          let first = ident st in
          let rec go acc = if accept st TComma then go (ident st :: acc) else List.rev acc in
          let ps = go [ first ] in
          expect st TRParen;
          ps
        end
      else []
    in
    let qargs =
      let first = ident st in
      let rec go acc = if accept st TComma then go (ident st :: acc) else List.rev acc in
      go [ first ]
    in
    expect st TLBrace;
    let body = ref [] in
    while peek st <> TRBrace do
      (* a gate body holds only unitary calls and barriers; a barrier inside a body has
         no effect on the DAG the oracle builds, so it is parsed and dropped *)
      let bl = line st in
      let g = ident st in
      if g = "barrier" then begin
        let _ = parse_arglist st in
        expect st TSemi
      end
      else begin
        let ps = parse_params st in
        let args = parse_arglist st in
        expect st TSemi;
        body := { cname = g; cparams = ps; cargs = args; cline = bl } :: !body
      end
    done;
    expect st TRBrace;
    GateDecl
      { gname = name; gparams = params; gqargs = qargs; gbody = List.rev !body; gline = ln }
  | TIdent "opaque" ->
    advance st;
    let name = ident st in
    let params =
      if accept st TLParen then
        if accept st TRParen then []
        else begin
          let first = ident st in
          let rec go acc = if accept st TComma then go (ident st :: acc) else List.rev acc in
          let ps = go [ first ] in
          expect st TRParen;
          ps
        end
      else []
    in
    let qargs =
      let first = ident st in
      let rec go acc = if accept st TComma then go (ident st :: acc) else List.rev acc in
      go [ first ]
    in
    expect st TSemi;
    Opaque (name, params, qargs)
  | TIdent "measure" ->
    advance st;
    let q = parse_arg st in
    expect st TArrow;
    let c = parse_arg st in
    expect st TSemi;
    Measure (q, c, ln)
  | TIdent "reset" ->
    advance st;
    let q = parse_arg st in
    expect st TSemi;
    Reset (q, ln)
  | TIdent "barrier" ->
    advance st;
    let args = parse_arglist st in
    expect st TSemi;
    Barrier (args, ln)
  | TIdent "if" ->
    advance st;
    expect st TLParen;
    let creg = ident st in
    expect st TEqEq;
    let v = int_lit st in
    expect st TRParen;
    let inner = parse_stmt st ~decls in
    If (creg, v, inner, ln)
  | TIdent name ->
    advance st;
    let ps = parse_params st in
    let args = parse_arglist st in
    expect st TSemi;
    check_arity ~decls ln name (List.length ps) (List.length args);
    Apply { cname = name; cparams = ps; cargs = args; cline = ln }
  | t -> fail ln "expected a statement, got %s" (tok_str t)

(* ------------------------------------------------------------------ program *)

let parse (src : string) : program =
  let st = { toks = lex src; pos = 0 } in
  let version = ref "2.0" in
  if peek st = TIdent "OPENQASM" then begin
    advance st;
    (match next st with
    | TReal f -> version := Printf.sprintf "%g" f
    | TInt i -> version := string_of_int i
    | t -> fail (line st) "expected a version number, got %s" (tok_str t));
    expect st TSemi
  end;
  let stmts = ref [] in
  let decls = ref [] in
  while peek st <> TEOF do
    if peek st = TIdent "include" then begin
      advance st;
      (* the standard library's contents are known statically (Qasm_ast.builtins), so an
         include is acknowledged and not read.  A non-standard include is refused rather
         than ignored: pretending to have read a file we did not is how a compiler ends
         up with the wrong arity and no error. *)
      (match next st with
      | TStr ("qelib1.inc" | "stdgates.inc") -> ()
      | TStr other -> fail (line st) "cannot resolve include %S (only qelib1.inc is known)" other
      | t -> fail (line st) "expected a filename, got %s" (tok_str t));
      expect st TSemi
    end
    else begin
      let s = parse_stmt st ~decls:!decls in
      (match s with GateDecl g -> decls := (g.gname, g) :: !decls | _ -> ());
      stmts := s :: !stmts
    end
  done;
  { version = !version; stmts = List.rev !stmts }

let parse_file path =
  let ic = open_in_bin path in
  Fun.protect
    ~finally:(fun () -> close_in ic)
    (fun () ->
      let n = in_channel_length ic in
      parse (really_input_string ic n))
