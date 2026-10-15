# Configuration Reference

Every environment variable this project reads, in one browsable table.
`.env.example` remains the source of truth for defaults and inline
guidance (copy it to `.env` and edit) — this document exists to make the
full set scannable at once, grouped by concern, with which module actually
reads each one. All parsing/validation happens in `config/settings.py`;
malformed values (not missing ones) fail fast at startup with a
`ConfigurationError` — see that module's docstring.

## Ollama (local LLM)

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Base URL of the Ollama server. `agent/llm_client.py`, `api/main.py`'s health check. |
| `OLLAMA_MODEL` | `llama3.1:8b` | Default model for SQL generation/planning/review/insight. Always selectable, and always the fallback when a caller doesn't pick a model — see "Model selection" below. |
| `OLLAMA_REQUEST_TIMEOUT_SECONDS` | `300` | Per-request timeout for Ollama calls. Raise further if you see `httpx.ReadTimeout`/`ConnectTimeout` on slower hardware. |
| `OLLAMA_MODEL_SELECTION_ENABLED` | `true` | Master switch for per-question model selection (see below). `false` collapses every request to `OLLAMA_MODEL`, matching this app's behavior before this feature existed. |
| `OLLAMA_ALLOWED_MODELS` | *(unset → a small starter set)* | Comma-separated list of additional Ollama model names selectable for Text-to-SQL. `OLLAMA_MODEL` is always included even if you omit it here. |

### Model selection

Users (or any `POST /ask` caller) can choose which locally-installed Ollama
model answers a given question, instead of the server always using
`OLLAMA_MODEL`. Three distinct, deliberately separate concepts are involved
— conflating them is the most common source of confusion:

