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
   or of SQL Server specifically. §11 names what a human with elevation
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
| `OPS-022` | **S2** | Onboarding's published semantic meaning is filed where the SQL pipeline never looks | **Flagged** (test only) |
| `OPS-023` | S3 | The R3 account's roles over-grant on one authz surface and under-grant on the other | **Flagged** |
| `OPS-024` | S3 | The blocking `pip-audit` gate had gone red again — 11 advisories in `pypdf` and `pyasn1` | **Fixed** |
| `OPS-025` | **S2** | `retrieve_schema`'s terminal failure was not terminal — the run continued, spent a model call, and answered HTTP 500 | **Fixed** |
| `OPS-026` | S4 | `GET /health` discloses the schema-index directory's absolute path to an unauthenticated caller | **Flagged** |

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

### 4.4 ChromaDB — drilled, and it found `OPS-025`

This section recorded **Unverified** until 2026-10-31, on the reasoning
that Chroma is embedded rather than a server, so "killing" it means
making its persist directory unavailable — and on Windows the running
process holds open SQLite handles, so the directory cannot be renamed
while the app is up. That reasoning was sound about the *mid-request*
case and wrong to stop there, because it never tested the case a UAT host
actually reaches: the directory being unusable **when a question
arrives**.

Driven without touching the live persist directory, by pointing
`CHROMA_PERSIST_DIR` at a path of this session's choosing. That matters
beyond convenience: `CLAUDE.md` §12 was corrected earlier in this phase
to say the directory holds golden examples, message feedback and
schema-drift baselines with **no live source to rebuild from**, so moving
the real one is the drill design to avoid.

Two separate unavailability modes, because they fail differently:

| | Observed |
|---|---|
| D1 `/health`, persist path is a regular file | **HTTP 503 `degraded`**, and per database `schema_index.ok: false` with `connection.ok: true` — the exact mirror of §4.3's C3, so the endpoint distinguishes "the server is fine, the index is not" in both directions |
| D1b app startup, same state | **Starts normally.** Correct per `CLAUDE.md` §4.2: the index is not a safety control, and refusing to boot would make a recoverable misconfiguration an outage |
| D2 `run_agent`, index absent (empty directory) | **Before `OPS-025`:** the node composed its refusal, the graph drove straight past it, `generate_sql` spent a model call on a prompt with no schema, and the run died 68.43 s later in `RuntimeError: reached with no selected_database` → HTTP 500. **After:** `0.38 s`, `status="failed"`, one `error_history` entry, `attempt_history[0].outcome == "schema_retrieval_error"`, `will_retry: False` |
| D3 `run_agent`, persist path is a regular file | **Before:** `FileExistsError [WinError 183]` out of `run_agent` in 0.04 s — `OSError`, which `except SchemaRetrievalError` never matched. **After:** `0.05 s`, `status="failed"`, path-free explanation naming `CHROMA_PERSIST_DIR` |
| D4 recovery | A valid directory is picked up with **no restart** for D2/D3, because `get_chroma_client` re-`mkdir`s per call and the cached client is keyed by path — but `CHROMA_PERSIST_DIR` itself is read from frozen `Settings`, so *changing the setting* is a restart either way |

**The finding is the drill's whole value, and it is why "Unverified" was
not a safe place to leave this.** `OPS-025` is the third instance of the
shape Phase 6 named — `LOP-001` (recursion limit) and `LOP-004`
(`ProviderUnavailable`) were the same defect: a *designed* terminal
condition escaping `run_agent` as an unhandled exception, so `POST /ask`
answered HTTP 500 instead of the refusal the pipeline had already
composed. Here the handler and the message both already existed; only the
edge out of the node was unconditional, while the three other nodes that
can end a run all route to `END` through a `route_after_*` function.

What makes it S2 rather than an edge case is how it is reached: an
unbuilt index is a **forgotten setup step**, not a rare fault.
`scripts/build_embeddings.py` is in `CLAUDE.md` §9's run order, §11 item
5 of this document tells an operator to run it, and `GET /health` already
reports "Index is empty — run scripts/build_embeddings.py" per database.
So on a fresh UAT host that skipped it, every question took ~70 s and
returned an opaque 500 while the health endpoint was simultaneously
reporting the exact cause.

`OPS-026` came out of the same drill and is recorded separately: D1's
`schema_index.detail` carries the persist directory's **absolute path**,
and `/health` takes no credentials.

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

