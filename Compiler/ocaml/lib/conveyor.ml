(* Rigid rotation as a routing primitive.

   The general router moves ions one at a time, and on a closed loop that runs out at
   about 46% occupancy: every hop needs a free slot at its destination, and past half full
   a crossing path is a traffic jam nothing can clear.  `qccd/compile/pipeline.py` does not
   have that problem, and the reason is not that it is cleverer -- it is that it moves the
   loop {b rigidly}.  One `loop_shift` template advances every ion at once, so occupancy is
   invariant and no path is ever blocked.

   That is `docs/PLAN.md` §1's thesis, and this module is it as a compiler pass.

   {1 The shape of a conveyor device}

   A loop of rail slots, with {b docks} hanging off it: a gate-capable site reachable from
   one rail slot by one spur segment.  `ring144_24v` is 144 rail slots and 24 docks; the
   racetrack and the cyclones are loops with no docks at all.

   {1 The movement vocabulary}

     rotate   one instruction, one template, every ion on the loop advances by delta
     dock     one ion leaves its rail slot for the dock beside it
     undock   and comes back

   The invariant that makes this sound: {b the loop never rotates while an ion is docked.}
   A docked ion is off the loop and does not move with it, so rotating underneath it would
   silently change which rail slot it returns to.  Every rotation therefore happens with
   the docks empty, which is exactly the dock/contact/undock structure of the shipped deck
   schedule.

   {1 What it cannot do}

   Rigid rotation preserves the cyclic ORDER of the ions on the loop.  Two ions riding the
   loop can never meet: no sequence of rotations changes their separation.  So this pass
   applies when the circuit's interaction graph is bipartite with one side small enough to
   sit at the docks -- which is precisely the shape of a syndrome-extraction round, and why
   the shipped ring was built for one.  Anything else falls back to the general router. *)

type dock = {
  site : string;      (* the gate-capable trap off the loop *)
  rail : string;      (* the loop slot it hangs from *)
  rail_idx : int;     (* that slot's index in the loop's cyclic order *)
  spur : string;      (* the segment between them *)
}

type t = {
  loop : string;
  nodes : string array;
  index : (string, int) Hashtbl.t;
  docks : dock array;
  n : int;
  cw : string;
  ccw : string;
  dock_cls : string;
  undock_cls : string;
  rail_segs : string list;
}

(* ------------------------------------------------------------------ detection *)

let class_named (a : Arch.t) ~(orbit : string) ~(delta : int option)
    ~(direction : string option) ~(fallback : string) : string =
  let ok (c : Arch.simd_class) =
    (match c.orbit with Some o -> o = orbit | None -> false)
    && (match (delta, c.delta) with None, _ -> true | Some d, Some e -> d = e | _ -> false)
    && match (direction, c.direction) with
       | None, _ -> true
       | Some d, Some e -> d = e
       | _ -> false
  in
  match List.find_opt ok a.classes with Some c -> c.cid | None -> fallback

(* Does this device rotate, and where are its docks? *)
let detect (a : Arch.t) : t option =
  match List.find_opt (fun (l : Arch.loop) -> l.closed && List.length l.nodes >= 4) a.loops with
  | None -> None
  | Some l ->
    let nodes = Array.of_list l.nodes in
    let n = Array.length nodes in
    let index = Hashtbl.create n in
    Array.iteri (fun i s -> Hashtbl.replace index s i) nodes;
    (* a dock: gate-capable, off the loop, one segment from a loop slot *)
    let docks =
      List.filter_map
        (fun (sg : Arch.segment) ->
          let pick rail other =
            match (Hashtbl.find_opt index rail, Hashtbl.find_opt index other) with
            | Some ri, None -> (
              match Arch.node a other with
              | Some nd when nd.can_gate && nd.kind = "site" ->
                Some { site = other; rail; rail_idx = ri; spur = sg.sid }
              | _ -> None)
            | _ -> None
          in
          match pick sg.a sg.b with Some d -> Some d | None -> pick sg.b sg.a)
        a.segments
      |> List.sort_uniq compare
      |> Array.of_list
    in
    if Array.length docks = 0 then None
    else
      Some
        {
          loop = l.lid;
          nodes;
          index;
          docks;
          n;
          cw = class_named a ~orbit:l.lid ~delta:(Some 1) ~direction:None ~fallback:"shuttle";
          ccw =
            class_named a ~orbit:l.lid ~delta:(Some (-1)) ~direction:None ~fallback:"shuttle";
          dock_cls =
            class_named a ~orbit:"spurs" ~delta:None ~direction:(Some "inward")
              ~fallback:"shuttle";
          undock_cls =
            class_named a ~orbit:"spurs" ~delta:None ~direction:(Some "outward")
              ~fallback:"shuttle";
          rail_segs =
            List.filter_map
              (fun (sg : Arch.segment) ->
                if sg.loop = Some l.lid then Some sg.sid else None)
              a.segments;
        }

(* ------------------------------------------------------------------ geometry *)

(* Fewest signed hops from loop offset `a` to offset `b`.  On a closed loop the short way
   round is usually backwards, and a router that only ever rotates one direction pays up
   to n-1 cycles for a move that costs 1. *)
let shortest (t : t) (from_ : int) (to_ : int) : int =
  let d = ((to_ - from_) mod t.n + t.n) mod t.n in
  if d <= t.n - d then d else d - t.n

let site_at (t : t) (slot : int) : string = t.nodes.((slot mod t.n + t.n) mod t.n)

let dock_for (t : t) (site : string) : dock option =
  Array.to_list t.docks |> List.find_opt (fun d -> d.site = site)

let describe (t : t) =
  Printf.sprintf "conveyor: loop %s of %d slots, %d docks, classes %s/%s + %s/%s" t.loop
    t.n (Array.length t.docks) t.cw t.ccw t.dock_cls t.undock_cls
