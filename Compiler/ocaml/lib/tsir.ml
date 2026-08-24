(* TSIR -- the hardware instruction format, as OCaml reads and writes it.

   The format is not ours to define: it is fixed by [qccd/ir/tsir.py] and documented in
   [docs/tsir.md].  This module is a faithful mirror, and "faithful" is a stronger
   requirement than "sufficient for the compiler".  A reader that silently drops a field
   it does not yet use is a reader that will one day drop the field that mattered, and
   the loss will surface as a cost figure nobody can explain.

   Two decisions keep the mirror honest.

   {b Unknown-but-present fields survive.}  [meta], [template] and [operating_point]
   carry arbitrary JSON that the Python side attaches for provenance.  They are held as
   raw [Yojson.Safe.t] and written back unchanged.

   {b Numeric annotations are not parsed into floats.}  [cost], [steps], [t0] and [t1]
   are claims, not content (docs/tsir.md), and JSON distinguishes [1924] from [1924.0].
   Parsing them to [float] and writing them back would rewrite every integer annotation
   in the document as a float -- a diff on every line, for no gain, and a round-trip that
   fails an equality check for a reason that has nothing to do with correctness.  They
   stay raw; whatever needs their value converts at the point of use.

   Field order on output follows [Instruction.to_json] exactly, so a byte diff against
   the Python writer localises a real disagreement instead of drowning in reordering. *)

type json = Yojson.Safe.t

(* ------------------------------------------------------------------ helpers *)

let mem key = function `Assoc kvs -> List.assoc_opt key kvs | _ -> None

let str_exn key j =
  match mem key j with
  | Some (`String s) -> s
  | Some other ->
    failwith
      (Printf.sprintf "field %S: expected a string, got %s" key
         (Yojson.Safe.to_string other))
  | None -> failwith (Printf.sprintf "field %S: missing" key)

let str_opt key j = match mem key j with Some (`String s) -> Some s | _ -> None

let int_exn key j =
  match mem key j with
  | Some (`Int i) -> i
  | Some other ->
    failwith
      (Printf.sprintf "field %S: expected an int, got %s" key
         (Yojson.Safe.to_string other))
  | None -> failwith (Printf.sprintf "field %S: missing" key)

let int_or key default j = match mem key j with Some (`Int i) -> i | _ -> default
let bool_or key default j = match mem key j with Some (`Bool b) -> b | _ -> default

let strings key j =
  match mem key j with
  | Some (`List xs) ->
    List.map (function `String s -> s | o -> Yojson.Safe.to_string o) xs
  | _ -> []

(* Assoc lists, not maps: Python dicts preserve insertion order and so does the
   document, so preserving it here is what makes the round-trip byte-stable. *)
let assoc_str key j =
  match mem key j with
  | Some (`Assoc kvs) ->
    List.map (function k, `String v -> (k, v) | k, o -> (k, Yojson.Safe.to_string o)) kvs
  | _ -> []

