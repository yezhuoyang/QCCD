// Which functions differ between two llvm-objdump -d listings? (addresses stripped)
// usage: node fndiff.mjs <a.dis> <b.dis>
import fs from 'node:fs';
function parse(f) {
  const fns = new Map(); let cur = null, body = [];
  for (const line of fs.readFileSync(f, 'utf8').split('\n')) {
    const m = /^[0-9a-f]+ <(.+)>:$/.exec(line);
    if (m) { if (cur) fns.set(cur, body.join('\n')); cur = m[1]; body = []; continue; }
    // drop the address and raw bytes; keep the instruction text
    const ins = line.replace(/^\s*[0-9a-f]+:\s+(?:[0-9a-f]{2} )+\s*/, '').trim();
    if (ins) body.push(ins.replace(/\s+/g, ' '));
  }
  if (cur) fns.set(cur, body.join('\n'));
  return fns;
}
const [a, b] = process.argv.slice(2).map(parse);
let same = 0; const diff = [];
for (const [k, v] of a) { if (!b.has(k)) diff.push(['only in A', k]); else if (b.get(k) === v) same++; else diff.push(['differs', k, v.split('\n').length, b.get(k).split('\n').length]); }
for (const k of b.keys()) if (!a.has(k)) diff.push(['only in B', k]);
console.log(`functions: A ${a.size}, B ${b.size}, identical ${same}, different ${diff.length}`);
for (const d of diff) console.log(' ', d.join(' | '));
