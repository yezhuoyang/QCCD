# Rules for coding agents in this repository

## Design pages: one template, no overlays

Every page that shows a programme on a machine (a board entry, a Goal point, an RSA-2048
program, a gadget, an official entry, a book or compilation example, the workspace studio) is
written by `qccd.viz.render.render_html` and by nothing else.

- **Never patch a page after `render_html` writes it.** No CSS or JS that restyles,
  rearranges or hides the template's own elements (the Program column, the listings, the
  stage card, the transport bar, the rail). A feature a page needs is a parameter of
  `render_html` (as `notes=`, `ruleset=`, `open_pane=` are) or part of the template for
  every page.
- **Always say `source=`.** Pass the circuit's source map (`qccd.ir.source_map.build(prog,
  cert, qasm)`) whenever the programme was compiled from a circuit: it gives the Program
  column its Hardware / Gates / Both views. Write `source=None` only when no circuit stands
  behind the programme.
- **Don't copy another generator's page code.** Call `render_html` with the parameters you
  need. If something is missing, add it to the template so every page gets it.
- **Delete what you replace.** When a feature moves into the template, remove the old code
  path in the same change.

`tests/test_one_template.py` enforces this. It fails on a `render_html` call without
`source=`, on a file outside the template that names the template's layout elements (apart
from a short, shrinking list of add-ons), and on the return of the removed overlay's names.

Editing `qccd/viz/render.py` or the JS it inlines changes the page stamp, so every built page
must be rebuilt (see `tests/test_board_engine.py`).
