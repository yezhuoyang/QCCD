(* Independent, executable contracts at compiler boundaries.  These checks are
   deliberately smaller than the optimizers.  Their certificates state coverage;
   numerical matrix comparison is not a formal proof or a full hardware verdict. *)
exception Failed of string

let require yes message = if not yes then raise (Failed message)
let strings xs = `List (List.map (fun x -> `String x) xs)
let ints xs = `List (List.map (fun x -> `Int x) xs)
let names = [
  "circuit_normalization", "Circuit Normalization";
  "dependency_analysis", "Dependency Analysis";
  "initial_placement", "Initial Placement";
  "interaction_placement", "Interaction Placement";
  "transport_synthesis", "Transport Synthesis";
  "native_gate_lowering", "Native Gate Lowering";
  "broadcast_packing", "Broadcast Packing" ]

let record ?(status = "passed") ?(diagnostics = []) ?(iteration = `Null)
    id ~input ~output ~witness ~coverage =
  `Assoc ["id", `String id; "name", `String (List.assoc id names);
          "iteration", iteration; "input", input; "output", output;
          "witness", witness;
          "check", `Assoc ["status", `String status;
                            "method", `String ("ocaml."^id^".v1"); "coverage", strings coverage;
                            "diagnostics", strings diagnostics]]

let position_json pos =
  `Assoc (Hashtbl.fold (fun i s acc -> (i, `String s) :: acc) pos [] |> List.sort compare)

let op_json (o : Circuit.op) =
  `Assoc ["i", `Int o.index; "name", `String o.name; "qubits", ints o.qubits;
          "clbits", ints o.clbits; "params", `List (List.map (fun x -> `Float x) o.params);
          "source_line", `Int o.src_line;
          "condition", (match o.cond with None -> `Null
                         | Some (r,v) -> `List [`String r; `Int v])]

(* Circuit.to_json is the deliberately rounded differential-DAG interchange.
   A replayable compiler artifact must retain the original floating-point values. *)
let circuit_json (c:Circuit.t) = match Circuit.to_json c with
  | `Assoc xs -> `Assoc (List.map (fun (k,v)->
      if k="ops" then k,`List (List.map op_json c.ops) else k,v) xs)
  | _ -> assert false

(* This evaluator reads gate definitions, not the pulse decomposition table.  The
   small matrix primitives/target definitions are shared with the existing numerical
   test oracle; custom bodies are interpreted independently of pulse inlining. *)
let rec logical_matrix ?(depth = 0) gates name params arity =
  let open Gateset_composites in
  require (depth < 64) "gate definitions are recursive or too deeply nested";
  match target arity name params with
  | Some m -> m
  | None ->
    let g = match List.assoc_opt name gates with
      | Some g -> g | None -> raise (Failed ("no semantic checker for gate " ^ name)) in
    require (List.length g.Qasm_ast.gqargs = arity) "custom gate arity mismatch";
    require (List.length g.gparams = List.length params) "custom gate parameter mismatch";
    let env = List.combine g.gparams params in
    let qmap = List.mapi (fun q s -> s,q) g.gqargs in
    List.fold_left (fun acc (call : Qasm_ast.call) ->
      let qs = List.map (function Qasm_ast.Whole s -> List.assoc s qmap
                           | _ -> raise (Failed "indexed custom gate operand")) call.cargs in
      let ps = List.map (Qasm_ast.eval ~env) call.cparams in
      let m = logical_matrix ~depth:(depth+1) gates call.cname ps (List.length qs) in
      let lifted = match qs with
        | [q] -> embed1 arity q m
        | [q;r] -> embed2 arity q r m
        | _ -> raise (Failed "custom gate checker supports one- and two-qubit bodies") in
      mmul lifted acc) (eye (dim arity)) g.gbody

let phase_distance a b =
  (* Gate programs preserve unitary semantics up to a physically irrelevant global
     phase.  Relative phases and operand order remain observable and checked. *)
  let open Gateset_composites in
  let pivot = ref None in
  Array.iteri (fun i row -> Array.iteri (fun j z ->
    if Complex.norm z > 1e-10 && !pivot = None then pivot := Some (i,j)) row) b;
  match !pivot with
  | None -> infinity
  | Some (i,j) ->
    let phase = Complex.div a.(i).(j) b.(i).(j) in
    if abs_float (Complex.norm phase -. 1.0) > 1e-9 then infinity
    else max_diff a (scale phase b)

let check_normalization (source : Circuit.t) (output : Circuit.t) sources =
  require (source.n_qubits = output.n_qubits && source.n_clbits = output.n_clbits &&
           source.qregs = output.qregs && source.cregs = output.cregs &&
           source.gates = output.gates) "normalization changed declarations";
  require (Array.length sources = List.length output.ops) "normalization source map length";
  let previous = ref (-1) in
  List.iteri (fun i (o : Circuit.op) ->
    require (o.index = i) "normalization IDs must be dense and stable";
    require (sources.(i) >= !previous) "normalization reordered source operations";
    previous := sources.(i);
    require (List.exists (fun (p : Circuit.op) -> p.index = sources.(i)) source.ops)
      "normalization invented a source operation") output.ops;
  List.iter (fun (o : Circuit.op) ->
    let children = List.filter (fun (p : Circuit.op) -> sources.(p.index) = o.index) output.ops in
    require (children <> []) "normalization dropped a source operation";
    List.iter (fun (p : Circuit.op) ->
      require (p.cond = o.cond && p.src_line = o.src_line) "normalization lost source condition/line";
      require (p.name = "barrier" || List.length p.qubits <= 2) "unnormalized gate") children;
    if List.length o.qubits <= 2 || o.name = "barrier" then
      require (List.map (fun (p : Circuit.op) -> {p with index=o.index}) children = [o])
        "normalization changed an atomic operation"
    else begin
      let open Gateset_composites in
      let n = List.length o.qubits in
      require (n <= 3) "normalization matrix checker limited to three operands";
      let local q =
        let rec find k = function [] -> raise (Failed "normalization invented an operand")
          | x::xs -> if x=q then k else find (k+1) xs in find 0 o.qubits in
      let actual = List.fold_left (fun acc (p : Circuit.op) ->
        require (p.clbits = []) "normalization invented a classical write";
        let m = logical_matrix source.gates p.name p.params (List.length p.qubits) in
        let m = match List.map local p.qubits with
          | [q] -> embed1 n q m | [q;r] -> embed2 n q r m
          | _ -> raise (Failed "normalization emitted invalid arity") in
        mmul m acc) (eye (dim n)) children in
      require (phase_distance actual (logical_matrix source.gates o.name o.params n) < 1e-8)
        ("normalization changed semantics of op " ^ string_of_int o.index)
    end) source.ops

let normalize c =
  (* TSIR currently has no classical conditional execution semantics.  Refuse it
     before any placement or emission instead of silently dropping the condition. *)
  require (List.for_all (fun (o : Circuit.op) -> o.cond=None) c.Circuit.ops)
    "conditional execution is unsupported by the TSIR backend";
  let out, n, sources = Circuit.lower_with_sources c in
  check_normalization c out sources;
  let witness = `Assoc ["source_op_by_normalized_op", ints (Array.to_list sources)] in
  out,n,sources,record "circuit_normalization" ~input:(circuit_json c)
    ~output:(circuit_json out) ~witness
    ~coverage:["declarations and atomic operations unchanged";
               "stable source-operation provenance, parameters, measurements, resets and barriers";
               "expanded gates: numerical unitary equivalence up to global phase, tolerance 1e-8"]

let check_dependencies (c : Circuit.t) layers =
  require (Array.length layers = List.length c.ops) "dependency layer length";
  let last = Array.make (c.n_qubits+c.n_clbits) None in
  List.iter (fun (o : Circuit.op) ->
    require (layers.(o.index) >= 0) "negative dependency layer";
    let reads = match o.cond with None -> [] | Some (r,_) ->
      let _,off,width = List.find (fun (s,_,_) -> s=r) c.cregs in
      List.init width (fun k -> off+k) in
    let wires = List.sort_uniq compare (o.qubits @
      List.map (fun b -> c.n_qubits+b) (o.clbits @ reads)) in
    List.iter (fun w ->
      require (w >= 0 && w < Array.length last) "dependency wire outside declarations";
      (match last.(w) with None -> () | Some prev ->
        require ((if o.name="barrier" then (>=) else (>)) layers.(o.index) layers.(prev))
          (Printf.sprintf "dependency %d -> %d violated" prev o.index));
      last.(w) <- Some o.index) wires) c.ops

let dependency_record c layers =
  check_dependencies c layers;
  record "dependency_analysis" ~input:(circuit_json c)
    ~output:(`Assoc ["circuit", circuit_json c; "layers", ints (Array.to_list layers)])
    ~witness:(`Assoc ["edges", `List (List.map (fun (a,b) -> ints [a;b]) (Circuit.edges c))])
    ~coverage:["independent replay of quantum/classical wire precedence, resets and barrier fences"]

