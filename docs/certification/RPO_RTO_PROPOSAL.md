# RPO / RTO — Proposed Targets and Feasibility Assessment

**Prepared:** 2026-10-17 · **Baseline commit:** `779d7fc`
**Status: PROPOSED — PENDING BUSINESS SIGN-OFF. Not approved requirements.**
**Achievement status: NOT TESTED.** No backup or restore drill has been executed.

> Neither target below is claimed as met. They are proposals with a
> feasibility opinion attached. Prompt 58 requires *measured* recovery
> times; this document contains none, and says so in every section where
> a measurement would otherwise belong.

---

## 1. Proposed targets (yours, recorded for review)

| Target | Proposed value | Meaning |
|---|---|---|
| **RPO** — Recovery Point Objective | **24 hours** | At most 24 hours of this platform's own state may be lost |
| **RTO** — Recovery Time Objective | **8 hours** | Service restored within 8 hours of a declared disaster |

---

## 2. The scoping question that must be settled first

**An RPO of 24 hours is meaningless unless it is clear what data it
covers.** This platform owns far less data than it touches, and
conflating the two would produce a target nobody can meet or test.

| Data | Owner | In RPO scope? |
|---|---|---|
| **The operator's business database** (`AdventureWorksDW2025`, `HrAutomationDb`, …) | **You / your DBA team** | **No.** This app is a strictly read-only client. It never writes business data, so it cannot lose any. Its recovery is your existing database DR process |
| Identity database (users, roles, conversations, artifacts, shares, audit log) | This app | **Yes** — the only genuinely irreplaceable state |
| ChromaDB vector index (`embeddings/.chroma/`) | This app | **Regenerable** — `scripts/build_embeddings.py` rebuilds from live schema. RPO effectively N/A; it affects RTO |
| Document/policy RAG store (SQL Server `VECTOR`) | Shared | **Partly** — the vector rows are regenerable by re-ingestion if the source documents survive |
| `.env` / configuration | You | **Yes** — and it is not in git by design |
| Uploaded attachment bytes | This app | **No** — deliberately ephemeral (`attachment_retention_hours`, default 24) |
| Generated media library | This app | **Decision needed** — regenerable only by paying the provider again |

**The practical reading:** RPO 24h applies to the **identity database and
configuration**. Everything else is either not ours, or rebuildable.

---

## 3. Can RPO 24h be met?

**Assessment: achievable, but not currently achieved — because no backup
schedule exists in this repository.**

| Requirement | Present? | Evidence |
|---|---|---|
| A documented backup procedure | **Yes** | `docs/DEPLOYMENT.md` "Backup & Recovery" (added at Prompt 25) |
| An automated, scheduled backup | **No** | `CLAUDE.md` §13: *"No scheduler, no background worker"*. Nothing runs `pg_dump` on a timer |
| Backup retention / offsite copy | **Not specified** | — |
| A tested restore | **No** | Gate report: *"documented and code-level-verified …, not drilled"* |

**Gap.** A 24-hour RPO requires a backup at least every 24 hours. The
procedure exists; the *schedule* does not, and this application
deliberately ships no scheduler. The backup must therefore be driven by
**your** infrastructure — a cron job, a managed-Postgres automatic
backup, or a platform snapshot.

**Recommendation.** If the identity database is managed Postgres, enable
its built-in automated backup with ≥24h retention; that alone satisfies
RPO 24h with no application change. Self-hosted Postgres needs a
scheduled `pg_dump` plus offsite retention.

---

## 4. Can RTO 8h be met?

**Assessment: plausible, and generous for this architecture — but
unproven, and one step is the long pole.**

Reconstruction sequence, with honest time estimates. **Every figure
below is an estimate, not a measurement.**

