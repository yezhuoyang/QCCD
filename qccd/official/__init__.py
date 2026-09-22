"""The official submission service: independent grading of submitted artifacts.

    api.py        HTTP: task discovery, authenticated upload (returns an id at once),
                  status, reports, leaderboards
    db.py         SQLite (development, tests) or PostgreSQL (deployment) behind one small layer
    queue.py      the database-backed job queue: claim, heartbeat, stale-lock recovery
    worker.py     moves queued bundles to a grader and records reports
    grader.py     the grader: consumes a spool directory and runs the reference evaluator
                  on each bundle in a resource-limited subprocess -- with no network and no
                  credentials (in the Compose deployment it is its own container with
                  `network_mode: none`, a read-only root file system and a tmpfs)

The server trusts nothing a client computed: it resolves the task from ITS OWN release
directory, recomputes every digest, always grades under the `reference` profile, and
never executes anything from the bundle -- a bundle is data.
"""