let check_placement ?rest (a : Arch.t) (c : Circuit.t) (pl : Place.t) =
  require (Array.length pl.ion = c.n_qubits) "placement does not cover every qubit";
  require (List.length (List.sort_uniq compare (Array.to_list pl.ion)) = c.n_qubits)
    "placement maps two qubits to one ion";
  require (Hashtbl.length pl.site = c.n_qubits) "placement has missing or extra ions";
  let loads = Hashtbl.create 16 in
  Array.iter (fun i ->
    let s = match Hashtbl.find_opt pl.site i with Some s -> s
      | None -> raise (Failed ("placement missing ion " ^ i)) in
    (match Arch.node a s with Some n -> require (n.kind="site") "placement is not a resting site"
      | None -> raise (Failed ("unknown placement site " ^ s)));
    (match rest with Some p -> require (p s) "placement violates resting-site restriction" | None -> ());
    let count=1+Option.value (Hashtbl.find_opt loads s) ~default:0 in
    Hashtbl.replace loads s count;
    require (count <= Arch.eff_capacity a s) ("initial placement exceeds capacity at " ^ s)) pl.ion

let placement_record ?rest a c pl =
  check_placement ?rest a c pl;
  record "initial_placement" ~input:(circuit_json c) ~output:(position_json pl.Place.site)
    ~witness:(`Assoc ["logical_to_ion", strings (Array.to_list pl.ion)])
    ~coverage:["complete bijective qubit/ion mapping"; "resting sites and effective capacities"]

type sited = Circuit.op * (string * string list) option
type native_pulse = [ `Frame of float * string | `Beam of float * float * string
                    | `Ms of float * string * string ]