### 6.2 ChromaDB — the schema index is derived, but the directory is not

An earlier draft of this section said Chroma "deliberately needs no
backup". That is **wrong in the dangerous direction**, and
`docs/DEPLOYMENT.md`'s existing "Backup & recovery" section already says
why in its own last bullet, which is worth reading past the first one.

The persist directory is not one thing. Enumerated by reading every
non-test caller of `embeddings.schema_indexer.get_chroma_client` —
`embeddings/golden_examples.py`, `feedback/store.py`, `media/store.py`,
`retrieval/vector_store.py`, `embeddings/retriever.py`,
`agent/orchestrator/router_catalog.py` — plus
`schema_drift/snapshot_store.py`, which writes
`.schema_drift_snapshot__<db>.json` files *beside* the Chroma store in the
same directory:

| What | Rebuildable from | Collections here |
|---|---|---|
| Schema DDL index | live introspection, `scripts/build_embeddings.py` | 7 `schema_ddl__*` |
| Business-context chunks | `python -m scripts.ingest_schema` | 4 `knowledge_base__*` (453 of them `adventureworks`) |
| **Golden examples** | **nothing — human-curated** | 4 `golden_examples__*`, **6 items** |
| **Message feedback** | **nothing — human-curated** | `response_feedback`, **8 items** |
| Media index | re-ingesting the media library (OCR/ASR cost) | `media_images`, `media_video_segments`, 0 items here |
| Schema-drift baselines | **nothing — a point-in-time record** | JSON files, not collections |

Measured for the Postgres database of §9.2: a full build of a 3-table
schema took **about one second**. The `adventureworks` index (36 tables)
was not rebuilt in this session, so no figure is claimed for it.

So the correct instruction is the one `docs/DEPLOYMENT.md` gives: rebuild
the schema index from the database, and take a filesystem-level copy of
`CHROMA_PERSIST_DIR` if golden examples, feedback and drift baselines
matter to the deployment. `AUZ-005` is relevant to how much the first of
those matters — any `user` can write a golden example that steers their
whole tenant, which cuts both ways: it is worth preserving *and* worth
being able to roll back.

41 MB in 805 files here, so a directory copy is trivial in cost — but
§6.3 is why that number is larger than it should be.

**Drilled end to end 2026-10-31**, which this section previously only
described. Cold (app stopped, so the SQLite files are consistent), copied
to a scratch path, restored to a second scratch path, and the restored
copy then **opened with a real `chromadb.PersistentClient` and its
collections counted** — because a byte-count match proves the copy ran,
not that the store is usable:

| Step | Measured |
|---|---|
| Source | 813 files, **39.1 MiB** (the 41 MB above, in MiB) |
| Backup (copy out) | **0.51 s** |
| Restore (copy back to a new path) | **0.41 s** |
| Open restored copy + list collections | **1.44 s**, 205 collections |
| **RTO achieved** | **≈ 2.4 s** end to end for a store this size |

The curated content §6.2's table says has no live source to rebuild from
was verified *in the restored copy*, not in the original:
`golden_examples__adventureworks` 4 + `golden_examples__default` 2 =
**6 golden examples**, `response_feedback` **8 items**, and **1**
`.schema_drift_snapshot__*.json` — matching the table above exactly.

**RPO is the honest half, and it is unbounded**: nothing schedules this
copy. RTO is ~2.4 s plus however long ago someone last ran it, which is
`OPS-018`'s point restated for Chroma rather than the identity database.
§11 item 6 carries the command; there is no scheduler in this project by
design, so it is an OS or platform task.

Two caveats a human should not have to rediscover. The copy was taken
**with the app stopped**; a hot copy of an open SQLite store is not
guaranteed consistent, so a zero-downtime backup needs either a Chroma-
level export or a filesystem snapshot, neither of which was drilled here.
And 205 collections is **up from the 203** counted earlier the same day —
see §6.3.

### 6.3 `OPS-022` — what counting the collections turned up

**Re-measured 2026-10-31 on the restored copy: 187 per-job collections of
205 total (91.2%), each still holding exactly one chunk** — up from the
185 of 203 recorded earlier the same day. The finding reproduces
unchanged: `knowledge_base__my_warehouse` (the `DB_CONNECTIONS` name the
pipeline queries) **still does not exist**, while `schema_ddl__my_warehouse`
does. The growth of two in a single day, with no deletions, is itself
evidence for the unbounded-growth half of the finding — one collection
per onboarding publish, none reused.

