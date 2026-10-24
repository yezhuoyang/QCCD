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
listens on 127.0.0.1:8300 only. It is JSON, except an entry's studio page (HTML) and its
bundle (zip):

| endpoint | who |
|---|---|
| `GET /official/v1/health`, `/v1/tasks`, `/v1/leaderboard/<task>` (each row: `by`, every metric, the LER interval) | anyone |
| `GET /official/v1/submissions/<id>` and `/report` | anyone for public submissions; the uploader (or any key of the same person) for private ones |
| `GET /official/v1/submissions/<id>/page`, `/files/<name>`, `/bundle.zip` | the same: the studio page the grader drew from the graded bundle (drawn on first request for an entry graded before pages existed, into `artifacts/pages/`), one bundle file, the whole archive |
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

A new studio template (a change to `qccd/viz/render.py`'s page or the JS it stamps) changes the
page every entry opens. Draw them all again from the stored archives, without grading again (a
regrade samples every logical error rate again, 25-45 min per BB entry), as a one-off container with
the grader's memory:

```bash
docker run --rm -m 1g --network none -e HOME=/tmp -e QCCD_GRADER_MEM_MB=4096 \
  -e QCCD_OFFICIAL_DB=sqlite:////data/official.db -e QCCD_OFFICIAL_ARTIFACTS=/artifacts \
  -v qccd-official_data:/data -v qccd-official_artifacts:/artifacts \
  qccd-official:<tag> python -m qccd.official.worker redraw [--task <release>]
```

A bundle uploaded before `c230f11` carries the submitter's checkout path in its programs' provenance.
`thin-provenance` lists the stored bundles it would rewrite the way a bundle is written today;
`--apply` stores them, points each submission at its new bundle and queues it to be graded again.
Run it the same way, after the queue is empty and no redraw is running (`--apply` deletes the
old archives):

```bash
docker run --rm -m 1g --network none -e HOME=/tmp \
  -e QCCD_OFFICIAL_DB=sqlite:////data/official.db -e QCCD_OFFICIAL_ARTIFACTS=/artifacts \
  -v qccd-official_data:/data -v qccd-official_artifacts:/artifacts \
  qccd-official:<tag> python -m qccd.official.service thin-provenance [--apply]
```

**Rolling back.** Set `.env` to the previous tag and `up -d`. To take the service down
completely: `docker compose -f docker-compose.prod.yml down` (volumes survive), then remove
the `include snippets/qccd-official.conf;` line and `nginx -t && systemctl reload nginx`.
The site file as it was before the include was added is
`/root/qccd.academy.nginx.bak-20260923-000848`.

**Deployed** 2026-10-24 06:36 UTC, from commit `d41a6e5` (pushed, also on `website/invitations`):
hardware labels on an entry's page (every site, junction and ion named on the canvas, on by default,
the Labels button), `thin-provenance`, and arch files without a checkout path. Derived from
`751d443` (`qccd/`, `arch/` and `Compiler/bridge` from `d41a6e5`; `Compiler/ocaml` is unchanged, so
the image keeps qccdc `c774b87b677f`); 51 layers identical; rollback `*.bak-751d443`. The switch
waited for a running `bb72seq_mem` grade to finish. Rehearsal: smoke passed, and `thin-provenance
--apply` on a copy of an old production bundle regraded it eligible with no root served. Production:
smoke passed (GHZ, private, 18 s); `worker redraw` drew all 68 entry pages (0 failed, 9 min); the
dry run found 68 stored bundles, 67 to thin and none skipped, and `--apply` queued their 67
regrades (about 3.2 h of grading; the worker takes them in turn, and an entry keeps its board row
until its new report lands).

A regrade judges the stored program under today's rules, not the ones it was first graded under.
Twelve private deploy-smoke uploads (GHZ and BB144, 2026-09-23 to 10-20, compiled before strict
gate order and the current timing model) are now ineligible: R7, or qcheck's strict order. No
public entry changed. The worker was stopped after the first 16 regrades, when those flips showed,
and ran the rest under a watchdog that compared each regraded public entry with its report from
before (eligibility, rank value, every metric) and would have stopped it at the first difference;
all 49 came out identical, 55 of the 67 overall, and the queue drained at 10:11 UTC. Afterwards no
stored archive names a checkout path, and Goal 1 still lists 49 entries.

