(* The expanded architecture, as the compiler sees it.

   Produced by [Compiler/bridge/export_arch.py].  The contract, restated here because it
   is the one thing about this module that must not erode: {b Python owns expansion.}  A
   generator is a parameter tuple ([ring(72, 2, 24)]) that expands into an explicit
   graph, and expansion is where every derived quantity the cost model hangs off is
   computed -- node degree, hence what counts as a junction (R18); loop corners, hence
   what a rigid hop costs.  Recomputing any of that here would create a second opinion
   about what a junction is, and the first symptom would be a cost figure that differs
   from the verifier's by an amount nobody can attribute.

   So: no generators, no degree computation, no corner derivation.  Read what the
   document says and fail loudly when it says nothing.

   {1 What the router actually needs from this}

   Three structural facts, and they are not the obvious ones:

   - [node_caps]: can this node host a 2Q gate, and what is its capacity (R1, R6).
   - [simd_classes] + [max_simd_classes_per_cycle]: which movement classes exist and how
     many may be active in one cycle (R4).  On every shipped device this is {b 1}.
   - [loops]: a named path is one conveyor, so "forward one slot" is the same instruction
     to every site on it.  Moves {e off} a named loop have no conveyor and no direction,
     which is why R4d is silent about a dock -- and why silence there is not a pass.

   The last one is what makes routing device-dependent in a way a generic MAPF solver
   cannot express.  See [regime] below. *)

type json = Yojson.Safe.t

let mem key = function `Assoc kvs -> List.assoc_opt key kvs | _ -> None

let str_opt key j = match mem key j with Some (`String s) -> Some s | _ -> None

let str_or key d j = Option.value (str_opt key j) ~default:d

let int_or key d j = match mem key j with Some (`Int i) -> i | _ -> d

let bool_or key d j = match mem key j with Some (`Bool b) -> b | _ -> d

let float_or key d j =
  match mem key j with Some (`Float f) -> f | Some (`Int i) -> float_of_int i | _ -> d

let list_of key j = match mem key j with Some (`List xs) -> xs | _ -> []

let strings key j =
  List.filter_map (function `String s -> Some s | _ -> None) (list_of key j)

(* ------------------------------------------------------------------ types *)

type node = {
  id : string;
  pos : float * float;
  kind : string;
  capacity : int;
  zone_type : string;
  labels : string list;
  degree : int;
  is_junction : bool;
  can_gate : bool;
  can_spam : bool;
  can_cool : bool;
}

type segment = {
  sid : string;
  a : string;
  b : string;
  seg_capacity : int;
  loop : string option;
  corner_endpoints : int;
}

type loop = { lid : string; nodes : string list; closed : bool; corners : string list }

type simd_class = {
  cid : string;
  ctype : string;
  orbit : string option;      (* the named path this class drives, or "any" *)
  delta : int option;         (* signed hops along that path, when fixed *)
  direction : string option;  (* "inward" / "outward" for spur classes *)
  entails : string list;      (* primitives implied, e.g. split + merge for a dock *)
}

type t = {
  name : string;
  sha256 : string;
  nodes : (string, node) Hashtbl.t;
  node_order : string list;
  segments : segment list;
  loops : loop list;
  classes : simd_class list;
  max_classes_per_cycle : int;
  wiring : string;   (* "direct" | "wise" | ... *)
  grouping : string; (* "direct" | "broadcast" *)
  switch_per_site : bool;
  raw : json;
}

(* ------------------------------------------------------------------ reading *)

