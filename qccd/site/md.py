"""The reference docs, Markdown to HTML, with the studio's hover vocabulary.

Enough Markdown for `docs/*.md` and no more: headings, paragraphs, fenced code, inline
code, bold, italic, links, tables, lists and quotes.  A rule id in prose (`R4d`) and the
first mention of each element the studio names (`site`, `junction`, `rail`, ...) become a
span whose hover card is the studio's own sentence for it -- the HINTS and RULE_HINTS
tables are read out of `editor.js` at build time, so the docs cannot drift from the tool.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

EDITOR_JS = Path(__file__).resolve().parents[1] / "viz" / "js" / "editor.js"

#: What each `el:*` hint is called in prose, longest alias first.
ALIASES = {
    "el:site": ("trapping site", "site"), "el:junction": ("junction",),
    "el:segment": ("segment", "rail"), "el:loop": ("transport loop", "loop"),
    "el:zone_type": ("zone type", "zone"), "el:curve_point": ("curve point",),
    "el:primitives": ("primitive",), "el:control": ("control plane",),
    "el:heating": ("heating",), "el:species": ("species",), "el:budget": ("budget",),
    "el:component": ("component",),
}


def _js_str(s: str) -> str:
    """A single-quoted JS string literal's contents, decoded."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)),
                  s.replace("\\'", "'").replace("\\\\", "\\"))


def hints() -> dict[str, dict]:
    """`{key: {t, d}}` for every `el:*` entry of HINTS and every rule of RULE_HINTS."""
    src = EDITOR_JS.read_text(encoding="utf-8")
    out: dict[str, dict] = {}
    for m in re.finditer(r"'(el:\w+)':\s*\{\s*t:\s*'((?:[^'\\]|\\.)*)',\s*d:\s*'((?:[^'\\]|\\.)*)'", src):
        out[m.group(1)] = {"t": _js_str(m.group(2)), "d": _js_str(m.group(3))}
    # a tree whose editor.js predates the tables renders the docs without hover cards
    if "var RULE_HINTS = {" in src:
        block = src[src.index("var RULE_HINTS = {"):]
        block = block[: block.index("};")]
        for m in re.finditer(r"^\s*(R\d+[a-z]?):\s*'((?:[^'\\]|\\.)*)'", block, re.M):
            out["rule:" + m.group(1)] = {"t": "Rule " + m.group(1), "d": _js_str(m.group(2))}
    return out


def slug(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\s-]", "", html.unescape(text).lower())
    return re.sub(r"[\s]+", "-", text.strip())