Listing the directory to size the backup is how this was found, which is
the same argument §8.2.1 makes: attempt the check even when a document
says the area is covered.

**203 collections. 185 of them (91%) are
`knowledge_base__My_Warehouse_<8 hex>`, each holding exactly one chunk.**

They are not junk, and that is the finding. Each holds a published
semantic-catalog concept, `status="published"`,
`truth_level="confirmed_business_truth"` — the output of UAT journey
**J2** (onboard a database) followed by **J3** (an SME confirms its
semantic meaning and publishes it). 185 of them because Phase 9 drove
onboarding through the browser repeatedly, and **every publish opens a
new namespace that no later publish reuses and nothing ever deletes.**

Two modules name the same onboarded database two different ways, and
neither is wrong on its own terms:

| Module | Produces | For |
|---|---|---|
| `onboarding/env_registration.py:70` `slugify_connection_name` | `my_warehouse` | the `DB_CONNECTIONS` entry |
| `onboarding/catalog_bridge.py:66` `catalog_database_id_for_job` | `My_Warehouse_33ef5683` | the catalog entry, and so the vector-store collection |

`agent/nodes.py:1255` retrieves business context with
`db_name = state["selected_database"]` — a `DB_CONNECTIONS` name. So
retrieval reads `knowledge_base__my_warehouse`, and **measured on this
checkout, `knowledge_base__my_warehouse` does not exist at all**, while
`schema_ddl__my_warehouse` does. The schema index for the onboarded
database is where the pipeline expects it; the human-confirmed business
meaning is in 185 places it never looks.

```
knowledge_base__my_warehouse exists: False
schema_ddl__my_warehouse exists:     True
knowledge_base__My_Warehouse_33ef5683: status='published' truth='confirmed_business_truth'
```

**Why this is S2 rather than a tidiness problem.** Nothing errors and no
screen looks broken: J2 completes, J3's review dashboard reads catalog
entries from the identity database and shows them correctly, and the
publish reports success. A business tester would sign both journeys off.
What never happens is the only thing they were for — the confirmed
meaning reaching the SQL prompt. It also inverts §4's provenance rule in
practice: the chunks marked `confirmed_business_truth`, the one grade that
requires an explicit human action, are precisely the ones that are
unreachable.

**It is wider than retrieval, which is what settles the severity.**
Every consumer of a published catalog entry filters it by `database_id`,
and they split cleanly into the one that can see these entries and the
ones that cannot:

| Consumer | Keys on | Sees an onboarding-bridged entry? |
|---|---|---|
| `api/semantic_catalog.py:289` — the SME review dashboard | a free `database_id` query filter, no gate | **yes** — which is exactly why J3 renders correctly |
| `agent/nodes.py:1255` business-context retrieval | `state["selected_database"]` | no |
| `api/kpi_monitoring.py:277`, `api/decision_center.py:259` | `_allowed_database`, which validates against `{d.name for d in settings.databases_for_tenant(...)}` | no — and the per-job id is **not even an addressable input**: an id outside `DB_CONNECTIONS` raises **404** |
| `api/data_quality.py`, `api/lineage.py`, `api/schema_drift.py`, `api/semantic_intelligence.py`, `api/bi_integration.py` | a configured database id | no |

So the one surface that can read these entries is the one that reviews
them, and every surface that would *act* on them is keyed to a
`DB_CONNECTIONS` name. That is the whole shape of the finding in one
line: **the review loop is closed and the consumption loop is not
connected to it.**

**Narrowed, so the fix is not aimed at the wrong module.** A catalog entry
authored directly against a configured database is filed under that
connection's own name and *is* retrievable — the four real
`knowledge_base__*` collections are named `adventureworks`, `hr`,
`test_db`, `default`, all `DB_CONNECTIONS` entries. Only the onboarding
bridge's id scheme diverges.

**Flagged, not fixed.** The job-id suffix is deliberate and documented:
`catalog_database_id_for_job`'s docstring says it exists so two labels
differing only in punctuation never share a catalog namespace — and
`slugify_connection_name` genuinely does collapse `"My Warehouse"` and
`"My-Warehouse!!"` to one name, so simply adopting it trades this finding
for a cross-database collision. A correct fix has to reconcile the two
schemes *and* backfill 185 existing collections plus their
`SemanticCatalogEntry.database_id` rows, which is a data migration and a
product decision about what happens when one label is onboarded twice.
Proposal in the register.

