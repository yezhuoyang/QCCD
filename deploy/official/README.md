# Official submission service: deployment

The API, a queue worker and a grader with no network, as one image run in three roles.

| file | what it is |
|---|---|
| `Dockerfile` | the image: the OCaml compiler, the Lean checker built without Mathlib, the Python runtime |
| `docker-compose.prod.yml` | **production** on the qccd.academy droplet (SQLite, memory caps) |
| `docker-compose.yml` | a local development stack with PostgreSQL (the PostgreSQL path has never run) |
| `nginx-qccd-official.conf` | the nginx snippet that serves `/official/` on qccd.academy |
| `smoke_test.py` | operator check: one private submission, graded by the server, compared with a local grade |

## Production: https://qccd.academy/official/

It lives in `/opt/qccd-official/` on the droplet (165.232.55.161). That directory holds
`docker-compose.prod.yml`, `.env` (`QCCD_OFFICIAL_TAG=<commit>`) and `VERSION` (the commit,
the image id and the date). nginx serves `https://qccd.academy/official/v1/…` through
`/etc/nginx/snippets/qccd-official.conf`, which the site's HTTPS block includes. The api
listens on 127.0.0.1:8300 only. It is JSON only:

| endpoint | who |
|---|---|
| `GET /official/v1/health`, `/v1/tasks`, `/v1/leaderboard/<task>` | anyone |
| `GET /official/v1/submissions/<id>` and `/report` | anyone for public submissions; the uploader for private ones |
| `POST /official/v1/submissions` | an uploader token (`add-uploader`); fails closed without one |

From a workspace, a person publishes an approved local submission with:

```bash
qccd publish --submission <sub_id>          # review + approve at a terminal (types the digest)
qccd publish --approval <ap_id> --server https://qccd.academy/official
```

The upload token comes from `QCCD_UPLOAD_TOKEN` or from `~/.qccd/credentials.json`
(`{"https://qccd.academy/official": "<token>"}`).

**Updating it.** Nothing is compiled on the server. It has 2 vCPUs and about 4 GB of memory,
and it also serves other sites.

```bash
git archive <commit> -- qccd arch Compiler/bridge Compiler/ocaml Compiler/lean/QCCDC/Cert \
    Compiler/lean/Main.lean Compiler/lean/lean-toolchain deploy/official | tar -x -C build/
docker build -f build/deploy/official/Dockerfile -t qccd-official:<commit> build/
docker save qccd-official:<commit> | gzip -1 | ssh root@165.232.55.161 'gunzip | docker load'
ssh root@165.232.55.161 'cd /opt/qccd-official && sed -i "s/^QCCD_OFFICIAL_TAG=.*/QCCD_OFFICIAL_TAG=<commit>/" .env \
    && docker compose -f docker-compose.prod.yml up -d'
python deploy/official/smoke_test.py https://qccd.academy/official --token-file <file>
# a heavy board too: upload an existing graded local submission, privately
python deploy/official/smoke_test.py https://qccd.academy/official --token-file <file> \
    --workspace <workspace> --submission <sub_id> --timeout 3000
```

A first build takes about 15 minutes, most of it compiling stim, which has no Linux wheel
for CPython 3.14. After that only the `COPY qccd/` layer changes. Rehearse locally first:
`docker compose -p rehearsal -f deploy/official/docker-compose.prod.yml up -d`, then run
`smoke_test.py` against `http://127.0.0.1:8300` (and `down -v` the rehearsal afterwards).
The server grades every release in the image's `qccd/workspace/releases/`; a new board is a
new release there and a new image, nothing else.

**Operating it** (on the droplet, in `/opt/qccd-official`):

```bash
docker compose -f docker-compose.prod.yml ps | logs --tail 50 grader
docker compose -f docker-compose.prod.yml run --rm -T api python -m qccd.official.service add-uploader <name> [--quota N]
docker compose -f docker-compose.prod.yml run --rm -T api python -m qccd.official.service regrade ghz4@1
```

`add-uploader` prints the new token once. Deliver it privately.

**Rolling back.** Set `.env` to the previous tag and `up -d`. To take the service down
completely: `docker compose -f docker-compose.prod.yml down` (volumes survive), then remove
the `include snippets/qccd-official.conf;` line and `nginx -t && systemctl reload nginx`.
The site file as it was before the include was added is
`/root/qccd.academy.nginx.bak-20260923-000848`.