let node_of_json caps j =
  let id = str_or "id" "?" j in
  let cap = match mem id caps with Some c -> c | None -> `Assoc [] in
  let pos =
    match mem "pos" j with
    | Some (`List [ x; y ]) ->
      let f = function `Float v -> v | `Int v -> float_of_int v | _ -> 0.0 in
      (f x, f y)
    | _ -> (0.0, 0.0)
  in
  {
    id;
    pos;
    kind = str_or "kind" "site" j;
    (* capacity is resolved during expansion (a node may inherit its zone type's), so
       the per-node capability block is authoritative over the raw node record. *)
    capacity = int_or "capacity" (int_or "capacity" 1 j) cap;
    zone_type = str_or "zone_type" (str_or "zone_type" "trap" j) cap;
    labels = (match strings "labels" cap with [] -> strings "labels" j | ls -> ls);
    degree = int_or "degree" 0 cap;
    is_junction = bool_or "is_junction" false cap;
    can_gate = bool_or "gate" false cap;
    can_spam = bool_or "spam" false cap;
    can_cool = bool_or "cool" false cap;
  }

let segment_of_json corner_ends j =
  let sid = str_or "id" "?" j in
  (* The document spells a segment's endpoints `ends: [a, b]` -- NOT `a`/`b`, and not
     `from`/`to`.  Getting this wrong is silent: every segment parses, the round-trip
     still succeeds (it never touches this record), and the router simply finds a graph
     with no edges.  `check_structure` below is what turns that into an error. *)
  let a, b =
    match mem "ends" j with
    | Some (`List [ `String a; `String b ]) -> (a, b)
    | Some other ->
      failwith
        (Printf.sprintf "segment %S: `ends` must be [a, b], got %s" sid
           (Yojson.Safe.to_string other))
    | None -> failwith (Printf.sprintf "segment %S: no `ends`" sid)
  in
  {
    sid;
    a;
    b;
    seg_capacity = int_or "capacity" 1 j;
    (* null for a spur: a segment on no named path has no conveyor, hence no direction,
       hence no R4d verdict.  The router must treat `None` as "individually addressable",
       not as "unconstrained". *)
    loop = str_opt "loop" j;
    corner_endpoints =
      (match mem "corner_endpoints" j with
      | Some (`Int n) -> n
      | _ -> ( match mem sid corner_ends with Some (`Int n) -> n | _ -> 0));
  }

let loop_of_json corners j =
  let lid = str_or "id" "?" j in
  {
    lid;
    nodes = strings "nodes" j;
    closed = bool_or "closed" false j;
    corners =
      (match mem lid corners with
      | Some (`List xs) ->
        List.filter_map (function `String s -> Some s | _ -> None) xs
      | _ -> []);
  }

let class_of_json (cid, j) =
  {
    cid;
    ctype = str_or "type" "shift" j;
    orbit = str_opt "orbit" j;
    delta = (match mem "delta" j with Some (`Int d) -> Some d | _ -> None);
    direction = str_opt "direction" j;
    entails = strings "entails" j;
  }

let of_json (j : json) : t =
  let geometry = Option.value (mem "geometry" j) ~default:(`Assoc []) in
  let caps = Option.value (mem "node_caps" j) ~default:(`Assoc []) in
  let corner_ends = Option.value (mem "corner_endpoints" j) ~default:(`Assoc []) in
  let corners = Option.value (mem "loop_corners" j) ~default:(`Assoc []) in
  let nodes_json = list_of "nodes" geometry in
  let node_list = List.map (node_of_json caps) nodes_json in
  let tbl = Hashtbl.create (List.length node_list) in
  List.iter (fun n -> Hashtbl.replace tbl n.id n) node_list;
  let control = Option.value (mem "control" j) ~default:(`Assoc []) in
  let channels = Option.value (mem "channels" control) ~default:(`Assoc []) in
  let wiring = Option.value (mem "wiring" control) ~default:(`Assoc []) in
  {
    name = str_or "name" "?" j;
    sha256 = str_or "sha256" "" j;
    nodes = tbl;
    node_order = List.map (fun n -> n.id) node_list;
    segments = List.map (segment_of_json corner_ends) (list_of "segments" geometry);
    loops = List.map (loop_of_json corners) (list_of "loops" geometry);
    classes =
      (match mem "simd_classes" j with
      | Some (`Assoc kvs) -> List.map class_of_json kvs
      | _ -> []);
    max_classes_per_cycle = int_or "max_simd_classes_per_cycle" 1 j;
    wiring = str_or "scheme" "direct" wiring;
    grouping = str_or "grouping" "direct" channels;
    switch_per_site = bool_or "switch_per_site" false channels;
    raw = j;
  }

let load path = of_json (Yojson.Safe.from_file path)

(* ------------------------------------------------------------------ queries *)

let node a id = Hashtbl.find_opt a.nodes id
let n_nodes a = Hashtbl.length a.nodes
let junctions a = List.filter (fun id -> match node a id with Some n -> n.is_junction | None -> false) a.node_order
(* How many ions may rest here at once.

   NOT simply `capacity`.  R2 caps a degree->=3 node at ONE ion at any instant, whatever
   its zone type declares -- and on the shipped ring the 24 dock rail slots are both:
   `data` zone with capacity 2, and degree 3 because a spur hangs off them.  Reading
   `capacity` alone puts two ions on one of those and the verifier rejects the program.
   Every occupancy decision in the compiler goes through this. *)
(* R13's bound: a two-qubit gate in a chain of more than ~15 ions degrades sharply, so a
   trap that will ever host a gate may hold at most this many.  R1's capacity can be far
   larger -- `stationary_chain`'s `register` zone declares 32 -- and placing against R1
   alone puts 16 ions in a trap and violates R13 at the very first gate. *)
let gate_chain_limit = 15

let eff_capacity a id =
  match node a id with
  | None -> 0
  | Some n ->
    if n.is_junction then min n.capacity 1
    else min n.capacity gate_chain_limit

