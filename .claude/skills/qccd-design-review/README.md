# qccd-design-review: an agent skill for reviewing a design against every rule

This folder is a skill: instructions plus scripts that an agent (Claude Code, Codex, Cursor) loads
when someone asks it to review a QCCD design. Agents read `SKILL.md`. This file is for the people
who maintain it.

## Using it

- **Claude Code** finds it automatically in this repository (`.claude/skills/`). Ask for a review
  ("review this bundle for the surface-code memory board", "does my device pass the rules?"), or
  type `/qccd-design-review`.
- **Codex** looks in `.agents/skills/`, and **Cursor** in `.cursor/skills/`. Link the folder there:
  `mkdir -p .agents/skills && ln -s ../../.claude/skills/qccd-design-review .agents/skills/`.
- **Without an agent:** `python .claude/skills/qccd-design-review/scripts/review.py --help`.

## How it stays current with the rules

The rules are not copied into this skill by hand. They are generated from the code:

| Source | Generated into |
|---|---|
| `qccd/ruleset/library/*.rules` (the text the website's Rules page prints) | `references/rules.md`, `rule-sets.md`, `machine-rules.md` |
| `qccd/ruleset/vocabulary.py` | `references/vocabulary.md` |
| `qccd/workspace/releases/*/release.json` | `references/boards.md` |
| `qccd/workspace/evaluator.py`, `qccd/verify/` (read by running them) | `references/grader.md` |

`references/MANIFEST.json` records a digest of every generated file, of every rule set (as
`RuleSet.text()` writes it) and of every board release. Four things keep the skill in step:

1. **CI.** `.github/workflows/skills.yml` runs on every push and pull request that touches
   `qccd/` or this skill. It fails when the references no longer match the code, and it runs
   `tests/test_design_review_skill.py`.
2. **The test.** `tests/test_design_review_skill.py` runs with the rest of the suite, so a local
   `pytest` catches staleness too.
3. **The skill checks itself.** Step 0 of `SKILL.md` has the agent run
   `sync_references.py --check` before every review, and regenerate if needed.
4. **Verdicts never come from the references.** `scripts/review.py` calls the repository's live
   checkers (the grader's rules stage, the rule language, the full `grade`). A stale reference
   could only mislead an explanation, never a verdict.

The deployed website can run ahead of, or behind, a checkout.
`sync_references.py --live` compares this checkout's rules with https://qccd.academy/rules/
rule by rule (title, meaning, assertion text).

## When you change a rule, a rule set, a board, or the grader's policy

```bash
python .claude/skills/qccd-design-review/scripts/sync_references.py --write
git add .claude/skills/qccd-design-review/references
```

Commit the regenerated references in the same commit. If the change is a new kind of judgement
(not a new rule), update the hand-written `references/review-guide.md` as well.

## Layout

```
SKILL.md                  the workflow the agent follows (loaded when the skill triggers)
scripts/review.py         runs every check and lays out each rule's verdict (rule-check.md, rule-check.json)
scripts/sync_references.py  regenerates references/ from the code; --check; --live
references/*.md           generated (except review-guide.md); loaded by the agent when needed
references/review-guide.md  hand-written: causes and fixes, what checkers cannot see
evals/evals.json          the test prompts the skill was developed against
```
