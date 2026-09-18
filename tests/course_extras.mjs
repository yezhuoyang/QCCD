// Drive every challenge and boundary in the course to the point where its check runs.
//
//   node extras_drive.mjs <page.html> <answers.json>
//
// The star a challenge awards is only reachable when the lesson is `passed` AND `shown` is
// false -- `lessonSolution` sets `shown`, which is why no test has ever reached one.  So
// the stages are solved by applying their own sources directly, exactly as a reader who
// works them out does, and the extra's answer is applied on top.
import fs from 'fs';
import { loadPage } from './shim.mjs';

const page = process.argv[2];
const ANSWERS = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

loadPage(page, ';globalThis.__E=EDITOR;');
const E = globalThis.__E;

const out = [];
for (const a of ANSWERS) {
  const rec = { lesson: a.lesson, kind: a.kind };
  try {
    const load = E.lessonLoad(a.lesson);
    if (!(load && load.ok !== false)) { rec.error = 'lessonLoad refused'; out.push(rec); continue; }

    // solve each stage with its own source until the lesson is passed
    let guard = 0;
    while (!E.lessonState().passed && guard++ < 8) {
      const src = (a.stages || [])[Math.min(E.lessonState().stage, (a.stages || []).length - 1)];
      if (src === undefined) break;
      const ap = E.applyProgramSource(src);
      if (!(ap && ap.ok)) { rec.stage_error = (ap && (ap.errors || ap.problems) || [])[0] || 'refused'; break; }
      E.lessonCheck();
    }
    const mid = E.lessonState();
    rec.passed = mid.passed; rec.shown = mid.shown; rec.stars_before = mid.stars;
    if (!mid.passed) { rec.error = rec.error || 'the stages were not solved'; out.push(rec); continue; }

    // now the extra
    const ap = E.applyProgramSource(a.answer);
    if (!(ap && ap.ok)) {
      rec.error = 'the answer did not lower: '
        + JSON.stringify((ap && (ap.errors || ap.problems) || [])[0] || null);
      out.push(rec); continue;
    }
    const c = E.lessonCheck();
    const st = E.lessonState();
    rec.ok = !!(c && c.ok);
    rec.stars_after = st.stars;
    rec.feedback = (st.feedback || {}).text;
  } catch (e) {
    rec.threw = String((e && e.message) || e);
  }
  out.push(rec);
}
console.log(JSON.stringify(out, null, 1));
