// The languages of the program columns (qccd/gadget/web/langs.js), checked on real programs:
//     node tests/gadget_langs.mjs <lang>=<file> ...
//
// For every line of every file:
//   changed   lines whose text, with the colouring taken off, is not the line as written
//             (the colouring may only ever add markup)
//   unnoted   words the page marks as having a note (`data-w`) whose note is missing
//   tags      how many statement tags each language drew, by word (a language whose
//             statement words were never tagged would pass the two checks above)
// and for every word of every table: a title and a note.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const L = createRequire(import.meta.url)(path.join(here, "..", "qccd", "gadget", "web", "langs.js"));
const un = (h) => h.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");

const out = { files: {}, changed: [], unnoted: [], tables: [] };
for (const arg of process.argv.slice(2)) {
  const [lang, file] = [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)];
  let text = readFileSync(file, "utf8");
  // a module handed to the page as a script: the text inside it
  const m = /__gadgetModule\((".*?"),(".*")\);\s*$/s.exec(text);
  if (m) text = JSON.parse(m[2]);
  const lines = text.split(/\r?\n/);
  const calls = L.defined(lang, lines);
  const tags = {};
  lines.forEach((s, i) => {
    const h = L.paint(lang, s, { calls });
    if (un(h) !== s) out.changed.push({ file: path.basename(file), line: i + 1, text: s.slice(0, 120), got: un(h).slice(0, 120) });
    for (const w of h.matchAll(/data-w="([^"]*)"/g)) {
      const word = w[1].replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
      const n = L.note(lang, word, calls);
      if (!n || !n[0] || !n[1]) out.unnoted.push({ file: path.basename(file), line: i + 1, word });
    }
    for (const t of h.matchAll(/class="tg t-[a-z]+" data-w="([^"]*)"/g)) tags[t[1]] = (tags[t[1]] || 0) + 1;
  });
  out.files[`${lang}:${path.basename(file)}`] = { lines: lines.length, tags };
}
for (const [lang, def] of Object.entries(L.LANGS)) {
  for (const [w, e] of Object.entries(def.words)) if (!(e[1] && e[2])) out.tables.push(`${lang}:${w}`);
}
console.log(JSON.stringify(out));