class Renderer:
    def __init__(self, hint_table: dict[str, dict], link: "callable"):
        self.hints = hint_table
        self.link = link          # rewrites a Markdown link target
        self.headings: list[tuple[int, str, str]] = []
        self.seen: set[str] = set()

    # ----------------------------------------------------------------- inline
    def _span(self, key: str, word: str) -> str:
        h = self.hints[key]
        return (f'<span class="h" data-hint="{key}" data-t="{html.escape(h["t"])}" '
                f'data-d="{html.escape(h["d"])}">{word}</span>')

    def _outside_tags(self, text: str, fn) -> str:
        """Apply `fn` to the text between tags only, so a span never lands inside another
        span's attributes."""
        return "".join(p if p.startswith("<") else fn(p) for p in re.split(r"(<[^>]+>)", text))

    def _hint_prose(self, text: str) -> str:
        def rule(m):
            k = "rule:" + m.group(0)
            return self._span(k, m.group(0)) if k in self.hints else m.group(0)
        for key, aliases in ALIASES.items():
            if key in self.seen or key not in self.hints:
                continue
            for alias in aliases:
                rx = re.compile(r"(?<![\w-])" + re.escape(alias) + r"s?(?![\w-])")
                hit = [False]

                def first(p):
                    if hit[0]:
                        return p
                    m = rx.search(p)
                    if not m:
                        return p
                    hit[0] = True
                    return p[: m.start()] + self._span(key, m.group(0)) + p[m.end():]
                text = self._outside_tags(text, first)
                if hit[0]:
                    self.seen.add(key)
                    break
        return self._outside_tags(text, lambda p: re.sub(r"\bR\d{1,2}[a-d]?\b", rule, p))

    def inline(self, text: str, hint: bool = True) -> str:
        codes: list[str] = []

        def stash(m):
            codes.append(f"<code>{html.escape(m.group(1))}</code>")
            return f"\x00{len(codes) - 1}\x00"
        text = re.sub(r"`([^`]+)`", stash, text)
        text = html.escape(text, quote=False)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", text)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                      lambda m: f'<a href="{html.escape(self.link(m.group(2)))}">{m.group(1)}</a>', text)
        text = re.sub(r"(?<![\"'>])\bhttps?://[^\s<)]+", lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', text)
        if hint:
            # hint the prose only: an anchor's text and a code span are left alone
            parts = re.split(r"(<a [^>]*>.*?</a>)", text)
            text = "".join(p if p.startswith("<a ") else self._hint_prose(p) for p in parts)
        return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)

    # ------------------------------------------------------------------ block
    def render(self, src: str) -> str:
        lines = src.replace("\r\n", "\n").split("\n")
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            ln = lines[i]
            if ln.startswith("```"):
                lang = ln[3:].strip()
                j = i + 1
                while j < n and not lines[j].startswith("```"):
                    j += 1
                body = html.escape("\n".join(lines[i + 1:j]))
                cls = f' class="lang-{html.escape(lang)}"' if lang else ""
                out.append(f"<pre><code{cls}>{body}</code></pre>")
                i = j + 1
                continue
            m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", ln)
            if m:
                level, text = len(m.group(1)), self.inline(m.group(2), hint=False)
                sid = slug(text)
                self.headings.append((level, sid, text))
                out.append(f'<h{level} id="{sid}">{text}<a class="anchor" href="#{sid}">#</a></h{level}>')
                i += 1
                continue
            if ln.startswith("|") and i + 1 < n and re.match(r"^\|?\s*:?-{2,}", lines[i + 1]):
                head = [c.strip() for c in ln.strip().strip("|").split("|")]
                aligns = [("right" if c.strip().endswith(":") and not c.strip().startswith(":") else "left")
                          for c in lines[i + 1].strip().strip("|").split("|")]
                rows = []
                j = i + 2
                while j < n and lines[j].startswith("|"):
                    rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                    j += 1
                th = "".join(f'<th style="text-align:{a}">{self.inline(c)}</th>' for c, a in zip(head, aligns))
                tr = "".join("<tr>" + "".join(f'<td style="text-align:{a}">{self.inline(c)}</td>'
                                              for c, a in zip(r, aligns)) + "</tr>" for r in rows)
                out.append(f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>')
                i = j
                continue
            m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
            if m:
                tag = "ol" if m.group(2)[0].isdigit() else "ul"
                items = []
                j = i
                while j < n:
                    mm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[j])
                    if mm and len(mm.group(1)) == len(m.group(1)):
                        items.append(mm.group(3))
                    elif lines[j].strip() and (lines[j].startswith(" ") or lines[j].startswith("\t")) and items:
                        items[-1] += " " + lines[j].strip()
                    else:
                        break
                    j += 1
                out.append(f"<{tag}>" + "".join(f"<li>{self.inline(t)}</li>" for t in items) + f"</{tag}>")
                i = j
                continue
            if ln.startswith(">"):
                j = i
                quote = []
                while j < n and lines[j].startswith(">"):
                    quote.append(lines[j][1:].strip())
                    j += 1
                out.append(f"<blockquote>{self.render(chr(10).join(quote))}</blockquote>")
                i = j
                continue
            if ln.strip() in ("---", "***", "___"):
                out.append("<hr>")
                i += 1
                continue
            if not ln.strip():
                i += 1
                continue
            j = i
            para = []
            while j < n and lines[j].strip() and not re.match(r"^(#{1,6}\s|```|\||>|\s*[-*]\s|\s*\d+\.\s|---$)", lines[j]):
                para.append(lines[j].strip())
                j += 1
            if not para:          # a line the block rules above claim but did not consume
                para, j = [ln.strip()], i + 1
            out.append(f"<p>{self.inline(' '.join(para))}</p>")
            i = j
        return "\n".join(out)
