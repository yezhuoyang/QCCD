"""The pages of an agent review: where a person submits a design for review, and where they read it.

    review_page(data, nonce)       /review/<submission>: the decision as soon as it is made, the
                                   agent's progress while it reviews, then its review; every rule
    console_page(..., nonce)       /review: submit a bundle (or, on a development server, one of
                                   the repository's examples) and pick who reviews it

Both are whole pages with their own style and one nonce'd script that polls the JSON API; nothing
from a bundle or an agent reaches them unescaped (the review's markdown is rendered with raw
HTML off).  EXAMPLES are bundles made from the compiled examples, for trying the flow locally.
"""

from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path

__all__ = ["review_page", "console_page", "md_html", "EXAMPLES", "example_archive", "page_headers"]

REPO = Path(__file__).resolve().parents[2]
COMPILED = REPO / "qccd" / "site" / "compiled"

_e = html.escape

#: entries made from qccd/site/compiled, for trying a review on a development server
EXAMPLES = {
    "ghz-chain4": {"title": "GHZ on the 4-site chain", "board": "ghz4@1", "dir": "algo_ghz", "device": "chain4.arch.json",
                   "about": "The GHZ starter board's own circuit, compiled onto chain4: every rule holds."},
    "ghz-chain4-hot": {"title": "GHZ on the 4-site chain, cooling taken out", "board": "ghz4@1", "dir": "algo_ghz",
                       "device": "chain4.arch.json", "strip_cools": True,
                       "about": "The same compile with every cool removed: too hot for R7, and R7c finds no cooling."},
    "bell-on-ghz": {"title": "The Bell pair, entered on the GHZ board", "board": "ghz4@1", "dir": "bell",
                    "device": "chain4.arch.json",
                    "about": "A 2-qubit programme and certificate on the 4-qubit board: the wrong circuit."},
}


def example_archive(name: str) -> tuple[str, bytes]:
    """(board, bundle archive) for example `name`, built as a workspace would build it."""
    from ..workspace.bundle import archive_bundle, write_bundle
    from ..workspace.tasks import board_device, find_release
    ex = EXAMPLES[name]
    rel = find_release(ex["board"])

    def load(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))
    d = COMPILED / ex["dir"]
    certified, prog, cert = load(d / "prog.tsir.json"), load(d / "prog.cooled.tsir.json"), load(d / "prog.qcert.json")
    if ex.get("strip_cools"):
        certified["instructions"] = [i for i in certified["instructions"] if i.get("type") != "cool"]
        prog = certified
    with tempfile.TemporaryDirectory() as td:
        b = write_bundle(Path(td) / "bundle", task_id=rel.id, task_digest=rel.digest,
                         device=board_device(load(COMPILED / ex["device"]), rel), program=prog,
                         certified_program=certified, certificate=cert,
                         provenance={"made_by": "qccd review console", "example": name})
        return rel.id, archive_bundle(b)