Pinned by `tests/test_onboarding_catalog_bridge.py
::TestTheCatalogIdAndTheConnectionNameDoNotMeet` — two
`xfail(strict=True)` that fail the day it is fixed, above a passing
non-vacuity test asserting the punctuation-collision property any
replacement scheme must keep.

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
- **The schema-index probe was confirmed in the other direction
  2026-10-31** (§4.4 D1): with the persist path unusable, `/health` →
  **503 `degraded`** with `schema_index.ok: false` and
  `connection.ok: true` on all four databases. Taken with §4.3's C3, the
  two components are independently reported, not one flag wearing two
  names — which is what the prompt's "health endpoints reflect real
  dependency health" asks for, for the dependencies §7.2 says are
  covered.

### 7.1a `OPS-026` — what that detail string contains

The same drill's response body carried, per database:

```
"schema_index": {"ok": false, "detail": "Unreachable: [WinError 183] Cannot
create a file when that file already exists: 'C:\Users\<user>\AppData\
Local\Temp\...\chroma_unavailable'"}
```

So an **unauthenticated** caller learns the absolute path of the
schema-index directory. `api/main.py:1347-1356` already anticipated
public exposure — its own comment says raw Chroma text here "is a real,
publicly-visible leak" — and applies `redact_secrets`, which covers
configured *secrets* and not filesystem topology. It is the `LOP-008`
class again, and the third place this phase has met it: §4.3's C2b found
`/execute` returning database host and port the same way.

Severity S4 and **flagged, not fixed**: it is a path rather than data, it
appears only in a misconfigured state, and the path is precisely what
tells an operator which directory is wrong — so trimming it is an
API-surface and operations trade-off rather than a defect fix. The
proposal is in the register. Worth noting that `OPS-025`'s fix, made in
the same session, deliberately kept this path **out** of the user-facing
`failure_explanation` and pinned that with a test, so the authenticated
path and the unauthenticated one now disagree about it — which is the
decision an owner should settle.

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

**R3 is granted `manager`, while §2.1 says `analyst`** — and §2.1 says
`analyst` precisely because `analyst` carries `catalog.review`,
`governance.review` and `recommendation.review`, the J3 and J4
permissions. An earlier draft of this section left "whether `manager`
carries them" unmeasured. **Measured now** (`OPS-023`), and the answer
is interesting in both directions, because this application authorizes
in two structurally different places — Phase 4's central lesson:

| Surface | `user,manager` | `user,analyst` | Difference |
|---|---|---|---|
| `identity.rbac` (re-reads the DB) | **24** permissions | 20 | `manager` is a strict **superset** — `_MANAGER = _ANALYST \| {…}` at `identity/rbac.py:361` |
| `agent.authz` (reads the JWT only) | **7** permissions | 12 | `manager` is **not a key** in `ROLE_PERMISSIONS`, so it grants nothing |

So R3 as provisioned is **simultaneously over- and under-granted**:

- **Over**, on the identity surface: `manager` adds `users.read`,
  `sessions.revoke_any`, `audit.read_any` and `admin.dashboard.read` on
  top of everything `analyst` has. An SME reviewer can read any account,
  sign any user out everywhere, read any audit record and open the admin
  dashboard. All three review permissions J3/J4 need *are* held, so the
  journeys work — but R3 stops being a usable negative control for
  Phase 4's "a reviewer may not administer" assertions.
- **Under**, on the agent surface: `manager` is absent from
  `agent.authz.ROLE_PERMISSIONS` (whose keys are exactly `viewer`,
  `user`, `analyst`, `admin`), so R3 falls back to what `user` alone
  grants and loses five permissions `analyst` would have carried:
  `view_restricted_columns`, `policy_rag_query`,
  `documents_read_sensitive`, `media_generate`, `run_optimization`.

This is `AUZ-004`'s shape — a role outside `ROLE_PERMISSIONS` granting
nothing, fail-closed and correct as a control, misleading as an account —
reached by a second role and with a concrete UAT consequence: a J3 or J4
step that reads a sensitive document or queries policy RAG fails for R3,
and the tester has no way to tell a deliberate refusal from a
misconfigured account.

