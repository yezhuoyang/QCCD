"""Right-click a part, and the page offers everything it can do to that part.

Reported on 2026-09-17, verbatim:

    "In Design, I should be able to right click and see all the options I can make for
    this selected component, such as 'delete', or if this is a site, I should be able to
    change its size directly by right click (click something like 'Modify', then a window
    pops up, where I can fill in the new size value). Same for all components. On the
    other hand, you can make the UI less busy, as the right click handles many jobs
    already!"

What was actually wrong, measured on this tree before the change:

* the ONLY per-element surface was the Selection popover, and `window.onInspector` threw
  it open on top of the canvas on every single selection -- the busiest thing on the page,
  attached to the one gesture everybody makes constantly;
* a site's capacity ("its size") could be READ there and not written: the panel showed
  `capacity 2 (from zone)` as text.  `set_site_capacity` had shipped for months;
* a rail could not be subdivided at all without knowing `add_site(on=...)` existed;
* `claim` answered `pan` for a right press, so the whole button was spent on a pan the
  middle button already does.

Every item in the menu is an adapter onto a verb that already existed, so these tests are
about what is OFFERED and what is REFUSED -- and, in particular, that an action which
cannot run is shown with its reason rather than hidden or offered and then refused.

Driven the way `tests/test_placement.py` drives: the emitted page under `tests/shim.mjs`,
with the editor's Event-free gestures called directly.  The shim has no events, no
`classList`, no timers and a `remove()` that does nothing, which is exactly why
`menuOpen` / `menuItems` / `menuInvoke` / `menuClose` / `menuState` are functions and not
listeners.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("menu") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(out)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return out


#: Everything the bodies below share: the page's own scope, the live layout, and the
#: handful of conversions a script needs to say "the point where N0 is drawn".
PRELUDE = """
const E = globalThis.__E, L = globalThis.__L;
const U = v => Number(globalThis.QCCD.unbox(v));
const dev = () => E.state().device;
const screenOf = (x, y) => [L.ox + x * L.sx, L.oy + y * L.sy];
const nodePt = id => { const n = dev().nodes[id]; return screenOf(U(n.pos[0]), U(n.pos[1])); };
const segPt = id => { const s = dev().segments[id];
  const a = dev().nodes[s.a], b = dev().nodes[s.b];
  return screenOf((U(a.pos[0]) + U(b.pos[0])) / 2, (U(a.pos[1]) + U(b.pos[1])) / 2); };
// A LOOP IS GRABBABLE ONLY IN THE BAND ITS OWN RAILS DO NOT OWN (see `hit`), so the point
// is FOUND rather than computed: step out perpendicular to one of its rails until the hit
// test answers 'loop'.  Null if the halo is unreachable, which the test asserts it is not.
const loopPt = lid => {
  const w = dev().loops[lid].nodes;
  for (let i = 0; i < w.length; i++) {
    const a = dev().nodes[w[i]], b = dev().nodes[w[(i + 1) % w.length]];
    if (!a || !b) continue;
    const q0 = screenOf(U(a.pos[0]), U(a.pos[1])), q1 = screenOf(U(b.pos[0]), U(b.pos[1]));
    const dx = q1[0] - q0[0], dy = q1[1] - q0[1], len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    for (const t of [0.5, 0.4, 0.6, 0.35, 0.65]) {
      const base = [q0[0] + t * dx, q0[1] + t * dy];
      for (const s of [1, -1]) for (let d = 0; d <= 120; d += 0.25) {
        const p = [base[0] + s * d * nx, base[1] + s * d * ny];
        const h = E.hit(p[0], p[1]);
        if (h && h.kind === 'loop') return p;
      }
    }
  }
  return null;
};
const place = (x, y) => { const p = screenOf(x, y); E.arm('site');
  E.ghostBegin('site', p[0], p[1]); E.ghostMove(p[0], p[1]);
  return E.clickStage(p[0], p[1], {}); };