**Before that** 2026-10-22, from commit `751d443` (pushed, also on `website/invitations`): the Program
pane's Both view in two columns (hardware and circuit side by side on an entry's page) and `worker
redraw`. Same derivation (`qccd/` from `751d443`); 47 layers identical; rollback `*.bak-6012b17`;
rehearsal smoke passed and a rehearsal redraw drew its page. On production, `worker redraw` drew all
62 entry pages again from their archives (0 failed, no report changed).

**Before that** 2026-10-21 (late), from commit `6012b17` (pushed, also on `website/invitations`):
`bb90seq_mem@1` (one [[90,8,10]] block, Z then X, 180 ions; the tightest grade, ~2600-2700 s on the
droplet) completes Goal 1's curated codes, and `/v1/goals/goal1` carries `logical_qubits`, `ions`
and `ions_per_logical_qubit` for every entry and the FTL target (60 logical qubits within 2000 ions).
Same derivation as below (`qccd/` from `6012b17`); 47 layers identical; rollback `*.bak-82f95f2`.
Rehearsal smoke passed, and the queue was empty at the switch.

**Before that** 2026-10-21 (evening), from commit `82f95f2` (pushed, also on `website/invitations`):
Goal 1's other codes (`steanexz`, `shorxz`, `rep3xz`, `rep5xz`, `bb18xz` two-copy; `bb72seq` one block,
Z then X, 144 ions; `ldpc` decodes the BB and Steane ones), `/v1/goals/goal1` with each release's
ions, logical qubits and entries, and the studio template that shows the Learn panel only in the
tutorial. Derived from `d2da98d` (`qccd/` from `82f95f2`; outside it only `Compiler/ocaml` changed,
the rotate fix released as qccdc `b4a003dbb4ae`, byte-identical on the suite and the seeds, so the
image keeps `c774b87b677f`). 47 layers identical; rollback `*.bak-0b8678f`. Rehearsal smoke passed and
an entry page drawn there hides the Learn tab at 1600 and 1100 px. The three public boards were
regraded on production (`regrade surface3xz_mem@1` / `surface5xz_mem@1` / `surface7xz_mem@1`, 28
jobs) to redraw every entry page; the numbers were the same.

**Before that** 2026-10-21 (afternoon), from commit `0b8678f` (pushed, also on `website/invitations`):
- A leaderboard entry opens: `/submissions/<id>/page` (the studio page the grader draws after a
  passing grade, in a child of its own; served with CSP `sandbox`), `/files/<name>`, `/bundle.zip`.
- Rows carry `by`, every metric, the LER interval and the experiment; `GET /v1/goals/goal1` ranks
  every curated `qccd-noise@2` memory under 200 ions together by `ler_per_round_per_logical`.
- New releases `surface3xz_mem@1` (34 ions) and `surface7xz_mem@1` (194 ions).
- The worker's `artifacts` mount is writable (it keeps the pages): `docker-compose.prod.yml` changed.

The image is derived from `qccd-official:d2da98d` with `qccd/` from `0b8678f` (nothing outside
`qccd/` changed); its 47 layers are identical on both machines. Rollback: `.env.bak-d2da98d`,
`VERSION.bak-d2da98d`, `docker-compose.prod.yml.bak-d2da98d`. Rehearsed with the prod compose,
then the production smoke (operator `deploy-smoke-0b8678f`, quota 2, token deleted) passed with no
stage and no metric different, and every new door checked (page sandboxed, file, archive byte for
byte, 404 for a stranger, absent from Goal 1): GHZ `os_c7641ce0f9c24fe0`, and the 194-ion
surface-7 seed `os_3f43da41121a7780` (its 4.6 MB page drawn inside the 1 GB grader).

**Before that** 2026-10-21, from commit `d2da98d` (pushed, also on `website/invitations`):
- `qccd-noise@2` and `qccd-physics@2`: every number cited, every channel a Pauli channel for stim.
- Goal 1's board, `surface5xz_mem@1`: a Z and an X surface-5 memory side by side, 98 ions.
- A design entered on a board takes that board's physics (`board_device`).
- The compiler is `c774b87b677f`. Its new last fallback, split rounds, compiles 98-ion memories,
  and it is byte-identical on every program `e799ecf5a5cd` produced (77/77), so suite@1 was not
  re-pinned.