**The decision the earlier draft left open is now closed by the
measurement: the document is right and the account is wrong.** `analyst`
is the grant that works on both surfaces; `manager` works on neither
cleanly. Resolve before **E4** is signed by granting R3 `analyst` and
removing `manager` — an operator action against the identity database,
so it is recorded here rather than performed by this audit (§11 item 8).

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

The `.env` block and the read-only GRANTs are in §11.

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

## 10. Gate re-measurement at the end of this phase

Shared rule 10 — the phase is not done if it broke something another
phase relies on. Run on this checkout 2026-10-31, each command exactly
as CI runs it.

| Gate | Command | Result |
|---|---|---|
| `ruff` | `ruff check .` | **0 findings**, exit 0 |
| `black` | `black --check .` | **1,692 files** unchanged, exit 0 |
| `bandit` | `bandit -c pyproject.toml -r . -f txt` | **0** issues at every severity, 0 skipped |
| `mypy` (production) | the 41-package list from `ci.yml` | **0 issues in 482 source files** |
| `pytest` | `pytest -q` | **9,687 passed, 0 failed**, 16 skipped, 11 deselected, 18 xfailed, 912 s |
| frontend `oxlint` | `npm run lint` | exit 0 — 32 warnings, **0 errors** (pre-existing; no frontend file was touched this phase) |
| frontend `vitest` | `npm run test` | **1,551 passed, 0 failed** across 107 files, 35.8 s |
| frontend `tsc` | `npx tsc --noEmit -p tsconfig.app.json` | clean |
| `pip-audit` | CI's command, 6 `--ignore-vuln` IDs | **red on 2026-10-31 (11 findings / 2 packages), green after the fix** — §10.1 |

Two notes on the numbers rather than just the numbers.

**The test count moved and the reason is attributable.** `CLAUDE.md` §13
recorded 9,485 passed / 5 xfailed on 2026-10-28. The further move within
this session — 9,674 → **9,687**, re-run after `OPS-025` — is exactly the
13 tests that fix shipped: 9 in
`tests/test_schema_retrieval_failure_is_terminal.py` and 4 added to
`tests/test_db_router.py` (11 collected there, 7 pre-existing). Nothing
else changed, and the skip and deselect counts are unmoved. This phase's own commits
account for the growth, and the xfail count rising from 5 to **18** is
deliberate: these phases pin open findings as `xfail(strict=True)` so a
fix cannot land silently (`OPS-015` 8, `OPS-022` 2, plus Phase 7's
`CTX-007` 3). **A rising strict-xfail count is not decay** — each one
fails the day its finding is fixed, which is the point. What would be
decay is a rising *skip* count, and that is unchanged at 16.

**`detect-secrets` was measured after all, and it is still red** — this
paragraph previously said no claim was made about it, which was the one
gate left unmeasured. It cannot be *refreshed* from a Windows checkout
(`CLAUDE.md` §5.7), and CI's own command cannot even be run here:
`detect-secrets scan $(git ls-files)` overflows Windows' argument limit
("Argument list too long"). But the security question it exists to answer
— *is any secret hash present that the baseline has not reviewed?* — is
answerable without touching the committed artifact: scan to a temporary
file, normalise path separators on both sides, and diff. Measured
2026-10-31:

| | |
|---|---|
| Baseline | 44 files, 672 reviewed findings |
| Fresh scan, tracked files only | 94 files, 1,469 findings |
| **Un-baselined** | **85 findings across 54 tracked files** |

By category: 60 in `tests/` fixtures, 10 Alembic revision hex in
`identity/migrations/versions/`, 10 i18n label strings in
`frontend/src/i18n/locales/{es,fr}/translation.json`, 5 example strings
in `docs/`. **The same categories `CLAUDE.md` §13's full triage already
cleared as false positives, grown from ~37 files to 54** — so the gate's
redness is still staleness, not a leak, and it is still decaying.

The one hit in production code was checked individually rather than by
category: `identity/security.py:363`, flagged `Secret Keyword`, is
`_DECOY_PASSWORD = "decoy-password-never-accepted-anywhere"` — the fixed,
public constant `AUN-001`'s timing-oracle fix spends Argon2id work on, and
it already carries `# nosec B105` explaining that treating it as a secret
would defeat its purpose. Benign, and deliberately so.