const ids = st => (st && st.items ? st.items : []).map(i => i.id);
const byId = (st, id) => (st && st.items ? st.items : []).filter(i => i.id === id)[0] || null;
const say = o => console.log(JSON.stringify(o));
// A CANVAS AND A ROW OF THREE SITES JOINED BY TWO RAILS: the smallest device on which
// every kind in the menu exists.
const chain = () => {
  E.newCanvas({ name: 'menutest', template: null });
  E.setMode('edit');
  place(0, 0); place(2, 0); place(4, 0);
  E.joinNodes('N0', 'N1', {});
  E.joinNodes('N1', 'N2', {});
};
"""


def drive(page: Path, tmp_path: Path, body: str) -> dict:
    js = tmp_path / "m.mjs"
    js.write_text(f"import {{ loadPage }} from '{SHIM}';\n"
                  "globalThis.__QCCD_SYNC = true;\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;globalThis.__L=L;');\n"
                  + PRELUDE + body,
                  encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       encoding="utf-8", timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-3000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------ who owns the press

def test_a_right_press_on_a_part_is_its_menu_and_on_empty_stage_still_pans(page, tmp_path):
    """`claim` is the one arbiter and it gains ONE verdict.

    The right button had exactly one meaning before -- pan -- and that meaning is kept
    where it is the only one available: on empty stage.  Over a part there is something
    better to do with it, and `render.py` already suppresses the browser's own context
    menu over the stage, so nothing is given up that was not already gone.
    """
    r = drive(page, tmp_path, """
chain();
const p = nodePt('N0');
say({ on_element: E.claim(p[0], p[1], {}),
      on_empty: E.claim(-5000, -5000, {}),
      shift_on_empty: E.claim(-5000, -5000, { shift: true }),
      with_space: E.claim(p[0], p[1], { space: true }),
      middle: E.claim(p[0], p[1], { button: 1 }),
      right_on_element: E.claim(p[0], p[1], { button: 2 }),
      right_on_empty: E.claim(-5000, -5000, { button: 2 }) });
""")
    assert r == {"on_element": "element", "on_empty": "pan", "shift_on_empty": "marquee",
                 "with_space": "pan", "middle": "pan",
                 "right_on_element": "menu", "right_on_empty": "pan"}, r


def test_the_menu_never_opens_in_view_only_mode(page, tmp_path):
    """The website embeds the studio read-only: every press pans and nothing may be
    edited, so a menu of edits there would be a page lying about what it can do."""
    r = drive(page, tmp_path, """
chain();
const p = nodePt('N0');
E.setViewOnly(true);
const st = E.menuOpen(p[0], p[1]);
say({ claim: E.claim(p[0], p[1], { button: 2 }),
      opened: st, open: E.menuState().open, dialog: E.menuState().dialog });
""")
    assert r["claim"] == "pan", "view-only pans every press, right included"
    assert r["opened"] is None and r["open"] is False and r["dialog"] is False


# ------------------------------------------------------------------ what it offers

def test_a_site_offers_modify_its_zone_another_of_itself_and_delete(page, tmp_path):
    r = drive(page, tmp_path, """
chain();
const st = E.menuOpen(...nodePt('N0'));
const open = E.menuInvoke('zone');
const zoned = E.menuState();
say({ subject: st.subject, ids: ids(st),
      head: st.items[0].label,
      all_enabled: st.items.filter(i => i.kind !== 'head').every(i => i.enabled || !!i.why),
      submenu: zoned.submenu,
      zones: (byId(zoned, 'zone') || {}).items.map(z => [z.id, z.enabled]),
      selection: st.selection });
""")
    assert r["subject"] == {"kind": "site", "id": "N0"}
    assert r["ids"] == ["head", "modify", "zone", "place", "delete", "close-loop"], r["ids"]
    assert r["head"].startswith("N0"), r["head"]
    assert r["selection"] == ["site:N0"], "the press selects what it is over"
    assert r["all_enabled"] is True, (
        "every item is either runnable or carries its reason; there is no third state")
    # the submenu is the declared zone types, and it opens on its own press
    assert r["submenu"] == "zone"
    assert r["zones"], "a canvas declares zone types; the submenu must list them"
    assert all(z[0].startswith("zone:") for z in r["zones"]), r["zones"]


def test_a_junction_offers_only_what_a_junction_has(page, tmp_path):
    """No zone and no capacity: `DeviceBuilder.junction` raises TypeError on either, so
    offering them would be a form that writes a call Python refuses."""
    r = drive(page, tmp_path, """
chain();
E.addNodeAt(6, 0, { kind: 'junction', id: 'J0' });
E.joinNodes('N2', 'J0', {});
const st = E.menuOpen(...nodePt('J0'));
E.menuInvoke('modify');
say({ subject: st.subject, ids: ids(st),
      fields: E.menuState().fields.map(f => [f.name, f.layer]) });
