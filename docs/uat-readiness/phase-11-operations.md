# Phase 11 — Operational readiness

**Date:** 2026-10-30. **Branch:** `uat-readiness/phase-11-operations`.
**Scope:** `UAT_READINESS_AUDIT_PROMPT.md` Phase 11 — load/capacity,
resilience drills, backup/restore, health/readiness endpoints, container
hardening, and the UAT environment itself.

Everything below was measured against the **live stack on this machine** —
the real API process, the real local Ollama, the real SQL Server
connections, the real PostgreSQL identity database — except where a row
says otherwise. What could not be run here says so and is never recorded
as passed.

---

## 1. The environment these numbers belong to

Stating this first because every latency figure in §3 is a property of
this hardware, not of the software.

| | |
|---|---|
| Host | Windows 11 Pro 26300, 16 logical CPUs, 32 GB RAM |
| GPU | NVIDIA T1200 Laptop GPU, **4 GB** — the reference machine `docs/PERFORMANCE.md` describes |
| LLM | Ollama, `llama3.1:8b` (≈4.9 GB, so it does **not** fit in 4 GB of VRAM — measured residency 2.2 GB, the rest on CPU) |
| `OLLAMA_NUM_PARALLEL` | unset, so Ollama's own default |
| Business databases | four `mssql` connections (`hr`, `adventureworks`, `employee_management_database`, `test_db`) |
| Identity database | PostgreSQL 18 on `localhost:5432` — 15 MB, 36 tables, 27 users, 196 conversations, 404 `ai_outputs`, Alembic head `q9u2v5w8x1y4` |
| API | `uvicorn api.main:app`, single worker, `127.0.0.1:8000` |
| Drill instance | a second `uvicorn` on `127.0.0.1:8001`, §4.2 |

### 1.1 Method limitations — read these before trusting a number

1. **k6 is not installed here, and the existing harness measures
   something else on purpose.** `eval/load/` runs k6 in Docker against
   `eval/load/mock_ollama_server.py`, an ~800 ms stand-in for the model.
   That is the right variable to hold constant when measuring the API
   tier, which is what `docs/SCALE_BASELINE.md` did. It is the **wrong**
   one to hold constant for a UAT capacity question, because on this
   hardware the model *is* the capacity. §3 therefore drives the real
   stack with a thread-per-virtual-user Python driver. At UAT concurrency
   (single digits) that is sufficient; it is not a substitute for k6 at
   scale and it is not what CI runs.
2. **Two Windows services could not be stopped.** This shell is not
   elevated (`IsInRole(Administrator)` → `False`), so `postgresql-x64-18`
   and `MSSQLSERVER` could not be taken down directly. Both drills ran
   instead against a **Docker PostgreSQL container** holding the identity
   database restored from this phase's own `pg_dump` and a seeded
   `postgres_uat` business database, which `docker stop` can take down at
   will. That is a faithful drill of *the application's* behaviour when
   its database disappears; it is not a drill of the production Postgres
   or of SQL Server specifically. §10 names what a human with elevation
   must still run.
3. **Two drills were run twice, because the first run of each was
   invalid.** Both are reported in §4 rather than quietly replaced,
   because the invalidity is the reusable lesson — see §4.1 and §4.3.
4. **A live browser session shared the instance** during part of this
   session. The §3 runs were driven after that stopped. The routing
   observations in §5 include it, which does not affect them.

---

## 2. Findings

| ID | Sev | Title | Status |
|---|---|---|---|
| `OPS-011` | S3 | The first two commands a UAT operator runs did not run as written | **Fixed** `684fbca` |
| `OPS-012` | **S2** | The question queue gives a question 120 s; `POST /ask` gives it 600 s | **Flagged** |
| `OPS-013` | S3 | A queued question's whole execution was untraceable in the logs | **Fixed** `c41b39c` |
| `OPS-014` | S3 | `/health` does not check the identity database at all | **Flagged** |
| `OPS-015` | **S2** | The live router sends 12 of 26 golden SQL questions somewhere other than the database | **Flagged** `b1fb44f` (test only) |
| `OPS-016` | S3 | "Both dialects" had no PostgreSQL environment to run in | **Fixed** |
| `OPS-017` | S3 | No UAT feature-flag profile existed, and five untested subsystems are on | **Fixed** |
| `OPS-018` | S3 | There is no backup procedure, so the identity DB's RPO is unbounded | **Fixed** (documented) |
| `OPS-019` | S3 | An unreachable database burned the retry budget, then blamed the question | **Fixed** `8e049ea` |
| `OPS-020` | S3 | Admission control is sized for ~50× the capacity this hardware has | **Flagged** |
| `OPS-021` | **S1** | `docker build .` failed outright — the image this project ships could not be built | **Fixed** `e7df257` |

Positive results, recorded as evidence so a later phase need not redo
them: Ollama resilience passes all five checks (§4.1); the identity
database restores and is **functionally** verified by signing in against
the restored copy (§6); `/live` and `/health` are correctly separated and
both report real dependency state for the dependencies they cover (§7);
the PostgreSQL leg of the product is **live-verified for the first time**
(§9.2); and the container runs non-root with all capabilities dropped and
resource limits set (§8).

---

## 3. Load and capacity

### 3.1 What was driven

