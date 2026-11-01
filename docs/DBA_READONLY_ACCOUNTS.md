# DBA guide — provisioning the read-only account this app connects with

**Audience:** the DBA who owns the database this platform will be pointed
at. You do not need to know anything about the application to use this
page.

**Why it exists.** Every other document here tells you to "use a
genuinely read-only account" — [`README.md`](../README.md#clone-install-configure),
[`docs/DEPLOYMENT.md`](DEPLOYMENT.md#quick-start),
[`SECURITY.md`](../SECURITY.md) on fourteen separate lines — and until
2026-10-30 **not one of them said how**. That was the gap UAT-readiness
Phase 10 found (`DOC-015`): the single most load-bearing operator
instruction in the project had no procedure behind it.

> **The database role is the real boundary, not the application.**
> `agent/sql_validator.py` parses every statement with `sqlglot` and
> accepts only a single read-only `SELECT`/`UNION`/`EXCEPT`/`INTERSECT`,
> and the row cap and query timeout are enforced twice each. That is
> defence in depth, not the guarantee. If the validator were ever
> bypassed, the account's own grants are what stops a write. Provision
> accordingly: assume the application is compromised and ask whether the
> account could still do damage.

---

## 1. What the application actually needs

Four things, and nothing else:

| # | Need | Why | Required? |
|---|---|---|---|
| 1 | **Connect** to the database | obvious | Yes |
| 2 | **`SELECT`** on the tables and views in scope | answering questions | Yes |
| 3 | **Read catalog metadata** for those objects | `db/schema_introspection.py` builds the schema the model is shown from SQLAlchemy's `Inspector`, which reads `INFORMATION_SCHEMA`/the system catalog. **There is no bundled schema file** — if this fails the app has nothing to tell the model | Yes |
| 4 | **Read a query plan** (`EXPLAIN`/`SHOWPLAN_XML`) and an approximate row count | `db/query_cost.py` shows the cost notice before "Confirm and Run"; `db/row_count_estimate.py` sizes tables | **No** — both fail open (reduced UX, never a blocked question) |

Two optional extras, each gated by its own feature flag and each failing
open when the permission is absent:

| Extra | Needs | When it runs |
|---|---|---|
| SQL Server Query Store insights (`db/query_store.py`) | `VIEW DATABASE STATE` on the target database | `ENABLE_QUERY_STORE_INSIGHTS` (default on, and a no-op on every non-`mssql` `DB_TYPE`) |
| Bounded low-cardinality value sampling (`db/value_sampling.py`) | nothing beyond `SELECT` | at **index-build** time only (`scripts/build_embeddings.py`, `POST /schema/refresh`) — never on the question path |

Point 2 therefore covers both reads. Note what the sampling one means for
you: a small `SELECT DISTINCT` over short text columns, bounded by
cardinality, puts **real column values** into the prompt the model sees.
That is deliberate and sanitised (`db/value_sampling.py`'s own docstring
explains the injection guard), but it is a reason the `SELECT` grant
should be scoped to the schema you intend to expose rather than granted
database-wide "just in case".

### Point 3 is the one that surprises people

The application reads the system catalog; **the user's questions may
not**. `agent/sql_validator.py` refuses any generated statement that
references `information_schema`, `pg_catalog`, SQL Server `sys`, or
Oracle `ALL_*`/`DBA_*`/`USER_*`/`V$`/`GV$`. So do not try to close
catalog access at the role level in order to hide the schema from users —
that is already closed in the query path, and closing it here only breaks
introspection. Catalog read is metadata only: `introspect_schema()` never
touches table data, which `db/value_sampling.py` being a separate opt-in
module keeps true by construction.

### What the application never needs

No `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, DDL, `CREATE`, `EXECUTE` on
procedures, linked-server access, `xp_cmdshell`, `OPENROWSET`, `COPY`,
`dblink`, `lo_import`, `pg_read_file`, or server-level administrative
roles of any kind. It also never needs write access to the *identity*
database through this account — that is a separate PostgreSQL database
with its own `AUTH_DATABASE_URL` credentials, which **do** need write
access (§4).

---

## 2. PostgreSQL

```sql
-- 1. The role. Prefer a password from your secrets manager; this account's
--    password goes in DB_PASSWORD in the application's .env and nowhere else.
CREATE ROLE app_readonly LOGIN PASSWORD '<from-your-secrets-manager>';

-- 2. Connect, and nothing inherited from PUBLIC.
REVOKE ALL ON DATABASE mydb FROM PUBLIC;
GRANT CONNECT ON DATABASE mydb TO app_readonly;

-- 3. One schema, read-only. DB_<NAME>_SCHEMA is one schema per
--    connection (a documented limitation), so grant the one you configure.
GRANT USAGE ON SCHEMA analytics TO app_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO app_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA analytics TO app_readonly;

-- 4. Future objects, so a new table does not silently become invisible.
--    Run as the role that owns the objects, or add FOR ROLE <owner>.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
  GRANT SELECT ON TABLES TO app_readonly;

-- 5. Belt and braces: make a write impossible even if a grant is
--    mis-applied later. PostgreSQL 14+.
GRANT pg_read_all_data TO app_readonly;   -- optional, read-everything shortcut
ALTER ROLE app_readonly SET default_transaction_read_only = on;
```

Notes:

- **`default_transaction_read_only = on` is the strongest single line
  here.** It makes the server refuse a write for this role regardless of
  table grants, and it costs the application nothing: every statement it
  issues is a `SELECT`, an `EXPLAIN` of a `SELECT`, or a catalog read.
- Catalog read (need 3) requires no grant — `information_schema` and
  `pg_catalog` are readable by `PUBLIC` by default, and step 2's
  `REVOKE ALL ON DATABASE` does not change that.
- `EXPLAIN (FORMAT JSON) <select>` (need 4) requires only the privileges
  the underlying `SELECT` needs. Nothing extra.
- **Do not** grant `pg_monitor`, `pg_read_server_files`,
  `pg_execute_server_program`, `SUPERUSER`, or `CREATEDB`.
- If you use row-level security, note that the application composes
  tenant scoping into the query's own `WHERE` clause; RLS on top is
  additive and welcome, never a substitute.

---

## 3. SQL Server

```sql
-- 1. Server-level login. No server role beyond the implicit `public`.
CREATE LOGIN app_readonly WITH PASSWORD = '<from-your-secrets-manager>',
  CHECK_POLICY = ON;

-- 2. Database user mapped to it.
USE mydb;
CREATE USER app_readonly FOR LOGIN app_readonly;

-- 3. Read-only, database-wide. db_datareader grants SELECT on every
--    current and future table and view -- no per-table maintenance.
ALTER ROLE db_datareader ADD MEMBER app_readonly;

-- 4. Explicitly deny every write, so a later mis-grant cannot take
--    effect. In SQL Server a DENY always beats a GRANT.
ALTER ROLE db_denydatawriter ADD MEMBER app_readonly;

-- 5. OPTIONAL (need 4): the cost notice and row-count estimates.
--    SHOWPLAN lets `SET SHOWPLAN_XML ON` return a plan without executing;
--    VIEW DATABASE STATE is what sys.dm_db_partition_stats needs.
GRANT SHOWPLAN TO app_readonly;
GRANT VIEW DATABASE STATE TO app_readonly;   -- also enables Query Store insights
```

Notes:

- **`db_datareader` + `db_denydatawriter` is the whole answer** for needs
  1–3. Do not add `db_owner`, `db_ddladmin`, `db_datawriter`, or any
  server role.
- Catalog read (need 3) needs no grant: SQL Server's metadata visibility
  rules expose an object's metadata to a principal that has any
  permission on it, which `db_datareader` supplies.
- **Omitting step 5 is a supported configuration.** You lose the pre-run
  cost estimate and the Query Store panel; no question becomes
  unanswerable. `db/query_store.py` treats a permission error as
  "unavailable", not as a failure.
- `SET SHOWPLAN_XML ON` changes *session* state. The application toggles
  it off again on the same pooled connection; see `db/query_cost.py`'s
  own note. If that worries you, do not grant `SHOWPLAN` — see above.
- `DB_TYPE=mssql` additionally needs the Microsoft ODBC Driver for SQL
  Server installed as a **system** package on the application host. That
  is a host prerequisite, not a grant.

---

## 4. The identity database is a different account, and it writes

Do not reuse the read-only account above for `AUTH_DATABASE_URL`. The
identity database (`identity/`, PostgreSQL only, present only when
`LOCAL_AUTH_ENABLED=true`) holds accounts, sessions, chat history, the
semantic catalog and governance records, and **needs ordinary read/write
plus DDL** so `alembic -c identity/alembic.ini upgrade head` can apply
migrations:

```sql
CREATE ROLE app_identity LOGIN PASSWORD '<from-your-secrets-manager>';
CREATE DATABASE text_to_sql_identity OWNER app_identity;
```

Owning its own database is the simplest correct answer; it needs no
privilege on anything else, and in particular **no privilege at all on
the business database**. Keep the two accounts separate so a mistake in
one cannot reach the other.

---

## 5. Verifying it, from the application's side

The application checks this for you at startup, once per warmed engine —
`db.connection.check_write_privileges`, called from
`api.main._enforce_database_write_privileges` in the app's `lifespan`.
**What it does with the answer depends on `ENVIRONMENT`:**

| `ENVIRONMENT` | A role that appears to hold `INSERT`/`UPDATE`/`DELETE` |
|---|---|
| `production` | **The application refuses to start**, with a `ConfigurationError` naming the database |
| anything else | A `critical` security-audit event plus a startup warning; the app comes up |

So on a UAT environment set `ENVIRONMENT=production` if you want this
enforced rather than logged. The check reads the privilege catalog for the
connected principal:

| `DB_TYPE` | What it reads | Verified against live docs? |
|---|---|---|
| `postgresql` | `information_schema.role_table_grants` where `grantee = current_user`, counting `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` | Yes |
| `mssql` | `fn_my_permissions(NULL, 'DATABASE')`, counting `INSERT`/`UPDATE`/`DELETE` | Yes |
| `mysql` | `information_schema.user_privileges` (MySQL shows the connected user's rows only) | **No** — not verified against a live instance |
| `oracle` | `session_privs` ∪ `all_tab_privs` for `USER` | **No** — not verified against a live instance |

Run the project's own connection check after provisioning:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\test_db_connection.py
```

A correctly provisioned account produces **no** write-privilege warning.
If you see

> The connected database role appears to have write privileges
> (INSERT/UPDATE/DELETE) …

the account is not read-only yet. Treat that warning as a blocker for
UAT, not advice.

**The check fails open by design**: if it cannot read the privilege
catalog it reports "unknown" and the application still starts. So a
*silent* startup is weaker evidence than it looks on MySQL and Oracle,
where the query itself is unverified. On those two engines, confirm
read-only by attempting a write **as that account** in your own client:

```sql
-- Expect a permission error, on every supported engine.
CREATE TABLE _uat_write_probe (x int);
```

Do this against a non-production database, and only with authorisation —
it is exactly the negative test UAT journey J7 covers from the
application side.

---

## 6. MySQL and Oracle sketches

Both engines are supported by the application and **neither the grant
recipe nor the privilege check below has been verified against a live
instance in this project**. They are included so a DBA has a starting
point, not as a tested procedure. Verify with §5's write probe.

```sql
-- MySQL
CREATE USER 'app_readonly'@'%' IDENTIFIED BY '<from-your-secrets-manager>';
GRANT SELECT ON mydb.* TO 'app_readonly'@'%';
-- EXPLAIN on a SELECT needs only the SELECT privilege.
```

```sql
-- Oracle
CREATE USER app_readonly IDENTIFIED BY "<from-your-secrets-manager>";
GRANT CREATE SESSION TO app_readonly;
GRANT SELECT ON appowner.some_table TO app_readonly;   -- per object
-- or, if your policy allows it:
-- GRANT SELECT ANY TABLE TO app_readonly;
```

One Oracle-specific wrinkle worth knowing: `db/query_cost.py` uses
`EXPLAIN PLAN FOR`, which writes to `PLAN_TABLE`. On a modern instance
`PLAN_TABLE` is a public synonym for `SYS.PLAN_TABLE$`, a **global
temporary table** whose rows are session-private and whose
insert/delete privilege is already held by `PUBLIC` — so this is not a
write to any business object and does not contradict a read-only role.
If your site has replaced the synonym with a real table, either grant
`INSERT`/`DELETE` on that one table or accept the loss of cost
estimation, which fails open.

---

## 7. Related reading

- [`SECURITY.md`](../SECURITY.md) — the full SQL-safety model this role
  sits underneath.
- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — where the credentials go, and
  the backup/rollback procedures.
- [`docs/CONFIGURATION.md`](CONFIGURATION.md#database-connection) —
  `DB_*` and `DB_CONNECTIONS` reference.
- [`docs/uat-readiness/phase-2-security.md`](uat-readiness/phase-2-security.md)
  — the adversarial sweep against the validator that sits above this role.