""")
    assert r["subject"] == {"kind": "junction", "id": "J0"}
    assert r["ids"] == ["head", "modify", "delete", "close-loop"], r["ids"]
    assert [f[0] for f in r["fields"]] == ["x", "y"], r["fields"]


def test_a_rail_offers_the_subdivide_gesture_and_it_splices_the_rail(page, tmp_path):
    """`add_site(on="X0")` is the only add that can safely put a node onto a transport
    loop -- it is the only one with an unambiguous splice index -- and it was reachable
    from nothing on the page."""
    r = drive(page, tmp_path, """
chain();
const before = { nodes: Object.keys(dev().nodes).length,
                 segs: Object.keys(dev().segments).length };
const st = E.menuOpen(...segPt('X0'));
const r0 = E.menuInvoke('insert-site');
const after = { nodes: Object.keys(dev().nodes).length,
                segs: Object.keys(dev().segments).length };
E.undoGroup();
say({ subject: st.subject, ids: ids(st), ok: !!r0.ok, id: r0.id || null,
      before: before, after: after,
      undone: Object.keys(dev().segments).length,
      still_there: Object.keys(dev().segments).indexOf('X0') >= 0 });
""")
    assert r["subject"] == {"kind": "segment", "id": "X0"}
    assert r["ids"] == ["head", "modify", "insert-site", "delete", "close-loop"], r["ids"]
    assert r["ok"] is True, r
    assert r["before"] == {"nodes": 3, "segs": 2}
    # one rail became two, and the new trap sits between them
    assert r["after"] == {"nodes": 4, "segs": 3}, r["after"]
    assert r["undone"] == 2 and r["still_there"], "one undo puts the rail back whole"


def test_a_loops_delete_is_disabled_with_the_reason_rather_than_offered(page, tmp_path):
    """`edit.js` has no `remove_loop` op.  A Delete that always refuses would be a lie
    told one click later than necessary, so the item carries the refusal itself -- the
    SAME sentence `removeSelected` gives, because there is one function for it."""
    r = drive(page, tmp_path, """
E.newCanvas({ name: 'loopy', template: null });
E.setMode('edit');
const drew = E.sketchDraw('rect', [0, 0], [6, 4], {});
const lid = Object.keys(dev().loops)[0];
const p = loopPt(lid);
const st = p ? E.menuOpen(p[0], p[1]) : null;
const del = st ? byId(st, 'delete') : null;
const direct = (() => { E.select([{ kind: 'loop', id: lid }]);
                        const x = E.removeSelected();
                        return (x.problems[0] || {}).message || ''; })();
say({ drew: !!drew.ok, lid: lid, found: !!p,
      subject: st ? st.subject : null, ids: st ? ids(st) : null,
      enabled: del ? del.enabled : null, why: del ? del.why : null,
      same_sentence: del ? del.why === direct : null });
""")
    assert r["drew"] is True
    assert r["found"] is True, "a loop has a grabbable halo; the test could not find it"
    assert r["subject"]["kind"] == "loop", r["subject"]
    assert r["ids"][:3] == ["head", "modify", "delete"], r["ids"]
    assert r["enabled"] is False
    assert "no delete verb" in r["why"], r["why"]
    assert r["same_sentence"] is True, (
        "the menu and removeSelected must not keep two explanations of one gap")


def test_a_part_of_a_component_offers_the_whole_component(page, tmp_path):
    """A sketched shape is ONE unit: every site and rail it made carries its instance
    label.  Right-clicking one of them has to be able to reach the whole thing."""
    r = drive(page, tmp_path, """
E.newCanvas({ name: 'cmp', template: null });
E.setMode('edit');
const drew = E.sketchDraw('rect', [0, 0], [6, 4], {});
const nid = Object.keys(dev().nodes)[0];
const st = E.menuOpen(...nodePt(nid));
const heads = st.items.filter(i => i.kind === 'head').map(i => i.label);
const picked = E.menuInvoke('select-component');
const n_sel = E.selection().length;
say({ instance: E.instanceAt(nid), ids: ids(st), heads: heads,
      members: E.instanceMembers(drew.instance).length,
      selected: picked.selected, n_sel: n_sel });