Two method notes, because both could mislead someone repeating this. A
no-`--baseline` scan also flags **`.secrets.baseline`'s own 672 hex
hashes** (detect-secrets excludes the baseline from its own scan only when
`--baseline` is passed), which inflates the raw count from 85 to 1,393 —
excluded above. And the committed baseline's keys turned out to match a
forward-slash scan almost completely (5 unreproduced entries), so it was
generated on Linux, consistent with §5.7's instruction to refresh it
there.

**`pip-audit` was re-run, and it was red**; see §10.1. Re-verified green
at the end of this session: `No known vulnerabilities found, 7 ignored`,
exit 0.

---
### 10.1 `OPS-024` — the `pip-audit` gate had gone red again

§10's table originally recorded `pip-audit` as *not re-run*, with
`CLAUDE.md` §5.7's reasoning quoted as the excuse: its result is a
statement about a date. Re-running it is how that stopped being an
excuse. The first attempt failed on a transient DNS outage
(`Failed to resolve 'pypi.org'`) — which is worth recording, because a
tool that cannot reach an index **exits non-zero and looks exactly like
a finding**. Retried once DNS recovered:

```
Found 11 known vulnerabilities, ignored 7 in 2 packages
pypdf  6.21.2  CVE-2026-119203/119204/119205  -> 6.21.4
pypdf  6.21.2  CVE-2026-119206/119207/119208/119209 -> 6.21.3
pyasn1 0.6.4   CVE-2026-119194/119195/119196/119197 -> 0.6.5
```

**Fixed by bumping both pins, not by ignoring anything** — the response
this repository has already chosen twice for this same package, and for
the same reason: `attachments/pdf_safety.py`,
`attachments/pipeline.py`, `attachments/processors/pdf_processor.py`,
`rag/ingestion.py` and `api/documents.py` all point `pypdf` at
caller-supplied bytes, so a PDF-parser advisory here is reachable by an
ordinary upload. `pyasn1` is reachable one step further out: it is what
`google-auth` decodes OIDC id-token certificates with, so it parses
bytes fetched from a remote issuer.

Both candidates were checked against the deploy target before being
pinned, which is the `OPS-021` lesson applied rather than restated:
`pypdf==6.21.4` declares `Requires-Python >=3.9` and `pyasn1==0.6.5`
`>=3.8`, both pure-python `py3` wheels, so one pin serves the 3.11 image
and the 3.14 local venv with no marker that could let the two drift.
`pip check` → "No broken requirements found"; the gate then reports
**"No known vulnerabilities found, 7 ignored"** (exit 0).

**This is the third time this one `pypdf` pin has gone red** (2026-10-22,
2026-10-25, 2026-10-31), which is the useful part. The decay is not a
lapse in anyone's diligence — it is the normal behaviour of a pinned
dependency with an active security tracker, and it means **a green
`pip-audit` recorded in a document is evidence about a date and nothing
else**. Any phase that reports this gate as green should print the date
beside it, as §10's table now does.

One side effect, benign and noted so it is not rediscovered as a
failure: `pypdf` 6.21.4 deprecates `add_js`, which
`tests/test_attachments_processors.py` uses *deliberately* to build a
PDF carrying embedded JavaScript for the rejection test. The test
passes and the warning is correct; the call must stay until pypdf 7.0
forces `add_open_action`, because replacing it would change what the
fixture is an attack sample of.

---

## 11. What a human must still run

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

2. **The *mid-request* ChromaDB drill only.** §4.4 now covers the
   unavailable-at-startup and unavailable-when-a-question-arrives cases,
   including recovery, and it found `OPS-025` doing so. What is still
   unverified is the directory becoming unavailable **while a request is
   in flight**, which Windows will not allow — the running process holds
   open SQLite handles. On Linux, with the app up:

   ```bash
   mv embeddings/.chroma embeddings/.chroma.bak   # while a question runs
   curl -s localhost:8000/health
   mv embeddings/.chroma.bak embeddings/.chroma
   ```

   Expect it to add little: the cached client already holds its handles,
   so this measures Chroma's own behaviour on a vanished file rather than
   this application's. Do it on a copy, never the live directory — §6.2
   is why.

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

