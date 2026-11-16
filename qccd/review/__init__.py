"""An agent review of a programme, on the person's own computer: the Debug page's "Run agent review".

The person opens a programme in the Debug page (or its panel beside their design in the Studio),
signs in once with their own Claude or ChatGPT account, and presses Run.  The rule check -- the
grader's own evaluator, `qccd.debug` -- judges every rule first, and its verdicts are the only
ones; then their agent (Claude Code or Codex) reads the review guide, steps through the programme
with the same debugger, and writes what fails, where, and how to fix it.  It runs in Docker, in a
container that sees only the review's folder and reaches only its own vendor's API, and the
review's page becomes its report when it is done.

    agents.py      the two accounts: what each runs, its sign-in (the CLI's own, in the image)
    container.py   Docker: the image (image/), the review's network and egress proxy, a sign-in's terminal
    engine.py      one review: stage the folder, check every rule, run the agent, keep its report

The workspace runs a review as a job (qccd/workspace/reviews.py) and serves its page
(`/reviewview/<id>`); docs/agent-review.md is the whole story.
"""