""")
    assert r["instance"], "the sketched shape labels its parts"
    assert "select-component" in r["ids"] and "delete-component" in r["ids"], r["ids"]
    assert any("part of" in h for h in r["heads"]), r["heads"]
    assert r["selected"] == r["members"] == 40, r
    assert r["n_sel"] == 40


def test_right_clicking_one_member_of_a_selection_keeps_it_and_acts_on_all(page, tmp_path):
    r = drive(page, tmp_path, """
chain();
E.select([{ kind: 'site', id: 'N0' }, { kind: 'site', id: 'N1' }, { kind: 'site', id: 'N2' }]);
const inside = E.menuOpen(...nodePt('N1'));
E.menuClose();
E.select([{ kind: 'site', id: 'N0' }, { kind: 'site', id: 'N1' }]);
const outside = E.menuOpen(...nodePt('N2'));
say({ kept: inside.selection, ids_in: ids(inside),
      del_label: (byId(inside, 'delete') || {}).label,
      close_loop: (byId(inside, 'close-loop') || {}).enabled,
      reset: outside.selection,
      close_loop_two: (byId(outside, 'close-loop') || {}).enabled,
      close_loop_why: (byId(outside, 'close-loop') || {}).why });
""")
    assert r["kept"] == ["site:N0", "site:N1", "site:N2"], (
        "a right press on a member of the selection must not collapse it")
    assert r["ids_in"] == ["head", "modify", "delete", "close-loop"], r["ids_in"]
    assert r["del_label"] == "Delete (3)"
    assert r["close_loop"] is True, "three nodes are a walk"
    assert r["reset"] == ["site:N2"], (
        "a right press on something NOT selected selects it first")
    # ... and the offer that cannot run says so rather than refusing on the click
    assert r["close_loop_two"] is False
    assert "at least three nodes" in r["close_loop_why"], r["close_loop_why"]


def test_modify_is_disabled_for_a_mixed_selection_with_the_reason(page, tmp_path):
    r = drive(page, tmp_path, """
chain();
E.select([{ kind: 'site', id: 'N0' }, { kind: 'segment', id: 'X0' }]);
const st = E.menuOpen(...nodePt('N0'));
const m = byId(st, 'modify');
const tried = E.menuInvoke('modify');
say({ enabled: m.enabled, why: m.why, ok: !!tried.ok,
      problem: (tried.problems[0] || {}).message || '',
      dialog: E.menuState().dialog });
""")
    assert r["enabled"] is False
    assert "mixes" in r["why"], r["why"]
    assert r["ok"] is False and r["dialog"] is False
    assert r["problem"] == r["why"], "invoking a disabled item answers with its own reason"


# ------------------------------------------------------------------ delete, and undo

def test_delete_through_the_menu_removes_exactly_that_and_one_undo_restores_it(page, tmp_path):
    """EXACTLY that: the site at the end of the chain and the one rail it carried, not
    the two nodes beyond it.  (Deleting the MIDDLE one is a different, correct answer --
    `remove_node(cascade=True)` then takes the two ends away too, because nothing is left
    to reach them -- and the menu is a thin adapter onto that verb, not a second opinion
    about it.)"""
    r = drive(page, tmp_path, """
chain();
const before = { n: Object.keys(dev().nodes).length, s: Object.keys(dev().segments).length,
                 digest: JSON.stringify(E.digest()) };
E.menuOpen(...nodePt('N0'));
const r0 = E.menuInvoke('delete');
const after = { nodes: Object.keys(dev().nodes).sort(),
                segs: Object.keys(dev().segments).sort() };
E.undoGroup();
say({ ok: !!r0.ok, before: before, after: after,
      closed: !E.menuState().open,
      restored: JSON.stringify(E.digest()) === before.digest });
""")
    assert r["ok"] is True
    assert r["before"]["n"] == 3 and r["before"]["s"] == 2
    assert r["after"] == {"nodes": ["N1", "N2"], "segs": ["X1"]}, r["after"]
    assert r["closed"] is True, "the menu goes away with the thing it acted on"
    assert r["restored"] is True, "one undo puts the whole gesture back, bit for bit"


# ------------------------------------------------------------------ the Modify panel

def test_modify_changes_a_sites_capacity_and_the_device_shows_it(page, tmp_path):
    """The user's own words: "if this is a site, I should be able to change its size
    directly".  `set_site_capacity` is a post-seal retune, so it works on every device."""
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...nodePt('N0'));
E.menuInvoke('modify');
const fields = E.menuState().fields.map(f => [f.name, f.layer, f.enabled]);
const was = dev().nodes['N0'].cap;
const r0 = E.modifyApply({ capacity: '7' });
say({ fields: fields, was: was, ok: !!r0.ok,
      cap: dev().nodes['N0'].cap,
      closed: !E.menuState().dialog,
      edits: E.edits().length,
      source: E.source().indexOf('set_site_capacity') >= 0 });
