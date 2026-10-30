open Qccdc

let checks=ref 0
let accepts name f = f (); incr checks; Printf.printf "PASS %s\n" name
let rejects name f =
  match f () with
  | () -> failwith ("incorrectly accepted: "^name)
  | exception Stages.Failed _ -> incr checks; Printf.printf "REJECT %s\n" name

let circuit body = Circuit.build (Qasm.parse ("OPENQASM 2.0; include \"qelib1.inc\"; qreg q[3]; creg c[3]; "^body))
let positions xs = let p=Hashtbl.create 8 in List.iter (fun (i,s)->Hashtbl.add p i s) xs;p
let node id capacity can_gate = Arch.{id;pos=(0.,0.);kind="site";capacity;zone_type="trap";
  labels=[];degree=2;is_junction=false;can_gate;can_spam=true;can_cool=true}
let arch =
  let nodes=Hashtbl.create 3 in
  List.iter (fun n->Hashtbl.add nodes n.Arch.id n) [node "A" 2 true;node "B" 2 true;node "C" 2 false];
  let seg sid a b=Arch.{sid;a;b;seg_capacity=2;loop=Some "L";corner_endpoints=0} in
  Arch.{name="fixture";sha256="";nodes;node_order=["A";"B";"C"];
    segments=[seg "AB" "A" "B";seg "BC" "B" "C";seg "CA" "C" "A"];
    loops=[{lid="L";nodes=["A";"B";"C"];closed=true;corners=[]}];
    classes=[];max_classes_per_cycle=1;wiring="direct";grouping="direct";switch_per_site=true;raw=`Assoc []}

let () =
  let source=circuit "ccx q[0],q[1],q[2];" in
  let normalized,_,sources,_=Stages.normalize source in
  accepts "normalization unitary equivalence" (fun ()->Stages.check_normalization source normalized sources);
  let wrong={normalized with ops=List.map (fun (o:Circuit.op)->if o.index=0 then {o with name="x"} else o) normalized.ops} in
  rejects "normalization changes a Toffoli decomposition" (fun ()->Stages.check_normalization source wrong sources);
  let wrong_sources=Array.copy sources in wrong_sources.(0)<-99;
  rejects "normalization invented source identity" (fun ()->Stages.check_normalization source normalized wrong_sources);
  rejects "conditional execution silently dropped" (fun ()->ignore (Stages.normalize (circuit "measure q[0] -> c[0]; if(c==1) x q[1];")));
  accepts "saved stage angles retain full floating-point precision" (fun ()->
    let c=circuit "rz(1.2345678901234567) q[0];" in
    let json=Stages.circuit_json c |> Yojson.Safe.to_string |> Yojson.Safe.from_string in
    let saved=Stages.field "ops" json |> Stages.list |> List.hd |> Stages.op_of_json in
    Stages.require (saved=(List.hd c.ops)) "stage serialization rounded a source parameter");
  let c=circuit "h q[0]; barrier q; cx q[0],q[1]; measure q[1] -> c[1]; reset q[1];" in
  let layers=Compile.layer_of c in
  accepts "dependency replay including barriers and resets" (fun ()->Stages.check_dependencies c layers);
  let bad=Array.copy layers in bad.(2)<-0;
  rejects "dependency broken across barrier" (fun ()->Stages.check_dependencies c bad);
  let pl=Place.{ion=[|"q0";"q1";"q2"|];site=positions ["q0","A";"q1","A";"q2","B"];notes=[]} in
  accepts "valid initial placement" (fun ()->Stages.check_placement arch c pl);
  rejects "overfull initial placement" (fun ()->Stages.check_placement arch c {pl with site=positions ["q0","A";"q1","A";"q2","A"]});
  rejects "aliasing logical ions" (fun ()->Stages.check_placement arch c {pl with ion=[|"q0";"q0";"q2"|]});
  let gate=List.hd c.ops in
  let sited=[gate,Some ("A",["q0"])] in
  accepts "valid interaction assignment" (fun ()->Stages.check_interactions arch pl sited);
  rejects "incapable interaction site" (fun ()->Stages.check_interactions arch pl [gate,Some ("C",["q0"])]);
  rejects "wrong interaction operands" (fun ()->Stages.check_interactions arch pl [gate,Some ("A",["q1"])]);
  let before=positions ["q0","A";"q1","B";"q2","C"] in
  let move=Route.{ion="q0";src="A";dst="B";via=["AB"]} in
  let plan=Route.{cycles=[{moves=[move]}];arrivals=["q0","B"];slots=1} in
  accepts "valid transport replay" (fun ()->ignore (Stages.check_transport arch ~before plan));
  rejects "transport shortcut" (fun ()->ignore (Stages.check_transport arch ~before {plan with cycles=[{moves=[{move with dst="C"}]}]}));
  rejects "transport wrong source" (fun ()->ignore (Stages.check_transport arch ~before {plan with cycles=[{moves=[{move with src="B";dst="A"}]}]}));
  rejects "transport double participant" (fun ()->ignore (Stages.check_transport arch ~before {plan with cycles=[{moves=[move;move]}]}));
  accepts "collective rotation includes every spectator" (fun ()->
    let after=Stages.check_rotation arch ~before ~loop:"L" ~delta:1 in
    Stages.require (Hashtbl.find after "q0"="B" && Hashtbl.find after "q1"="C" && Hashtbl.find after "q2"="A") "wrong collective state");
  rejects "rotation on unknown loop" (fun ()->ignore (Stages.check_rotation arch ~before ~loop:"other" ~delta:1));
  let dc=Gateset_composites.decompose_op "h" [] [0] in
  let ps=List.map (function
    | Gateset.Frame {lam;_}->`Frame (lam,"q0")
    | Beam {theta;phi;_}->`Beam (theta,phi,"q0")
    | Ms _->assert false) dc.pulses in
  let native=[gate,"A",["q0"],ps] in
  accepts "native pulse numerical semantics" (fun ()->Stages.check_native c sited native);
  rejects "native pulse lost virtual-Z frame" (fun ()->Stages.check_native c sited [gate,"A",["q0"],List.filter (function `Frame _->false|_->true) ps]);
  let instr id gate params = Tsir.instr_of_json (`Assoc ["type",`String "gate";"id",`Int id;
    "meta",`Assoc ["op",`List [`Int 0]];
    "gate",`String gate;"ions",`List [`String "q0"];"params",`List [`List (List.map (fun x->`Float x) params)]]) in
  let packed=List.mapi (fun id ->function `Frame (l,_)->instr id "VZ" [l]
    | `Beam (t,p,_)->instr id "R" [t;p] | `Ms _->assert false) ps in
  accepts "packing preserves ordered pulses" (fun ()->Stages.check_packing native packed);
  rejects "packing reordered a frame" (fun ()->Stages.check_packing native (List.rev packed));
  rejects "packing changed an angle" (fun ()->Stages.check_packing native
    (List.mapi (fun k (i:Tsir.instr)->if k=0 then {i with params=[[0.;0.]]} else i) packed));
  rejects "packing forged source-operation provenance" (fun ()->Stages.check_packing native
    (List.map (fun (i:Tsir.instr)->{i with meta=["op",`List [`Int 99]]}) packed));
  Printf.printf "%d stage contract checks passed\n" !checks
