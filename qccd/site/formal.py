"""The furniture of the formal Learn pages: definition, example, figure and table blocks.

The Machine page writes its definitions, examples, figures and tables with these, in one
style (`CSS`).  They were born on the old Algorithm section of the Compilation page, which
QCCDCert's page (qccd/site/certpage.py) replaced.
"""

from __future__ import annotations

import html

CSS = """
.algo p{max-width:78ch}
.algo .df{margin:14px 0 10px;padding:12px 16px;border-left:3px solid #1c2a4a;background:var(--card);max-width:100ch}
.algo .df:has(pre.alg){max-width:none}
.algo .df>b:first-child,.algo .eg>b:first-child{font-family:var(--sans);font-size:12px;letter-spacing:.06em;text-transform:uppercase;margin-right:8px}
.algo .df>b:first-child{color:#1c2a4a} .algo .df p{margin:6px 0 0} .algo .df .words{margin-top:8px;color:var(--ink2);font-size:14px}
.algo .eg{margin:8px 0 18px 0;padding:10px 16px 12px;border-left:3px solid #0b7a4b;background:#f4faf6;max-width:none;font-size:14.5px}
.algo .eg>b:first-child{color:#0b7a4b} .algo .eg p{margin:6px 0 0;max-width:78ch} .algo .eg table{margin:8px 0 2px}
.algo .eg pre{margin:8px 0 4px}
.algo .eq{font-family:var(--serif);font-size:16.5px;text-align:center;margin:12px auto;max-width:78ch;line-height:1.7}
.algo var,.algo i{font-style:italic}
.algo pre.alg{font-size:12.5px;line-height:1.65;background:#faf9f5;padding:12px 16px;border:1px solid var(--line);border-radius:10px;white-space:pre;overflow-x:auto}
.algo pre.alg b{color:#1c2a4a}
.algo .notes{font-size:12px;background:#f7f6f2;padding:8px 10px;margin:6px 0 10px;border-left:3px solid #1c2a4a}
.algo .notes .lab{font-family:var(--sans);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;font-weight:700;color:var(--ink3);display:block;margin:0 0 4px}
.algo pre.ex{font-size:11.5px;background:#f7f6f2;padding:8px 10px;margin:6px 0 10px}
.algo table.sm{font-size:12.5px;width:auto;margin:6px 0 10px} .algo table.sm td,.algo table.sm th{padding:3px 14px 3px 0;white-space:nowrap}
.algo table.sm td:first-child{color:var(--ink3)} .algo table.sm code{font-size:12px} .algo table.sm .pick{font-weight:700;color:#0b7a4b} .algo table.sm .out{color:#c62828}
.algo figure.dg{margin:10px 0 8px} .algo figure.dg figcaption{font-size:12.5px;color:var(--ink2);margin-top:6px;line-height:1.45;max-width:90ch}
.algo figure.dg .figsvg{padding:10px 12px} .algo figure.dg svg{display:block;width:auto;max-width:100%;height:auto;margin:0 auto}
.algo .strip{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start;margin:8px 0} .algo .strip figure{flex:1 1 180px;margin:0;max-width:260px}
.algo .strip .figsvg{padding:8px} .algo .strip figcaption{font-size:12px;color:var(--ink2);margin-top:4px;line-height:1.4}
.algo .strip figure.wide{flex:1 1 100%;max-width:560px}
.algo .wide svg.qc{max-width:100%}
.algo table.glance{font-size:12.5px} .algo table.glance td:first-child{white-space:nowrap;font-weight:600;color:#1c2a4a}
.algo ul.refs{font-size:12.5px;color:var(--ink2);padding-left:18px;max-width:78ch} .algo ul.refs li{margin:0 0 3px}
.algo ol.pf{max-width:78ch;padding-left:22px} .algo ol.pf li{margin:0 0 6px}
.algo .guide{background:var(--soft);border:1px solid #d6e2f4;border-radius:10px;padding:10px 16px;max-width:100ch;font-size:14.5px}
.algo table.notation{width:100%;font-size:13px} .algo table.notation td,.algo table.notation th{white-space:normal;vertical-align:top;padding:5px 12px 5px 0}
.algo table.notation td:first-child{font-family:var(--serif);font-size:15px;color:#1c2a4a;white-space:nowrap} .algo table.notation td:nth-child(2){color:var(--ink2)}
.algo table.notation th{font-family:var(--sans);font-size:12px;color:var(--ink3)} .algo .notecap{font-size:14px;margin:6px 0 14px}
.algo .strip figure{flex:1 1 220px;max-width:340px}
"""


def df(kind: str, name: str, body: str, words: str = "", anchor: str | None = None) -> str:
    w = f'<p class="words"><b>In words.</b> {words}</p>' if words else ""
    i = f' id="{anchor}"' if anchor else ""
    return f'<div class="df"{i}><b>{kind}</b> <i>{html.escape(name)}.</i> {body}{w}</div>'


def eg(body: str, label: str = "Example") -> str:
    return f'<div class="eg"><b>{label}</b> {body}</div>'


def fig(svg: str, caption: str) -> str:
    return f'<figure class="dg"><div class="figsvg">{svg}</div><figcaption>{caption}</figcaption></figure>'


def tbl(head: list[str], rows: list[list[str]]) -> str:
    h = "".join(f"<th>{c}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="sm"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>'


def esc(s) -> str:
    return html.escape(str(s))
