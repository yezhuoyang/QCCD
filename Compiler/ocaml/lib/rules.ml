(* The rule set the compiler plans with: `qccdc compile ... --rules <file>`.

   A rule set is a text file of assertions (qccd/ruleset/syntax.py: `rule R13 "..."`,
   `param max_chain = 15`).  The compiler does not evaluate the assertions -- the checker
   does, on the compiler's output, every rule of the same file -- but it reads which rules
   are in force and their parameters, and plans to satisfy the ones it knows how to:

     R13  present: no gate chain longer than `param max_chain` (15 if the set says nothing);
          absent: a trap's capacity is the only bound on its chain
     R22  present: a transport cycle is one waveform, so unlike motions are split into
          separate cycles; absent: one cycle may carry different motions, as on a machine
          whose every electrode is its own channel

   A rule the compiler does not know is not planned for; the output is checked against it
   all the same, and a programme that breaks it is reported as breaking it.  Every other
   rule the compiler knows (capacity, junctions, gate order, ...) it obeys whatever the set
   says: a programme that obeys a stricter rule obeys any weaker one.

   Without `--rules` the compiler plans with the standard rule set, which has both. *)

let chain_limit : int option ref = ref (Some 15)
let uniform_motion = ref true
let source : string option ref = ref None
let ids : string list ref = ref []

let read_file path =
  let ic = open_in_bin path in
  Fun.protect
    ~finally:(fun () -> close_in ic)
    (fun () -> really_input_string ic (in_channel_length ic))

(* The words of a line, split on blanks; a `#` starts a comment. *)
let words line =
  let line = match String.index_opt line '#' with Some i -> String.sub line 0 i | None -> line in
  String.split_on_char ' ' (String.map (fun c -> if c = '\t' || c = '\r' then ' ' else c) line)
  |> List.filter (fun w -> w <> "")

exception Bad of string

(* Only the lines that start an item matter here: `rule <ID> ...` and `param <name> = <n>`.
   A rule set that includes or uses others must be resolved first (the checker writes the
   resolved text: qccd.ruleset.RuleSet.text). *)
let load path =
  let text = read_file path in
  let found = ref [] and max_chain = ref None in
  List.iteri
    (fun i line ->
      let starts = String.length line > 0 && line.[0] <> ' ' && line.[0] <> '\t' in
      if starts then
        match words line with
        | "rule" :: id :: _ -> found := id :: !found
        | "param" :: "max_chain" :: "=" :: n :: _ | "param" :: "max_chain=" :: n :: _ -> (
          match int_of_string_opt n with
          | Some k when k > 0 -> max_chain := Some k
          | _ -> raise (Bad (Printf.sprintf "%s:%d: max_chain must be a positive whole number" path (i + 1))))
        | ("include" | "use") :: _ ->
          raise (Bad (Printf.sprintf "%s:%d: give the compiler a resolved rule set (every rule written out)" path (i + 1)))
        | _ -> ())
    (String.split_on_char '\n' text);
  ids := List.rev !found;
  if !ids = [] then raise (Bad (path ^ ": no rule in this rule set"));
  chain_limit :=
    if List.mem "R13" !ids then Some (Option.value !max_chain ~default:15) else None;
  uniform_motion := List.mem "R22" !ids;
  source := Some path

let describe () =
  match !source with
  | None -> "the standard rule set"
  | Some p ->
    Printf.sprintf "rule set %s (%d rules; chain limit %s; one waveform per cycle %s)" p
      (List.length !ids)
      (match !chain_limit with Some k -> string_of_int k | None -> "none")
      (if !uniform_motion then "yes" else "no")