""")
    assert [f[0] for f in r["fields"]] == ["capacity", "x", "y", "zone", "labels"], r["fields"]
    assert r["fields"][0][1] == "mutate" and r["fields"][0][2] is True
    assert r["ok"] is True and r["cap"] == 7, r
    assert r["was"] != 7
    assert r["closed"] is True, "a panel that applied has nothing left to say"
    assert r["source"] is True, (
        "the change is a statement in the one listing, not a second kind of state")


def test_modify_moves_a_site_and_a_coincident_move_is_refused_inline(page, tmp_path):
    r = drive(page, tmp_path, """
chain();
E.setSnap(false);
E.menuOpen(...nodePt('N0'));
E.menuInvoke('modify');
const good = E.modifyApply({ x: '-3', y: '1.5' });
const moved = [U(dev().nodes['N0'].pos[0]), U(dev().nodes['N0'].pos[1])];
E.menuOpen(...nodePt('N0'));
E.menuInvoke('modify');
const bad = E.modifyApply({ x: '2', y: '0' });      // exactly where N1 sits
const st = E.menuState();
say({ good: !!good.ok, moved: moved, bad: !!bad.ok, why: st.why, dialog: st.dialog,
      still: [U(dev().nodes['N0'].pos[0]), U(dev().nodes['N0'].pos[1])] });
""")
    assert r["good"] is True and r["moved"] == [-3, 1.5], r
    assert r["bad"] is False
    assert "on top of" in r["why"], r["why"]
    assert r["dialog"] is True, "a refused panel stays open, with the reason in it"
    assert r["still"] == [-3, 1.5], "a refused move changes nothing"


def test_modify_refuses_capacity_zero_with_the_reason_in_the_panel(page, tmp_path):
    """Not a toast that has already faded by the time the field is fixed: the sentence
    belongs to the field the user just typed in."""
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...nodePt('N0'));
E.menuInvoke('modify');
const was = { cap: dev().nodes['N0'].cap, edits: E.edits().length };
const r0 = E.modifyApply({ capacity: '0' });
const st = E.menuState();
say({ ok: !!r0.ok, code: (r0.problems[0] || {}).code || '',
      why: st.why, dialog: st.dialog, was: was,
      cap: dev().nodes['N0'].cap, edits: E.edits().length });
""")
    assert r["ok"] is False
    assert r["why"] == "a site must be able to hold at least one ion", r["why"]
    assert r["dialog"] is True, "the panel stays open so the number can be fixed"
    assert r["cap"] == r["was"]["cap"] and r["edits"] == r["was"]["edits"], (
        "a refusal must leave the device and the statement list exactly as they were")


def test_a_rails_length_is_the_mutate_layer_and_zero_is_refused(page, tmp_path):
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...segPt('X0'));
E.menuInvoke('modify');
const fields = E.menuState().fields.map(f => [f.name, f.layer]);
const good = E.modifyApply({ length: '3.5' });
const len = U(dev().segments['X0'].length);
E.menuOpen(...segPt('X0'));
E.menuInvoke('modify');
const bad = E.modifyApply({ length: '0' });
say({ fields: fields, good: !!good.ok, len: len, bad: !!bad.ok, why: E.menuState().why });
""")
    assert r["fields"] == [["length", "mutate"], ["capacity", "builder"],
                           ["loop", "builder"], ["labels", "builder"]], r["fields"]
    assert r["good"] is True and r["len"] == 3.5
    assert r["bad"] is False and "positive length" in r["why"], r["why"]


def test_a_sites_zone_is_a_builder_rewrite_and_the_device_takes_it(page, tmp_path):
    """There is no post-seal verb for a site's zone, so this rewrites the ONE `d.site`
    record -- the same path `addNodeAt` takes, and the builder keys on the id."""
    r = drive(page, tmp_path, """