6. **A scheduled backup of the identity database *and* the Chroma
   persist directory** (`OPS-018`). There is no scheduler in this project
   by design, so this is an operating-system or platform task. Both are
   now drilled with measured RTOs (§6.1, §6.2); what neither has is a
   schedule, which is the whole of the remaining RPO problem:

   ```powershell
   pg_dump -h <host> -U <role> -d text_to_sql_identity -Fc -f identity_$(Get-Date -f yyyyMMdd).dump
   Stop-Service <the api service>   # a cold copy; see §6.2's caveat
   Copy-Item -Recurse embeddings\.chroma chroma_$(Get-Date -f yyyyMMdd)
   Start-Service <the api service>
   ```

   Restore of the identity database needs a role with `CREATEDB` or a
   pre-created empty database — `identity_admin` cannot do it (§6.1). The
   Chroma copy needs the app stopped to be consistent, and it is the only
   copy of the golden examples, message feedback and drift baselines
   (§6.2).

7. **`expected_sql` for the golden cases marked `GOLD SQL REQUIRED`**,
   including the five Postgres ones that now have an environment to run
   in. §9.2 verified the path, not the answers.

8. **Grant R3 the role the script specifies** (`OPS-023`). Measured, so
   the choice is no longer open — `analyst` is correct on both
   authorization surfaces and `manager` on neither. This is a write to
   the identity database's role grants, so it is an operator action:

   ```powershell
   # via the admin UI (R1 account) -- Platform Admin > Users > uat9.r3@example.com
   # grant: analyst      remove: manager
   ```

   Then re-check that R3 can reach a J3 catalog review and a J4
   governance review, and that it can **no longer** open the admin
   dashboard — the over-grant half is what made R3 useless as a negative
   control.

9. **A decision on `OPS-022`**, which this audit deliberately did not
   take. The repair is not the code change but the backfill: **187**
   existing collections (re-counted at the end of this session, up from
   185 earlier the same day) and their `SemanticCatalogEntry.database_id`
   rows are filed under a per-job id. Until it is taken, a UAT tester
   should be told that J2 + J3 publish successfully and that the
   confirmed meaning does **not** yet reach J4's SQL prompt, so J4 must
   not be signed off as evidence that the catalog works.

10. **Refresh the `detect-secrets` baseline on Linux**, which is the last
    red blocking gate and the one §10 left unmeasured until now. 85
    un-baselined findings across 54 tracked files, every one in a
    category already triaged benign — so this is a stale artifact, not a
    leak, and it cannot be refreshed from this checkout (`CLAUDE.md`
    §5.7: OS-native path separators):

    ```bash
    detect-secrets scan $(git ls-files) --baseline .secrets.baseline
    detect-secrets audit .secrets.baseline
    python scripts/check_secrets_baseline.py <the pre-scan copy>
    ```

---

## 12. Session summary

- **S1 found and fixed: 1.** `OPS-021` — `docker build .` failed
  outright, so the image this project ships could not be built. A
  regression introduced by Phase 8 of this audit, invisible because the
  only gate that could see it sits in a job widely described as
  advisory. `AUZ-002` remains the project's only *open* S1 and is not
  this phase's.
- **S2 found and fixed: 1.** `OPS-025` — `retrieve_schema`'s terminal
  failure was not terminal. Found by running the one drill this document
  had recorded as **Unverified** (§4.4), which is the lesson: an
  unverified check is not a low-risk check, it is an unmeasured one. The
  node composed its refusal, the graph drove past it, `generate_sql`
  spent a model call on a prompt with no schema, and the run died in a
  `RuntimeError` that became **HTTP 500** — 68.43 s to discard an
  actionable message, against 0.38 s for the graceful failure after the
  fix. Third instance of the shape Phase 6 named (`LOP-001`, `LOP-004`),
  so `CLAUDE.md` §4.1 now carries the rule rather than the anecdote.