let gate_sites a = List.filter (fun id -> match node a id with Some n -> n.can_gate | None -> false) a.node_order

(* Adjacency, built once.  The router asks for neighbours in its inner loop, and
   scanning 288 segments per query is the difference between a routing pass that runs
   and one that does not. *)
let adjacency a =
  let adj = Hashtbl.create (n_nodes a) in
  let push k v =
    Hashtbl.replace adj k (v :: (try Hashtbl.find adj k with Not_found -> []))
  in
  List.iter
    (fun s ->
      push s.a (s.b, s);
      push s.b (s.a, s))
    a.segments;
  adj

let loop_of a lid = List.find_opt (fun l -> l.lid = lid) a.loops

(* {1 The structural self-check}

   Python computed the degree of every node during expansion, and the document carries
   it.  We can rebuild it from the segment list.  If the two disagree, this reader has
   misunderstood the document -- and the failure mode that motivates the check is
   entirely silent: spell the endpoint keys wrong and every segment still parses, the
   TSIR round-trip still passes (it never reads a segment), and the router just finds a
   graph with no edges and reports that nothing is reachable.

   A degree comparison catches that on the first architecture loaded, which is the whole
   argument for exporting a derived quantity rather than only the primitive one: it gives
   the consumer something to check itself against. *)
let check_structure a =
  let errors = ref [] in
  let add e = errors := e :: !errors in
  let adj = adjacency a in
  List.iter
    (fun id ->
      match node a id with
      | None -> ()
      | Some n ->
        let got = List.length (try Hashtbl.find adj id with Not_found -> []) in
        if got <> n.degree then
          add
            (Printf.sprintf "node %s: document says degree %d, segments give %d" id
               n.degree got))
    a.node_order;
  List.iter
    (fun s ->
      if not (Hashtbl.mem a.nodes s.a) then
        add (Printf.sprintf "segment %s: unknown endpoint %S" s.sid s.a);
      if not (Hashtbl.mem a.nodes s.b) then
        add (Printf.sprintf "segment %s: unknown endpoint %S" s.sid s.b))
    a.segments;
  List.iter
    (fun l ->
      List.iter
        (fun n ->
          if not (Hashtbl.mem a.nodes n) then
            add (Printf.sprintf "loop %s: unknown node %S" l.lid n))
        l.nodes)
    a.loops;
  List.rev !errors

(* {1 The routing regime}

   Which of three problems the router is actually solving on this device.  This is not
   cosmetic: it selects the encoding, and getting it wrong means either an unsound
   schedule or a needlessly crippled one.

   The discriminator is (are there named loops?) x (is the wiring broadcast?), because
   R4d judges only moves along a named path -- "a named path is one conveyor, so forward
   one slot is the same instruction to every site on it" -- and says nothing at all about
   a move off one.

   - [Conveyor]: named loops + broadcast wiring.  Every ion moving along a loop in a
     cycle moves the same signed delta.  `ring144_24v`, `ladder_2x72`, `cyclone_*`,
     `h2_racetrack`.  The hard case, and the one ordinary MAPF cannot express.
   - [Broadcast_free]: no named loop, but broadcast channels.  Moves are individually
     chosen yet must still be producible by the declared channels.  `deck_unit_cell`.
   - [Free]: no named loop, direct wiring, per-site switches -- every electrode driven
     individually, so a cycle may ask different ions to do different things.  R4's class
     budget still binds.  `grid9x9`, `chain72`, `stationary_chain`.

   `grid9x9` and `deck_unit_cell` are the same 225-node graph and land in different
   regimes purely on the strength of their wiring, which is exactly the README's point
   that "the wiring is the whole cost". *)
type regime = Conveyor | Broadcast_free | Free

let regime a =
  (* `direct` grouping is one channel per site per role ("channels are O(sites)"), so a
     channel never drives two sites and R4d's "asked to do 2 different things" can never
     fire.  Every move is individually addressable, whether or not the device also
     declares named loops -- which is why `chain72` and `stationary_chain` are Free
     despite both carrying a path. *)
  match a.grouping with
  | "direct" -> Free
  | _ -> if a.loops = [] then Broadcast_free else Conveyor

let regime_name = function
  | Conveyor -> "conveyor"
  | Broadcast_free -> "broadcast-free"
  | Free -> "free"

let describe a =
  Printf.sprintf
    "%s: %d nodes, %d segments, %d junctions, %d gate sites, %d loops, %d classes (max \
     %d/cycle), wiring %s/%s -> %s regime"
    a.name (n_nodes a) (List.length a.segments)
    (List.length (junctions a))
    (List.length (gate_sites a))
    (List.length a.loops) (List.length a.classes) a.max_classes_per_cycle a.wiring
    a.grouping
    (regime_name (regime a))