let assoc_raw key j = match mem key j with Some (`Assoc kvs) -> kvs | _ -> []
let raw_opt key j = mem key j

(* ------------------------------------------------------------------ types *)

type participant = { ion : string; src : string; dst : string; via : string list }

type instr = {
  ityp : string;
  id : int;
  cls : string option;
  mode : string option;
  template : json option;
  participants : participant list;
  holds : string list;
  gate : string option;
  (* how many ions one instance of this gate acts on; `None` infers it from `pairs`
     or from two `ions`, exactly as the IR always did.  `Some 1` says `ions` is a BATCH
     of single-qubit gates driven together -- what lets N beams at N traps be one cycle. *)
  arity : int option;
  (* one tuple per operand, aligned with `pairs` or `ions`: `R` with no angle is not a
     hardware instruction *)
  params : float list list;
  pairs : (string * string) list;
  ions : string list;
  sites : string list;
  broadcast : bool;
  placement : (string * string) list;
  quanta : (string * json) list;
  t0 : json option;
  t1 : json option;
  cost : json option;
  steps : json option;
  quanta_delta : json option;
  operating_point : json option;
  meta : (string * json) list;
}

type t = {
  name : string;
  arch_spec : string;
  instructions : instr list;
  metrics : (string * json) list;
  prog_meta : (string * json) list;
  id_seq : int;
}

(* ------------------------------------------------------------------ reading *)

let participant_of_json j =
  {
    ion = str_exn "ion" j;
    src = str_exn "from" j;
    dst = str_exn "to" j;
    via = strings "via" j;
  }

let pairs_of_json j =
  match mem "pairs" j with
  | Some (`List xs) ->
    List.map
      (function
        | `List [ `String a; `String b ] -> (a, b)
        | o -> failwith ("pairs: expected [a, b], got " ^ Yojson.Safe.to_string o))
      xs
  | _ -> []

let instr_of_json j =
  {
    ityp = str_exn "type" j;
    id = int_exn "id" j;
    cls = str_opt "class" j;
    mode = str_opt "mode" j;
    template = raw_opt "template" j;
    participants =
      (match mem "participants" j with
      | Some (`List xs) -> List.map participant_of_json xs
      | _ -> []);
    holds = strings "holds" j;
    gate = str_opt "gate" j;
    arity = (match mem "arity" j with Some (`Int n) -> Some n | _ -> None);
    params =
      (match mem "params" j with
      | Some (`List xs) ->
        List.map
          (function
            | `List ys ->
              List.map (function `Float f -> f | `Int i -> float_of_int i | _ -> 0.0) ys
            | _ -> [])
          xs
      | _ -> []);
    pairs = pairs_of_json j;
    ions = strings "ions" j;
    sites = strings "sites" j;
    broadcast = bool_or "broadcast" false j;
    placement = assoc_str "placement" j;
    quanta = assoc_raw "quanta" j;
    t0 = raw_opt "t0" j;
    t1 = raw_opt "t1" j;
    cost = raw_opt "cost" j;
    steps = raw_opt "steps" j;
    quanta_delta = raw_opt "quanta_delta" j;
    operating_point = raw_opt "operating_point" j;
    meta = assoc_raw "meta" j;
  }

let of_json j =
  let instructions =
    match mem "instructions" j with
    | Some (`List xs) -> List.map instr_of_json xs
    | _ -> []
  in
  let id_seq =
    match mem "id_seq" j with
    | Some (`Int i) -> i
    (* the same migration hook the Python loader carries: a document written before
       `id_seq` existed has ids that were positions, and positions are unique, so the
       allocator only has to resume above the highest. *)
    | _ -> 1 + List.fold_left (fun acc i -> max acc i.id) (-1) instructions
  in
  {
    name = str_exn "name" j;
    arch_spec = str_exn "arch_spec" j;
    instructions;
    metrics = assoc_raw "metrics" j;
    prog_meta = assoc_raw "meta" j;
    id_seq;
  }

let load path = of_json (Yojson.Safe.from_file path)

(* ------------------------------------------------------------------ writing *)

(* [Instruction.to_json] omits absent and empty fields rather than writing nulls, so
   these combinators drop rather than emit. *)
let ( @? ) kvs = function Some kv -> kv :: kvs | None -> kvs
let opt key f = function None -> None | Some v -> Some (key, f v)
let non_empty key f = function [] -> None | xs -> Some (key, f xs)

let participant_to_json p =
  let base = [ ("ion", `String p.ion); ("from", `String p.src); ("to", `String p.dst) ] in
  let via =
    match p.via with
    | [] -> []
    | vs -> [ ("via", `List (List.map (fun v -> `String v) vs)) ]
  in
  `Assoc (base @ via)

let instr_to_json i =
  (* built back-to-front, then reversed: the order is [Instruction.to_json]'s, and it is
     load-bearing for a byte-level diff against the Python writer. *)
  let kvs = [ ("id", `Int i.id); ("type", `String i.ityp) ] in
  let kvs = kvs @? opt "class" (fun s -> `String s) i.cls in
  let kvs = kvs @? opt "mode" (fun s -> `String s) i.mode in
  let kvs = kvs @? opt "template" (fun t -> t) i.template in
  let kvs =
    kvs
    @? non_empty "participants"
         (fun ps -> `List (List.map participant_to_json ps))
         i.participants
  in
  let kvs =
    kvs @? non_empty "holds" (fun hs -> `List (List.map (fun h -> `String h) hs)) i.holds
  in
  let kvs = kvs @? opt "gate" (fun s -> `String s) i.gate in
  let kvs = kvs @? opt "arity" (fun n -> `Int n) i.arity in
  let kvs =
    kvs
    @? non_empty "params"
         (fun ps -> `List (List.map (fun p -> `List (List.map (fun f -> `Float f) p)) ps))
         i.params
  in
  let kvs =
    kvs
    @? non_empty "pairs"
         (fun ps -> `List (List.map (fun (a, b) -> `List [ `String a; `String b ]) ps))
         i.pairs
  in
  let kvs =
    kvs @? non_empty "ions" (fun xs -> `List (List.map (fun s -> `String s) xs)) i.ions
  in
  let kvs =
    kvs @? non_empty "sites" (fun xs -> `List (List.map (fun s -> `String s) xs)) i.sites
  in
  let kvs = if i.broadcast then ("broadcast", `Bool true) :: kvs else kvs in
  let kvs =
    kvs
    @? non_empty "placement"
         (fun ps -> `Assoc (List.map (fun (k, v) -> (k, `String v)) ps))
         i.placement
  in
  let kvs = kvs @? non_empty "quanta" (fun q -> `Assoc q) i.quanta in
  let kvs = kvs @? opt "t0" (fun v -> v) i.t0 in
  let kvs = kvs @? opt "t1" (fun v -> v) i.t1 in
  let kvs = kvs @? opt "cost" (fun v -> v) i.cost in
  let kvs = kvs @? opt "steps" (fun v -> v) i.steps in
  let kvs = kvs @? opt "quanta_delta" (fun v -> v) i.quanta_delta in
  let kvs = kvs @? opt "operating_point" (fun v -> v) i.operating_point in
  let kvs = kvs @? non_empty "meta" (fun m -> `Assoc m) i.meta in
  `Assoc (List.rev kvs)

let to_json p : json =
  `Assoc
    [
      ("name", `String p.name);
      ("arch_spec", `String p.arch_spec);
      ("instructions", `List (List.map instr_to_json p.instructions));
      ("metrics", `Assoc p.metrics);
      ("meta", `Assoc p.prog_meta);
      ("id_seq", `Int p.id_seq);
    ]

let save path p =
  let oc = open_out_bin path in
  Fun.protect
    ~finally:(fun () -> close_out oc)
    (fun () ->
      output_string oc (Yojson.Safe.to_string (to_json p));
      output_char oc '\n')

(* ------------------------------------------------------------------ queries *)

let length p = List.length p.instructions
let of_type ty p = List.filter (fun i -> i.ityp = ty) p.instructions

(* The allocator, mirroring [TSIR.next_id]: [id] is an IDENTITY, never a position.
   Every join in the platform keys on it, so a pass that reassigns ids hands out
   handles that lie.  Side-effecting by design; never call it to peek. *)
let next_id p =
  let n = p.id_seq in
  (n, { p with id_seq = n + 1 })

(* PLAN 1's quantity: rigid rotation needs exactly one movement template where an
   odd-even sort needs many.  A shift by k is *not* k templates -- it is one unit-hop
   template driven k times, which is the property that makes one broadcast waveform
   enough -- so the key normalises to (loop, direction) and the value counts unit
   cycles.  Mirrors [TSIR.templates] and is checked against it in the round-trip test. *)
let templates p =
  let tbl = Hashtbl.create 8 in
  let bump key n =
    Hashtbl.replace tbl key (n + try Hashtbl.find tbl key with Not_found -> 0)
  in
  List.iter
    (fun i ->
      if i.ityp = "simd" then
        match i.template with
        | Some (`Assoc _ as t) when mem "kind" t = Some (`String "loop_shift") ->
          let loop = match mem "loop" t with Some (`String l) -> l | _ -> "?" in
          let delta = match mem "delta" t with Some (`Int d) -> d | _ -> 0 in
          if delta <> 0 then
            bump
              (Printf.sprintf "loop_shift:%s:%s" loop (if delta > 0 then "+1" else "-1"))
              (abs delta)
        | _ -> bump (Printf.sprintf "class:%s" (Option.value i.cls ~default:"?")) 1)
    p.instructions;
  Hashtbl.fold (fun k v acc -> (k, v) :: acc) tbl []
  |> List.sort (fun (a, _) (b, _) -> compare a b)

(* ------------------------------------------------------------------ validation *)

(* Shape checks that need no architecture, mirroring [validate_program].  Running them
   on both sides is not redundant: it is the differential test that catches a reader
   which accepted something the writer's own validator would have refused. *)
let validate p =
  let errors = ref [] in
  let add e = errors := e :: !errors in
  let seen = Hashtbl.create 64 in
  let known =
    [ "init"; "simd"; "gate"; "measure"; "reset"; "cool"; "barrier" ]
  in
  List.iteri
    (fun n i ->
      let where = Printf.sprintf "instructions[%d] (id=%d)" n i.id in
      if not (List.mem i.ityp known) then
        add (Printf.sprintf "%s: unknown type %S" where i.ityp);
      if Hashtbl.mem seen i.id then add (Printf.sprintf "%s: duplicate instruction id" where);
      Hashtbl.replace seen i.id ();
      if i.ityp = "init" && i.placement = [] then
        add (Printf.sprintf "%s: init carries no placement" where);
      if i.ityp = "simd" then begin
        if i.cls = None then add (Printf.sprintf "%s: simd carries no class (R4 needs one)" where);
        if i.participants = [] && i.template = None then
          add (Printf.sprintf "%s: simd has neither participants nor a template" where);
        match i.mode with
        | Some ("intra" | "inter") -> ()
        | _ -> add (Printf.sprintf "%s: simd mode must be 'intra' or 'inter' (R4b)" where)
      end;
      if i.ityp = "gate" then begin
        (match i.arity with
        | Some 1 ->
          if i.ions = [] then add (Printf.sprintf "%s: arity=1 gate carries no `ions`" where);
          if i.pairs <> [] then
            add (Printf.sprintf "%s: arity=1 gate must not carry `pairs`" where)
        | _ ->
          if i.pairs = [] && not (List.mem (List.length i.ions) [ 1; 2 ]) then
            add (Printf.sprintf "%s: gate needs `pairs`, two `ions`, or one `ion`" where));
        if i.gate = None then add (Printf.sprintf "%s: gate carries no gate name" where)
      end)
    p.instructions;
  let highest = List.fold_left (fun acc i -> max acc i.id) (-1) p.instructions in
  if p.instructions <> [] && p.id_seq <= highest then
    add
      (Printf.sprintf "id_seq=%d would re-issue a live id (highest in use is %d)" p.id_seq
         highest);
  (match p.instructions with
  | first :: _ when first.ityp <> "init" ->
    add "instructions[0]: a program must open with `init`"
  | _ -> ());
  List.rev !errors
