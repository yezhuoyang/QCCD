# QCCD co-design workflow (reference)

This file ships with the `qccd` skill and is served, version-matched, by
`qccd_read_reference(section="workflow")`.

## The objects

| object | what it is | how you reach it |
|---|---|---|
| workspace | one directory with `qccd.lock.json`; pins ONE task release | `qccd_get_context().workspace/task` |
| revision | an integer per branch; every committed change set makes the next one | `revision`, `expected_revision` |
| change set | a list of semantic operations, validated whole, committed atomically | `qccd_apply_change_set` |
| branch | `main`, or a candidate `cand/<name>` forked from main at a revision | `qccd_manage_branch` |
| prompt | the user's versioned request from Studio, with a frozen context snapshot | `qccd_manage_comment` |
| job | compile / evaluate, asynchronous, cancellable | `qccd_start_job`, `qccd_get_job` |
| submission | an immutable bundle of one revision, graded by the reference evaluator | `qccd_submit_local`, `qccd_inspect_run` |

Entity keys: `node:<id>`, `segment:<id>`, `loop:<id>`, `zone:<name>`, `block:<primitives|control|heating|species|budget|description>`.
Ids are identities: a node keeps its id when its zone, capacity or position changes.

## Coordinates

Node `pos` is in lattice units (the diagram's model coordinates). Sketches, lassos and points
in a prompt's context use the same units; the recorded `viewport` is only the view box they
were drawn in. A sketch is the user's INTENT, never a physical constraint by itself.

## Change sets

```json
{"expected_revision": 12, "request_id": "a-unique-string", "mode": "preview",
 "origin_prompt_id": "p_1a2b3c", "summary": "shorten the route around the gate zones",
 "operations": [
   {"type": "add_site", "id": "T9", "pos": [6, 2], "zone": "trap"},
   {"type": "add_segment", "a": "T8", "b": "T9"},
   {"type": "protect", "keys": ["node:T3"], "note": "the user asked to keep it"}]}
```

- `mode: preview` validates everything and returns the diff and diagnostics without writing.
- The response's `diagnostics` are draft-quality findings (rule violations of the geometry,
  topology warnings, program errors, physics mode). A draft may have them; it is still saved.
- Refusals (4xx) are structural: malformed operations, a design the toolchain cannot build,
  protected entities, stale revisions, the prompt's mode, a stopped session.
- `request_id` makes retries safe: the same id with the same content returns the stored
  result; the same id with different content is refused.
- `rebase: "if_disjoint"` lets a stale change through only when nothing it touches changed
  since; otherwise it is a conflict with the intervening change sets listed.

## Demonstrations ("like this, over there")

A prompt's context may carry `demonstration`: the before/after revisions of an edit the user
made by hand, its structured diff and change sets, and `targets` (entity keys of where to
apply it). Generalise the DOMAIN change -- which nodes, zones, capacities, segments -- rather
than copying pixel offsets; `replicate` with an `offset` and `attach` is often the right tool.
Do not overwrite the demonstrated edit itself or any later edit.

## Grading, honestly

`qccd_inspect_run(part="stages")` lists: bundle, device, physics_lock, program, rules,
correspondence, certificate_binding, lean_certificate, semantics, metrics. Eligibility needs
every required stage `passed` under the `reference` profile. Metrics are MODELLED execution
time and resources under the task's physics tables -- never the evaluator's wall-clock time.
A local result is comparison data; the official server grades the same bundle independently.

## Sessions and stopping

If the user presses Stop, your session's writes are fenced: change sets and jobs are refused
until they resume you. Changes already committed are not rolled back; the user undoes them
with "Undo this change" (a new, validated change set).