The image is derived from `qccd-official:db3e6fc`:
- `qccd/`, `arch/` and `Compiler/bridge` are the git archive of `d2da98d`;
- `qccdc_cli` is the published Linux `c774b87b677f` (sha256 `ed8838c6...`);
- qcheck is db3e6fc's (`e8acd6640fd3`, sha256 `923528c0...`).

The 45 layers are identical on both machines, and the previous deployment is in `.env.bak-db3e6fc`,
`VERSION.bak-db3e6fc` and `docker-compose.prod.yml.bak-db3e6fc`.

The local rehearsal and production agreed. The operator uploader was `deploy-smoke-d2da98d`
(quota 2, token deleted). Both private smoke tests matched their local reports, with no stage
and no metric different:
- GHZ, `os_b9a317a40d4cfee2`: eligible.
- The Goal 1 board's starter, `os_25336188e127741b`: eligible in about 23 s, at 5.2e-5 per round
  and 598 ms.

**Before that** 2026-10-21 07:57 UTC (00:57 PDT), from commit `db3e6fc` (pushed): the platform fixes
#15-#21 that the hillclimb PRs (#10-#14) prompted. The device stage fails a device over the
release's budget (`DEVICE.OVER_BUDGET`), counting what it declares about its own wiring at no
less than the deck's physics (#15); a move is charged split and merge for what it does -- the
class, a hop from a conveyor to a dead end, the chains -- whatever its class is called (#16,
#17); R22 labels a hop by its direction and a chord is not a spur (#18, #19); `verify()` judges
R19-R21 (#21); and the compiler is `e799ecf5a5cd`, which reads a device in one canonical order
(#20) and mirrors the new R22 labels. suite@1 was re-pinned for it (283ec95, measured in this
image: 100 valid / 0 wrong / 8 refused, T_jones median 1.78x, digest `303ed0d5...`).
The image was derived from `qccd-official:6ac56ad`: `rm` and `COPY` of `qccd/`, `arch/` and
`Compiler/bridge` from the git archive of `db3e6fc`, and `qccdc_cli` the published Linux
`e799ecf5a5cd` (sha256 `2b5f562e...`); qcheck is 6ac56ad's (sha256 `923528c0...`, `e8acd6640fd3`).
The 40 layers are identical on both machines. The previous deployment is in `.env.bak-6ac56ad`,
`VERSION.bak-6ac56ad` and `docker-compose.prod.yml.bak-6ac56ad`. Private smoke tests by the
operator uploader `deploy-smoke-db3e6fc` (quota 2, token deleted): GHZ `os_279a40d1b742be4e`
eligible, and the Compiler board `os_c91a6abb7a186f36` eligible in 473 s, 108 valid / 0 wrong /
8 refused of 116, speedup 1.0, ler_ratio 1.000, no public pair different from the baseline. The
local rehearsal gave the same (721 s).

**Before that** 2026-10-20 17:22 UTC (10:22 PDT), from commit `6ac56ad` (pushed): the proved
checker reads its input strictly -- a file missing a field the checker relies on, or with an
entry it cannot read (a move whose `t` is `20.0`, an op's qubit `0.5`), is REJECTED naming
what is wrong, where before `{}` was accepted as an empty programme and an unreadable move was
dropped (qcheck `e8acd6640fd3`, the version published for Windows and Linux the same day);
and `check_cert.py` checks a certificate's shape before reading it, so a malformed one is
`R10 FAILED` by name rather than a TypeError. Well-formed certificates, which is every one the
compiler writes, get the verdicts they got before, so suite@1 was not re-pinned. Compiler
`f88754a2fa71` is unchanged.

The image was derived from `qccd-official:e9bbf0b`: the Dockerfile's Lean stage verbatim, then
`rm` and `COPY` of `qccd/`, `arch/`, `Compiler/bridge` and `qcheck` (sha256 `923528c0…`);
`qccdc_cli` is e9bbf0b's (sha256 `3a11247f…`, the published Linux `f88754a2fa71`). The code
under `qccd/`, `arch/` and `Compiler/bridge` hashes the same as the git archive of `6ac56ad`;
the 34 layers are identical on both machines. The previous deployment is in
`.env.bak-e9bbf0b`, `VERSION.bak-e9bbf0b` and `docker-compose.prod.yml.bak-e9bbf0b`. Private
smoke tests by the operator uploader `deploy-smoke-6ac56ad` (quota 2, token deleted): GHZ
`os_dc4bbf2d0d94a0f7` eligible in 16 s, and the Compiler board `os_5a163b8811cfcbcd` eligible
in 444 s, 108 valid / 0 wrong / 8 refused of 116, speedup 1.0, ler_ratio 1.000, no public pair
different from the baseline. The local rehearsal gave the same (`os_eff78fb8ebdecfaa`, 642 s).

**Before that** 2026-10-20 07:00 UTC (00:00 PDT), from commit `e9bbf0b` (pushed): compiler
`f88754a2fa71` (built from `651f39c`), which takes a rule set (`qccdc --rules`) and without one
writes what `dbd13d8952f3` wrote: all 216 compiles of suite@1's public pairs (`compile` and
`rotate`) are byte-identical between the two, so suite@1 was not re-pinned. The grader now
fails a submission's semantics stage when `check_cert.py`'s per-qubit order check is not
`ok` (before, only O1, unrealised ops and O2 could fail it), and `check_cert.py` ties the
programme's transport to the certificate's, ion by ion and in order. qcheck `1c067263bdc9`
is unchanged.

The image was derived from `qccd-official:972d8bf`: the Dockerfile's OCaml stage verbatim,
then `rm` and `COPY` of `qccd/`, `arch/`, `Compiler/bridge` and `qccdc_cli`; qcheck is
972d8bf's (sha256 `4c78a9d5…`). Its `qccdc_cli` is the published Linux `f88754a2fa71` byte
for byte (sha256 `3a11247f…`); the suite files are identical to 972d8bf's; the 29 layers are
identical on both machines. The previous deployment is in `.env.bak-972d8bf`,
`VERSION.bak-972d8bf` and `docker-compose.prod.yml.bak-972d8bf`. Private smoke tests by the
operator uploader `deploy-smoke-e9bbf0b` (quota 2, token deleted): GHZ `os_0bd9e3e6c91a0cbf`
ok in 15 s, and the Compiler board `os_f1165eea3f41707f` eligible in 463 s, 108 valid /
0 wrong / 8 refused of 116, speedup 1.0, ler_ratio 1.000, no public pair different from the
baseline. The local rehearsal gave the same (`os_b22b2bf98b42f6f3`, 671 s).

**Before that** 2026-10-19 20:30 UTC, from commit `972d8bf` (pushed): compiler `dbd13d8952f3`,
which never changes the order of a qubit's gates, commuting or not (the user's decision of
2026-10-19), and whose conveyor sweeps greedily; `check_cert.py`'s R10 checks each qubit's
order from the program; and qcheck `1c067263bdc9`, whose order clause lost its commutation
exemption. suite@1 was re-pinned in `972d8bf`, measured in a rehearsal of this image: only the
three memory pairs on `ring144_24v` changed, and the reference still has 100 valid, 0 wrong and
8 refused. Production also gets the R10 work of 2026-10-18 (d5717e1, 80d4d71, b26eeca, acca452)
and the Linux-measured baseline of c6491e0, which the previous image predated.

The image was derived from `qccd-official:5b1a3f3`: the Dockerfile's OCaml AND Lean stages
verbatim, then `rm` and `COPY` of `qccd/`, `arch/`, `Compiler/bridge`, `qccdc_cli` and `qcheck`
(the previous derived images reused e70144e's qcheck; the strict order clause needed a new
one). Its `qccdc_cli` is the published Linux `dbd13d8952f3` byte for byte (sha256
`9897b5f6…`), its qcheck sha256 `4c78a9d5…`; the 24 layers are identical on both machines. The
previous deployment is in `.env.bak-5b1a3f3`, `VERSION.bak-5b1a3f3` and
`docker-compose.prod.yml.bak-5b1a3f3`. Private smoke tests by the operator uploader
`deploy-smoke-972d8bf` (quota 2, token deleted): GHZ `os_463dedd83cadfe37` ok in 16 s, and the
Compiler board `os_108ea475e6c5f306` eligible in 410 s, 108 valid / 0 wrong / 8 refused of 116,
speedup 1.0, ler_ratio 1.000, no public pair different from the baseline. The local rehearsal
gave the same (`os_c9fdff1a1274e4c2`). `smoke_test.py` must run from the checkout: from a git
archive it has no compiler for its local grade.

**Before that** 2026-10-18 03:23 UTC (2026-10-17 20:23 PDT), from commit `5b1a3f3` (pushed):
compiler `7265883d596f`, in which a barrier is a fence and a docked two-qubit gate is its own
gate. Every release from `35bf68427d9c` to `c7c535380bfb` compiled a two-qubit `barrier a,b`,
and any docked `cz`, in the conveyor as a CX. Production ran `f88a49f79af1`, which predates
them, so this deployment also brings what they added: R23 (no ion rests on a junction, in the
verifier and in the compiler), the barrier as a fence, and the race-track pass. The previous
deployment is in `.env.bak-f1e6273`, `VERSION.bak-f1e6273` and
`docker-compose.prod.yml.bak-f1e6273` for a rollback.

suite@1 was re-pinned in `5b1a3f3`, because the reference compiler's programs changed. The old
and new published compilers on its 216 compiles (108 public pairs, rotate and compile) are
byte-identical in rotate mode and differ on 41 compile runs. R23 changes 22 programs on
`ladder_2x72` and `ring144_24v`, `02bceb9117e7` finishes 8 that ended unrealised, the race track
compiles 3 on `h2_racetrack`, and `7265883d596f` itself changes none. The reference now has 100
valid, 0 wrong and 8 refused (it had 88, 0 and 20). Only `baseline.json` and `suite.json`
changed, so suite@1's digest is now `sha256:6624543e…`; every board's digest is unchanged.
suite@1's public leaderboard was empty.

The image was derived from `qccd-official:e70144e` again, the same way as `f1e6273`: the
Dockerfile's OCaml stage verbatim, then `rm` and `COPY` of `qccd/`, `arch/` and
`Compiler/bridge`. The OCaml stage was `CACHED` up to `dune build`, and its `qccdc_cli` is the
published Linux `7265883d596f`, byte for byte (sha256 `ea3d4d42…`). Checked: the 312 files
under `/opt/qccd/{qccd,arch,Compiler/bridge}` equal the `git archive` of `5b1a3f3`. Against
`f1e6273`'s image, exactly the 38 files git changed and `qccdc_cli` differ, and `qcheck` is
unchanged. The 18 layers are identical on both machines, and the first 13 are `e70144e`'s.
`git archive` on this Windows checkout writes CRLF (core.autocrlf), so the image's Python files
have CRLF line ends, as in `e70144e`'s and `f1e6273`'s. The suites and releases are marked
`-text` and keep their pinned bytes.

All checks were PRIVATE uploads by the operator uploader `deploy-smoke-5b1a3f3` (quota 2, its
token deleted), one set in a local rehearsal and one in production:

| check | rehearsal | production |
|---|---|---|
| GHZ starter, end to end | 11 s, `os_6dfa797b663dedda` | 9 s, `os_27bca510de26893f` |
| Compiler board, the starter compiler (= the reference), 116 pairs | 721 s, `os_d5c2c3b0f1f13e5a` | 425 s, `os_c4bce259b7d0bc7a` |

GHZ was eligible on both, with no stage or metric differences from the local grade. The
compiler board was ELIGIBLE on both: 108 valid, 0 wrong, 8 refused (all 8 hidden pairs valid),
speedup 1.0. Status and metrics equal `baseline.json` on all 108 public pairs. The LERs are
equal on only 3 of the 32 memory pairs, so `ler_ratio` is 0.992, not 1. Seeded sampling is
deterministic on each platform but differs between Windows, where the baseline is measured,
and Linux. The `e70144e` run (2026-09-29) showed the same thing. The rehearsal's and
production's 108 public records are identical, LERs included.

**Deployed before** 2026-10-05 16:36 UTC, from commit `f1e6273` (pushed): compiler `f88a49f79af1`, which
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

**And before that** 2026-09-26 19:11 UTC (12:11 PDT), from commit `67703cb` (pushed). It carries
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

**Earlier** 2026-09-25 05:00 UTC (2026-09-24 22:00 PDT), from commit `259aba6` (pushed), which
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