1. **Available in the online Ollama Library** (https://ollama.com/library) —
   this application never queries that site at runtime; it was only used as
   a one-time research input (2026-09-27) to pick the starter set below.
2. **Configured/allowed** — `OLLAMA_ALLOWED_MODELS` (plus `OLLAMA_MODEL`,
   always implicitly included). This is the *only* thing that gates what a
   caller may request; a model not in this set is rejected with HTTP 400
   before any Ollama call is made, regardless of whether it's a real,
   installable Ollama model.
3. **Installed locally** — whatever `ollama list` actually shows on the
   machine `OLLAMA_HOST` points at, checked live by `GET /models`. A model
   can be "configured" but not yet "installed" — the UI shows it as
   *Not installed* rather than hiding it, and selecting it is disabled
   until you run `ollama pull <model>`.

**Leaving `OLLAMA_ALLOWED_MODELS` unset does not mean "only the default
model"** — it falls back to a small, practical starter set
(`config/settings.py`'s `_DEFAULT_OLLAMA_ALLOWED_MODELS`, evaluated against
the live Ollama Library on 2026-09-27 for local Text-to-SQL suitability —
instruction-following, structured/JSON output, a spread of sizes):

| Model | Size | Notes |
|---|---|---|
| `llama3.1:8b` | 8B | This app's own default. |
| `qwen2.5:7b` | 7B | Strong structured/JSON output and coding/math; a close alternative to the default. |
| `qwen2.5:14b` | 14B | Same family, more capacity, for more capable hardware. |
| `llama3.2:3b` | 3B | Lightweight option for limited hardware. |
| `mistral:7b` | 7B | Function-calling support (v0.3+). |
| `deepseek-r1:8b` | 8B | Reasoning-focused — worth trying for harder multi-step questions (top-N-per-group, period-over-period comparisons). |

None of this is a claim that these are the *only* good choices, or that
every one of them is already pulled on your machine — see
`config/ollama_models.yaml` for the full hand-authored metadata (display
name, size, capabilities, description) behind each entry, and add your own
entry there for a model with no curated description (it'll still work fine
without one — see `config/ollama_models.py::describe_model`'s fallback).

**How to change what's offered:**
- **Add a model**: `ollama pull <model>` locally, then add its name to
  `OLLAMA_ALLOWED_MODELS` in `.env` (comma-separated) and restart the app.
  Optionally add a `config/ollama_models.yaml` entry for a friendlier
  display name/description.
- **Remove a model**: delete it from `OLLAMA_ALLOWED_MODELS` and restart —
  no React or LangGraph code change needed either way. (Settings are a
  cached-per-process singleton like everywhere else in this app — a
  restart is what makes any `.env` change take effect, this one included.)
- **Change the default**: change `OLLAMA_MODEL` and restart.
- **Disable selection entirely**: `OLLAMA_MODEL_SELECTION_ENABLED=false`.

**How local availability is detected**: `GET /models` calls the same
`ollama.Client().list()` API `ollama list` itself uses against
`OLLAMA_HOST`, live, on every call to that endpoint — never cached
server-side, and never called from `/ask` itself (the frontend caches the
`/models` response for 30 seconds, matching `GET /health`'s own caching).
If Ollama itself is unreachable, `GET /models` still returns every
configured model, each marked `installed: false`, rather than failing —
see `agent/model_registry.py`'s module docstring.

**Request-scoped, never global**: `POST /ask`'s optional `model` field
(validated against `OLLAMA_ALLOWED_MODELS` before anything else runs) flows
through `AgentState["selected_model"]` for the life of that one request
only — concurrent callers with different `model` values never interfere
with each other, and no process-wide "current model" variable exists
anywhere in this codebase. See `agent/model_registry.py` and
`agent/state.py`'s `selected_model` field docstring for the full design.

**What this does *not* affect**: the embedding model
(`EMBEDDING_MODEL_NAME`), the vision model (`MEDIA_VISION_MODEL`), voice
transcription/synthesis, document/policy RAG's own LLM calls
(`rag/llm.py`), and web-search answer synthesis all keep using their own,
separately-configured models regardless of what's picked here — Text-to-SQL
model selection is deliberately scoped to generation/planning/review/insight
only (the four LLM calls `agent/llm_client.py` makes for one question), per
this feature's own design boundary.

Example request:

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What were total sales last quarter?", "model": "qwen2.5:7b"}'
```

Omit `model` entirely to keep using `OLLAMA_MODEL` — every existing
integration continues to work unchanged.

**Troubleshooting:**
- `400 Bad Request` naming your model: it isn't in `OLLAMA_ALLOWED_MODELS`
  (or selection is disabled) — check `.env` and restart.
- A model shows "Not installed" and can't be selected: run
  `ollama pull <model>`, no restart needed (`GET /models` re-checks live).
- A question fails with "Is `ollama serve` running and has the model been
  pulled?": the model passed every allowlist check but isn't actually
  installed on the server Ollama instance — same fix as above.

## Database connection

| Variable | Default | Purpose |
|---|---|---|
| `DB_TYPE` | *(required)* | `postgresql` \| `mysql` \| `mssql` \| `oracle`. Selects the SQLAlchemy driver + sqlglot dialect (`db/connection.py::SUPPORTED_DB_TYPES`). |
| `DB_HOST` | *(required unless `DB_CONNECTION_STRING` set)* | Database host. |
| `DB_PORT` | DB_TYPE's default | Database port. |
| `DB_NAME` | *(required unless `DB_CONNECTION_STRING` set)* | Database/catalog name. |
| `DB_USER` | — | Login username. **Use a dedicated read-only account** — see `SECURITY.md`. |
| `DB_PASSWORD` | — | Login password. Wrapped in `SecretStr`, never logged in plaintext. |
| `DB_SCHEMA` | *(database default)* | Restrict introspection (and what the LLM sees) to one schema. |
| `DB_CONNECTION_STRING` | — | Full SQLAlchemy connection string, used as-is instead of the discrete fields above if set. Also `SecretStr`-wrapped. |
| `DB_ODBC_DRIVER` | `ODBC Driver 17 for SQL Server` | Only used for `DB_TYPE=mssql` — must match a driver actually installed (`odbcinst -j` / Windows ODBC Data Sources). |
| `DB_POOL_SIZE` | `10` | `QueuePool`'s `pool_size` for every configured database's engine (`db/connection.py::_cached_engine`). Raise once more than a handful of callers query the same database at once — each held connection is one `execute_sql_node`/`POST /execute` call in flight. |
| `DB_MAX_OVERFLOW` | `20` | `QueuePool`'s `max_overflow` for the same engine — the burst ceiling above `DB_POOL_SIZE` before a caller waits for a connection. |

### Multiple databases (optional)

| Variable | Default | Purpose |
|---|---|---|
| `DB_CONNECTIONS` | *(unset)* | Comma-separated list of connection names, e.g. `sales,hr`. Unset means a plain single-database setup — the `DB_*` block above is used as-is, internally named `"default"`. When set, the agent auto-routes each question to whichever configured database looks relevant (`embeddings/retriever.py::select_database`) — there is no manual database picker. |
| `DB_<NAME>_TYPE`, `DB_<NAME>_HOST`, `DB_<NAME>_PORT`, `DB_<NAME>_NAME`, `DB_<NAME>_USER`, `DB_<NAME>_PASSWORD`, `DB_<NAME>_SCHEMA`, `DB_<NAME>_CONNECTION_STRING`, `DB_<NAME>_ODBC_DRIVER` | — | Per-connection fields, one full `DB_*` set per name listed in `DB_CONNECTIONS` (`<NAME>` = the name uppercased, non-alphanumeric characters replaced with `_`). Same meaning as the unprefixed fields above. See `.env.example` for a worked two-database example. |

Each configured database gets its own Chroma collection and schema-index
cache (`embeddings/schema_indexer.py`), so `python scripts/build_embeddings.py`
builds/refreshes all of them in one run; `python scripts/test_db_connection.py`
checks all of them too.

## ChromaDB (schema retrieval index)

| Variable | Default | Purpose |
|---|---|---|
| `CHROMA_PERSIST_DIR` | `./embeddings/.chroma` | Where the Chroma index persists to disk. |
| `CHROMA_COLLECTION_NAME` | `schema_ddl` | Name of the Chroma collection holding schema DDL. |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | Embedding model for schema DDL and questions. |
| `SCHEMA_TOP_K` | `4` | Number of most-relevant tables retrieved per question. |

## Agent limits

| Variable | Default | Purpose |
|---|---|---|
| `MAX_RETRIES` | `3` | Max self-correction retries in the LangGraph loop. |
| `COMPLEX_QUERY_MAX_RETRY_BONUS` | `2` | Extra retries (on top of `MAX_RETRIES`) for questions `agent/complexity.py` detects as likely needing more self-correction (top-N-per-group phrasing, YoY/period-over-period comparisons, several metrics requested at once) — one extra retry per distinct signal matched, capped at this value. `0` disables the adaptive bonus. |
| `MAX_RESULT_ROWS` | `1000` | Row cap applied to every executed query (enforced two independent ways — see `SECURITY.md`). |
| `QUERY_TIMEOUT_SECONDS` | `15` | Wall-clock timeout for query execution. |
| `LLM_MAX_TOKENS` | `1024` | Max tokens the LLM may generate per SQL-generation call. |
| `INSIGHT_MAX_TOKENS` | `120` | Max tokens for the post-query plain-English insight sentence. |
| `MAX_QUESTION_LENGTH` | `500` | Max accepted character length of a typed question (`agent/input_guard.py`). |
| `MAX_CONVERSATION_HISTORY_TURNS` | `20` | Max prior turns from a caller-supplied `conversation_history` that are ever processed — older turns beyond this count are dropped (`agent/input_guard.py::sanitize_conversation_history`). |

## Agentic query planning + plan-conformance self-correction

`agent/nodes.py::plan_query_node`/`review_sql_node` -- only triggers for a
question `agent/complexity.py` judges non-trivial (the same signals that
drive `COMPLEX_QUERY_MAX_RETRY_BONUS` above); an ordinary question makes
zero extra LLM calls regardless of `ENABLE_QUERY_PLANNING`.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_QUERY_PLANNING` | `true` | Master switch for the planning + plan-review LLM calls. Off means every question behaves exactly as it did before this feature existed. |
| `QUERY_PLAN_MAX_TOKENS` | `300` | Max tokens for the up-front query plan (a short JSON array of step strings). |
| `SQL_REVIEW_MAX_TOKENS` | `200` | Max tokens for the plan-conformance review verdict ("PASS" or a one-sentence "FAIL: ..."). |

## Rate limiting

Real, in-process safeguards — not yet distributed across replicas (see
`docs/SCALE_OUT_PROMPT.md`'s Phase 3). See `SECURITY.md`,
`docs/RISK_REGISTER.md`'s R-001.

| Variable | Default | Purpose |
|---|---|---|
| `QUESTION_RATE_LIMIT_PER_MINUTE` | `10` | Max question submissions/minute, per caller — an authenticated subject when local/OIDC auth is on, else client IP (`api/main.py`'s `_rate_limit_key`). |
| `LLM_CALL_RATE_LIMIT_PER_MINUTE` | `20` | Max LLM *generation* calls/minute, process-wide — stricter, since retries can multiply calls. |
| `MAX_CONCURRENT_ASK_REQUESTS` | `50` | Max `POST /ask` executions running at once, process-wide — an *in-flight* concurrency cap (distinct from the per-minute limit above), also what sizes the bounded ask-worker thread pool. A caller past this gets an immediate 429, not a queued wait. |
| `MAX_CONCURRENT_ASK_REQUESTS_PER_CALLER` | `2` | Max `POST /ask` executions one caller may have in flight at once (same caller key as `QUESTION_RATE_LIMIT_PER_MINUTE`) — a fairness bound, not a throughput control. |

## Query cost estimation

| Variable | Default | Purpose |
|---|---|---|
| `COST_ESTIMATION_ENABLED` | `true` | Whether `db/query_cost.py` runs a non-executing EXPLAIN/SHOWPLAN before a validated query. Fails open regardless. |
| `COST_ESTIMATION_TIMEOUT_SECONDS` | `3` | Timeout for the plan-only estimate call itself. |
| `COST_MODERATE_ROW_THRESHOLD` | `50000` | Estimated rows above which a query still runs, with a "this may take a moment" notice first. |
| `COST_HIGH_ROW_THRESHOLD` | `1000000` | Estimated rows above which a query is not run at all (retryable, fed back to generation). Must be strictly greater than the moderate threshold. |

## Logging

| Variable | Default | Purpose |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Root logging level. |
| `LOG_REDACTION_LEVEL` | `standard` | `standard` (row/column counts + column names) or `strict` (counts only) — how much result-set shape gets logged. Never cell values, at either level. |
| `LOG_FORMAT` | `text` | `text` (this app's original terminal-friendly format, unchanged) or `json` (one JSON object per line — `timestamp`/`level`/`logger`/`correlation_id`/`message`, plus `exception` when present) for a real log-aggregation pipeline (ELK/CloudWatch/Datadog/Loki). Same underlying log calls either way — only the rendering changes. See `config.settings._JsonLogFormatter`. |

## REST API (`api/`)

| Variable | Default | Purpose |
|---|---|---|
| `API_AUTH_TOKEN` | *(unset)* | Optional shared bearer token, checked when no OIDC identity is present. Grants a fixed admin-equivalent identity — see `docs/AUTHENTICATION.md`. Can be set alongside OIDC below ("Combining modes"). |
| `ENVIRONMENT` | `development` | `development` or `production`. Only consequence today: `production` refuses to start at all if neither `API_AUTH_TOKEN` nor `OIDC_ISSUER` is configured, and refuses to start if any configured database's role appears to hold write privileges — see `docs/AUTHENTICATION.md`. |
| `TRUSTED_PROXY_COUNT` | `0` | Number of trusted reverse proxies/load balancers between the internet and this process. `0` (default) never reads `X-Forwarded-For` (the original, tested-safe behavior for a directly-exposed instance). Set to the *exact* number of hops you've deliberately put in front of this app (usually `1`) to key rate limiting/audit logging on the real client IP instead of the proxy's own — see `security/client_ip.py` and `docs/DEPLOYMENT.md`. |

### Authentication (OIDC/JWT) and authorization (see [`docs/AUTHENTICATION.md`](AUTHENTICATION.md) / [`docs/AUTHORIZATION.md`](AUTHORIZATION.md))

Off entirely unless `OIDC_ISSUER` is set — leaving it blank (the default)
is a complete no-op, identical behavior to before this feature existed.

| Variable | Default | Purpose |
|---|---|---|
| `OIDC_ISSUER` | *(unset)* | Your identity provider's issuer URL. Setting this turns OIDC mode on. |
| `OIDC_AUDIENCE` | *(unset)* | Required whenever `OIDC_ISSUER` is set (refuses to start otherwise) — the API identifier your IdP mints access tokens for. |
| `OIDC_JWKS_URL` | *(blank = discovered)* | Only needed if your provider doesn't support standard `/.well-known/openid-configuration` discovery. |
| `OIDC_ALGORITHMS` | `RS256` | Comma-separated server-side algorithm allowlist — never read from the token's own `alg` header. Must never include `none` (refuses to start if it does). |
| `OIDC_CLOCK_SKEW_SECONDS` | `60` | Leeway for ordinary clock drift when checking token expiration/not-before. |
| `OIDC_ROLE_CLAIM` | `roles` | Which JWT claim carries the caller's role(s) — see `docs/AUTHORIZATION.md` for the role → permission mapping (`agent/authz.py`). |
| `ENABLE_SECURITY_HEADERS` | `true` | HSTS/CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy/Permissions-Policy on every response — see `SECURITY.md`. |
| `CONTENT_SECURITY_POLICY` | *(unset = built-in default)* | Overrides the built-in CSP entirely (set to an empty string to omit the CSP header while keeping the others). |
| `HSTS_MAX_AGE_SECONDS` | `31536000` (1 year) | `Strict-Transport-Security` header's `max-age`. |
| `CORS_ALLOWED_ORIGINS` | *(unset)* | Comma-separated allowed origins for cross-origin requests (e.g. a separate Vite dev server). Must never contain `*` — refuses to start if it does, since this app's CORS middleware always sets `allow_credentials=True`. |

### Frontend OIDC login (`frontend/`, build-time Vite env vars — see [`frontend/src/lib/auth.ts`](../frontend/src/lib/auth.ts))

**Not read by the Python backend** — these are baked into the built JS at
`npm run build`/`npm run dev`, and are not secrets (a public OIDC client,
Authorization Code + PKCE, no `client_secret`, is designed to be embedded
in a public SPA). Leaving `VITE_OIDC_AUTHORITY`/`VITE_OIDC_CLIENT_ID`
unset (the default) leaves the dashboard's login screen off entirely — it
falls back to `VITE_API_AUTH_TOKEN` or no auth, exactly as before this
feature existed.

| Variable | Default | Purpose |
|---|---|---|
| `VITE_OIDC_AUTHORITY` | *(unset)* | Your identity provider's issuer URL. Must be registered as a separate public/SPA OIDC client there. |
| `VITE_OIDC_CLIENT_ID` | *(unset)* | The SPA's own client id at the identity provider. |
| `VITE_OIDC_SCOPE` | `openid profile` | OAuth scopes requested. |
| `VITE_OIDC_REDIRECT_URI` | `<origin>/auth/callback` | Must be registered as an allowed redirect URI at the identity provider. |
| `VITE_OIDC_POST_LOGOUT_REDIRECT_URI` | `<origin>` | Where the IdP sends the user after sign-out. |
| `VITE_API_AUTH_TOKEN` | *(unset)* | A build-time copy of `API_AUTH_TOKEN` above, used as a fallback when OIDC isn't configured (or as the sole credential for a trusted-network/single-operator deployment). |

### Local self-hosted accounts, Google sign-in & conversation sharing

`LOCAL_AUTH_ENABLED=true` (plus `AUTH_DATABASE_URL` — a dedicated
PostgreSQL database — and `JWT_SECRET_KEY`) turns on this app's own
accounts (`identity/`): real login/registration, per-user chat history,
and (once set up) Google sign-in and secure conversation sharing on top.
See `.env.example`'s own "Local self-hosted accounts" block for the full
set (password/session/lockout tuning), and `docs/AUTHENTICATION.md` for
the complete design — the two tables below cover only what's new.

**Google sign-in** ("Continue with Google" — requires `LOCAL_AUTH_ENABLED=true`;
refuses to start otherwise). See `docs/AUTHENTICATION.md`'s "Google
sign-in" section for the chosen flow, every server-side token check, and
the Google Cloud Console setup steps.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | *(unset)* | Setting this turns Google sign-in on. Unlike every other credential in this table, this value is **public by design** (it ships in the Google Identity Services JS the browser runs) — served live to the frontend via `GET /health`, never baked into a `VITE_*` build-time variable, so it can be rotated with a process restart alone. |
| `GOOGLE_OAUTH_ALLOWED_HOSTED_DOMAINS` | *(unset = any account)* | Comma-separated Google Workspace domain(s) to restrict sign-in to. Enforced from the token's own signed `hd` claim — never inferred from the email's `@domain` suffix. |
| `GOOGLE_OAUTH_CLOCK_SKEW_SECONDS` | `60` | Leeway for the Google ID token's `iat`/`exp` validation — same purpose as `OIDC_CLOCK_SKEW_SECONDS` above, kept separate since this feature doesn't share `security/oidc.py`'s validation path. |
| `GOOGLE_OAUTH_NONCE_TTL_SECONDS` | `300` | How long a server-issued sign-in nonce (`GET /auth/google/nonce`) stays claimable before it expires unused. |

**Secure conversation sharing** ("Share conversation" — requires
`LOCAL_AUTH_ENABLED=true`). See `docs/SHARING_SECURITY.md` for the full
RBAC/ABAC model, token lifecycle, and snapshot/projection rules.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_CONVERSATION_SHARING` | `true` | Master switch for the whole feature. |
| `SHARE_PUBLIC_LINKS_ENABLED` | `false` | A deliberately separate, deny-by-default switch: even with sharing on, an owner can only create an "anyone with the link" share once an operator explicitly opts in here. Invite-only sharing works with this left off. |
| `SHARE_DEFAULT_EXPIRY_DAYS` | `30` | Default expiry for a new share (and for a re-share after revocation) — always finite, shown in the owner UI. |
| `SHARE_INVITATION_EXPIRY_DAYS` | `14` | How long an invited member's own accept link stays valid. |
| `SHARE_MAX_MEMBERS_PER_CONVERSATION` | `50` | Anti-abuse cap on invited members per share. |
| `SHARE_LINK_ACCESS_RATE_LIMIT_PER_MINUTE` | `30` | Per-IP rate limit on the anonymous-reachable `GET /share-view/{ref}` (and its attachment sibling) — the only sharing routes with no authenticated caller identity to key a limiter on. |
| `SHARE_INVITE_RATE_LIMIT_PER_HOUR` | `20` | Per-caller rate limit on inviting members, regenerating a link, and accepting an invitation. |

**Operator note**: after deploying either feature to an already-provisioned
database, run `python scripts/bootstrap_admin.py` once (idempotent, safe to
re-run) — this codebase has no automatic "re-seed RBAC permissions on
startup" hook, so a newly-added permission (like the ones sharing needs)
is invisible to existing roles until this is run. See
`docs/SHARING_SECURITY.md`'s "External configuration / staging steps".

### Governed model routing (optional, off by default)

Picks which locally-installed Ollama model drafts each LLM call the SQL
pipeline makes, instead of using one model for every call in a run. See
[`47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md`](../47_MODEL_ROUTING_COST_OPTIMIZATION_CONTRACT.md)
for the full design and limitations.

**Off by default**, unlike the pure-computation analytics engines: this
changes which model answers a real question. With `ENABLE_MODEL_ROUTING`
unset or false, every call in a run uses `OLLAMA_MODEL` (or the caller's
own `AskRequest.model`), byte-identically to before this feature existed.

Three things worth knowing before turning it on:

1. **A caller's explicit model choice always wins.** When `AskRequest.model`
   is set, the router is not even constructed.
2. **With `MODEL_ROUTING_REQUIRE_EVALUATION_EVIDENCE` on (the default),
   routing does nothing until a model has been evaluated and approved
   through the AI Governance Center** (`/governance`, an `approved` or
   `deployed` `model`-typed asset version with recorded
   `evaluation_results`). That is deliberate — "governed routing" means a
   human approved the model routing may move to — but it does mean a fresh
   deployment will see every decision recorded as `no_evaluation_evidence`
   until that is done. Set it to `false` to route on
   `config/ollama_models.yaml`'s tier/capability metadata alone.
3. **Routing is not a security control.** It never validates SQL, checks a
   permission or resolves a tenant; `agent/sql_validator.py`'s AST allowlist
   and the RBAC/restricted-column gates are unchanged and still decide
   everything they decided before.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_MODEL_ROUTING` | `false` | Enables routing and the three `/model-routing/*` routes (404 when off). |
| `MODEL_ROUTING_REQUIRE_EVALUATION_EVIDENCE` | `true` | A model is only chosen over `OLLAMA_MODEL` when a governed, approved evaluation record exists for it. Applies to downgrades too. |
| `MODEL_ROUTING_UPGRADE_COMPLEXITY_THRESHOLD` | `2` | Complexity score at or above which routing may escalate. |
| `MODEL_ROUTING_DOWNGRADE_COMPLEXITY_THRESHOLD` | `0` | Score at or below which a low-risk task (`analytical_intent`, `sql_insight`) may use a smaller model. Must be strictly less than the upgrade threshold. |
| `MODEL_ROUTING_MAX_P95_LATENCY_MS` | `30000.0` | A model this deployment has *measured* as slower than this at p95 is dropped from the escalation set. An unmeasured model is never blocked. |
| `MODEL_ROUTING_MAX_FALLBACKS` | `2` | Alternative models one decision may try if the chosen one is unavailable. `0` disables fallback. |
| `MODEL_ROUTING_MAX_LLM_CALLS_PER_REQUEST` | `40` | Per-request routed-call cap. |
| `MODEL_ROUTING_MAX_COST_UNITS_PER_REQUEST` | `60` | Per-request compute-unit cap (small 1, medium 2, large 5 — ordinal units, never money: every model runs locally). |
| `MODEL_ROUTING_ESCALATIONS_PER_HOUR` | `500` | Per-tenant hourly cap on escalations (not on questions). |
| `MODEL_ROUTING_BREAKER_FAILURE_THRESHOLD` | `3` | Consecutive *availability* failures before a model is skipped. A malformed answer is a quality failure and never trips this. |
| `MODEL_ROUTING_BREAKER_COOLDOWN_SECONDS` | `60.0` | Time before an open breaker allows one probe. |
| `MODEL_ROUTING_EVIDENCE_TTL_SECONDS` | `300.0` | How long a tenant's governed evaluation evidence is cached on the request path. |

A spent budget never fails a question — it removes the *option* to escalate,
and the request proceeds on `OLLAMA_MODEL`. Breakers, budget windows and the
per-route metrics rollup are process-local, so a multi-worker deployment has
one of each per worker (the same already-disclosed limitation as
`agent/rate_limit.py` and `observability/metrics.py`).

### Proactive KPI monitoring (on by default)

Watches **governed** catalog metrics for meaningful movement, investigates
what moved, suppresses repeats and noise, and ranks what is left by
impact. See
[`50_PROACTIVE_AI_ANALYST_CONTRACT.md`](../50_PROACTIVE_AI_ANALYST_CONTRACT.md)
for the full design and limitations.

Three things worth knowing before configuring it:

1. **Only an approved metric can be monitored.** A policy must name a
   *published* `METRIC` entry in the caller's own semantic catalog
   (Prompt 09/10's approval workflow). A draft metric, a dimension, or an
   unknown key is refused with a distinct reason code.
2. **It never fetches anything.** The period series and dimension
   breakdowns are supplied by the caller, already fetched through the
   governed path (`POST /execute`). That is why monitoring cannot bypass
   `agent/sql_validator.py` -- it has no way to produce a row.
3. **No alerts are actually delivered.** This platform has no email,
   webhook or chat channel. A run returns a typed description of what
   *would* be sent per recipient.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_KPI_MONITORING` | `true` | Enables the two `/kpi-monitoring/*` routes (404 when off). |
| `KPI_MONITOR_MIN_CHANGE_PERCENT` | `10.0` | Default "meaningful change" floor for a policy that does not set its own. |
| `KPI_MONITOR_MAX_POLICIES_PER_SCAN` | `50` | Policies evaluated per scan; the excess is reported, not dropped. |
| `KPI_MONITOR_MAX_POINTS_PER_METRIC` | `500` | Most recent points kept per series. |
| `KPI_MONITOR_MAX_FINDINGS_PER_SCAN` | `200` | Findings kept per scan, ranked before truncation. |

Per-policy settings (thresholds, comparison period, dimension columns)
and per-run settings (business calendar, quiet periods, recipient
preferences) are supplied in the request, not in `.env` -- they are
per-metric and per-tenant, not per-deployment.

`scripts/run_kpi_monitor.py` runs a scan from cron/Task Scheduler and
exits `2` when any finding is high-risk. It cannot persist a governed
review record (no actor/tenant) -- see the contract's §9.

### Executive AI briefing (on by default)

An evidence-linked daily/weekly/monthly summary over records the KPI
monitor, schema drift, the recommendation engine and the data-quality
engine already produced. See
[`51_EXECUTIVE_AI_BRIEFING_CONTRACT.md`](../51_EXECUTIVE_AI_BRIEFING_CONTRACT.md)
for the full design and limitations.

Four things worth knowing:

1. **It reads; it never detects, computes or persists.** Every claim is
   read back from a record another governed subsystem already wrote, and
   nothing is stored -- a briefing is a read of governed state, not a new
   governed artifact. No monitored-database query, no LLM call, no
   outbound call.
2. **Every claim links to evidence and versions, or it is withheld.** A
   claim with no evidence, with an evidence link recording no version, or
   stating a number its own evidence does not contain is reported in
   `withheld_claims` rather than shown.
3. **Facts, estimates and recommendations are separate claim classes**,
   and the mismatched pairings are refused at construction. Nothing a
   briefing shows is ever confirmed business truth -- confirming stays an
   explicit human action elsewhere (publishing a catalog entry, accepting
   a finding, resolving a recommendation).
4. **Nothing is delivered.** This platform has no email, webhook or chat
   channel, and no scheduler. `period` selects the freshness budget and
   the heading; it is not a date filter, because period labels stay
   opaque strings throughout.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_EXECUTIVE_BRIEFING` | `true` | Enables `POST /briefing/generate` (404 when off). |
| `BRIEFING_STALE_AFTER_DAILY_HOURS` | `36.0` | How old evidence may be before a daily briefing marks it stale. |
| `BRIEFING_STALE_AFTER_WEEKLY_HOURS` | `192.0` | The same budget for a weekly briefing. |
| `BRIEFING_STALE_AFTER_MONTHLY_HOURS` | `840.0` | The same budget for a monthly briefing. |
| `BRIEFING_MAX_ITEMS_PER_SECTION` | `10` | Claims kept per section, ranked before truncation; the affected sections are named in the response. |
| `BRIEFING_RISK_MIN_TIER` | `medium` | Tier a finding must reach before a *second*, inferred risk/opportunity claim is made about it. Never gates the observed movement itself. |
| `BRIEFING_MAX_DATA_QUALITY_FINDINGS` | `200` | Payload bound on the one source that arrives in the request body. |

Three budgets rather than one because the honest threshold scales with
the cadence: evidence gathered a week ago is misleading in a daily
briefing and entirely normal in a monthly one. A stale claim is marked
and ranked below fresh claims, never dropped -- hiding it would turn a
freshness problem into an apparent absence of problems.

Authorization reuses the gates the underlying records already answer to
(catalog review for findings, recommendation view for recommendation
records); there is no briefing-specific permission. A caller entitled to
one source but not the other gets the other named as omitted.

### Executive Decision Center (on by default)

One screen over everything Prompts 33-50 record. See
[`52_EXECUTIVE_DECISION_CENTER_CONTRACT.md`](../52_EXECUTIVE_DECISION_CENTER_CONTRACT.md)
for the full design and limitations.

Three things worth knowing:

1. **It aggregates; it never computes a business fact.** Every number
   comes from a record an earlier capability already wrote, carried with
   that capability's own truth level.
2. **Only capabilities that persist tenant-scoped records can appear** --
   KPI monitoring, schema drift, semantic intelligence, recommendations,
   governance, workflows and onboarding. The compute-on-demand engines
   (forecasting, scenarios, optimization, predictive, data quality,
   lineage, root cause) persist nothing, so they appear only through what
   a persisted record captured. Every briefing lists what did not
   contribute, so "nothing to report" is distinguishable from "not read".
3. **Personalization reorders; it never changes a fact.** Section order
   and the per-section cap come from the viewer's server-resolved role.
   Every section is present for every role, and the numbers are identical.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_DECISION_CENTER` | `true` | Enables `GET /decision-center/briefing` (404 when off). |
| `DECISION_CENTER_MAX_CHAINS` | `10` | Decision chains assembled per briefing. |

Authorization is `ADMIN_DASHBOARD_READ` (admin, auditor, manager) -- the
same tier the tenant-admin read routes use.

## AI analysis layers (optional, all off by default)

See the Prompt 33/34/35 contract documents for design and limitations. Every
limit below is a server-side stop rule that a request cannot raise.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_DATA_ANALYST_AGENT` | `false` | Enables `POST /analyst/investigate`. |
| `ANALYST_MAX_STEPS` | `12` | Charged node executions per analysis. |
| `ANALYST_MAX_SUBQUERIES` | `4` | Sub-questions run per analysis (planner and follow-ups). |
| `ANALYST_MAX_LLM_CALLS` | `2` | The analyst's own planner LLM calls. |
| `ANALYST_MAX_FOLLOWUPS` | `2` | Anomaly follow-ups per analysis. |
| `ANALYST_TIMEOUT_SECONDS` | `180` | Wall-clock deadline, checked between steps. |
| `ANALYST_MAX_RECOMMENDATIONS` | `10` | Recommendations kept in a report. |
| `ENABLE_MULTI_AGENT_SUPERVISOR` | `false` | Enables `POST /analyst/supervise`. |
| `MULTIAGENT_MAX_AGENT_CALLS` | `16` | Specialist invocations per turn. |
| `MULTIAGENT_MAX_COST_UNITS` | `40` | Cost units per turn (governed SQL 5, planner 3, engines 1, control 0). |
| `MULTIAGENT_CIRCUIT_FAILURE_THRESHOLD` | `3` | Consecutive failures that open an agent's breaker (scoped per tenant). |
| `MULTIAGENT_CIRCUIT_COOLDOWN_SECONDS` | `60` | Time before an open breaker allows one probe. |
| `ENABLE_SEMANTIC_INTELLIGENCE` | `false` | Enables `/semantic-intelligence/*`. |
| `SEMANTIC_INTELLIGENCE_MAX_FINDINGS` | `500` | Most findings one run persists (riskiest first). Any excess is reported as truncated. |

## Multi-source router (optional, off by default)

See [`docs/MULTI_SOURCE_GUIDE.md`](MULTI_SOURCE_GUIDE.md) for a walkthrough
of turning each of these on; [`docs/ARCHITECTURE.md`](ARCHITECTURE.md#4-multi-source-orchestration)
for how the router/subgraphs work internally.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_MULTI_SOURCE_ROUTER` | `false` | Routes questions through `agent.orchestrator.graph.run_orchestrated` instead of calling `agent.graph.run_agent` directly. Off means the orchestrator graph is never even constructed — a pure pass-through. |

### Document/policy RAG store

| Variable | Default | Purpose |
|---|---|---|
| `RAG_STORE_CONNECTION_STRING` | *(unset)* | SQLAlchemy connection string for a **dedicated** SQL Server 2025+/Azure SQL database (native `VECTOR` column type — `rag/store.py`). Never reuse a `DB_CONNECTIONS` business database. Required for `ENABLE_DOCUMENT_RAG`/`ENABLE_POLICY_RAG` to actually work; `SecretStr`-wrapped. |
| `RAG_STORE_ODBC_DRIVER` | `ODBC Driver 17 for SQL Server` | Same meaning as `DB_ODBC_DRIVER`, for the RAG store connection. |
| `ENABLE_DOCUMENT_RAG` | `false` | Offers the general "documents" collection to the router. Needs `RAG_STORE_CONNECTION_STRING` too. |
| `ENABLE_POLICY_RAG` | `false` | Offers the separate, more access-sensitive "policies" collection. Independent of `ENABLE_DOCUMENT_RAG`. |
| `RAG_TOP_K` | `4` | Chunks retrieved per question, per collection. |
| `RAG_MAX_RETRIES` | `2` | Query-rewrite retries in the agentic RAG subgraph before falling back to "insufficient information." |
| `RAG_CHUNK_SIZE` | `1200` | Target chunk length (characters) when splitting an ingested PDF's text. |
| `RAG_CHUNK_OVERLAP` | `150` | Character overlap between consecutive chunks. |
| `RAG_EMBEDDING_MODEL_NAME` | *(blank = reuse `EMBEDDING_MODEL_NAME`)* | Embedding model for document/policy chunks, if it needs to differ from schema retrieval's. |
| `MAX_DOCUMENT_PAGES` | `2000` | Upper bound on one uploaded PDF's page count, enforced before extracting text from any page — closes a decompression-bomb-shaped gap `MAX_DOCUMENT_UPLOAD_MB` alone doesn't (a PDF's on-disk size says little about how many pages it takes to extract). |

### Live web search

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_WEB_SEARCH` | `false` | Offers the web_search node to the router. Needs `WEB_SEARCH_API_KEY` too. |
| `WEB_SEARCH_PROVIDER` | `tavily` | Selects the provider (`search/web_search.py::SUPPORTED_SEARCH_PROVIDERS`). Only `tavily` is implemented today. |
| `WEB_SEARCH_API_KEY` | *(unset)* | API key for the configured provider. Get a Tavily key at [tavily.com](https://tavily.com). `SecretStr`-wrapped. |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Max results requested per search call. |

### REST/OpenAPI connector (optional, off by default)

Calls a fixed set of operator-declared, allowlisted HTTPS endpoints
read-only. See
[`docs/architecture/source-connector-contract.md`](architecture/source-connector-contract.md)
§9.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_REST_CONNECTOR` | `false` | Enables `rest/`. **Not routable yet** — there is no `sources/descriptors.py` entry, so this does not appear in `GET /sources` and the router cannot reach it. |
| `REST_ALLOWED_HOSTS` | *(empty)* | Comma-separated hostnames the connector may call. Matched **exactly** and case-insensitively, never as a suffix — an `example.com` entry does not permit `evil-example.com` or `example.com.evil.net`. Empty refuses everything. |
| `REST_TIMEOUT_SECONDS` | `10.0` | Per-request timeout. |
| `REST_MAX_RESPONSE_BYTES` | `2000000` | Cap on bytes read from one response. Enforced against a declared `Content-Length` before any body is read *and* against bytes actually received, since a response can omit or lie about that header. |
| `REST_MAX_REDIRECTS` | `3` | Redirect hops a call may follow. Each hop is a fresh SSRF check, so an unbounded chain means unbounded validation work. `0` follows none. |

**Enabling the flag without naming hosts is inert, not open.** An
allowlist with no entries permits nothing — for an allowlist, "unset" must
mean "nothing", never "any".

Every URL is validated on **every** use, never once at configuration time:
HTTPS-only, the host allowlist checked *before* DNS (so a rejected URL
causes no network request at all, not even a lookup), and **every**
resolved address checked against the private/loopback/link-local/reserved
blocklist — not just the first, or record ordering would decide. Redirects
are followed manually with each hop re-validated. See
[`security/outbound_url.py`](../security/outbound_url.py).

### Domain analytics skills (optional, off by default)

Frames an analysis using a declared skill for the question's business
domain. See
[`docs/architecture/domain-skills.md`](architecture/domain-skills.md).

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_DOMAIN_SKILLS` | `false` | Appends an additional system message framing the analysis, when the routed domain has a declared skill. |

**An accuracy aid, so it fails open.** A domain with no declared skill, an
unknown domain (`GENERAL`), or the flag off all change nothing — the
analysis proceeds exactly as it did before.

**Skills are in-code constants** (`domain_skills/catalog.py`) and cannot be
added or edited at runtime. There is no API route, no admin surface and no
setting that introduces one: a database row that becomes a system prompt
would turn every write into prompt injection with full pipeline reach.

**A skill grants nothing.** Authorization stays with `agent/authz.py` and
`identity/rbac.py`; what may be returned stays with `governance/`. None of
those gates reads a skill.

Coverage today is **Sales, MIS / Management Reporting, Marketing, HR,
eCommerce, Training / L&D, Finance & Corporate Functions, BFSI, Supply
Chain / Procurement & Logistics, Manufacturing & Operations, Customer
Service & Support and Project & Portfolio Management** — 60 skills across
11 governance domains, since Sales and Marketing share `SALES_MARKETING`.
The other 7 governance domains have none declared — a skill is added when
its prompt arrives, never in anticipation.

Guidance for a domain automatically carries that domain's prohibited
actions, derived from `governance/domains.py`. HR, for example, prohibits
individual performance ranking, so its guidance says so even though the
shared contract otherwise invites ranking.

A domain holding two prompt-domains (as `SALES_MARKETING` does) produces
**no** skill framing unless a specific skill is named, rather than guessing
between them.

### MCP client (optional, off by default)

A client *of* operator-declared remote MCP servers. Not to be confused
with [`TOOLS.md`](TOOLS.md)'s `agent/tools/`, which is MCP-*shaped* so this
app's own capabilities could be exposed *to* an external MCP client --
opposite direction. See
[`docs/architecture/source-connector-contract.md`](architecture/source-connector-contract.md)
Section 10.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_MCP_CLIENT` | `false` | Enables `mcp_client/`. |
| `MCP_REQUEST_TIMEOUT_SECONDS` | `30.0` | Per-exchange wall-clock cap. |
| `MCP_MAX_RESPONSE_BYTES` | `1000000` | Cap on one response line. |
| `MCP_SERVERS` | *(empty)* | Comma-separated server names, each with its own `MCP_<NAME>_*` block -- the same convention `DB_CONNECTIONS` uses. |

Per server `X` (prefix from the name, as `DB_<NAME>_*` does it):

| Variable | Required | Purpose |
|---|---|---|
| `MCP_X_COMMAND` | yes | The executable. Run as `argv[0]` with **`shell=False`** -- nothing here is interpreted by a shell. |
| `MCP_X_ARGS` | no | Comma-separated argv entries, each passed verbatim. |
| `MCP_X_PERMISSION` | yes | The `agent.authz.Permission` a caller needs. No default -- there is no unrestricted remote server. |
| `MCP_X_ALLOWED_TOOLS` | no | Tool names an administrator has approved. **Omitted means none**: the server is reachable but no tool may be called. Matched exactly -- approving `read_file` does not approve `read_file_and_delete`. |
| `MCP_X_ENV` | no | `KEY=VALUE` pairs. The subprocess receives **exactly** these and nothing else. |

**Two things to understand before enabling this.**

First, it spawns a subprocess -- the only production feature here that
does. The command never goes through a shell, and the child does **not**
inherit this process's environment: it gets only what `MCP_X_ENV` names.
That matters because this process's environment holds `DB_PASSWORD`,
`RAG_STORE_CONNECTION_STRING`, `JWT_SECRET_KEY` and every other secret in
`.env`, and `subprocess`'s default would hand all of it to a third-party
binary.

Second, everything a server sends is data, never instructions. That
includes each tool's `description` and the `instructions` string a server
may return from `initialize` -- the latter is read, logged at debug, and
**discarded**, because feeding it to a model would be prompt injection
with full pipeline reach.

Protocol revision **2025-06-18**. A server speaking only the 2026-07-28
stateless revision (which removes the handshake) is refused with a clear
error rather than mis-parsed.

### Voice mode (optional, on by default)

Local speech-to-text/text-to-speech — no cloud API, same posture as
Ollama; unlike media generation, this feature spends no money and calls
no external API once its one-time local model downloads are done, so it
ships enabled. See `CLAUDE.md`'s "Voice mode" section for the full
design.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_VOICE_MODE` | `true` | Whether the mic button/settings toggle appear at all (`GET /health`'s `voice_enabled`). |
| `STT_MODEL_SIZE` | `base` | `faster-whisper` model size (`tiny`/`base`/`small`/`medium`/`large-v3`). Auto-downloaded from Hugging Face Hub on first use. |
| `STT_DEVICE` | `cpu` | `cpu` or `cuda`. Only set `cuda` on a machine with a confirmed working CUDA + cuDNN setup — nothing in this project's Docker image assumes a GPU. |
| `STT_VOCABULARY_MAX_CHARS` | `200` | Caps the schema-derived vocabulary hint fed to Whisper's `initial_prompt` (`voice/stt.py::_build_vocabulary_hint`). |
| `VOICE_MAX_UPLOAD_MB` | `10` | Max size of one recorded-question upload to `POST /voice/transcribe`. |
| `VOICE_MAX_DURATION_SECONDS` | `30` | Max recording duration, checked before transcription runs. |
| `TTS_VOICE` | `en_US-lessac-medium` | Piper voice name — download it once with `python scripts/download_voice_model.py`. |
| `TTS_VOICE_MODEL_PATH` | *(unset)* | Override for the `.onnx`/`.onnx.json` location, if not the default `voice/models/<TTS_VOICE>.onnx`. |

### Media search (optional, off by default)

Content-based search over an untagged local image/video library. Unlike
voice mode, this stays off by default: it needs a real
`MEDIA_LIBRARY_PATH` configured, and its default local-CLIP embedding
path pulls in `torch`/`opencv-python` — a real, meaningfully larger
dependency footprint than this project's other optional features. See
`CLAUDE.md`'s "Media search" section for the full design.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_MEDIA_SEARCH` | `false` | Whether the "media_search" orchestrator source and `POST /search/media` are offered at all. Also requires `MEDIA_LIBRARY_PATH`. |
| `MEDIA_LIBRARY_PATH` | *(unset)* | Root folder `scripts/build_media_index.py` walks to ingest images/videos. |
| `MEDIA_EMBEDDING_PROVIDER` | `local_clip` | Only `local_clip` (on-device, via `sentence-transformers`) is implemented today — see `media/embedding.py`. |
| `MEDIA_CLIP_MODEL_NAME` | `clip-ViT-B-32` | The CLIP checkpoint used to embed both images and query text. |
| `MEDIA_VISION_MODEL` | *(unset)* | An Ollama vision-capable model name (e.g. `llava`) for per-video-segment captioning — pull it once with `ollama pull llava`. Blank skips captioning; segments are still indexed via ASR transcript + OCR text alone. |
| `MEDIA_MAX_FILE_MB` | `200` | Max size of one file `media/ingest.py` will process. |
| `MEDIA_SEARCH_TOP_K` | `5` | Max hits returned per query, across images and video segments combined. |
| `MEDIA_SCENE_DETECT_THRESHOLD` | `27.0` | `PySceneDetect`'s `ContentDetector` sensitivity — lower detects more (subtler) scene changes. |

### Content moderation gate (mandatory, not a feature flag)

Runs before anything is embedded/stored by **either** media search
(`ENABLE_MEDIA_SEARCH`) or document/policy RAG (`ENABLE_DOCUMENT_RAG`/
`ENABLE_POLICY_RAG`) — there is deliberately no flag to disable this while
either pipeline is on; missing configuration below fails ingestion closed
with a clear error instead. See `moderation/taxonomy.py`'s module
docstring for the full category table and `SECURITY.md`'s "Content
moderation gate" section for the design rationale.

| Variable | Default | Purpose |
|---|---|---|
| `MODERATION_PROVIDER` | `azure_content_safety` | Selects the provider (`moderation/provider.py::SUPPORTED_MODERATION_PROVIDERS`). Only Azure AI Content Safety is implemented today. |
| `AZURE_CONTENT_SAFETY_ENDPOINT` | *(unset)* | Your Content Safety resource's REST endpoint. |
| `AZURE_CONTENT_SAFETY_KEY` | *(unset)* | Subscription key for the endpoint above. `SecretStr`-wrapped. |
| `MODERATION_SEVERITY_THRESHOLD` | `4` | Minimum Azure severity (0/2/4/6) that hard-rejects a Hate/SelfHarm/Sexual/Violence category. Verify against your Content Safety API version. |
| `MODERATION_BLOCKLIST_PATH` | *(blank = `config/moderation_blocklist.yaml`)* | Override for the weapons/drugs text-term blocklist — Azure has no dedicated category for either. |
| `MODERATION_STORE_CONNECTION_STRING` | *(unset)* | Dedicated SQL Server connection for moderation decisions/dedupe metadata (`moderation.media_assets`) — separate from `DB_CONNECTIONS` **and** `RAG_STORE_CONNECTION_STRING` (point both at the same database if you want one shared store). `SecretStr`-wrapped. |
| `MODERATION_STORE_ODBC_DRIVER` | `ODBC Driver 17 for SQL Server` | Same meaning as `DB_ODBC_DRIVER`, for this connection. |
| `MODERATION_STORE_POOL_SIZE` | `10` | `QueuePool` size — explicit here (unlike `DB_CONNECTIONS`/`RAG_STORE_CONNECTION_STRING`, both on SQLAlchemy's default of 5) since ingestion can run many concurrent DB writes. |
| `MODERATION_STORE_MAX_OVERFLOW` | `20` | `QueuePool` burst ceiling above the pool size. |
| `MODERATION_STORE_POOL_RECYCLE_SECONDS` | `1800` | Discard/replace a pooled connection after this long, regardless of use — set below your SQL Server/network's idle-connection timeout. |
| `MEDIA_INGEST_WORKERS` | `4` | Concurrent worker threads `scripts/build_media_index.py` uses (this project's first bounded thread pool — previously a sequential loop). |
| `MEDIA_IMAGE_TILE_THRESHOLD_PX` | `2048` | An ingested image larger than this (either dimension) is tiled before moderation, so a classifier's own downsampling can't hide a small region of concern. |

## Chat attachments (images, PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, JSON)

On by default — no cost, no outbound network call. `POST /attachments/upload`
validates, malware-scans (see below), stores, and eagerly processes each
file (`attachments/pipeline.py`); `POST /ask`'s `attachment_ids` gives the
model actual access via a dedicated LangGraph subgraph
(`attachments/graph.py`). Works even with `ENABLE_MULTI_SOURCE_ROUTER=false`
— attaching a file is a per-request opt-in, not a standing routing
decision. See `CLAUDE.md`'s "Chat attachments" and "Explicit image
actions" sections for the full design.

**Image understanding needs a vision-capable model.** Set
`MEDIA_VISION_MODEL` (above, in the Media search section) to a
vision-capable Ollama model — check with `ollama list` and look for
`"vision"` in that model's own capabilities, since not every locally
pulled model can see images (e.g. this project's own default
`OLLAMA_MODEL=llama3.1:8b` cannot). Without it, an attached image still
uploads and stores normally, but a question about its visual content
degrades to on-screen-text OCR only (needs the system Tesseract binary —
see `CLAUDE.md`'s Windows-specific notes) and returns a specific
"image understanding is not configured" message rather than a generic
failure — never a false claim that the image was analyzed.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_CHAT_ATTACHMENTS` | `true` | Whether the composer's attachment upload/`POST /ask` attachment path is offered at all. |
| `MAX_ATTACHMENT_IMAGE_BYTES` | `10485760` (10MB) | Per-image upload size cap. |
| `MAX_ATTACHMENT_DOCUMENT_BYTES` | `26214400` (25MB) | Per-document (PDF/DOCX/XLSX/PPTX/TXT/MD/CSV/JSON) upload size cap. |
| `MAX_ATTACHMENTS_PER_MESSAGE` | `5` | Max files one `/ask` call may attach at once. |
| `MAX_TOTAL_ATTACHMENT_BYTES` | `52428800` (50MB) | Combined size cap across every attachment on one message. |
| `MAX_ATTACHMENT_TEXT_CHARS` | `80000` | Ceiling on combined extracted-text characters injected into one generation prompt, across all attachments. |
| `MAX_ATTACHMENT_DOCUMENT_PAGES` | `200` | Max pages read from one attached PDF. |
| `MAX_ATTACHMENT_SPREADSHEET_ROWS` | `500` | Max rows read per sheet from one attached XLSX/CSV. |
| `MAX_ATTACHMENT_IMAGE_DIMENSION_PX` | `1568` | An attached image wider/taller than this is downscaled before being sent to the vision model (never affects the stored original). |
| `ATTACHMENT_STORAGE_DIR` | `./data/attachments` | Where attachment bytes are stored, one file per generated `attachment_id` (never the caller's filename). Gitignored, like every other runtime-data directory. |
| `ATTACHMENT_RETENTION_HOURS` | `24` | How long a stored attachment is kept before opportunistic cleanup considers it eligible for deletion (this project has no background scheduler — cleanup runs on each new upload, not on a timer). |
| `MAX_ATTACHMENT_RESIZE_DIMENSION_PX` | `4096` | Upper bound on a requested `POST /attachments/{id}/resize` target width/height, and the working size OCR/text-removal downscale an oversized source image to first. |
| `ATTACHMENT_OCR_TIMEOUT_SECONDS` | `20.0` | Per-call timeout for a Tesseract OCR pass (`POST /attachments/{id}/extract-text`, and the auto-detect step of `remove-text`). |
| `MAX_TEXT_REMOVAL_REGIONS` | `20` | Max text regions one `POST /attachments/{id}/remove-text` call will mask and inpaint at once. |
| `MAX_ATTACHMENT_ZIP_UNCOMPRESSED_BYTES` | `209715200` (200MB) | Decompression-bomb guard for DOCX/XLSX/PPTX (all plain ZIP archives) — rejected if the archive's own central-directory metadata reports more than this total uncompressed size, checked before any parser decompresses a single entry. |
| `MAX_ATTACHMENT_ZIP_ENTRIES` | `2000` | Companion entry-count cap for the same ZIP-container guard. |
| `ATTACHMENT_PROCESSING_TIMEOUT_SECONDS` | `30.0` | Hard wall-clock bound on one attachment's processor call — a pathological file can't hang a request thread past this. |

**Explicit image actions** — `POST /attachments/{id}/extract-text` (real
Tesseract OCR, distinct from vision-model description), `POST
/attachments/{id}/resize` (deterministic Pillow work, no model call),
`GET /attachments/{id}/detect-text-regions` + `POST
/attachments/{id}/remove-text` (classical OpenCV inpainting — explicitly
**not** generative AI, and never a solid rectangle), and `POST
/attachments/{id}/blur-region` (deterministic Pillow Gaussian blur, no
model call — see the AI-guided editing subsection below for why this one
is deliberately kept free/local rather than routed through a paid
provider) all reuse the settings above; see `docs/API.md`'s "Attachments"
section for the request/response shapes and `GET
/attachments/capabilities` for a live, per-deployment capability report.

### AI-guided (generative) image editing

Off by default — unlike the deterministic actions above, this spends real
money per call. Reuses the same `IMA_API_KEY` this app's separate
`ENABLE_MEDIA_GENERATION` image/video-generation feature uses (see
`CLAUDE.md`'s "Media generation" section — that setting isn't in this
table, only its key is reused here) — this flag only gates whether `POST
/attachments/{id}/ai-edit` is willing to use it. Local editing
(crop/rotate/draw), OCR, resize, and blur all keep working regardless of
this flag.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_IMAGE_EDITING` | `false` | Whether `POST /attachments/{id}/ai-edit` will call the real IMA Studio `image_to_image` provider. Requires `IMA_API_KEY` to also be set — an enabled flag with no key still reports `image_ai_editing: false` from `GET /attachments/capabilities`. |
| `IMAGE_EDIT_TIMEOUT_SECONDS` | `90` | Hard ceiling on one provider poll (task creation → completion) — not the whole request end-to-end; the upload and result-download legs are bounded separately by their own HTTP client timeouts. |
| `IMAGE_EDIT_POLL_INTERVAL_SECONDS` | `3.0` | How often to poll IMA for a task's status while waiting for it to complete. |
| `IMAGE_EDIT_MAX_PROMPT_LENGTH` | `500` | Max length of the free-text edit instruction (`AiImageEditRequest.prompt`). |

IMA's `image_to_image` models have no native mask/inpainting parameter
(verified live against the real provider) — a painted mask is conveyed as
a translucent overlay baked into the image plus a text instruction, never
presented as pixel-exact masking; this is disclosed to the caller via the
response's own `warnings` field, not a separate setting. See
`docs/image-editing-architecture.md` for the full design and
`CLAUDE.md`'s "AI-guided (generative) image editing" section.

## Malware scanning

Binary-signature scan of an upload's raw bytes (`security/malware_scanner.py`),
before any parser (`pypdf`/`pymupdf`/Pillow/`python-docx`/`openpyxl`/
`python-pptx`) ever touches them — shared by chat attachments
(`attachments/pipeline.py`), document/policy RAG PDF uploads
(`rag/ingestion.py`), and media-library ingestion (`media/ingest.py`).

Off by default (no scanning capability existed in this codebase before it
was added — defaulting it "on" would break every existing deployment with
no ClamAV daemon reachable), but **fail-closed once configured**: an
infected result and a scanner error/timeout/unreachable daemon are both
treated as a rejection, never silently treated as clean. A production
deployment (`ENVIRONMENT=production`) with chat attachments, document RAG,
policy RAG, or media search enabled **must** set this to `clamav` —
`Settings` refuses to start otherwise (see
`config/settings.py::_require_malware_scanning_in_production`).

| Variable | Default | Purpose |
|---|---|---|
| `MALWARE_SCAN_PROVIDER` | `disabled` | `disabled` \| `clamav` (`security/malware_scanner.py::SUPPORTED_MALWARE_SCAN_PROVIDERS`). |
| `CLAMAV_HOST` | `localhost` | Hostname/IP of the `clamd` daemon. Only read when `MALWARE_SCAN_PROVIDER=clamav`. |
| `CLAMAV_PORT` | `3310` | TCP port `clamd` listens on (ClamAV's own documented default). |
| `MALWARE_SCAN_TIMEOUT_SECONDS` | `15.0` | Socket timeout for one `clamd` `INSTREAM` scan call. |

## Validation behavior worth knowing

- **Missing vs. malformed are treated differently.** A missing `DB_HOST`
  is fine at import time (some non-DB functionality, like linting, doesn't
  need it) but raises `ConfigurationError` the moment something actually
  tries to connect (`db/connection.py::build_connection_url`). A
  *malformed* value (e.g. `DB_PORT=notanumber`) raises immediately at
  `Settings` construction — see `config/settings.py::_env_optional_int_strict`.
- **Security-relevant values are validated for sanity, not just type.**
  `MAX_RETRIES`, `MAX_RESULT_ROWS`, both rate limits, both cost thresholds,
  `RAG_TOP_K`, `RAG_MAX_RETRIES`, `RAG_CHUNK_SIZE`, `WEB_SEARCH_MAX_RESULTS`,
  etc. must be positive (`COMPLEX_QUERY_MAX_RETRY_BONUS` is the one
  exception -- `0` is a valid, deliberate "disable this" value, so only
  negative is rejected); `COST_MODERATE_ROW_THRESHOLD` must be strictly
  less than `COST_HIGH_ROW_THRESHOLD`; `LOG_REDACTION_LEVEL` must be
  `standard` or `strict` — all enforced in
  `Settings._validate_security_settings()`, with regression coverage in
  `tests/test_settings_validation.py`.
- **`Settings` is a process-wide, cached singleton** (`get_settings()`,
  `@lru_cache`) — changing `.env` requires a process restart to take
  effect, same as `db.connection`'s cached engine and
  `agent.rate_limit`'s process-wide limiter.