def page_headers(nonce: str) -> dict:
    return {"Content-Security-Policy": f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
                                       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                                       "frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def md_html(text: str) -> str:
    """Markdown -> HTML with raw HTML off (markdown-it), or escaped text without it."""
    try:
        from markdown_it import MarkdownIt
    except ImportError:
        return f'<pre class="raw">{_e(text)}</pre>'
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    return md.enable("table").enable("strikethrough").render(text)


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e4e3de;--acc:#2f5bd3;--good:#1e7a46;
--good-bg:#e7f4ec;--bad:#b42318;--bad-bg:#fdecea;--hold:#9a6700;--hold-bg:#fff4d6;--und:#55555a;--und-bg:#eeeef0;
--code:#f1f0ec;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1d1d1c;--ink:#ececea;--mute:#a1a19b;--line:#33332f;
--acc:#8aa8ff;--good:#6fd39b;--good-bg:#173323;--bad:#ff8d82;--bad-bg:#3a1c19;--hold:#f3c34f;--hold-bg:#3a2f12;
--und:#c4c4c8;--und-bg:#2a2a2c;--code:#262624;color-scheme:dark}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif}
main{max-width:920px;margin:0 auto;padding:28px 16px 64px}a{color:var(--acc)}
h1{font-size:24px;line-height:1.25;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
.sub{color:var(--mute);margin:0 0 22px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px 20px;margin:0 0 16px}.muted{color:var(--mute)}code,pre{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
code{background:var(--code);padding:1px 5px;border-radius:5px}pre{background:var(--code);padding:12px;border-radius:10px;overflow:auto}
pre code{background:none;padding:0}.steps{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px;padding:0;list-style:none}
.steps li{flex:1;min-width:140px;border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--card);
color:var(--mute);font-size:13px}.steps li b{display:block;color:var(--ink);font-size:14px}
.steps li.done{border-color:var(--good)}.steps li.done b:before{content:"✓ ";color:var(--good)}
.steps li.active{border-color:var(--acc)}.steps li.active b:before{content:"● ";color:var(--acc)}
.steps li.fail{border-color:var(--bad)}.steps li.fail b:before{content:"✕ ";color:var(--bad)}
.decision{border-radius:14px;padding:16px 20px;margin:0 0 16px;border:1px solid transparent}
.decision .w{font-size:20px;font-weight:650}.decision ul{margin:8px 0 0;padding-left:20px}
.accepted{background:var(--good-bg);color:var(--good)}.not_accepted{background:var(--bad-bg);color:var(--bad)}
.held{background:var(--hold-bg);color:var(--hold)}.undetermined{background:var(--und-bg);color:var(--und)}
.decision li,.decision .note{color:var(--ink)}.decision .note{font-size:13px;margin-top:8px;opacity:.8}
.live{display:flex;gap:10px;align-items:center}.dot{width:9px;height:9px;border-radius:50%;background:var(--acc);
animation:p 1.2s infinite}@keyframes p{50%{opacity:.25}}ol.act{margin:10px 0 0;padding-left:22px;max-height:260px;overflow:auto;
font-size:13px;color:var(--mute)}ol.act li:last-child{color:var(--ink)}
.review h1{font-size:20px}.review h2{font-size:16px;margin-top:22px}.review table,table.rules{border-collapse:collapse;
width:100%;font-size:13px;margin:8px 0}.review table{display:block;overflow-x:auto}table.rules{table-layout:fixed}
table.rules td{overflow-wrap:anywhere}.review th,.review td,table.rules th,table.rules td{
border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
.v{font-weight:600;white-space:nowrap}.v.FAIL,.v.BLOCKS{color:var(--bad)}.v.pass{color:var(--good)}
details{margin:0 0 16px}summary{cursor:pointer;font-weight:600;padding:4px 0}.err{color:var(--bad)}
.who{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
.opt{display:block;border:1px solid var(--line);border-radius:12px;padding:12px 14px;cursor:pointer;background:var(--card)}
.opt input{margin-right:8px}.opt.on{border-color:var(--acc);box-shadow:0 0 0 2px color-mix(in srgb,var(--acc) 25%,transparent)}
.opt.off{opacity:.55;cursor:not-allowed}.opt small{display:block;color:var(--mute);margin-top:4px}
.drop{border:1.5px dashed var(--line);border-radius:12px;padding:18px;text-align:center;color:var(--mute)}
.drop.on{border-color:var(--acc);color:var(--ink)}button{font:inherit;font-weight:600;border:0;border-radius:10px;
padding:10px 18px;background:var(--acc);color:#fff;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}
button.ghost{background:none;color:var(--acc);padding:4px 6px}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.tabs{display:flex;gap:6px;margin:0 0 12px}.tabs button{background:none;color:var(--mute);border:1px solid var(--line)}
.tabs button.on{color:var(--ink);border-color:var(--acc)}.ex{display:grid;gap:8px}
ul.subs{list-style:none;margin:0;padding:0}ul.subs li{border-bottom:1px solid var(--line);padding:8px 0}
.tag{display:inline-block;font-size:12px;border-radius:999px;padding:1px 8px;margin-left:6px}
"""

DECISION_NOTE = ("Decided from the server's grade and the rule check alone; the review explains the decision and "
                 "never changes it.")

STEP_TITLES = (("submitted", "Submitted"), ("graded", "Graded"), ("checked", "Rules checked"), ("reviewed", "Reviewed"))


def _steps(data: dict) -> dict:
    sub = data.get("submission") or {}
    graded = sub.get("status") in ("eligible", "ineligible")
    st = {"submitted": "done", "graded": "done" if graded else ("fail" if sub.get("status") == "grading_failed" else "active")}
    st["checked"] = "done" if data.get("decision") else ("active" if graded else "")
    rs = data.get("status")
    st["reviewed"] = ("done" if rs == "done" else "fail" if rs == "failed" else
                      "active" if rs == "reviewing" else "")
    return st


def _decision_block(data: dict) -> str:
    d = data.get("decision")
    if not d:
        return ""
    reasons = "".join(f"<li>{_e(r)}</li>" for r in data.get("reasons") or [])
    not_run = data.get("not_run") or []
    nr = (f'<div class="note">Not run on this server: {_e(", ".join(not_run))}. These say nothing about the design; '
          "the official server runs every stage.</div>") if not_run else ""
    return (f'<section class="decision {_e(d)}" id="decision"><div class="w">{_e(data.get("decision_words") or d)}</div>'
            + (f"<ul>{reasons}</ul>" if reasons else "") + nr + f'<div class="note">{_e(DECISION_NOTE)}</div></section>')


def _files_block(data: dict) -> str:
    files = data.get("files") or []
    if not files:
        return ""
    items = "".join(f'<li><a href="{_e(f["url"])}" download>{_e(f["name"])}</a> <span class="muted">· {_e(f["about"])}'
                    "</span></li>" for f in files)
    return f'<section class="card" id="files"><h2>To download</h2><ul class="subs">{items}</ul></section>'


def _rules_table(rc: dict | None) -> str:
    if not rc or not rc.get("rules"):
        return ""
    sets = rc.get("rule_sets") or {}
    head = f"the board's set {sets.get('board')}" + (f" and the entry's own {sets['declared']}" if sets.get("declared") else "")
    rows = []
    for r in rc["rules"]:
        rv = str(r.get("review") or "")
        cls = "FAIL" if rv == "FAIL" else "BLOCKS" if rv.startswith("BLOCKS") else "pass" if rv.startswith("pass") else ""
        ev = " · ".join([*(r.get("evidence") or [])[:2], *(r.get("notes") or [])[:2]])
        rows.append(f'<tr><td><a href="{_e(r.get("url") or "")}" target="_blank" rel="noopener">{_e(r.get("id"))}</a></td>'
                    f'<td>{_e(r.get("title") or "")}</td><td>{_e(r.get("grader") or "")}</td>'
                    f'<td class="v {cls}">{_e(rv)}</td><td class="muted">{_e(ev[:400])}</td></tr>')
    opened = " open" if rc.get("open", True) else ""
    return (f'<details id="rules"{opened}><summary>Every rule ({len(rc["rules"])}, '
            f'{_e(head)})</summary><p class="muted">{_e(rc.get("verdict") or "")}</p><table class="rules">'
            '<colgroup><col style="width:7%"><col style="width:25%"><col style="width:17%"><col style="width:13%">'
            '<col style="width:38%"></colgroup><thead><tr>'
            "<th>Rule</th><th>Title</th><th>Grader's rules stage</th><th>Review</th><th>Evidence / notes</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></details>")


def _usage(u: dict) -> str:
    bits = []
    if u.get("tokens"):
        bits.append(f"{u['tokens']:,} tokens")
    if u.get("seconds"):
        bits.append(f"{round(u['seconds'] / 60, 1)} min")
    if u.get("num_turns"):
        bits.append(f"{u['num_turns']} turns")
    if u.get("total_cost_usd"):
        bits.append(f"${u['total_cost_usd']:.2f}")
    return " · ".join(bits)


def review_page(data: dict, nonce: str) -> str:
    """The review of one submission, live until it is done."""
    sub = data.get("submission") or {}
    title = sub.get("display_name") or sub.get("submission_id")
    steps = _steps(data)
    li = "".join(f'<li class="{steps.get(k, "")}" id="step-{k}"><b>{t}</b><span>{_step_note(k, data)}</span></li>'
                 for k, t in STEP_TITLES)
    no_agent = data.get("reviewer") == "none"
    who = ("no agent (the decision and every rule's verdict only)" if no_agent else
           f"{_e(data.get('reviewer_title') or '')}" + (f" · {_e(data['model'])}" if data.get("model") else ""))
    running = data.get("status") not in ("done", "failed")
    acts = data.get("progress") or []
    act_list = "".join(f"<li>{_e(a.get('text', ''))}</li>" for a in acts[-14:])
    live = (f'<section class="card" id="live"><div class="live"><span class="dot"></span><b id="live-line">'
            f'{_e(_live_line(data))}</b></div><ol class="act" id="act">{act_list}</ol></section>') if running else ""
    review = ""
    if data.get("review_md"):
        review = f'<section class="card review" id="review">{md_html(data["review_md"])}</section>'
    elif data.get("status") == "failed":
        review = (f'<section class="card"><b class="err">The review did not finish.</b> <span>{_e(data.get("error") or "")}'
                  "</span><p class=\"muted\">The decision above stands: it never depended on the review.</p></section>")
    rc = data.get("rule_check")
    rules = _rules_table({**rc, "open": not data.get("review_md")} if rc else None)
    others = "".join(f'<li><a href="?id={_e(r["review_id"])}">{_e(r["reviewer"])}</a> · {_e(r["status"])}'
                     f'{" · " + _e(r["decision"]) if r.get("decision") else ""}</li>'
                     for r in data.get("reviews") or [] if r["review_id"] != data.get("review_id"))
    all_acts = "".join(f"<li>{_e(a.get('text', ''))}</li>" for a in acts)
    details = (f'<details><summary>Details</summary><p>Reviewer: {who}<br>Review {_e(data.get("review_id"))} of submission '
               f'<code>{_e(sub.get("submission_id"))}</code> on <code>{_e(sub.get("task"))}</code>'
               + (f"<br>{_e(_usage(data.get('usage') or {}))}" if _usage(data.get("usage") or {}) else "")
               + "</p>" + (f"<p>Other reviews of this submission:</p><ul>{others}</ul>" if others else "")
               + (f'<p>Activity:</p><ol class="act">{all_acts}</ol>' if all_acts and not running else "")
               + "</details>")
    poll = ""
    if running or sub.get("status") not in ("eligible", "ineligible", "grading_failed"):
        poll = (f'<script nonce="{_e(nonce)}">(function(){{var url={json.dumps(_poll_url(data))},'
                f'seen={json.dumps([data.get("status"), data.get("decision"), sub.get("status")])};'
                "function esc(s){var d=document.createElement('span');d.textContent=s;return d.innerHTML;}"
                "function tick(){fetch(url,{headers:{'Accept':'application/json'}}).then(function(r){return r.json();})"
                ".then(function(d){var now=[d.status,d.decision,(d.submission||{}).status];"
                "if(JSON.stringify(now)!==JSON.stringify(seen)){location.reload();return;}"
                "var a=d.progress||[],ol=document.getElementById('act'),ln=document.getElementById('live-line');"
                "if(ol){ol.innerHTML=a.slice(-14).map(function(x){return '<li>'+esc(x.text||'')+'</li>';}).join('');"
                "ol.scrollTop=ol.scrollHeight;}if(ln&&a.length)ln.textContent=a[a.length-1].text;"
                "setTimeout(tick,2500);}).catch(function(){setTimeout(tick,5000);});}setTimeout(tick,2500);})();</script>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            f'initial-scale=1"><title>Review · {_e(title or "")}</title><style>{CSS}</style></head><body><main>'
            f'<h1>{_e(title or "Submission")}</h1>'
            f'<p class="sub">{_e(data.get("board_title") or "")} <code>{_e(sub.get("task") or "")}</code> · reviewed by '
            f'{who} · <a href="/review">Submit another design</a></p>'
            f'<ol class="steps">{li}</ol>{_decision_block(data)}{live}{_files_block(data)}{review}{rules}{details}'
            f'</main>{poll}</body></html>')


def _poll_url(data: dict) -> str:
    return f"/v1/submissions/{(data.get('submission') or {}).get('submission_id')}/review?id={data.get('review_id')}&brief=1"


def _step_note(k: str, data: dict) -> str:
    sub = data.get("submission") or {}
    rep = sub.get("report") or {}
    if k == "submitted":
        return "the bundle, stored as uploaded"
    if k == "graded":
        if sub.get("status") == "grading_failed":
            return "the grader could not grade it"
        if rep:
            return "eligible" if rep.get("eligible") else "not eligible"
        return "the reference evaluator is grading it…"
    if k == "checked":
        return "every rule, by the skill's script" if data.get("decision") else ("next" if not rep else "running…")
    if data.get("reviewer") == "none":
        return "no agent was asked"
    if data.get("status") == "done":
        return f"by {data.get('reviewer_title')}"
    if data.get("status") == "failed":
        return "did not finish"
    return "reading the rule check…" if data.get("status") == "reviewing" else "after the rule check"


def _live_line(data: dict) -> str:
    acts = data.get("progress") or []
    if acts:
        return acts[-1].get("text", "")
    sub = data.get("submission") or {}
    if sub.get("status") not in ("eligible", "ineligible"):
        return "Waiting for the grader…"
    return "Checking every rule…"


def console_page(*, boards: list[dict], reviewers: dict, examples: dict, recent: list[dict], dev: bool,
                 nonce: str) -> str:
    """Submit a design for review: a bundle (or an example, on a development server) and a reviewer."""
    rv = reviewers.get("reviewers") or []
    first = next((r["id"] for r in rv if r.get("available")), "none")
    opts = []
    for r in rv:
        ok = bool(r.get("available"))
        if ok:
            state = f"signed in for reviews · {_e(r.get('account') or '')}" if r.get("mode") == "local" else \
                _e(r.get("account") or "")
        elif r.get("signin") and "sign" in str(r.get("why") or ""):
            state = f"Not signed in for reviews yet. In a terminal:<br><code>{_e(r['signin'])}</code>"
        else:
            state = _e(r.get("why") or "not available")
        opts.append(f'<label class="opt{" on" if r["id"] == first else ""}{"" if ok else " off"}" data-id="{_e(r["id"])}">'
                    f'<input type="radio" name="rv" value="{_e(r["id"])}"{" checked" if r["id"] == first else ""}'
                    f'{"" if ok else " disabled"}><b>{_e(r["title"])}</b> <span class="muted">{_e(r.get("model") or "")}'
                    f' · {_e(r.get("cli") or "")}</span><small>{state}</small></label>')
    opts.append(f'<label class="opt{" on" if first == "none" else ""}" data-id="none"><input type="radio" name="rv" '
                f'value="none"{" checked" if first == "none" else ""}><b>No agent</b><small>The decision and every '
                "rule's verdict, without a written review: no model is used.</small></label>")
    ex = "".join(f'<label class="opt" data-ex="{_e(k)}"><input type="radio" name="ex" value="{_e(k)}"><b>{_e(v["title"])}</b>'
                 f'<small>{_e(v["about"])} · <code>{_e(v["board"])}</code></small></label>' for k, v in examples.items())
    from .review import DECISION_WORDS

    def tag(r: dict) -> str:
        d = r.get("decision")
        said = DECISION_WORDS.get(d) if d else {"queued": "Waiting for the grade", "checking": "Checking the rules",
                                                 "reviewing": "Reviewing", "failed": "Review failed"}.get(r.get("status"), "")
        return f'<span class="tag {_e(d or "")}">{_e(said or "")}</span>'
    recent_li = "".join(
        f'<li><a href="/review/{_e(s["submission_id"])}">{_e(s.get("display_name") or s["submission_id"])}</a> '
        f'<span class="muted">· {_e(s.get("task"))}</span>' + (tag(s["review"]) if s.get("review") else "") + "</li>"
        for s in recent)
    where = ("Local development server. Reviews run on this computer, with your own Claude Code or Codex sign-in."
             if dev else "Reviews run on this server.")
    tabs = ('<div class="tabs"><button type="button" class="on" data-tab="upload">Upload a bundle</button>'
            + ('<button type="button" data-tab="example">Use an example</button>' if dev and examples else "") + "</div>")
    script = (
        f'<script nonce="{_e(nonce)}">(function(){{var tab="upload",file=null;function $(s){{return document.querySelector(s);}}'
        "function all(s){return Array.prototype.slice.call(document.querySelectorAll(s));}"
        "all('.tabs button').forEach(function(b){b.addEventListener('click',function(){tab=b.dataset.tab;"
        "all('.tabs button').forEach(function(x){x.classList.toggle('on',x===b);});"
        "$('#upload').hidden=tab!=='upload';var e=$('#example');if(e)e.hidden=tab!=='example';ready();});});"
        "all('.opt').forEach(function(o){o.addEventListener('click',function(){var i=o.querySelector('input');"
        "if(!i||i.disabled)return;i.checked=true;all('.opt').forEach(function(x){var j=x.querySelector('input');"
        "if(j&&j.name===i.name)x.classList.toggle('on',x===o);});ready();});});"
        "var d=$('#drop'),inp=$('#file');d.addEventListener('click',function(){inp.click();});"
        "inp.addEventListener('change',function(){pick(inp.files[0]);});"
        "['dragenter','dragover'].forEach(function(n){d.addEventListener(n,function(ev){ev.preventDefault();d.classList.add('on');});});"
        "['dragleave','drop'].forEach(function(n){d.addEventListener(n,function(ev){ev.preventDefault();d.classList.remove('on');});});"
        "d.addEventListener('drop',function(ev){pick(ev.dataTransfer.files[0]);});"
        "function pick(f){file=f||null;$('#fname').textContent=f?f.name+' ('+Math.round(f.size/1024)+' KB)':'';ready();}"
        "function rv(){var i=$('input[name=rv]:checked');return i?i.value:'';}"
        "function exv(){var i=$('input[name=ex]:checked');return i?i.value:'';}"
        "function ready(){$('#go').disabled=!rv()||(tab==='upload'?!file:!exv());}"
        "$('#go').addEventListener('click',function(){var b=$('#go'),msg=$('#msg');b.disabled=true;msg.textContent='Submitting…';"
        "var h={'X-QCCD-Console':'1','X-QCCD-Reviewer':rv()},body;"
        "if(tab==='upload'){h['Content-Type']='application/zip';body=file;}"
        "else{h['Content-Type']='application/json';body=JSON.stringify({example:exv()});}"
        "fetch('/review/submit',{method:'POST',headers:h,body:body}).then(function(r){return r.json().then(function(j){return [r.ok,j];});})"
        ".then(function(x){if(!x[0]){msg.textContent='Not submitted: '+((x[1].error||{}).message||'error');ready();return;}"
        "location.href=x[1].page;}).catch(function(e){msg.textContent='Not submitted: '+e;ready();});});ready();})();</script>")
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
        f'initial-scale=1"><title>Submit for review</title><style>{CSS}</style></head><body><main>'
        f'<h1>Submit a design for review</h1><p class="sub">{_e(where)} The server grades it, decides from the grade and '
        "a check of every rule, and the agent explains the decision.</p>"
        f'<section class="card"><h2>1 · The design</h2>{tabs}<div id="upload"><div class="drop" id="drop">Drop a submission '
        'bundle (<code>bundle.zip</code>) here, or click to choose one<div id="fname"></div></div>'
        '<input type="file" id="file" accept=".zip,application/zip" hidden></div>'
        + (f'<div id="example" class="ex" hidden>{ex}</div>' if dev and examples else "") + "</section>"
        f'<section class="card"><h2>2 · Who reviews it</h2><div class="who">{"".join(opts)}</div>'
        '<p class="muted" style="margin:10px 0 0">The decision never depends on the reviewer: only the explanation does. '
        '<a href="/review">Refresh</a> after signing in.</p></section>'
        '<div class="row"><button id="go" disabled>Submit for review</button><span id="msg" class="muted"></span></div>'
        + (f'<section class="card" style="margin-top:22px"><h2>Your submissions</h2><ul class="subs">{recent_li}</ul></section>'
           if recent_li else "")
        + f"</main>{script}</body></html>")
