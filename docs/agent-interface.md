# The agent interface: a feature ships with its declaration, or it does not ship

Agents (Claude, Codex, any MCP client) work on QCCD pages the way a person does, through the
page tools (`qccd_page_read`, `qccd_page_act`). They may do **only what the code declares**:

- a control that is not declared is read and pointed at, never pressed or typed into;
- a path that is not a place is refused;
- a Studio function not declared for agents is refused.

Every refusal names what *is* declared, so the agent recovers instead of guessing, and the
refusal is recorded in the trace (`qccd trace`), so we see it and can fix it.

Declarations live in code and are read when a page is served. Rebuilding therefore changes
what agents know; nothing is kept up to date by hand. `tests/test_agent_interface.py` is the
gate: a change that adds UI without its declaration fails it.

The mechanism is in `qccd/workspace/interface.py`. Agents read all of it as one document,
`qccd_read_reference(section="interface")`.

## What you declare, and where

| you add | you also add | checked by |
|---|---|---|
| a button, field, select or other control on a Studio page | `data-hint="<key>"` on the element and a `HINTS` entry for the key in `qccd/viz/js/editor.js`. It is the same sentence the hover card, Explain and the guide show a person. | the Studio check: every control declared, every key described |
| a control on a website page | `data-hint="<key>"` and an entry in the page's `window.QCCD_HINTS = {key: {t: name, d: what it does}}`, as the site bar's search box does (`qccd/site/nav.html`) | the live-site audit (below) |
| a region the code generates controls into from a schema | its selector and one sentence in `interface.DECLARED_FORMS`; each field keeps its own label | the Studio check (the region must exist) |
| a choice inside a control group (Hardware / Gates / Both) | nothing more: the group's own `data-hint` declares its options. A `region:` hint does not. | the Studio check |
| a function on `window.EDITOR` | an entry in `qccd/viz/js/editor_api.json`: `use` (`agent`, `harness` for tests and the pointer, `never` for the person's files or storage), `does`, and for an agent verb its `kind` (`read`, `view`, `design`, `program` or `course`) and `args` | every API key has an entry and every entry names a key |
| a page on the website | nothing more: the site build puts every page in the search index, and the index is the list of places | — |
| a page action | the name in `service.PAGE_ACTIONS`, a `case` in `web/pageact.js act()`, the MCP tool's `action` enum, and a line in the skill | the four agree |

Links and native fold-outs (`<summary>`) are declared by being what they are.

Text written for agents (the MCP instructions, tool descriptions, the skill, the site guide)
may name only Studio functions declared for agents; the gate checks this too.

## The out-of-line list (debt that only shrinks)

The Studio's template and scripts are stamped into every site page. Editing them means
regenerating all 69 entry pages, so a few controls are declared **out of line** in
`interface.OUT_OF_LINE` (by selector) until the next regeneration. The list is pinned: the
test fails if it grows. At the next regeneration:

1. move each entry inline (`data-hint` plus a `HINTS` entry);
2. delete it from the list;
3. lower `OUT_OF_LINE_PINNED`.

As of 2026-09-24 there are ten, all in the Studio: the lesson back button, the program chip,
circuit Follow, the device filter, the source editor and its three buttons, the Write text,
and Test drive. The site's search box was the eleventh; it is now declared inline in
`qccd/site/nav.html`, and the live pages were patched the same day.

## Not declared yet (agents cannot operate these; their owners declare them inline)

Measured on the live site on 2026-09-24. All 106 places in the site's index, plus site Studio
lessons, were checked with `QCCD_LIVE_WEB=1 python tests/workspace_live_interface.py`. That
check fails on any undeclared control not listed here.

| where | undeclared controls | code |
|---|---|---|
| gadget viewer pages (`gadgets/algorithms/*`, `gadgets/demo/`, `gadgets/showcase/`) | 11–12: `bStart`, `bPlay`, `bStep`, `rate`, `bLib`, `checksBadge`, `bEdit`, `bUp`, `bFit`, `bIons`, `bClassical` | `qccd/gadget/web/` |
| leaderboard index pages (`board/<task>/`) | the chart's `ysel`, `xsel`, `log`, `vchart`, `vtable` | the site build |
| leaderboard entry pages (`board/<task>/<entry>.html`) | the Study-notes overlay (`bbpill`, `bbx`, `bbprev`, `bbnext`, `bbreopen`), the label box in `bblab`, two pane close buttons (`pHw`, `paneQ`), `qcShowOn` | the board overlay, the compiled pages' template, `qccd/site/qec_cycle.js` |
| site Studio pages | the QEC-cycle panel's `qcShowOn` | `qccd/site/qec_cycle.js` |

Every other page type is fully declared: home, rules, learn, compilation, language,
physics, people, publications, the gadgets index, discuss, docs, and the site Studio's
lessons.

## What an agent sees

- `qccd_page_read` lists controls with their declaration: `hint` and `what` for declared
  ones, `undeclared: true` for the rest.
- `studio` verb `verbs` lists the agent verbs with what each does. On the person's own
  workspace Studio the design and program verbs work too, and what each draws is committed
  as the agent's change set. The course's verbs are off there, since they would replace the
  design. A design verb that refuses fails the action with its rule and reason.
- A refused `navigate` names the nearest places (the site index, the page's links, `/studio`).
- A refused `click`/`fill`/`press` names declared controls on the page. A key that acts
  (Enter, arrows) needs a declared target.
