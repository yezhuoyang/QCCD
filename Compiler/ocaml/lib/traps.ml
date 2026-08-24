(* The trap graph: what a single machine cycle can actually move an ion between.

   The expanded architecture is a graph of *nodes*, and only some of them are traps.  On
   a grid every trap is separated from its neighbour by a junction; on the shipped ring a
   dock sits on a spur behind one.  An ion never rests on a junction -- R2 allows at most
   one ion on a degree-≥3 node at any instant, and the movement convention the platform
   already uses is that "a move goes trap to trap and lists the segments it crosses in
   `via`; it never stops on a junction" (`qccd/compile/programs.py`).

   So the router does not plan on the node graph.  It plans on the graph whose vertices
   are trap sites and whose edges are one-cycle hops, each carrying the `via` list the
   move will need.  Building that graph here, once, is what lets the router stay simple
   and what keeps the `via` lists correct by construction rather than by care. *)

type hop = { dst : string; via : string list; junctions : string list }

type t = {
  sites : string list;                       (* every trap, in document order *)
  hops : (string, hop list) Hashtbl.t;       (* trap -> the traps one cycle away *)
  index : (string, int) Hashtbl.t;
}

(* ------------------------------------------------------------------ building *)

(* A hop leaves a trap, crosses zero or more NON-trap nodes, and lands on a trap.
   Searching for those paths is a bounded BFS from each trap through non-trap nodes only:
   bounded because a chain of junctions with no trap between them is not a thing any
   shipped device has, and an unbounded search would happily walk the whole device. *)
let build ?(max_transit = 3) (a : Arch.t) : t =
  let adj = Arch.adjacency a in
  let is_site id =
    match Arch.node a id with Some n -> n.kind = "site" | None -> false
  in
  let sites = List.filter is_site a.node_order in
  let hops = Hashtbl.create (List.length sites) in
  List.iter
    (fun src ->
      let found = Hashtbl.create 8 in
      (* frontier entries: node, segments so far (reversed), non-trap nodes crossed *)
      let rec walk node via transit depth =
        if depth > max_transit then ()
        else
          List.iter
            (fun ((nbr : string), (seg : Arch.segment)) ->
              if List.mem seg.sid via then ()  (* never re-cross a segment *)
              else
                let via' = via @ [ seg.sid ] in
                if is_site nbr then begin
                  if nbr <> src then
                    (* keep the shortest via for a given destination: two routes to the
                       same neighbour would otherwise both be offered and the router
                       would pick arbitrarily *)
                    match Hashtbl.find_opt found nbr with
                    | Some (h : hop) when List.length h.via <= List.length via' -> ()
                    | _ ->
                      Hashtbl.replace found nbr
                        { dst = nbr; via = via'; junctions = List.rev transit }
                end
                else
                  let is_junction =
                    match Arch.node a nbr with Some n -> n.is_junction | None -> false
                  in
                  walk nbr via' (if is_junction then nbr :: transit else transit)
                    (depth + 1))
            (try Hashtbl.find adj node with Not_found -> [])
      in
      walk src [] [] 0;
      Hashtbl.replace hops src
        (Hashtbl.fold (fun _ h acc -> h :: acc) found []
        |> List.sort (fun (x : hop) y -> compare x.dst y.dst)))
    sites;
  let index = Hashtbl.create (List.length sites) in
  List.iteri (fun i s -> Hashtbl.replace index s i) sites;
  { sites; hops; index }

let neighbours (t : t) (s : string) : hop list =
  match Hashtbl.find_opt t.hops s with Some h -> h | None -> []

let n_sites (t : t) = List.length t.sites

(* ------------------------------------------------------------------ distances *)

(* Breadth-first distance in hops from one trap to every other.  |sites| is at most 288
   on the shipped devices, so the full table is at most ~83k entries -- cheap to build
   once and worth having, because the router asks "which meeting point is closest to
   both of these ions" for every two-qubit gate in the program. *)
let bfs (t : t) (src : string) : (string, int) Hashtbl.t =
  let dist = Hashtbl.create (n_sites t) in
  Hashtbl.replace dist src 0;
  let q = Queue.create () in
  Queue.add src q;
  while not (Queue.is_empty q) do
    let u = Queue.pop q in
    let du = Hashtbl.find dist u in
    List.iter
      (fun (h : hop) ->
        if not (Hashtbl.mem dist h.dst) then begin
          Hashtbl.replace dist h.dst (du + 1);
          Queue.add h.dst q
        end)
      (neighbours t u)
  done;
  dist

type dists = (string, (string, int) Hashtbl.t) Hashtbl.t

let all_dists (t : t) : dists =
  let d = Hashtbl.create (n_sites t) in
  List.iter (fun s -> Hashtbl.replace d s (bfs t s)) t.sites;
  d

let dist (d : dists) a b =
  match Hashtbl.find_opt d a with
  | None -> None
  | Some row -> Hashtbl.find_opt row b

(* The shortest hop path from `a` to `b`, as the hops to take.  Reconstructed by
   descending the distance field from `b`, which needs no predecessor table. *)
let path (t : t) (d : dists) (a : string) (b : string) : hop list option =
  match Hashtbl.find_opt d b with
  | None -> None
  | Some to_b -> (
    match Hashtbl.find_opt to_b a with
    | None -> None
    | Some _ ->
      let rec go cur acc =
        if cur = b then Some (List.rev acc)
        else
          let dcur = Hashtbl.find to_b cur in
          match
            List.find_opt
              (fun (h : hop) ->
                match Hashtbl.find_opt to_b h.dst with
                | Some dh -> dh = dcur - 1
                | None -> false)
              (neighbours t cur)
          with
          | None -> None
          | Some h -> go h.dst (h :: acc)
      in
      go a [])

(* ------------------------------------------------------------------ reporting *)

let describe (t : t) (a : Arch.t) =
  let degs = List.map (fun s -> List.length (neighbours t s)) t.sites in
  let total = List.fold_left ( + ) 0 degs in
  let gate_sites = List.length (Arch.gate_sites a) in
  Printf.sprintf
    "trap graph: %d traps, %d one-cycle hops (mean degree %.2f), %d gate-capable"
    (n_sites t) total
    (if t.sites = [] then 0.0 else float_of_int total /. float_of_int (n_sites t))
    gate_sites
