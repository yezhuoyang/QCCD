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

It lives in `/opt/qccd-official/` on the droplet (qccd.academy). That directory holds
`docker-compose.prod.yml`, `.env` (`QCCD_OFFICIAL_TAG=<commit>`) and `VERSION` (the commit,
the image id and the date). nginx serves `https://qccd.academy/official/v1/…` through
`/etc/nginx/snippets/qccd-official.conf`, which the site's HTTPS block includes. The api
listens on 127.0.0.1:8300 only. It is JSON only:

| endpoint | who |
|---|---|
| `GET /official/v1/health`, `/v1/tasks`, `/v1/leaderboard/<task>` | anyone |
| `GET /official/v1/submissions/<id>` and `/report` | anyone for public submissions; the uploader (or any key of the same person) for private ones |
| `POST /official/v1/submissions` | an uploader token (`add-uploader`) or a person's key (below); fails closed without one |
| `POST /official/v1/links`, `GET /v1/links/<code>`, `POST /v1/links/<code>/token` | a workspace signing in (the code, then its key, once) |
| `POST /official/v1/links/<code>/approve` / `deny` | the site's `/connect/` page, with a grant from the accounts service |
| `GET /official/v1/me`, `POST /v1/keys/self/revoke`, `GET /v1/submissions` | a key: who it is, sign out, what that person submitted |
| `POST /official/v1/account/keys`, `/account/keys/<id>/revoke` | the site's `/connect/` page: the person's keys, with a `manage` grant |

**Signing in with a site account** (`qccd/official/accounts.py`, docs/workspace.md "The official
leaderboard"). A person's workspace gets a key that submits in their name; the leaderboard shows
that name (`by`). The site's accounts service (`qccd-comments`) vouches for the person with a
5-minute grant, HMAC-signed under a secret that it and this service share, and nothing else. One-time
setup, as root on the droplet:

```bash
umask 077; mkdir -p /etc/qccd
S=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
printf 'QCCD_ACCOUNT_SECRET=%s\n' "$S" > /etc/qccd/account.env
chgrp qccd /etc/qccd/account.env; chmod 640 /etc/qccd/account.env
# the accounts service reads it: add under [Service] in /etc/systemd/system/qccd-comments.service
#   EnvironmentFile=-/etc/qccd/account.env
systemctl daemon-reload && systemctl restart qccd-comments
# this service reads it from .env beside docker-compose.prod.yml
cd /opt/qccd-official && printf 'QCCD_ACCOUNT_SECRET=%s\n' "$S" >> .env && chmod 600 .env
docker compose -f docker-compose.prod.yml up -d api
unset S
```

Without it, `approve` answers 503 ("not set up") and the site's grant route answers 503; uploads with
maintainer tokens are unaffected. Rotating the secret only invalidates grants in flight (they last
five minutes); keys already handed out keep working, since they are checked by their hash here.

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
docker save qccd-official:<commit> | gzip -1 | ssh root@qccd.academy 'gunzip | docker load'
ssh root@qccd.academy 'cd /opt/qccd-official && sed -i "s/^QCCD_OFFICIAL_TAG=.*/QCCD_OFFICIAL_TAG=<commit>/" .env \
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

**Deployed** 2026-10-05 16:36 UTC, from commit `f1e6273` (pushed): compiler `f88a49f79af1`, which
keeps one waveform per cycle (R22) on drawn designs too. Before it, the reference compiler in
the image put 15 R22 violations into `adder3` on an L-shaped path; now none. `VERSION` on the
server is the record of every deployment, including the two between this one and the next
paragraph (`2bc13ae`, `e70144e`). The previous one is in `.env.bak-e70144e`,
`VERSION.bak-e70144e` and `docker-compose.prod.yml.bak-e70144e` for a rollback.

This image was DERIVED from `qccd-official:e70144e`, not built clean. `python:3.14-slim` had
moved upstream, so a clean build would have recompiled every wheel (15 to 40 minutes) and
changed production's Python base with it, for a change of seven files. The derived image
replaces `qccd/`, `arch/` and `Compiler/bridge` with the `git archive` of the commit and
`qccdc_cli` with the one the Dockerfile's own OCaml stage builds from it; the base, the wheels
and `qcheck` are `e70144e`'s. Checked: every file under `/opt/qccd` hashed in both images
differs only in `qccdc_cli` (now byte-identical to the published Linux `f88a49f79af1`), the
five changed `qccd/workspace` files, and 30 stale `.pyc` files the old image carried; the 18
layers are identical on both machines; the suite's baseline stands, because the old and the
new published compilers give identical bytes on all 216 compiles of its public pairs. The
local rehearsal and the production smoke test (`os_f69559da338c9c99`, private, uploader
`deploy-smoke-f1e6273`, token deleted) were eligible with no stage or metric differences;
production took 20 s end to end. The next clean build pays for the wheels once.

Two traps this deployment met. Watch the build log for `CACHED` on the `wheels` stage and
cancel if it is not: the base tag moves without notice. And a script piped to
`ssh ... 'bash -s'` is cut short by the first `docker compose exec` in it, which reads the
rest of the script as its stdin: give those commands `</dev/null`.

**Deployed before** 2026-09-26 19:11 UTC (12:11 PDT), from commit `67703cb` (pushed). It carries
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

**And before that** 2026-09-25 05:00 UTC (2026-09-24 22:00 PDT), from commit `259aba6` (pushed), which
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
