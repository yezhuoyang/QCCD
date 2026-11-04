# qccd-design-review: an agent skill for reviewing a design against every rule

This folder is a skill: instructions plus scripts that an agent (Claude Code, Codex, Cursor) loads
when someone asks it to review a QCCD design. Agents read `SKILL.md`. This file is for the people
who maintain it.

## Using it

- **Claude Code** finds it automatically in this repository (`.claude/skills/`). Ask for a review
  ("review this bundle for the surface-code memory board", "does my device pass the rules?",
  "check my floorplan before I schedule on it"), or type `/qccd-design-review`.
- **Codex** looks in `.agents/skills/`, and **Cursor** in `.cursor/skills/`. Link the folder there:
  `mkdir -p .agents/skills && ln -s ../../.claude/skills/qccd-design-review .agents/skills/`.
- **Without an agent:** `python .claude/skills/qccd-design-review/scripts/review.py --help`.

## How it stays current with the rules

The rules are not copied into this skill by hand. They are generated from the code:

| Source | Generated into |
|---|---|
| `qccd/ruleset/library/*.rules` (the text the website's Rules page prints) | `references/rules.md`, `rule-sets.md`, `machine-rules.md` |
| `qccd/ruleset/vocabulary.py` (each word's group and where it can be read) | `references/vocabulary.md`, and each rule's "Reads" and "Judged on" in `rules.md` |
| `qccd/workspace/releases/*/release.json`, `goals/goal3/families.py` | `references/boards.md` |
| `qccd/workspace/evaluator.py`, `qccd/verify/` (read by running them) | `references/grader.md` |
| `qccd/gadget/judge.py`, `qccd/gadget/place_checks.py` | `references/machine-rules.md` |

`references/MANIFEST.json` records a digest of every generated file, of every rule set (as
`RuleSet.text()` writes it) and of every board release. Five things keep the skill in step:

1. **CI.** `.github/workflows/skills.yml` runs on every push and pull request that touches
   `qccd/`, `goals/goal3/families.py`, `examples/gadgets/` or this skill. It fails when the
   references no longer match the code, and it runs `tests/test_design_review_skill.py`.
2. **The test.** `tests/test_design_review_skill.py` runs with the rest of the suite, so a local
   `pytest` catches staleness too.
3. **The hand-written guide must name every rule.** The same test fails when a rule of any
   shipped set (device or machine) has no row in `references/review-guide.md`, so a new rule
   cannot arrive without someone saying what its failure means and how to fix it.
4. **The skill checks itself.** Step 0 of `SKILL.md` has the agent run
   `sync_references.py --check` before every review, and regenerate if needed.
5. **Verdicts never come from the references.** `scripts/review.py` calls the repository's live
   checkers (the grader's rules stage, the rule language, the full `grade`, `qccd.gadget.judge`).
   Even what a device alone decides is read off each rule's words, not a list. A stale reference
   could only mislead an explanation, never a verdict.

The deployed website can run ahead of, or behind, a checkout.
`sync_references.py --live` compares this checkout's rules with https://qccd.academy/rules/:
every rule (title, meaning, assertion text) and every rule set (its rules and parameters).

## When you change a rule, a rule set, a board, or the grader's policy

```bash
python .claude/skills/qccd-design-review/scripts/sync_references.py --write
git add .claude/skills/qccd-design-review/references
```

Commit the regenerated references in the same commit. A new rule also needs a row in the
hand-written `references/review-guide.md` (section 2, or 2b for a machine rule): what its failure
usually means, and the fix. The test names the rule until it has one. If the change is a new
kind of judgement (not a new rule), update the guide's other sections as well.

## Layout

```
SKILL.md                  the workflow the agent follows (loaded when the skill triggers)
scripts/review.py         runs every check and lays out each rule's verdict (rule-check.md, rule-check.json):
                          a device, a programme, a bundle, a report, or a floorplan and an algorithm on it
scripts/sync_references.py  regenerates references/ from the code; --check; --live
references/*.md           generated (except review-guide.md); loaded by the agent when needed
references/review-guide.md  hand-written: causes and fixes, what checkers cannot see
evals/evals.json          the test prompts the skill was developed against
```