chain();
const zones = Object.keys(E.state().zone_types).sort();
const target = zones.filter(z => z !== dev().nodes['N0'].zone)[0];
const st = E.menuOpen(...nodePt('N0'));
E.menuInvoke('zone');
const r0 = E.menuInvoke('zone:' + target);
say({ zones: zones, was: st.subject, target: target, ok: !!r0.ok,
      zone: dev().nodes['N0'].zone,
      closed: !E.menuState().open,
      statements: (E.source().match(/d\\.site\\("N0"/g) || []).length });
""")
    assert r["ok"] is True, r
    assert r["zone"] == r["target"], r
    assert r["closed"] is True
    assert r["statements"] == 2, (
        "the rewrite is one more d.site statement; the builder keys on the id and the "
        "later one is the one in force")


def test_a_builder_field_is_disabled_on_a_generator_device_and_enabled_after_explode(page, tmp_path):
    """A generator device has no builder statements at all, so `d.site(zone=)` has
    nothing to rewrite.  `clickStage` already says this in words when a component
    placement is refused; the panel closes the loop by offering the way out -- behind a
    confirmation, because it is what the device is saved as from then on."""
    r = drive(page, tmp_path, """
E.newFromGenerator('ring', { width: 12, height: 2, verticals: 4 }, { name: 'ring' });
E.setMode('edit');
const nid = Object.keys(dev().nodes).filter(k => dev().nodes[k].kind === 'site')[0];
E.menuOpen(...nodePt(nid));
const zone_item = byId(E.menuState(), 'zone');
E.menuInvoke('modify');
const before = E.menuState();
const first = E.modifyExplode();
const armed = E.menuState();
const second = E.modifyExplode();
const after = E.menuState();
say({ builder: !!E.state().builder,
      zone_offer: [zone_item.enabled, zone_item.why],
      before: before.fields.map(f => [f.name, f.layer, f.enabled]),
      explode_offered: before.explode,
      confirm_first: [!!first.ok, armed.confirm],
      after: after.fields.map(f => [f.name, f.layer, f.enabled]),
      explode_after: after.explode,
      statements: second.statements || 0,
      warning: (second.warning || '').slice(0, 40) });
""")
    off = [f for f in r["before"] if f[1] == "builder"]
    assert off and all(f[2] is False for f in off), r["before"]
    assert all(f[2] is True for f in r["before"] if f[1] == "mutate"), (
        "a post-seal retune works on every device, generator or not")
    assert r["explode_offered"] is True
    # the menu's own Set zone says the same thing, in the same words
    assert r["zone_offer"][0] is False and "generator" in r["zone_offer"][1], r["zone_offer"]
    assert r["confirm_first"] == [False, True], (
        "the first press arms the confirmation; it must not explode on one click")
    assert r["statements"] > 0 and r["warning"]
    assert all(f[2] is True for f in r["after"]), r["after"]
    assert r["explode_after"] is False, "there is nothing left to offer once it is explicit"


# ------------------------------------------------------------------ dismissal

def test_escape_closes_the_menu_before_it_closes_anything_else(page, tmp_path):
    """There are three Escape owners on this page already.  The menu joins the editor's
    ORDERED list rather than adding a fourth listener to race them, and it goes first:
    it is a panel at the pointer opened by the press just made."""
    r = drive(page, tmp_path, """
chain();
E.select([{ kind: 'site', id: 'N0' }]);
E.measureToggle(true);
E.menuOpen(...nodePt('N0'));
const open = E.menuState().open;
const a = E.cancelGesture();
const b = E.cancelGesture();
const c = E.cancelGesture();
say({ open: open, order: [a, b, c],
      closed: !E.menuState().open, measure: E.measureOn(), sel: E.selection().length });
""")
    assert r["open"] is True
    assert r["order"] == ["menu", "measure", "selection"], (
        f"the menu must be the first thing Escape takes away: {r['order']}")
    assert r["closed"] is True and r["measure"] is False and r["sel"] == 0


def test_escape_takes_the_submenu_first_then_the_menu(page, tmp_path):
    """One thing per press, which is the rule the ruler and the sketch already follow."""
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...nodePt('N0'));
E.menuInvoke('zone');
const sub = E.menuState().submenu;
const a = E.cancelGesture();
const mid = E.menuState();
const b = E.cancelGesture();
say({ sub: sub, a: a, still_open: mid.open, after_sub: mid.submenu,
      b: b, closed: !E.menuState().open });
""")
    assert r["sub"] == "zone"
    assert r["a"] == "menu" and r["still_open"] is True and r["after_sub"] is None
    assert r["b"] == "menu" and r["closed"] is True