`POST /ask` with distinct authenticated accounts, one question per
virtual user, questions chosen to route to the SQL path. A sampler thread
polled `GET /questions/stats`, Ollama's `/api/ps` and `GET
/metrics/harness` every 10 s throughout.

### 3.2 Results

| Run | Users | p50 | p95 | max | wall clock | `succeeded` | throughput |
|---|---|---|---|---|---|---|---|
| 1u | 1 | **79.1 s** | 79.1 s | 79.1 s | 79.1 s | 1 / 1 | **0.76 q/min** |
| 3u | 3 | **193.5 s** | 193.5 s | 202.7 s | 202.8 s | 2 / 3 | **0.59 q/min** |
| 6u | 6 | **318.4 s** | 318.7 s | 326.8 s | 327.1 s | **1 / 6** | **0.18 q/min** |

A separate single-question baseline on a cold model took **126.7 s**
(12.2 s of it model load).

### 3.3 What the hardware supports — plainly

**One concurrent question.** Adding users does not add throughput, it
subtracts it: 0.76 → 0.59 → 0.18 questions per minute. The GPU holds 2.2
GB of a 4.9 GB model, so every token is partly CPU work and a second
request does not find idle capacity, it finds a queue.

**At six concurrent users the deployment does not degrade, it fails.**
Five of six questions returned `status="failed"`, every one attributed to
the same cause:

> `Ollama call to model 'llama3.1:8b' … did not answer within the
> per-call limit of 300s (OLLAMA_REQUEST_TIMEOUT_SECONDS)`

— a single `generate_sql` call waiting behind five others exceeded the
300 s per-call budget. And after the fifth consecutive timeout the
provider circuit breaker opened:

> `[harness] provider=ollama breaker is now open after model_unavailable`

which is the breaker working exactly as designed — five timeouts *are*
evidence of an unhealthy provider — but it means **a seventh user,
arriving after the burst, is refused for the cooldown with no outage
having occurred**. This is the `OPS-009` shape reached by load rather
than by a missing model, and the `OPS-009` fix correctly does not cover
it, because a genuine timeout is a legitimate health signal.

**For UAT this means the six testers must not ask questions at the same
time.** Stagger them, or give the deployment a GPU that fits the model.

### 3.4 The model is 99.9% of the wall clock

From `GET /metrics/performance` over the 19 questions of this session:

| Stage | p50 | p95 |
|---|---|---|
| `generate_sql` | **119,016 ms** | 303,800 ms |
| `generate_insight` | 0.14 ms | 80,707 ms |
| `retrieve_schema` | 1,842 ms | 9,407 ms |
| `retrieve_business_context` | 316 ms | 3,407 ms |
| **`execute_sql`** | **8.06 ms** | **140 ms** |
| `validate_sql` | 7.53 ms | 58.7 ms |
| `estimate_cost` | 15.7 ms | 71.1 ms |

`generate_sql`'s p50 is about **14,000×** `execute_sql`'s.

### 3.5 Queue depth and DB pool — what the numbers mean

- **Queue depth was 0 in all 51 samples, and that is not a capacity
  result.** `running_global`/`queued_global` count the *asynchronous*
  queue (`POST /questions`); the load runs used `POST /ask`, which has
  its own admission control and never enqueues. Reported so nobody reads
  "queue depth 0 at six users" as headroom.
- **The connection pool was never the constraint, and §3.4 is why.** A
  question holds a connection for ~8 ms out of ~80–320 s. With
  `db_pool_size=10` and `db_max_overflow=20`, six concurrent questions
  need six connections at most and hold them for a rounding error of the
  request. No pool exhaustion was observed or is reachable at this
  concurrency.
- **Ollama saturation** (`/api/ps`): one model resident, **2.2 GB** in
  VRAM on a 4 GB card, for a model whose weights are ≈4.9 GB.

### 3.6 `OPS-020` — admission control is sized for other hardware

`max_concurrent_ask_requests` defaults to **50**,
`max_concurrent_questions_per_user` to **100** and
`max_concurrent_questions_global` to **500** (confirmed live:
`GET /questions/stats` → `max_concurrent_per_user: 100`,
`max_concurrent_global: 500`). Measured capacity is **1**. Nothing was
rejected during the 6u run, because all six callers were distinct and
well inside every cap — so the first control a burst meets is not
admission control, it is a 300 s timeout and then the circuit breaker.
Admission control that admits 50× what the hardware can serve converts a
queue into a pile of timeouts.

**Flagged, not changed:** the right ceiling is a deployment decision and
these defaults are reasonable for a GPU that fits the model.
`CLAUDE.md` §13 already records that the 100-concurrent path has only
ever been exercised against a mocked runner; this is the first
measurement of what happens at 6.

---

## 4. Resilience drills

Each drill breaks one dependency, observes, restores it, and then checks
that the application recovers **without being restarted**.

### 4.1 Ollama — passes all five checks

The first attempt was **invalid and is reported as such**, because the
lesson generalises. Killing `ollama.exe` alone is not an outage: the
`ollama app.exe` tray process restarted the server within seconds, so the
request measured "while down" **succeeded in 75.8 s** and `/health`
cheerfully reported `ollama.ok: true`. Read as data, that run claimed a
false-health defect that does not exist. The rerun stops the tray process
first and asserts `/api/tags` is unreachable — and holds that state for
5 s — before recording anything.

| | Observed |
|---|---|
| A1 in-flight kill | HTTP 200, `status="failed"` in **33.3 s**. `error_history`: "…[WinError 10054] An existing connection was forcibly closed by the remote host". No hang, no 500, no stack trace to the client |
| A2 `/health` while genuinely down | **HTTP 503**, `status="degraded"`, `ollama.ok: false`, "Unreachable: [WinError 10061]…" |
| A2b `/live` while down | **HTTP 200** `{"status": "alive"}` — correct separation: the process is alive, its dependency is not |
| A3 new question while down | HTTP 200, `status="failed"` in **10.2 s**, with the actionable message "Is `ollama serve` running and listening on http://localhost:11434?" |
| A4/A5 recovery | `/health` → 200 `ok`; the same question **succeeded in 88.6 s** with **no API restart**. The breaker closed on its own |

### 4.2 Identity database — recovers cleanly, but an outage reports as HTTP 500

Run against the port-8001 instance, whose `AUTH_DATABASE_URL` points at
the restored copy in the `phase11-pg` container.

| | Observed |
|---|---|
| B1 login against the **restored** identity DB | **HTTP 200**, token issued; `/auth/me` → `email: phase11.ops@example.com`, `roles: ["user"]`. This is the functional half of §6 |
| B4 login with identity DB down | **HTTP 500** `{"detail": "An unexpected error occurred. Please try again."}` |
| B5 `/auth/me` with an already-issued token | **HTTP 500**, same body |
| B6 `GET /models` with identity DB down | **HTTP 200** — the `agent.authz` surface authorizes from the JWT alone and touches no database. Consistent with `AUZ-003`; not a new finding |
| B7 `GET /conversations` | **HTTP 500** |
| B9–B11 recovery | login → 200, `/conversations` → 200, `/health` → 200 `ok`, all **without restarting the app**: the SQLAlchemy pool reconnected by itself |

**The 500s are the finding.** Nothing internal leaks — the body is the
fixed generic message and the global handlers did their job — but a
dependency outage answered as `500 Internal Server Error` tells a client
"this is a bug, retrying will not help" when the truthful answer is
`503 Service Unavailable`, optionally with `Retry-After`. J13's objective
is that failures are *clear*. Graded S3 and folded into `OPS-014`'s
decision below, since both concern how an identity-tier outage is
reported.

### 4.3 Target (business) database — graceful, and it exposed `OPS-019`

The first attempt was **invalid for the opposite reason to §4.1**: the
question finished in **4.95 s** — small Postgres schema, warm model — so
the container stop landed 15 s after the request had already succeeded.
A kill timed against an assumed duration is not a drill. The rerun stops
the database *before* the question, which measures the same code path
without depending on a race.

| | Observed |
|---|---|
| C2 question with target DB down | HTTP 200, `status="failed"`. **Before `OPS-019`:** three identical `psycopg2.OperationalError: Connection refused` entries in `error_history`, three `generate_sql` model calls spent, and a `failure_explanation` reading "the same query came back unchanged … Try rephrasing the question, or naming the table or column you mean." **After:** `retry_count` 1, one `error_history` entry, `attempt_history[0].outcome == "connectivity"`, 52.7 s, and an explanation that names the database |
| C2b `POST /execute` with target DB down | **HTTP 200** with an `error` field carrying the raw `psycopg2` text **including host and port**. See the note below |
| C3 `/health` | **HTTP 503** `degraded`, `connection.ok: false`, `schema_index.ok: true` — correctly distinguishing "the index is fine, the server is not" |
| C5/C6 recovery | `/health` → 200 `ok`; the question **succeeded with no app restart** |

On C2b: the raw driver text reaching a response body is the same class
as `LOP-008` (already open, S3), which concerns driver errors reaching
`error_history`. What this run adds is that the disclosure includes
**database host and port** — `redact_secrets` removes connection
*secrets*, not topology — and that it happens on `/execute`'s `error`
field too, not only `error_history`. Recorded against `LOP-008` rather
than opened again.

### 4.4 ChromaDB — Unverified here, with the reason

Chroma is embedded, not a server, so "killing" it means making its
persist directory unavailable. On Windows the running process holds open
SQLite handles, so the directory cannot be renamed or deleted while the
app is up — the failure mode this drill wants cannot be induced without
stopping the app, which makes it a restart test rather than a resilience
test.

What *was* verified: `/health` reports the schema index independently of
the connection (§4.3's C3 shows `schema_index.ok: true` while
`connection.ok: false`), and building a brand-new index into an empty
persist directory works end to end (§9.2). The genuine Chroma failure
modes for UAT are "index empty" and "index stale", both of which
`/health` already reports per database ("Index is empty -- run
scripts/build_embeddings.py").

**Recorded as Unverified, not passed.** §10 has the command.

---

## 5. What the router actually does on this hardware

This is the sharpest finding of the phase and it was found while
measuring something else.

`agent/orchestrator/nodes.py::router_node` gives the LLM classifier
`router_timeout_seconds` (default **5.0 s**) and otherwise falls back to
`keyword_fallback` — pure regex, no model. `docs/ROUTING.md` already
records this honestly: on the reference hardware routing costs 31–40 s,
so "the keyword fallback routes every question", and it calls that "the
intended outcome, not a defect: the fallback is correct on every case the
brief names".

**Measured over eight consecutive live `/ask` calls: 7 of 8 fell back
(87.5%).** So the fallback is not a degraded mode here; it is the router.

And the brief's cases are no longer the corpus — `eval/uat_golden/` is,
and it is what this project gates UAT on. Routing its **28 accuracy
cases** through `keyword_fallback` offline (milliseconds, no model) puts
**14** somewhere other than `sql_database`. Two of those are defensible
and are excluded by id with the reason. That leaves **12 of 26
unambiguous database questions — 46%**:

| Destination | Count | Example |
|---|---|---|
| `general_knowledge` | 10 | "What was our total revenue last year?", "What is the average order value?", "List the products that are the color black.", "Show sales by country as a chart." |
| `web_search` | 1 | "Show me the product name, category, subcategory, colour, **list price** and standard cost for every bike." |
| `policy_kb` | 1 | — |

Per journey: **J4 reaches the database on 1 of 4** — the journey the UAT
script's own §2.5 calls *"the single highest-value row in this
document"*. **J11 on 0 of 2.** J5 on 8 of 11, J9 on 4 of 7.

### 5.1 Two causes, both visible in `_FALLBACK_RULES`

1. **`web_search` is tested before `sql_database` and its vocabulary is
   not news-specific.** The pattern matches a bare `price`, so the bike
   question goes to Tavily; a bare `score` sends "average employee
   satisfaction score per product" the same way. Confirmed live, three
   times consecutively: HTTP 200, `status="succeeded"`,
   `sources_used: ["web"]`, **184 s**, no SQL, no rows, and an **empty
   `synthesized_answer`** — a three-minute wait ending in a blank answer
   to a question the database could have answered in 8 ms.
2. **The `sql_database` pattern is a list of aggregate and superlative
   phrasings**, so an ordinary retrieval or ratio question matches
   nothing and falls through to `general_knowledge`: "total revenue last
   year" has no `total … by`, "average order value" reads as prose,
   "List the products…" needs the literal `list all`.

Falling through to `general_knowledge` is the **worse** outcome, not the
milder one. `ENABLE_GENERAL_KNOWLEDGE_ANSWERS` is off by default, so the
caller gets one fixed no-match sentence; and per `HAL-006` **no input
guard runs on that route**, so the question is never recorded as refused
either.

### 5.2 Flagged, not fixed — and why

Reordering the rules or narrowing the web vocabulary both change routing
behaviour that nothing currently measures, and the ordering is deliberate
and documented (so "the top international news stories today" stays web).
That is rule 8 territory.

**Proposed fix, for an owner to accept or reject:** when the question
names a catalog keyword — which `_catalog_keywords` already computes, and
which the `sql_database` rule already consults — test `sql_database`
first. A question that names something the deployment actually holds is a
database question before it is a news question. Then narrow the web rule's
generic commercial words (`price`, `prices`, `stock`, `score`, `scores`,
`current`) to require an external-world signal, and widen the
`sql_database` rule beyond aggregate phrasing. Either change should be
decided with `scripts/run_benchmark`, not from the code.

Pinned by `tests/test_router_fallback_reaches_the_database.py`: 5 passing
tests hold the fallback's documented-correct cases (news still goes to
web, aggregates still go to SQL, greetings still go to conversation, and
rule 4 still refuses a question naming nothing in the catalog), and 8
`xfail(strict=True)` tests fail the day this is fixed. Needs no database,
Ollama or network.

---

## 6. Backup and restore

### 6.1 Identity database — drilled end to end

| Step | Command | Measured |
|---|---|---|
| Dump | `pg_dump -Fc` | **1 s**, 607,252 bytes from a 15 MB database |
| Provision scratch | `docker run postgres:18-alpine` | ~15 s |
| Restore | `pg_restore --no-owner --no-privileges` | **5 s** |
| Verify (structural) | row counts | 36 tables, 27 users, 196 conversations, 404 `ai_outputs`, Alembic head `q9u2v5w8x1y4` — all identical to source |
| Verify (functional) | sign in against the restored copy | **HTTP 200**, token issued, `/auth/me` returns the right email and roles (§4.2 B1) |

The functional step is the one that matters and is the one a row-count
comparison cannot give: it proves the Argon2id hashes, the role grants
and the tenant rows all survived the round trip.

**RTO achieved: under one minute at this data volume**, dominated by
provisioning rather than by data. It will grow with `ai_outputs`, which
is the table that accumulates.

**RPO achieved: unbounded — there is no backup.** `CLAUDE.md` §13
records that this project has no scheduler and no background worker, and
nothing in `scripts/` takes a backup. So the recovery point is "whenever
someone last ran `pg_dump` by hand", which before this session was never.
That is `OPS-018`, and the honest statement for UAT is that **a lost
identity database loses every account, conversation and stored answer.**

**One trap worth not rediscovering:** `identity_admin` — the role in
`AUTH_DATABASE_URL` — **cannot create a database**
(`ERROR: permission denied to create database`), which is correct
least-privilege and also means it cannot perform a restore-to-new-database
on its own. A restore needs a role with `CREATEDB`, or a
pre-created empty database. Any runbook that assumes the application's own
credentials suffice is wrong.

### 6.2 ChromaDB — a directory copy, and it does not need a drill

The index is 41 MB in 805 files under `embeddings/.chroma`. It is
**regenerated data, not a backup target**: `scripts/build_embeddings.py`
rebuilds it from live introspection, `/health` reports per database
whether it is populated, and `.gitignore` already treats it as generated.
So its RPO is "whatever the current schema is" and its RTO is one command.

Measured for the Postgres database created in §9.2: a full build of a
3-table schema took **about one second**. The `adventureworks` index (36
tables) is the realistic case and was not rebuilt in this session, so no
figure is claimed for it.

The practical backup instruction is therefore *not* "copy the directory"
but "be able to run `build_embeddings.py`", which needs the database
reachable — which is why `OPS-014` below matters more than a Chroma
backup would.

---

## 7. Health and readiness

### 7.1 What is correct

- `/live` and `/health` are properly separated, and the separation is
  load-bearing rather than cosmetic: with Ollama down, `/live` → 200 and
  `/health` → 503. `/live` touches no dependency, so its cost does not
  scale with how many are configured.
- `/health` reports **real, non-cached** state per configured database,
  distinguishing the connection from the schema index — measured
  `connection.ok: false` with `schema_index.ok: true` while the server
  was down (§4.3 C3).
- It returns **503 with `status: "degraded"`**, not a 200 with a sad
  body, so orchestrator tooling that reads the status code behaves
  correctly.
- Driver and Chroma exception text is passed through `redact_secrets`
  before being returned, which matters because this endpoint is
  unauthenticated.

### 7.2 `OPS-014` — the identity database is not checked

`HealthResponse` (`api/schemas.py`) has fields for `databases`, `ollama`,
and feature availability. **It has no field for the identity database.**
`local_auth_enabled` is a configuration flag, not a probe. So in a
deployment where identity is a separate PostgreSQL — which is the
documented topology — stopping identity alone leaves `/health` at **200
`"ok"`** while every login, every conversation read and every
locally-authenticated question fails.

This was derived from the schema rather than from the drill, and the
distinction is worth stating: in §4.2 the identity database and the
business database were the same container, so that 503 is attributable to
the `postgres_uat` row and **cannot** confirm or refute this. The schema
is unambiguous on its own.

Why it matters operationally: `/health` is what entry criterion **E1**
tells a UAT operator to check, and what the Dockerfile's `HEALTHCHECK`
and the compose healthcheck both poll. A container whose identity
database is unreachable currently reports itself healthy and keeps
serving, and `restart: unless-stopped` will never act.

**Flagged, not fixed.** Adding a component to `HealthResponse` is an API
contract change on a frozen response model (`CLAUDE.md` §6.3), and it has
a real design question attached: a probe must not become a login-rate
amplifier or a new unauthenticated information channel.

**Proposal:** an optional `identity: ComponentHealth | None` field —
optional with a default, so the change stays backward compatible — set
only when `local_auth_enabled` is true, implemented as `SELECT 1` on the
identity engine (the same shape `test_connection` already uses), and
folded into `overall_ok` so the status code tells the truth. Pair it with
returning **503** rather than 500 from the identity routes when the
database is unreachable (§4.2), so the two surfaces agree.

### 7.3 One number worth knowing

`/health` takes **2.43 s** on this deployment, because it tests four
database connections, counts four Chroma collections and calls Ollama's
`list()` on every request. That is correct for a readiness probe and is
exactly why `/live` exists; it is also why `/health` should not be
anyone's liveness probe. The compose and Dockerfile healthchecks poll
`/health` every 30 s with a 5 s timeout, which fits — but the margin is
2.6 s, and it shrinks as connections are added.

---

## 8. Container

### 8.1 Hardening — verified by reading the files, which is where it lives

| Control | State |
|---|---|
| Non-root | **Yes.** `groupadd --gid 1000 app && useradd --uid 1000`, `USER app` before `CMD` |
| Base image pinned | **Yes, by digest**, not tag — `python:3.11-slim@sha256:9534e5a8…` and `node:22-slim@sha256:83f487e0…` |
| Capabilities | **`cap_drop: [ALL]`** in `docker-compose.yml` |
| Privilege escalation | **`no-new-privileges:true`** |
| Resource limits | **`cpus: "2.0"`, `memory: 4G`** |
| Port binding | `${API_BIND_HOST:-127.0.0.1}:8000` — localhost by default, not `0.0.0.0` |
| Healthcheck | present in both the Dockerfile and compose |
| Dev dependencies | **excluded** — the image installs `requirements.txt`, not `requirements-dev.txt` |
| Build toolchain | `build-essential` purged in the **same** `RUN` layer as the pip install |
| Read-only root filesystem | **No**, and deliberately not — see §8.3 |

### 8.2 CVE scan

Trivy 2026-10-30, `--scanners vuln --severity HIGH,CRITICAL`, against the
**pinned base image digest**:

| | Count |
|---|---|
| CRITICAL | **3** |
| HIGH | **60** |
| With a fixed version available | **19 of 63** |

All three CRITICALs are `perl-base` 5.40.1-6 (CVE-2026-13221,
CVE-2026-42496, CVE-2026-8376), all fixed in `5.40.1-6+deb13u1`. The
other heavily-affected packages are the `util-linux` family
(`bsdutils`, `libmount1`, `libblkid1`, `libuuid1`, `libsmartcols1`,
`liblastlog2-2`, `login`, `mount` — 4 each) and `libpcre2-8-0`.

Two observations rather than alarm. **First, none of these is this
project's code or its Python dependencies** — they are Debian packages in
the upstream `python:3.11-slim` image, and `perl` is not invoked by
anything this application does. **Second, the remedy is a base-image
bump, and the Dockerfile's own comment says that digest must be
re-verified deliberately rather than automatically** — which is the right
policy and also why 19 fixable findings are sitting here. A
`apt-get upgrade` line in the Dockerfile would close most of them at the
cost of making the build non-reproducible; bumping the digest keeps
reproducibility. That is the decision to take, and it is an owner's.

### 8.2.1 `OPS-021` — and the image could not be built at all

Attempting the application image scan is how this was found, which is the
argument for attempting a check even when a document says it is covered.

```
ERROR: Could not find a version that satisfies the requirement numpy==2.5.3
ERROR: No matching distribution found for numpy==2.5.3
```

`requirements.txt` pinned `numpy==2.5.3`, and **numpy 2.5.x declares
`Requires-Python >=3.12`** while the Dockerfile and CI both target 3.11.
The single-image deployment this project documents as its entire
footprint could not be produced.

**It is a regression introduced by Phase 8 of this same audit.** `BP-009`
declared `numpy` as a direct dependency — correctly; it is imported by
`attachments/inpaint.py` — and pinned whatever the **3.14 dev venv** had
installed. The image had been built and verified *from the inside* on
2026-10-17, and that pass recorded `numpy 2.4.6` resolving transitively
within it, so this broke on 2026-10-29. It is the same class as the scipy
incident `requirements.txt` documents at length, reached from the
opposite direction: there a 3.14-chosen pin had no 3.11 wheel, here it
excludes 3.11 by metadata.

Repinned to `numpy==2.4.6` — `Requires-Python >=3.11` with cp311, cp312,
cp313 **and** cp314 wheels, so one pin serves both targets without a
marker that would let them drift apart again. `pip check` clean, 465
numpy/scipy-dependent tests pass, and the image then built.

**Why nothing caught it, which is the reusable part.**
`tests/test_declared_dependencies.py` asserts a package is *declared*; it
never asserts a pin is *installable on 3.11*, which no mocked test can
know. `docker build` is the only gate that does — and it lives in the
`docker-scan` job whose Trivy step is report-only. Both `CLAUDE.md` §13
and `SECURITY.md` describe "the Trivy container scan" as non-blocking,
which reads naturally as *the container job is advisory*. It is not: the
`run: docker build -t text-to-sql-dashboard:ci .` step above the scan
fails the job like any other command, so that job has been red since
2026-10-29. `tests/test_ci_gate_coverage.py` now asserts that step
exists, builds the repository Dockerfile, and is not
`continue-on-error`.

So the honest blocking-gate count is **ten**, not nine.

### 8.3 Read-only root filesystem — the specific reason it cannot be set

`docker-compose.yml`'s comment says `read_only: true` was not added
because "this app writes to more paths than the one named `chroma_index`
volume already covers". That is correct, and here is the enumerated list,
derived from the source rather than guessed:

| Path | Written by |
|---|---|
| `settings.chroma_persist_dir` | `embeddings/schema_indexer.py`, `schema_drift/snapshot_store.py` — **already a named volume** |
| `settings.attachment_storage_dir` (`./data/attachments`) | `attachments/storage.py` |
| `media/.thumbnails` | `media/keyframes.py` — **inside the package directory**, i.e. a write into the application's own source tree |
| system temp | `tempfile.mkstemp` in `rag/ingestion.py`, `attachments/processors/pdf_processor.py`, `media/ingest.py` |
| `/home/app/.cache` | sentence-transformers / faster-whisper model caches |
| `voice/models/` | the Piper voice model |
| **`.env`** | `onboarding/env_registration.py` — writes the repository's own `.env` at runtime |

The last row is the one that settles it: **a read-only root filesystem
would break database onboarding, which is UAT journey J2.**
`env_registration.py` writes a new `DB_<NAME>_*` block into `.env` and
can roll it back, so the onboarding lifecycle needs its own configuration
file to be writable.

`media/.thumbnails` is worth a separate note: writing into the package
directory means the application's code directory must be writable, which
is its own small hardening problem independent of `read_only`.

**Flagged as a documentation improvement rather than a change:** the
compose comment lists four paths and is missing `.env` and
`attachment_storage_dir`, the two that actually decide the question. A
reader could conclude from it that the remaining work is a few `tmpfs`
mounts; it is not, while onboarding writes `.env`.

---

## 9. The UAT environment

### 9.1 Accounts — present, with one mismatch to resolve

The six role accounts exist (created during Phase 9) and their grants
were read from the identity database:

| UAT role | Account | Roles granted | Matches §2.1? |
|---|---|---|---|
| R1 Platform Admin | `uat9.r1@example.com` | `user`, `platform_admin`, `admin` | yes (with the `admin` second account §2.1 recommends) |
| R1b | `uat9.r1b@example.com` | `platform_admin` | yes — the `AUZ-004` control case |
| R2 Tenant Admin | `uat9.r2@example.com` | `user`, `admin` | yes |
| R3 SME / Semantic Reviewer | `uat9.r3@example.com` | `user`, **`manager`** | **no** — §2.1 specifies `analyst` |
| R4 Business Analyst | `uat9.r4@example.com` | `user`, `analyst` | yes |
| R5 Business User | `uat9.r5@example.com` | `user` | yes |
| R6 Read-only Viewer | `uat9.r6@example.com` | `viewer` | yes |

**R3 is granted `manager`, while §2.1 says `analyst`** — which it says
precisely because `analyst` carries `catalog.review`,
`governance.review` and `recommendation.review`, the J3 and J4
permissions. Whether `manager` carries them was not measured here, so
this is reported as a mismatch to resolve rather than as a defect in
either place. Resolve it before E4 is signed: either grant R3 `analyst`
per the document, or measure `manager` and correct the document.

### 9.2 `OPS-016` — both dialects now have an environment, and the
Postgres leg is live-verified

`eval/uat_golden/sql_accuracy.yaml` carries five cases whose `database:`
is `postgres_uat`, every one marked `UNVERIFIED` because the authoring
machine had four `mssql` connections and no PostgreSQL business database.
Nothing described how to create one, so "both dialects" was a property of
a question file and not of any environment a tester could sign in to.

`eval/uat_golden/seed_postgres_uat.sql` is that environment: a
snake_case Postgres star schema (`dim_product`, `dim_date`, `fact_sales`)
shaped like a Postgres warehouse rather than a copy of AdventureWorks,
which is what makes the TOP-versus-LIMIT case a real test instead of the
same test twice. Verified by running it:

```
12 products | 3 calendar years (2023-2025) | 272 sales rows | max year 2025
total sales by year: 2023 = 473,878   2024 = 947,756   2025 = 1,141,379
top product by sales: Road Bike 150 = 1,127,155
```

The year factor in the quantity expression is deliberate: without it
every full year totalled identically, and `uat_pg_j9_sales_by_year` plus
any year-over-year question become untestable — a correct answer and a
broken one both read as "no change".

**Then the whole Postgres path was exercised live for the first time**,
on the port-8001 instance:

- `scripts/build_embeddings.py` → connection test passed, 3 tables
  introspected, collection `schema_ddl__postgres_uat` built.
- `/health` → 200 `ok`, `postgres_uat` connection and index both ok.
- "How many products are there?" →
  `SELECT COUNT(product_key) FROM dim_product LIMIT 1000`, 1 row, **2.0 s**.
- "What are total sales by product category?" →
  `SELECT category, SUM(sales_amount) AS total_sales FROM fact_sales AS fs
  INNER JOIN dim_product AS dp ON fs.product_key = dp.product_key
  GROUP BY category LIMIT 1000`.

Both use `LIMIT`, neither uses `TOP`: the dialect difference is handled
in the adapter and does not leak into the agent, which is exactly the
claim those five cases exist to test. **The golden cases' own
`expected_sql` still requires a human** (they are marked
`GOLD SQL REQUIRED`), so this verifies the *path*, not the *answers*.

The `.env` block and the read-only GRANTs are in §10.

A second, unrelated observation from this instance: the same question
answered in **2.0 s** against a 3-table Postgres schema versus **79–127 s**
against `adventureworks`. The prompt is the difference, and it is the
cleanest evidence in this session for the relationship Phase 7 measured.

### 9.3 `OPS-017` — the feature-flag profile

`config/settings.py` carries **74** flag-shaped settings. The UAT script
named **5** in prose, and entry criterion **E6** asked for "the feature
flags the journeys in scope need are on, and the ones out of scope are
off" without anywhere defining which those are — an unactionable
criterion.

The profile now exists as **§2.4 of
`docs/certification/UAT_SCRIPTS_AND_SIGNOFF.md`** (the doc an operator
actually reads), split into required-on, required-if-in-scope, and
turn-off-for-UAT, with each flag's real default beside it because a flag
absent from `.env` is not necessarily off.

The measured delta that motivated it: **five subsystems were on in this
environment and no journey exercises any of them** —
`ENABLE_MEDIA_GENERATION`, `ENABLE_IMAGE_EDITING`, `ENABLE_VOICE_MODE`,
`ENABLE_MEDIA_SEARCH`, `ENABLE_POLICY_RAG`. Two carry more than unused
surface: media generation **spends money** per call against IMA Studio,
and voice mode is subject to the open `UI-013` (live captions send
microphone audio to the browser vendor with no on-screen notice) — which
is not something to put in front of business users while the disclosure
is missing.

Also recorded there: `BOOTSTRAP_ADMIN_ENABLED=true` with
`BOOTSTRAP_ADMIN_PASSWORD` still present in the environment, which §2.1
step 2 says to unset once the account exists.

### 9.4 `OPS-011` — the commands at the top of the script did not run

`UAT_SCRIPTS_AND_SIGNOFF.md` §2.1 step 1 contained a literal **BEL
(0x07)** where `identity\alembic.ini` was meant and a literal
**backspace (0x08)** where `scripts\bootstrap_admin.py` was meant: the
backslash escapes were interpreted on the way to disk. Both bytes are
invisible in GitHub, in VS Code's preview and in a terminal `cat` — the
page renders `identitylembic.ini` and nothing marks it wrong, while a
terminal that honours BEL beeps. Phase 10 measured "449 documented
command lines all run as written" and these two were among them, because
a reviewer reading the rendered page cannot distinguish a missing
character from a missing escape.

Fixed, along with three backspaces in
`governance/domain_detection.py`'s docstring (prose only — the regexes
were never affected), and pinned by
`tests/test_no_control_characters_in_tracked_text.py`, which scans every
`git ls-files` text file and skips binaries by NUL detection. Proven
non-vacuous against the committed version: the detector returns
`{7: 1, 8: 1}` on `git show HEAD:…UAT_SCRIPTS_AND_SIGNOFF.md` and `{}` on
the fixed file.

---

## 10. What a human must still run

Nothing here was skipped silently; each is blocked by this environment.

1. **The two service-level resilience drills**, from an elevated shell:

   ```powershell
   Stop-Service postgresql-x64-18      # identity DB, then re-check /health and /auth/login
   Start-Service postgresql-x64-18
   Stop-Service MSSQLSERVER            # the real target DB, then re-ask a question
   Start-Service MSSQLSERVER
   ```

   §4.2 and §4.3 exercised the same code paths against a Docker Postgres.
   What this adds is SQL Server's own driver text (`pyodbc` 08001 /
   `Communication link failure`), which `OPS-019`'s classifier handles by
   keyword but which has not been seen live.

2. **The ChromaDB unavailability drill**, on Linux or with the app
   stopped, where the persist directory can be moved while handles are
   open:

   ```bash
   mv embeddings/.chroma embeddings/.chroma.bak && curl -s localhost:8000/health
   mv embeddings/.chroma.bak embeddings/.chroma
   ```

3. **The application image CVE scan.** The image builds again after
   `OPS-021`; scanning the built image was not completed here, so only
   the base-image figures in §8.2 are measured:

   ```bash
   docker build -t text-to-sql-dashboard:local .
   docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest \
     image --scanners vuln --severity HIGH,CRITICAL text-to-sql-dashboard:local
   ```

   Worth also running the smoke check the 2026-10-17 pass used, since it
   is what would have caught `OPS-021` from the inside:

   ```bash
   docker run --rm --entrypoint python text-to-sql-dashboard:local -c \
     "import sys, numpy, scipy, api.main; print(sys.version, numpy.__version__, scipy.__version__)"
   ```

4. **A real load test at the intended UAT concurrency on the intended
   hardware.** §3's numbers are this laptop's. If UAT runs anywhere else,
   re-measure — and use `eval/load/run_load_test.sh full` for the API
   tier, but drive the real Ollama for the capacity question.

5. **The `postgres_uat` connection, if the Postgres journeys are in
   scope.** Create the database, run the seed as a user that may create
   objects, point a read-only role at it
   (`docs/DBA_READONLY_ACCOUNTS.md` has the GRANTs), then:

   ```
   DB_CONNECTIONS=<existing>,postgres_uat
   DB_POSTGRES_UAT_TYPE=postgresql
   DB_POSTGRES_UAT_HOST=<host>
   DB_POSTGRES_UAT_PORT=5432
   DB_POSTGRES_UAT_NAME=postgres_uat
   DB_POSTGRES_UAT_USER=<read-only role>
   DB_POSTGRES_UAT_PASSWORD=<its password>
   DB_POSTGRES_UAT_SCHEMA=public
   ```

   ```powershell
   psql -h <host> -U <owner> -d postgres_uat -v ON_ERROR_STOP=1 -f eval\uat_golden\seed_postgres_uat.sql
   python scripts\build_embeddings.py
   ```

6. **A scheduled backup of the identity database** (`OPS-018`). There is
   no scheduler in this project by design, so this is an operating-system
   or platform task:

   ```powershell
   pg_dump -h <host> -U <role> -d text_to_sql_identity -Fc -f identity_$(Get-Date -f yyyyMMdd).dump
   ```

   Restore needs a role with `CREATEDB` or a pre-created empty database —
   `identity_admin` cannot do it (§6.1).

7. **`expected_sql` for the golden cases marked `GOLD SQL REQUIRED`**,
   including the five Postgres ones that now have an environment to run
   in. §9.2 verified the path, not the answers.

---

## 11. Session summary

- **S1 found and fixed: 1.** `OPS-021` — `docker build .` failed
  outright, so the image this project ships could not be built. A
  regression introduced by Phase 8 of this audit, invisible because the
  only gate that could see it sits in a job widely described as
  advisory. `AUZ-002` remains the project's only *open* S1 and is not
  this phase's.
- **S2 open from this phase: 2** — `OPS-012` (the queue's 120 s budget
  versus `/ask`'s 600 s, measured on both sides) and `OPS-015` (the live
  router misses 46% of the golden SQL set, including three quarters of
  J4). Both flagged for an owner with a concrete proposal; neither is
  fixable without changing behaviour nothing currently measures.
- **Fixed this session: 6** — `OPS-011`, `OPS-013`, `OPS-016`,
  `OPS-017`, `OPS-019`, `OPS-021`. Five shipped with tests that fail
  before and pass after; `OPS-013` and `OPS-019` are mutation-verified.
- **Flagged for a decision: 4** — `OPS-012`, `OPS-014`, `OPS-015`,
  `OPS-020`.
- **The capacity answer UAT needs, in one line:** this hardware serves
  **one question at a time**; three testers at once triples latency to
  ~195 s, and six breaks it — 5 of 6 fail on the 300 s per-call timeout
  and the provider circuit breaker opens, penalising the next user too.
- **Next phase:** 12 — the consolidated UAT readiness report and gate.