**Deployed** 2026-09-26 19:11 UTC (12:11 PDT), from commit `67703cb` (pushed). It carries
`3eea898`, in which the Lean checker computes a certificate's replay once instead of about
3,700 times. The specification, the soundness theorem and its axioms are unchanged. The
previous deployment (`259aba6`) is in `.env.bak-259aba6` and `VERSION.bak-259aba6` for a
rollback. All checks were PRIVATE uploads, never on a leaderboard:

| check | GHZ starter | BB [[144,12,12]] (the two-triangle design) |
|---|---|---|
| local rehearsal, end to end | 6 s | 16 s |
| the server, end to end | 9 s | 34 s |

Before this deployment, the BB grade on the server took about 8.5 minutes. Both were eligible
on both, with no stage or metric differences from the local grade. The BB local report came
from the old checker, so this also compares the two checker versions. The image's 13 layers
are identical on this machine and on the droplet; the image ids differ only because the two
image stores name images differently. The operator uploader `deploy-smoke-67703cb` (quota 2)
holds the private smoke submissions `os_2901e4026a976ff4` (GHZ) and `os_0909b51fd3c47007` (BB);
its token was deleted after use.

**Deployed before** 2026-09-25 05:00 UTC (2026-09-24 22:00 PDT), from commit `259aba6` (pushed), which
adds the website's five boards to the GHZ starter: `bb144@1`, `rep9@1`, `five_qubit@1`,
`steane@1`, `surface17@1`. `VERSION` on the server records it; the previous deployment
(`10152b1`, 2026-09-23) is in `.env.bak-10152b1` for a rollback. The GHZ release is
byte-identical, so its digest and every earlier submission stand.

Checked before and after the switch, all as PRIVATE uploads (never on a leaderboard): the
GHZ smoke test in a local rehearsal and on the server, and a BB [[144,12,12]] design (a
drawn two-triangle device, 503.8 ms per round) in both. On the server the BB grade took
about 8.5 minutes end to end with the grader at 115 MiB peak (161 MiB in the rehearsal;
153 MB over the whole process tree on Windows), all ten stages passing and identical to the
local grade. No limit had to change.

The first uploader is `yezhuoyang` (50 uploads a day), whose token is in that person's
`~/.qccd/credentials.json`. Operator uploaders `deploy-smoke` (the 2026-09-23 GHZ check,
`os_d47f8b8542b1b207`) and `deploy-smoke-259aba6` (quota 2: `os_283ef695dd1e8f21` GHZ,
`os_eb92e18f75331375` BB) hold the private smoke submissions; their tokens were deleted
after use.

**Data** lives in Docker volumes: `qccd-official_data` (the SQLite database),
`qccd-official_artifacts` (uploaded archives, by digest) and `qccd-official_spool` (jobs in
flight). They are not backed up yet.

## Roles and limits

| service | may | may not | limits in production |
|---|---|---|---|
| `api` | accept authenticated uploads, serve status, reports, leaderboards | run the toolchain; accept client verdicts, scores or profiles | 256 MB, 0.5 CPU, loopback port |
| `worker` | claim queued jobs, stage bundles into the spool, record reports | run the toolchain; reach any network | 256 MB, 0.5 CPU, no network |
| `grader` | grade spooled bundles with the reference evaluator | reach any network; see any credential or the database | 1 GB, 1 CPU, no network, read-only root, 128 pids |

All three drop every capability and run with `no-new-privileges` as an unprivileged user.
Inside the grader, the evaluator runs each external tool as a subprocess with its own
timeout and address-space limit (`qccd/workspace/procs.py`). Containers reduce the attack
surface; they are not a proof of isolation. Kernel escapes, resource exhaustion inside the
limits, and bugs in the checkers themselves remain risks.

## What was tested, and what was not

- **The development code paths** (Windows 11): SQLite with the inline grader,
  `tests/test_official.py`. This covers authenticated upload, fail-closed auth, the
  loopback-only development token, wrong-task and tampered archives, idempotent resubmission,
  private visibility, regrades that keep history, the HTTP API, and local-versus-server
  parity.
- **The image and the production compose file** (Docker 27.5, 2026-09-22). A local rehearsal
  ran `smoke_test.py`: the server graded a private upload eligible, all ten stages passed,
  and it agreed with the local report with no stage or metric differences. Peak memory was
  87 MB for the grader, 36 MB for the worker and 43 MB for the api. The first container run
  found a bug and fixed it: a Lean stage under the grader's inherited address-space limit
  could not start.
- **Not run:** the PostgreSQL path (`docker-compose.yml`, `FOR UPDATE SKIP LOCKED`).