- **S2 open from this phase: 3** — `OPS-012` (the queue's 120 s budget
  versus `/ask`'s 600 s, measured on both sides), `OPS-015` (the live
  router misses 46% of the golden SQL set, including three quarters of
  J4), and `OPS-022` (onboarding's published, human-confirmed semantic
  meaning is filed under a `database_id` the SQL pipeline never
  queries — 185 collections' worth, with no error anywhere). None is
  fixable without changing behaviour nothing currently measures; all
  three carry a concrete proposal and a test that fails the day they
  are fixed.
- **Fixed this session: 8** — `OPS-011`, `OPS-013`, `OPS-016`,
  `OPS-017`, `OPS-019`, `OPS-021`, `OPS-024`, `OPS-025`. Six shipped with
  tests that fail before and pass after; `OPS-013`, `OPS-019` and
  `OPS-025` are mutation-verified — reverting `OPS-025`'s two source
  files fails 6 of its 13 tests, and the captured log shows the defect
  itself (`[generate_sql] attempt=1/4 last_error='Schema retrieval
  failed: ...'`).
- **Flagged for a decision: 7** — `OPS-012`, `OPS-014`, `OPS-015`,
  `OPS-020`, `OPS-022`, `OPS-023`, `OPS-026` (S4: `GET /health` gives an
  unauthenticated caller the schema-index directory's absolute path —
  the `LOP-008` class, and the third place this phase met it).
- **Five of this phase's own records were corrected rather than left
  to stand**, which is the pattern worth carrying into Phase 12: the
  things this audit got wrong were not missed checks but **checks whose
  results had expired**. `OPS-024` is the cleanest case — the gate
  table recorded `pip-audit` as "not re-run" and cited `CLAUDE.md`'s own
  note that its result is a statement about a date; running it found 11
  reachable advisories. §6.2 had said ChromaDB "deliberately needs no backup" —
  wrong in the dangerous direction, since golden examples, message
  feedback and schema-drift baselines share that directory with no live
  source to rebuild from; `CLAUDE.md` §12 listed the whole directory as
  derived and no longer does. §9.1 had recorded R3's role mismatch with
  `manager`'s permissions unmeasured; measuring them (`OPS-023`) showed
  the account is **both** over-granted (4 admin permissions on
  `identity.rbac`) and under-granted (5 analyst permissions missing on
  `agent.authz`), which closes the decision the earlier draft left
  open. **Two more were corrected at the end of the session, and both
  were of the remaining kind — a check this document had declined to
  run.** §4.4 recorded the ChromaDB drill as *Unverified* because the
  mid-request case cannot be induced on Windows; running the cases that
  *can* be found `OPS-025`, an S2. §10 said nothing about
  `detect-secrets` because its baseline cannot be refreshed here;
  refreshing it and *measuring* it are different acts, and measuring it
  showed the gate still red with the staleness grown from ~37 files to
  54. **So the count is five**, and the generalisation for Phase 12 is
  sharper than "results expire": of the five, two were expired
  measurements and three were checks recorded as blocked when only part
  of them was.
- **Backup/restore is now drilled on both stores, with measured RTOs
  and an honestly unbounded RPO.** Identity database: §6.1, end to end,
  functionally verified by signing in against the restored copy. Chroma:
  **39.1 MiB / 813 files, backup 0.51 s, restore 0.41 s, open-and-verify
  1.44 s → RTO ≈ 2.4 s**, with the 6 golden examples, 8 feedback items
  and 1 drift baseline counted *in the restored copy* rather than the
  original. Neither has a schedule, which is the whole of the remaining
  RPO problem (`OPS-018`) and an OS task, not a code one.
- **The capacity answer UAT needs, in one line:** this hardware serves
  **one question at a time**; three testers at once triples latency to
  ~195 s, and six breaks it — 5 of 6 fail on the 300 s per-call timeout
  and the provider circuit breaker opens, penalising the next user too.
- **Gates at the end of the phase:** `ruff` 0, `black` clean over 1,693
  files, `bandit` 0 at every severity over 119,611 lines, `mypy`
  (production) 0 in 482 files, `pytest` **9,687 passed / 0 failed**
  (912 s; +13 = `OPS-025`'s own tests), frontend `vitest` **1,551
  passed / 0 failed**, `tsc` clean. **`pip-audit` was red** — 11
  advisories in `pypdf` and `pyasn1`, both reachable, fixed by bumping
  the pins (`OPS-024`, §10.1); green again, and the date matters more
  than the colour. **`detect-secrets` is red** and now measured rather
  than passed over: 85 un-baselined findings across 54 tracked files,
  all in already-triaged-benign categories, refresh being a Linux task
  (§10, §11 item 10). **`docker build` passes** and the image was
  verified from the inside on the deploy target — Python **3.11.16**,
  `numpy 2.4.6`, `scipy 1.17.1`, `pypdf 6.21.4`, `pyasn1 0.6.5`, `import
  api.main` clean, running as **uid=1000(app)**.
- **Next phase:** 12 — the consolidated UAT readiness report and gate.