| Step | Action | Estimated | Confidence |
|---|---|---|---|
| 1 | Provision host / container runtime | 15–60 min | Depends on your infra |
| 2 | Restore `.env` configuration | 5 min | High |
| 3 | `docker run` the digest-pinned image, or rebuild | 5–30 min | Image is digest-pinned and tag-addressable |
| 4 | Restore identity DB (`pg_restore`) | 5–30 min | Scales with conversation history volume |
| 5 | `alembic -c identity/alembic.ini upgrade head` | < 5 min | Every migration has a real `downgrade()` (verified by read at Prompt 25) |
| 6 | **Rebuild ChromaDB** (`scripts/build_embeddings.py`) | **Unknown — the long pole** | **Low.** Requires live database connectivity and embeds every table. Never timed |
| 7 | Re-ingest documents, if RAG is enabled | Unknown | Low — scales with corpus |
| 8 | Start Ollama, confirm the model is present | 5–20 min | A 4.9 GB model pull on a cold host is not instant |
| 9 | Health check (`GET /health`) | < 5 min | Observed: full startup in ~2 s warm |

**Estimated total: roughly 1–3 hours warm, materially more cold**, which
sits inside 8 hours with margin — *provided* step 6 is not pathological.

**The honest risk.** Step 6 is the only step whose duration nobody has
ever measured, and it is the one that scales with your schema size. On
the four databases configured here (11 + 36 + 3 + 2 tables) it is small.
On a 500-table warehouse it may not be. **An 8-hour RTO should not be
signed off until `scripts/build_embeddings.py` has been timed against a
representative schema.** That is a one-command measurement and is the
single highest-value DR test available.

---

## 5. Architectural changes required

| # | Need | Why | Owner |
|---|---|---|---|
| 1 | **A backup schedule** | The app has no scheduler by design | You / infrastructure |
| 2 | **Offsite/retained backup copies** | A backup on the failed host is not a backup | You |
| 3 | **A timed `build_embeddings.py` run** | The only unquantified step in RTO | Can be measured here, given a representative schema |
| 4 | **A documented restore runbook with real timings** | The procedure exists; the measurements do not | Joint |
| 5 | Decide whether generated media is in scope | Regenerating costs money | You |
| 6 | Multi-worker / HA (optional) | Rate limiters, caches, circuit breakers and the result cache are **per-worker** (`CLAUDE.md` §13). This affects availability, not recovery | You |

**None of these is an application code change.** The gaps are
operational.

---

## 6. What would have to be tested to claim these targets

Marked NOT TESTED until each is executed against **isolated** resources.
None may run against production data.

| Test | Measures | Status |
|---|---|---|
| `pg_dump` → fresh database → `pg_restore` → `alembic upgrade head` → `/health` | End-to-end identity recovery, RTO steps 4–5 and 9 | **NOT TESTED** |
| Timed `scripts/build_embeddings.py` on a representative schema | RTO step 6, the long pole | **NOT TESTED** |
| `alembic downgrade` one revision, then `upgrade` again | Migration rollback | **NOT TESTED** |
| Container rollback to a previous digest | Deployment rollback | **NOT TESTED** |
| Verify restored audit history is complete | No silent loss of audit trail | **NOT TESTED** |
| Verify a restored multi-tenant database keeps tenants separate | No tenant mixing on recovery | **NOT TESTED** |

A partial, non-destructive subset of these is feasible here now that
Docker is running — specifically the migration up/down drill and a
restore into a throwaway Postgres container. **Confirm you want that and
it will be run against disposable containers only.**

---

## 7. Sign-off

| Field | Entry |
|---|---|
| RPO target (24 h) — approved? | ☐ Approved ☐ Amended: ______ ☐ Rejected |
| RTO target (8 h) — approved? | ☐ Approved ☐ Amended: ______ ☐ Rejected |
| RPO scope (identity DB + configuration only) — confirmed? | ☐ Yes ☐ Amend: ______ |
| Business database excluded from this app's RPO — confirmed? | ☐ Yes ☐ No |
| Generated media in scope? | ☐ Yes ☐ No |
| Approver |  |
| Date |  |

**Until this table is signed, both targets remain proposals, and the
release gate records disaster recovery as NOT TESTED.**