type native_op = Circuit.op * string * string list * native_pulse list

let sited_json sited = `List (List.map (fun ((o : Circuit.op),site) ->
  `Assoc ["operation",op_json o; "assignment", (match site with None -> `Null
    | Some (s,ions) -> `Assoc ["site",`String s;"ions",strings ions])]) sited)

let check_interactions ?(sequential=false) a (pl : Place.t) (sited : sited list) =
  let claimed=Hashtbl.create 16 in
  let ids=List.map (fun ((o : Circuit.op),_)->o.index) sited in
  require (List.length ids=List.length (List.sort_uniq compare ids)) "duplicate interaction operation";
  List.iter (fun ((o : Circuit.op),site) -> match site with
    | None -> require (o.name="barrier") "missing execution site"
    | Some (s,ions) ->
      require (ions=List.map (Array.get pl.ion) o.qubits) "wrong ordered interaction operands";
      let n=match Arch.node a s with Some n -> n | None -> raise (Failed "unknown interaction site") in
      let spam=List.mem o.name ["measure";"reset"] in
      require (if spam then n.can_spam else n.can_gate) "site lacks operation capability";
      require (List.length ions <= Arch.eff_capacity a s) "interaction operands exceed capacity";
      if not spam && not sequential then begin
        require (not (Hashtbl.mem claimed s)) "two simultaneous gates share an execution site";
        Hashtbl.add claimed s ()
      end) sited

let route_json (plan : Route.layer_plan) =
  `List (List.map (fun (cy : Route.cycle) -> `List (List.map (fun (m : Route.move) ->
    `Assoc ["ion",`String m.ion;"from",`String m.src;"to",`String m.dst;
            "via",strings m.via]) cy.moves)) plan.cycles)

let check_transport (a : Arch.t) ~before (plan : Route.layer_plan) =
  let pos=Hashtbl.copy before in
  let segments=Hashtbl.create (List.length a.segments) in
  List.iter (fun (s : Arch.segment) -> Hashtbl.add segments s.sid s) a.segments;
  List.iter (fun (cy : Route.cycle) ->
    let moved=Hashtbl.create 16 and junctions=Hashtbl.create 16 and used=Hashtbl.create 16 in
    List.iter (fun (m : Route.move) ->
      require (not (Hashtbl.mem moved m.ion)) "transport moves an ion twice in one cycle";
      Hashtbl.add moved m.ion ();
      require (Hashtbl.find_opt pos m.ion = Some m.src) "transport starts at wrong position";
      require (m.via<>[]) "transport has no physical path";
      let at=ref m.src in
      List.iter (fun sid ->
        let s=match Hashtbl.find_opt segments sid with Some s -> s
          | None -> raise (Failed ("unknown transport segment " ^ sid)) in
        let next=if !at=s.a then s.b else if !at=s.b then s.a
          else raise (Failed "discontinuous transport path") in
        let uses=Option.value (Hashtbl.find_opt used sid) ~default:[] in
        require (List.for_all (fun (src,_) -> src= !at) uses) "opposing transport on one segment";
        let uses=(!at,next)::uses in Hashtbl.replace used sid uses;
        require (List.length uses <= s.seg_capacity) "transport exceeds segment capacity";
        at := next;
        if next<>m.dst then match Arch.node a next with
          | Some n when n.is_junction ->
            require (not (Hashtbl.mem junctions next)) "multiple junction transits in one cycle";
            Hashtbl.add junctions next ()
          | _ -> ()) m.via;
      require (!at=m.dst) "transport path ends at wrong destination";
      (match Arch.node a m.dst with Some n -> require (n.kind="site") "transport ends outside a resting site"
        | None -> raise (Failed "transport ends at unknown site"))) cy.moves;
    List.iter (fun (m : Route.move) -> Hashtbl.replace pos m.ion m.dst) cy.moves;
    let loads=Hashtbl.create 16 in
    Hashtbl.iter (fun _ s -> Hashtbl.replace loads s (1+Option.value (Hashtbl.find_opt loads s) ~default:0)) pos;
    Hashtbl.iter (fun s n -> require (n <= Arch.eff_capacity a s) ("transport exceeds site capacity at "^s)) loads)
    plan.cycles;
  List.iter (fun (i,s) -> require (Hashtbl.find_opt pos i=Some s) "transport arrival claim disagrees with replay") plan.arrivals;
  pos

let check_rotation (a : Arch.t) ~before ~loop ~delta =
  let l=match List.find_opt (fun (l:Arch.loop)->l.lid=loop) a.loops with
    | Some l -> l | None -> raise (Failed "rotation names an unknown loop") in
  require l.closed "rotation requires a closed loop";
  let nodes=Array.of_list l.nodes in
  let n=Array.length nodes in
  require (n>0) "rotation loop is empty";
  let index=Hashtbl.create n in
  Array.iteri (fun k s->Hashtbl.add index s k) nodes;
  let pos=ref (Hashtbl.copy before) in
  let direction=if delta>=0 then 1 else -1 in
  for _=1 to abs delta do
    let moves=Hashtbl.fold (fun ion src acc -> match Hashtbl.find_opt index src with
      | None -> acc
      | Some k ->
        let dst=nodes.((k+direction+n) mod n) in
        let segment=match List.find_opt (fun (s:Arch.segment)->
          (s.a=src && s.b=dst)||(s.b=src && s.a=dst)) a.segments with
          | Some s->s | None->raise (Failed "rotation loop skips a physical segment") in
        Route.{ion;src;dst;via=[segment.sid]}::acc) !pos [] in
    pos := check_transport a ~before:!pos Route.{cycles=[{moves}];arrivals=[];slots=1}
  done;
  !pos

let pulse_json = function
  | `Frame (l,i) -> `Assoc ["gate",`String "VZ";"ions",strings [i];"params",`List [`Float l]]
  | `Beam (t,p,i) -> `Assoc ["gate",`String "R";"ions",strings [i];"params",`List [`Float t;`Float p]]
  | `Ms (t,i,j) -> `Assoc ["gate",`String "MS";"ions",strings [i;j];"params",`List [`Float t]]

let native_json (native : native_op list) = `List (List.map (fun (o,s,ions,ps) ->
  `Assoc ["operation",op_json o;"site",`String s;"ions",strings ions;
          "pulses",`List (List.map pulse_json ps)]) native)

let check_native (c : Circuit.t) (sited : sited list) (native : native_op list) =
  let expected=List.filter_map (fun (o,site) -> Option.map (fun (s,ions)->o,s,ions) site) sited in
  require (List.map (fun (o,s,ions,_) -> o,s,ions) native = expected)
    "native lowering dropped/reordered operations or changed operands/sites";
  List.iter (fun ((o : Circuit.op),_,ions,ps) ->
    if List.mem o.name ["measure";"reset";"barrier"] then
      require (ps=[]) "native lowering changed a non-unitary operation"
    else begin
      let local i=let rec find k=function [] -> raise (Failed "native pulse names a foreign ion")
        | x::xs -> if x=i then k else find (k+1) xs in find 0 ions in
      let pulses=List.map (function
        | `Frame (lam,i) -> Gateset.Frame {lam;qubit=local i}
        | `Beam (theta,phi,i) -> Gateset.Beam {theta;phi;qubit=local i}
        | `Ms (theta,i,j) -> Gateset.Ms {theta;a=local i;b=local j}) ps in
      let n=List.length ions in
      let actual=Gateset_composites.replay n Gateset.{pulses;phase=0.0} in
      let target=logical_matrix c.gates o.name o.params n in
      require (phase_distance actual target < 1e-8)
        (Printf.sprintf "native pulse semantics differ for operation %d (%s)" o.index o.name)
    end) native

let check_packing (native : native_op list) (instructions : Tsir.instr list) =
  (* Compare per-ion ordered atomic effects, independently flattening both sides.
     Each packed operand is matched to its own parameter vector, so a swapped angle,
     missing frame, reversed MS pair or reordered same-ion pulse cannot pass. *)
  let expected=Hashtbl.create 32 and actual=Hashtbl.create 32 in
  let owners=Hashtbl.create 32 and consumed=Hashtbl.create 32 in
  let append tbl i x=Hashtbl.replace tbl i (x::Option.value (Hashtbl.find_opt tbl i) ~default:[]) in
  let add tbl gate ions params=
    let x=gate,ions,params in List.iter (fun i -> append tbl i x) ions in
  List.iter (fun ((o:Circuit.op),_,ions,ps) ->
    let expect gate ions params = add expected gate ions params;
      List.iter (fun i->append owners i o.index) ions in
    if List.mem o.name ["measure";"reset"] then List.iter (fun i -> expect o.name [i] []) ions;
    List.iter (function
    | `Frame (l,i)->expect "VZ" [i] [l]
    | `Beam (t,p,i)->expect "R" [i] [t;p]
    | `Ms (t,i,j)->expect "MS" [i;j] [t]) ps) native;
  Hashtbl.iter (fun i os->Hashtbl.replace owners i (List.rev os)) owners;
  List.iter (fun (i : Tsir.instr) ->
    require (List.mem i.ityp ["gate";"measure";"reset"]) "packing emitted an unrelated command";
    let groups=if i.gate=Some "MS" then List.map (fun (x,y)->[x;y]) i.pairs else List.map (fun x->[x]) i.ions in
    let params=if i.ityp="gate" then i.params else List.map (fun _->[]) groups in
    require (List.length groups=List.length params) "packing parameter/operand arity mismatch";
    let flat=List.concat groups in
    require (List.length flat=List.length (List.sort_uniq compare flat)) "packing double-addresses an ion";
    let gate=if i.ityp="gate" then Option.value i.gate ~default:"" else i.ityp in
    let operation_ids=List.map (fun ions ->
      let ids=List.map (fun ion ->
        let k=Option.value (Hashtbl.find_opt consumed ion) ~default:0 in
        let os=Option.value (Hashtbl.find_opt owners ion) ~default:[] in
        require (k<List.length os) "packing emits an extra atomic effect";
        Hashtbl.replace consumed ion (k+1);List.nth os k) ions in
      require (ids<>[] && List.for_all ((=) (List.hd ids)) ids)
        "packed multi-ion pulse combines different logical operations";
      List.hd ids) groups in
    let claimed=match List.assoc_opt "op" i.meta with Some (`List xs)->
      List.map (function `Int n->n | _->raise (Failed "invalid packed operation provenance")) xs
      | _->raise (Failed "packed command lacks operation provenance") in
    require (List.sort_uniq compare claimed=List.sort_uniq compare operation_ids)
      "packed operation provenance does not match its actual effects";
    List.iter2 (fun ions params -> add actual gate ions params) groups params) instructions;
  require (Hashtbl.length actual=Hashtbl.length expected) "packing changes participating ions";
  Hashtbl.iter (fun i ps -> require (Hashtbl.find_opt actual i=Some ps)
    ("packing changed pulse order/parameters for "^i)) expected

let manifest ~engine ~circuit_path ~architecture_path ~program_path ~accepted stages =
  `Assoc ["schema",`String "qccd.compiler.stages.v1";"engine",`String engine;
          "accepted", `Bool accepted;
          "bindings",`Assoc ["circuit_fnv1a",`String (Cert.hash_file circuit_path);
                              "architecture_fnv1a",`String (Cert.hash_file architecture_path);
                              "output_tsir_fnv1a",`String (Cert.hash_file program_path);
                              "rules_fnv1a",`String (match !(Rules.source) with None -> "default" | Some p -> Cert.hash_file p);
                              "rules",`String (Rules.describe ())];
          "stages",`List stages]

let save path json =
  let oc=open_out_bin path in
  Fun.protect ~finally:(fun ()->close_out oc) (fun ()->
    output_string oc (Yojson.Safe.to_string json); output_char oc '\n')

let specialized ~engine (c : Circuit.t) (a : Arch.t) (p : Tsir.t) (cert : Cert.t) =
  let c,_,_,normalization=normalize c in
  let pl=Place.{ion=Array.init c.n_qubits (fun q -> List.assoc q cert.map_);
               site=Hashtbl.create c.n_qubits;notes=[]} in
  List.iter (fun (i,s)->Hashtbl.add pl.site i s) cert.init;
  let placement=placement_record a c pl in
  normalization :: List.map (fun (id,_) ->
    if id="initial_placement" then placement else
      record ~status:"unsupported" id ~input:(circuit_json c) ~output:(Tsir.to_json p)
        ~witness:`Null ~coverage:[]
        ~diagnostics:[engine^" engine has not yet been decomposed at this boundary; end-to-end checks remain required"])
    (List.tl names)

(* Saved evidence is re-checkable without running placement or routing again. *)
let field k = function `Assoc kv -> (match List.assoc_opt k kv with Some x -> x
  | None -> raise (Failed ("missing stage field "^k))) | _ -> raise (Failed "expected JSON object")
let list = function `List xs -> xs | _ -> raise (Failed "expected JSON list")
let string = function `String s -> s | _ -> raise (Failed "expected JSON string")
let int = function `Int n -> n | _ -> raise (Failed "expected JSON integer")
let number = function `Int n -> float_of_int n | `Float x -> x | _ -> raise (Failed "expected JSON number")
let pos_of_json = function
  | `Assoc xs ->
    let p=Hashtbl.create (List.length xs) in
    List.iter (fun (i,s)->require (not (Hashtbl.mem p i)) "duplicate position key";
      Hashtbl.add p i (string s)) xs; p
  | _ -> raise (Failed "expected ion-position object")

let op_of_json j =
  let opt k = match j with `Assoc xs -> List.assoc_opt k xs | _ -> None in
  let cond=match opt "condition",opt "cond" with
    | Some (`List [r;v]),_ | _,Some (`List [r;v]) -> Some (string r,int v)
    | _ -> None in
  Circuit.{index=int (field "i" j);name=string (field "name" j);
    qubits=List.map int (list (field "qubits" j));clbits=List.map int (list (field "clbits" j));
    params=List.map number (list (field "params" j));cond;
    src_line=(match opt "source_line" with Some n -> int n | None -> 0)}

let sited_of_json j = List.map (fun x ->
  let o=op_of_json (field "operation" x) in
  let site=match field "assignment" x with `Null -> None | a ->
    Some (string (field "site" a),List.map string (list (field "ions" a))) in
  o,site) (list j)

let native_of_json j = List.map (fun x ->
  let ps=List.map (fun p ->
    let ions=List.map string (list (field "ions" p)) and params=List.map number (list (field "params" p)) in
    match string (field "gate" p),ions,params with
    | "VZ",[i],[l] -> `Frame (l,i)
    | "R",[i],[t;ph] -> `Beam (t,ph,i)
    | "MS",[i;j],[t] -> `Ms (t,i,j)
    | _ -> raise (Failed "invalid native pulse snapshot")) (list (field "pulses" x)) in
  op_of_json (field "operation" x),string (field "site" x),
  List.map string (list (field "ions" x)),ps) (list j)

let check_manifest ~circuit_path ~architecture_path ~program_path doc =
  require (field "schema" doc=`String "qccd.compiler.stages.v1") "unknown stage schema";
  require (field "accepted" doc=`Bool true) "stage manifest is an unaccepted candidate";
  let bindings=field "bindings" doc in
  List.iter (fun (key,path)->require (field key bindings=`String (Cert.hash_file path))
    ("stage binding mismatch: "^key))
    ["circuit_fnv1a",circuit_path;"architecture_fnv1a",architecture_path;"output_tsir_fnv1a",program_path];
  require (field "rules_fnv1a" bindings=`String (match !(Rules.source) with None -> "default" | Some p->Cert.hash_file p))
    "stage rule binding mismatch";
  let source=Circuit.build ~name:(Filename.remove_extension (Filename.basename circuit_path)) (Qasm.parse_file circuit_path) in
  let a=Arch.load architecture_path and program=Tsir.load program_path in
  require (Tsir.validate program=[]) "serialized executable has invalid TSIR structure";
  (* Replay the executable independently of the saved stage order.  A manifest
     cannot move a transport before a gate in its evidence while leaving the actual
     transport after that gate in TSIR.  Store only addressed-ion positions. *)
  let execution_positions=Hashtbl.create 128 and actual_pos=ref (Hashtbl.create 1) in
  List.iter (fun (i:Tsir.instr) ->
    match i.ityp with
    | "init" -> actual_pos:=pos_of_json (`Assoc (List.map (fun (q,s)->q,`String s) i.placement))
    | "simd" ->
      (match i.template with
      | Some (`Assoc xs) when List.assoc_opt "kind" xs=Some (`String "loop_shift") ->
        actual_pos:=check_rotation a ~before:!actual_pos ~loop:(string (List.assoc "loop" xs))
          ~delta:(int (List.assoc "delta" xs))
      | _ ->
        let moves=List.map (fun (m:Tsir.participant)->Route.{ion=m.ion;src=m.src;dst=m.dst;via=m.via}) i.participants in
        actual_pos:=check_transport a ~before:!actual_pos Route.{cycles=[{moves}];arrivals=[];slots=1})
    | "gate" | "measure" | "reset" ->
      let ions=i.ions @ List.concat_map (fun (x,y)->[x;y]) i.pairs in
      Hashtbl.replace execution_positions i.id (List.map (fun ion ->
        ion,(match Hashtbl.find_opt !actual_pos ion with Some s->s
          | None->raise (Failed "executable addresses an unknown ion"))) ions)
    | _ -> ()) program.instructions;
  let c=ref source and pl=ref None and positions=ref (Hashtbl.create 1) in
  let current_sited=ref None and current_native=ref None and packed=ref [] in
  let checked_motions=ref [] in
  let realized=ref [] in
  let source_ids=ref [||] in
  let canonical_moves moves =
    `Assoc ["kind",`String "moves"; "participants",`List (List.sort compare moves)] in
  let canonical_rotation loop delta =
    `Assoc ["kind",`String "rotation";"loop",`String loop;"delta",`Int delta] in
  let seen=Hashtbl.create 8 in
  List.iter (fun stage ->
    let id=string (field "id" stage) in
    require (field "status" (field "check" stage)=`String "passed")
      ("independent stage coverage unavailable: "^id);
    let input=field "input" stage and output=field "output" stage and witness=field "witness" stage in
    (match id with
    | "circuit_normalization" ->
      require (not (Hashtbl.mem seen id)) "duplicate normalization stage";
      require (input=circuit_json source) "normalization input differs from bound QASM";
      let sources=Array.of_list (List.map int (list (field "source_op_by_normalized_op" witness))) in
      source_ids:=sources;
      let ops=List.map (fun j -> let o=op_of_json j in
        require (o.index>=0 && o.index<Array.length sources) "invalid normalized source index";
        let parent=List.find (fun (p:Circuit.op)->p.index=sources.(o.index)) source.ops in
        {o with src_line=parent.src_line}) (list (field "ops" output)) in
      let normalized={source with ops} in
      require (circuit_json normalized=output) "normalization snapshot declarations/annotations differ";
      check_normalization source normalized sources; c:=normalized
    | "dependency_analysis" ->
      require (Hashtbl.mem seen "circuit_normalization") "dependency analysis precedes normalization";
      require (input=circuit_json !c && field "circuit" output=input) "dependency input/output program differs";
      check_dependencies !c (Array.of_list (List.map int (list (field "layers" output))))
    | "initial_placement" ->
      require (Hashtbl.mem seen "dependency_analysis") "placement precedes dependency analysis";
      require (input=circuit_json !c) "placement input circuit differs";
      let placement=Place.{ion=Array.of_list (List.map string (list (field "logical_to_ion" witness)));
                          site=pos_of_json output;notes=[]} in
      check_placement a !c placement;
      let init=List.filter (fun (i:Tsir.instr)->i.ityp="init") program.instructions in
      require (List.length init=1) "executable needs exactly one initial placement";
      require (List.sort compare (List.hd init).placement =
        List.sort compare (Hashtbl.fold (fun i s acc -> (i,s)::acc) placement.site []))
        "checked placement differs from serialized executable";
      pl:=Some placement;positions:=Hashtbl.copy placement.site
    | "interaction_placement" ->
      require (field "positions" input=position_json !positions) "interaction state does not follow earlier stages";
      let pending=List.map op_of_json (list (field "operations" input)) in
      List.iter (fun o -> require (List.mem o (!c).ops) "interaction input has invented source operation") pending;
      let sited=sited_of_json output in
      let sequential=match witness with `Assoc xs -> List.assoc_opt "sequential" xs=Some (`Bool true) | _->false in
      check_interactions ~sequential a (Option.get !pl) sited;
      List.iter (fun (o,_) -> require (List.mem o pending) "interaction output has invented operation") sited;
      current_sited:=Some output;current_native:=None
    | "transport_synthesis" ->
      require (input=position_json !positions) "transport state does not follow earlier stages";
      let arrivals=Hashtbl.fold (fun i s acc -> (i,s)::acc) (pos_of_json (field "arrivals" witness)) [] in
      let rotation=match output with `Assoc xs -> List.assoc_opt "rotation" xs | _->None in
      let after=match rotation with
        | Some r ->
          let loop=string (field "loop" r) and delta=int (field "delta" r) in
          let after=check_rotation a ~before:!positions ~loop ~delta in
          if delta<>0 then checked_motions:=!checked_motions @ [canonical_rotation loop delta];
          List.iter (fun (i,s)->require (Hashtbl.find_opt after i=Some s) "rotation arrival disagrees with replay") arrivals;
          after
        | None ->
          let cycles=List.map (fun cy -> Route.{moves=List.map (fun m ->
            {ion=string (field "ion" m);src=string (field "from" m);dst=string (field "to" m);
             via=List.map string (list (field "via" m))}) (list cy)}) (list (field "cycles" output)) in
          List.iter (fun (cy:Route.cycle) -> if cy.moves<>[] then
            checked_motions:=!checked_motions @ [canonical_moves (List.map (fun (m:Route.move)->
              Tsir.participant_to_json Tsir.{ion=m.ion;src=m.src;dst=m.dst;via=m.via}) cy.moves)]) cycles;
          check_transport a ~before:!positions Route.{cycles;arrivals;slots=List.length cycles} in
      require (position_json after=field "final_positions" output) "transport final snapshot differs from replay";
      positions:=after
    | "native_gate_lowering" ->
      require (!current_sited=Some input) "native input does not match checked interaction assignment";
      let sited=sited_of_json input in
      List.iter (function _,None -> () | _,Some (s,ions) ->
        List.iter (fun i -> require (Hashtbl.find_opt !positions i=Some s)
          "native gate operand has not arrived") ions) sited;
      let native=native_of_json output in check_native !c sited native;current_native:=Some output
      ; realized:=List.map (fun ((o:Circuit.op),_,_,_)->o.index) native @ !realized
    | "broadcast_packing" ->
      require (!current_native=Some input) "packing input does not match checked native program";
      let instructions=List.map Tsir.instr_of_json (list output) in
      let native=native_of_json input in
      check_packing native instructions;
      let sites=Hashtbl.create 16 in
      List.iter (fun (_,s,ions,_)->List.iter (fun i->
        (match Hashtbl.find_opt sites i with None -> () | Some s' ->
          require (s=s') "packing changes an ion's site inside one native batch");
        Hashtbl.replace sites i s) ions) native;
      List.iter (fun (i:Tsir.instr)->
        let at=match Hashtbl.find_opt execution_positions i.id with Some at->at
          | None->raise (Failed "packed command missing from executable") in
        List.iter (fun (ion,s)->require (Hashtbl.find_opt sites ion=Some s)
          "executable gate/SPAM site differs from checked native assignment") at;
        let ids=match List.assoc_opt "op" i.meta with Some (`List xs)->List.map int xs
          | _->raise (Failed "missing normalized operation provenance") in
        let source=match List.assoc_opt "source_op" i.meta with Some (`List xs)->List.map int xs
          | _->raise (Failed "missing source operation provenance") in
        require (List.sort_uniq compare source=List.sort_uniq compare
          (List.map (fun k->require (k>=0 && k<Array.length !source_ids) "invalid normalized operation ID";
            (!source_ids).(k)) ids)) "source operation provenance does not match normalization") instructions;
      packed:=!packed @ instructions
    | _ -> raise (Failed ("unknown stage "^id)));
    Hashtbl.replace seen id ()) (list (field "stages" doc));
  List.iter (fun id -> require (Hashtbl.mem seen id) ("missing stage "^id))
    (List.map fst names);
  let expected_ops=List.filter_map (fun (o:Circuit.op)->if o.name="barrier" then None else Some o.index) (!c).ops in
  require (List.sort compare !realized=List.sort compare expected_ops)
    "checked native stages omit or duplicate source operations";
  require (List.map Tsir.instr_to_json !packed =
    List.map Tsir.instr_to_json (List.filter (fun (i:Tsir.instr)->List.mem i.ityp ["gate";"measure";"reset"]) program.instructions))
    "packed stage outputs differ from serialized executable gates/readout/reset";
  let actual_motions=List.filter_map (fun (i:Tsir.instr)->
    if i.ityp<>"simd" then None else match i.template with
    | Some (`Assoc xs) when List.assoc_opt "kind" xs=Some (`String "loop_shift") ->
      Some (canonical_rotation (string (List.assoc "loop" xs)) (int (List.assoc "delta" xs)))
    | _ when i.participants<>[] -> Some (canonical_moves (List.map Tsir.participant_to_json i.participants))
    | _ -> raise (Failed "unsupported executable transport command")) program.instructions in
  require (!checked_motions=actual_motions) "checked transport differs from serialized executable motion"
  ;
  (* The checked dependency graph constrains the executable, not merely the saved
     layer annotation.  Compare complete operation intervals on every source wire. *)
  let intervals=Hashtbl.create 64 in
  List.iteri (fun k (i:Tsir.instr)->if List.mem i.ityp ["gate";"measure";"reset"] then
    match List.assoc_opt "op" i.meta with Some (`List ids)->
      List.iter (fun v->let id=int v in
        let first=match Hashtbl.find_opt intervals id with Some (f,_)->f | None->k in
        Hashtbl.replace intervals id (first,k)) ids
    | _->raise (Failed "executable operation lacks provenance")) program.instructions;
  let last=Array.make ((!c).n_qubits+(!c).n_clbits) (-1) in
  List.iter (fun (o:Circuit.op)->
    let wires=Circuit.wires_of !c o |> List.sort_uniq compare in
    if o.name="barrier" then begin
      let fence=List.fold_left (fun n w->max n last.(w)) (-1) wires in
      List.iter (fun w->last.(w)<-fence) wires
    end else begin
      let first,final=match Hashtbl.find_opt intervals o.index with Some x->x
        | None->raise (Failed "executable omits a source operation") in
      List.iter (fun w->require (first>last.(w)) "executable violates source dependency order";
        last.(w)<-final) wires
    end) (!c).ops