def test_the_modify_panel_is_cancelled_by_escape_and_applied_by_enter(page, tmp_path):
    """Its rows are text inputs, so the page's generic field rule would answer 'blur' for
    Escape and nothing at all for Enter.  One keymap, two more meanings for keys that
    already had them -- no new binding, and both rows of the `?` guide say so."""
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...nodePt('N0'));
E.menuInvoke('modify');
const enter = E.keyGesture('Enter', {}, { field: 'mdlg_capacity' });
const esc = E.keyGesture('Escape', {}, { field: 'mdlg_capacity' });
const other = E.keyGesture('Escape', {}, { field: 'pwText' });
const gone = E.cancelGesture();
say({ enter: enter, esc: esc, other: other, verb: gone, dialog: E.menuState().dialog });
""")
    assert r["enter"] == "modify-apply" and r["esc"] == "modify-cancel"
    assert r["other"] == "blur", "every other field keeps the meaning it had"
    assert r["verb"] == "menu" and r["dialog"] is False


def test_another_press_on_the_stage_puts_the_menu_away(page, tmp_path):
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...nodePt('N0'));
const open = E.menuState().open;
const p = nodePt('N2');
// what the pointer adapter does with a press the arbiter did NOT give to the menu
const who = E.claim(p[0], p[1], {});
if (who !== 'menu') E.menuClose();
say({ open: open, who: who, closed: !E.menuState().open });
""")
    assert r["open"] is True and r["who"] == "element" and r["closed"] is True


# ------------------------------------------------------------------ the three trims

def test_the_tools_bar_no_longer_carries_a_selection_button(page, tmp_path):
    """Its popover showed `#palInspect`, the SAME node the rail already renders, and
    `window.onInspector` popped it open on every selection.  The inspector stays where it
    is read; the popover, the button and the auto-pop are gone together -- with every one
    of the hand-maintained lists that enumerated the popover keys."""
    html = page.read_text(encoding="utf-8")
    assert 'id="tbSelection"' not in html, "the button is gone"
    assert 'data-pop="selection"' not in html
    assert "window.onInspector = function" not in html, (
        "nothing pops itself open any more")
    assert 'id="palInspect"' in html and 'id="palInsp"' in html, (
        "the inspector itself stays in the rail: it is what tests read")
    # THE HAND-MAINTAINED LISTS THAT ENUMERATE THE POPOVER KEYS, trimmed together or not
    # at all -- a key left in one of them is a button that half exists.
    lines = html.splitlines()
    for decl in ("const POP_BTN", "const POP_NAMES"):
        row = [ln for ln in lines if ln.strip().startswith(decl)]
        assert len(row) == 1, decl
        assert "selection" not in row[0], f"{decl} still carries the key: {row[0]}"
    assert "'start','row','block','component','selection'" not in html, (
        "an inline popover-key list still names 'selection'")
    r = drive(page, tmp_path, """
chain();
E.select([{ kind: 'site', id: 'N0' }]);
say({ pop: window.TOOLSBAR ? window.TOOLSBAR.current() : 'no toolbar',
      insp: (document.getElementById('palInsp').innerHTML || '').indexOf('N0') >= 0,
      del: (document.getElementById('palInsp').innerHTML || '').length > 0 });
""")
    assert r["pop"] is None, "selecting a part must not throw a panel over the canvas"
    assert r["insp"] is True, "the rail's inspector still names what is selected"


def test_the_stage_toolbar_no_longer_duplicates_the_shape_tiles(page, tmp_path):
    """Three ways to arm one tool, two of them a bare word in a crowded bar.  The rail's
    tiles say what each shape draws AND what the gesture is; the keys r/e/n/p stay."""
    html = page.read_text(encoding="utf-8")
    for t in ("rect", "ellipse", "line", "poly"):
        assert f'id="tShape_{t}"' not in html, f"tShape_{t} is still in the bar"
    assert 'id="tShapes"' not in html, "the span that had to be hidden in Parts mode too"
    assert 'data-el="shape:' not in html, "the rail's tiles are built at run time"
    r = drive(page, tmp_path, """
E.newCanvas({ name: 'sk', template: null });
E.setDesignMode('sketch');
E.sketchTool('rect');
const armed = E.sketchOn();
const tiles = (document.getElementById('palBody').children || []).length;
const k = E.keyGesture('e', {}, {});
E.sketchTool(null);
say({ armed: armed, tiles: tiles, key: k, tools: E.sketchTools() });
""")
    assert r["armed"] == "rect", "the verb the tiles and the keys share still works"
    assert r["key"] == "shape-ellipse", "r/e/n/p are still the third hand"
    assert r["tools"] == ["rect", "ellipse", "line", "poly"]
    assert r["tiles"] > 0, "the rail still leads with the shapes in Sketch mode"


