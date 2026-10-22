namespace QCCDBook
open Lean QCCDC.Cert in
/-- What the book's figures draw, computed by the checker's own definitions -- `posAt`, `look`,
`ionOf`, `check` -- so a figure shows what Lean replays, never a second implementation of it.

* `pos[t]`: every ion's trap at the start of cycle `t`, for `t = 0 .. T`;
* `moves`: `[t, ion, src, dst, is a hop of the device, departs from where the replay has it]`;
* `gates`: `[op, t, site, ions, where each of those ions really is at t, the site can gate,
  the ions are the op's own, the op's name]`;
* `ops`: `[index, name, qubits, how many witnesses name it]`. -/
def figure (c : Input) : Json :=
  let ts := c.moves.map (·.t) ++ c.gates.map (·.t) ++ c.rots.map (·.t)
  let horizon := ts.foldl max 0 + 2
  let pos := (List.range (horizon + 1)).map fun t => toJson (posAt c t)
  let moves := c.moves.map fun m =>
    toJson [toJson m.t, toJson m.ion, toJson m.src, toJson m.dst,
            toJson (c.hops.contains (m.src, m.dst)),
            toJson (look (posAt c m.t) m.ion == some m.src)]
  let gates := c.gates.map fun g =>
    let op := c.ops.find? (·.idx == g.dag)
    let own := match op with
      | some o => decide ((g.ions.map some).Perm (o.qubits.map (ionOf c)))
      | none => false
    toJson [toJson g.dag, toJson g.t, toJson g.site, toJson g.ions,
            toJson (g.ions.map fun i => (look (posAt c g.t) i).getD "?"),
            toJson (c.gateSites.contains g.site), toJson own,
            toJson ((op.map (·.name)).getD "?")]
  let ops := c.ops.map fun o =>
    toJson [toJson o.idx, toJson o.name, toJson o.qubits,
            toJson (c.gates.filter (·.dag == o.idx)).length]
  Json.mkObj [("arch", toJson c.arch), ("T", toJson horizon), ("qmap", toJson c.qmap),
    ("init", toJson c.init), ("pos", Json.arr pos.toArray), ("moves", Json.arr moves.toArray),
    -- a flat [t, loop, delta]: a Lean triple is a nested pair, and would print as [t, [loop, delta]]
    ("rots", Json.arr (c.rots.map fun r => toJson [toJson r.t, toJson r.loop, toJson r.delta]).toArray),
    ("gates", Json.arr gates.toArray),
    ("ops", Json.arr ops.toArray), ("loops", toJson c.loops), ("check", toJson (check c))]
end QCCDBook
