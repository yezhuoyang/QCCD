# Official submission service: development deployment

What this is: the API, the database-backed queue worker, PostgreSQL, and a grader with no
network, run together on one machine with Docker Compose. It is **not** a production
deployment, and nothing here touches the live qccd.academy site or its leaderboard.

```bash
docker compose -f deploy/official/docker-compose.yml up --build
docker compose -f deploy/official/docker-compose.yml run --rm api \
    python -m qccd.official.service add-uploader dev         # prints an upload token ONCE
curl http://127.0.0.1:8300/v1/tasks
```

From a workspace, publishing an approved local submission to it:

```bash
qccd publish --submission <sub_id>                 # review + approve at a terminal (types the digest)
QCCD_UPLOAD_TOKEN=<token> qccd publish --approval <ap_id> --server http://127.0.0.1:8300
```

## Roles

| service | what it may do | what it may not do |
|---|---|---|
| `api` | accept authenticated uploads, return ids at once, serve status, reports, leaderboards | run the toolchain; accept client verdicts, scores or profiles |
| `worker` | claim queued jobs, stage bundles into the spool, record reports | run the toolchain |
| `grader` | grade spooled bundles with the reference evaluator | reach any network, see any credential or database |
| `db` | PostgreSQL 16 | |

The grader container has `network_mode: none`, a read-only root file system, a 2 GB tmpfs,
all capabilities dropped, `no-new-privileges`, a process limit, and memory and CPU caps. The
evaluator also runs each external tool as a subprocess with its own timeout and memory limit
(`qccd/workspace/procs.py`). Containers reduce the attack surface; they are not a proof of
isolation. Kernel escapes, resource exhaustion inside the limits, and bugs in the checkers
themselves are residual risks.

## What was tested, and what was not

- Tested on the development machine (Windows 11, 2026-09-22): the same code paths with SQLite
  and the inline grader (`tests/test_official.py`). That covers authenticated upload,
  fail-closed auth, a loopback-only development token, wrong-task and tampered archives,
  idempotent resubmission, private visibility, regrades that keep history, the HTTP API, and
  local-vs-server report parity on the same bundle.
- **Not run here**: this Compose file and the Dockerfile. The Docker daemon was not running,
  and building the image fetches the OCaml and Lean toolchains. The PostgreSQL code path
  (`qccd/official/db.py`, `FOR UPDATE SKIP LOCKED`) has not been executed against a server.
  Build and exercise it before relying on it.