def test_an_empty_canvas_reads_zero_problems_and_the_notes_stay_reachable(page, tmp_path):
    """The reported greeting was "10 problems" on a canvas with nothing on it: ten
    statements about a device the user had not started drawing.  Every one of them was a
    NOTE -- a zone type no site uses, a movement class whose orbit matches no loop.
    Nothing is silenced: `lints()` still returns all of them, the notes have their own
    chip and their own heading, and drawing something that IS wrong still counts."""
    r = drive(page, tmp_path, """
E.newCanvas({ name: 'blank' });
E.setMode('edit');
const chip = id => document.getElementById(id).textContent || '';
const blank = { problems: chip('eProb'), notes: chip('eNotes'),
                lints: E.lints().length, split: E.notes().length + E.lintProblems().length,
                note_codes: E.notes().map(l => l.code).sort()
                            .filter((v, i, a) => a.indexOf(v) === i) };
// ... and a finding about WHAT WAS DRAWN is still a problem: a node wired to nothing is a
// structural warning about a site that exists, not about one that does not.
E.addSite(3, 3, null, {});
const drawn = { problems: chip('eProb'),
                rules: E.lintProblems().map(l => l.rule || l.code) };
say({ blank: blank, drawn: drawn });
""")
    b = r["blank"]
    assert b["problems"] == "0 problems", (
        f"an empty canvas must not open on a pile of red: {b}")
    assert b["lints"] > 0, "the findings are still there; only their register changed"
    assert b["split"] == b["lints"], "every lint is in exactly one of the two registers"
    assert set(b["note_codes"]) <= {"zone_unused", "class_no_participants"}, b["note_codes"]
    assert b["notes"].endswith("notes") and b["notes"] != "0 notes", (
        "the notes keep their own chip, and it says how many")
    # something wrong with what the user drew still counts, and counts on the chip
    assert r["drawn"]["problems"] != "0 problems", r["drawn"]
    assert "structure" in r["drawn"]["rules"], r["drawn"]["rules"]


def test_every_menu_control_the_page_can_draw_has_a_hover_card(page, tmp_path):
    """`data-hint` and the HINTS table are two halves of one fact, and the failure is
    silent on four surfaces at once.  `tests/test_hints.py` scans the emitted HTML; the
    menu's own items are built at run time, so they are scanned here instead."""
    r = drive(page, tmp_path, """
chain();
E.menuOpen(...nodePt('N0'));
E.menuInvoke('zone');
// the DOM is walked, not regex'd: `paintMenu` appends children, and the shim holds
// `innerHTML` as whatever was last assigned to it
const walk = (el, out) => { const h = el.getAttribute && el.getAttribute('data-hint');
  if (h) out.push(h);
  for (const c of (el.children || [])) walk(c, out);
  return out; };
const keys = walk(document.getElementById('ctxmenu'), ['ctxmenu'])
  .filter((v, i, a) => a.indexOf(v) === i).sort();
E.menuInvoke('modify');
const dlg = walk(document.getElementById('ctxmenu'), []).filter((v, i, a) => a.indexOf(v) === i);
const all = keys.concat(dlg).filter((v, i, a) => a.indexOf(v) === i).sort();
say({ keys: all, missing: all.filter(k => !E.hintFor(k)),
      pointer: E.keyHelp().pointer.filter(r => /right-click/.test(r[0])).length });
""")
    assert len(r["keys"]) > 4, f"the menu draws hinted controls: {r['keys']}"
    assert r["missing"] == [], (
        f"these menu controls claim a hover card HINTS does not carry: {r['missing']}")
    assert r["pointer"] == 1, "the `?` guide has a row for the right-click gesture"
