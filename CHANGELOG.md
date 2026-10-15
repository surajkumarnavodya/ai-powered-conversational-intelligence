# Changelog

Newest first. The README's "News and Updates" section shows only the three most
recent entries. This file keeps the full record. Each entry names the contract
document that holds the design, the API, the configuration and the limitations.

## 2026-10-15: Source connectivity, streaming, and an evidence-based audit

Everything added here is off by default except streaming and the source
picker. `POST /ask` is unchanged: `/ask/stream` calls that handler rather
than reimplementing it, and omitting `AskRequest.sources` takes the exact
path it always did.

### Added

- **Source-type contract and registry** (`sources/`). Single owner of each
  source's routing description, required permission and "is it configured"
  rule. `agent/orchestrator/nodes.py` now derives `_SOURCE_DESCRIPTIONS`,
  `SOURCE_PERMISSIONS` and `get_available_sources` from it instead of three
  parallel literals that could drift. Also a `SourceConnector` lifecycle
  Protocol plus a mock and a conformance suite.
  Design: [`docs/architecture/source-connector-contract.md`](docs/architecture/source-connector-contract.md).
- **Per-conversation source selection** (`AskRequest.sources`, plus a picker
  in the chat composer and `GET /sources`). Restricts one question to named
  sources. Additive and optional; omitting it is the previous behaviour.
- **REST/OpenAPI connector** (`rest/`, flag `ENABLE_REST_CONNECTOR`).
  Operator-declared HTTPS endpoints, read-only, over an allowlist. Not
  routable yet: no `sources/descriptors.py` entry and no orchestrator node,
  so `GET /sources` does not advertise it.
- **SSRF-safe outbound helper** (`security/outbound_url.py`). HTTPS-only, a
  host allowlist checked *before* DNS, every resolved address checked against
  the private/reserved blocklist, redirects re-validated hop by hop, and a
  byte-capped JSON GET.
- **MCP client** (`mcp_client/`, flag `ENABLE_MCP_CLIENT`). A client *of*
  remote MCP servers over stdio — the opposite direction to `agent/tools/`,
  with no shared code. Targets MCP revision 2025-06-18, whose wire shapes were
  verified against the specification rather than written from memory.
- **Streaming** (`POST /ask/stream`, `api/streaming.py`, `agent/progress.py`).
  Server-sent events: per-node progress while a run is going, then the same
  `AskResponse` `POST /ask` returns. The chat composer's pending badge shows
  the running stage. Closes the "no streaming" gap `CLAUDE.md` §13 disclosed.
- **Opt-in live integration tests** (`tests/live/`, marked `live`,
  deselected by default via `addopts = "-m 'not live'"`). Covers what exists
  and is credential-blocked: web search, the RAG vector store, the REST
  connector against real DNS, the MCP handshake, and Ollama. Each skips by
  name when its credential is absent.
  Runbook: [`docs/LIVE_INTEGRATION_TESTING.md`](docs/LIVE_INTEGRATION_TESTING.md).
- **Capability inventory** ([`docs/architecture/feature-inventory.md`](docs/architecture/feature-inventory.md)),
  a source matrix and an implementation roadmap, all produced by an
  evidence-based audit rather than from a plan document.
- Settings: 8 new variables (`ENABLE_REST_CONNECTOR`, `REST_ALLOWED_HOSTS`,
  `REST_TIMEOUT_SECONDS`, `REST_MAX_RESPONSE_BYTES`, `REST_MAX_REDIRECTS`,
  `ENABLE_MCP_CLIENT`, `MCP_REQUEST_TIMEOUT_SECONDS`, `MCP_MAX_RESPONSE_BYTES`)
  plus the open-ended `MCP_SERVERS` + `MCP_<NAME>_*` block, all documented in
  `docs/CONFIGURATION.md` and `.env.example`.
- The header product name is now a home link that starts a new chat, running
  the identical two calls the sidebar's New chat button runs.

### Changed

- `agent/nodes.py::_timed_node` emits a progress event. With no sink bound —
  every caller except an SSE stream — it is a contextvar read and an early
  return. No node body changed.
- `AskResponse` gains `source_selection_notice`, additive with a default and
  deliberately separate from `permission_denied_notice`: "not configured" and
  "not permitted" have different causes and different remedies.
- `tests/test_sources_conformance.py` split into corpus-aware checks and a
  universal tier, correcting an overstated claim that any connector could be
  added to `_CONNECTORS` and inherit every check.

### Fixed

- Four tests were making **real DNS lookups** for `api.example.com` through
  `RestConnector.health`. They passed — the name NXDOMAINs — which is why it
  went unnoticed, but a green test that quietly depends on the network breaks
  the fully-mocked rule. Fixed with a transport-patching hook; re-verified at
  137 passed with `getaddrinfo` globally blocked.
- A `mypy` error introduced with source selection: `_apply_source_selection`
  inferred `notice` as `str` from its first assignment, making the later
  `notice = None` invalid. That batch's type check had not covered
  `agent/orchestrator/`.
- `CLAUDE.md` §13 listed mocked tests for `search/` as an open follow-up;
  `tests/test_web_search.py` had already closed it. §13 now warns that its own
  items are dated.

### Security

- **Source selection is a filter, never a grant.** `AskRequest.sources` is
  intersected with what configuration makes available *and* what the caller's
  roles permit, and the pre-existing role check still runs on whatever
  survives. Replacing the intersection with a union fails 19 tests.
- **MCP trust is administrator-controlled only.** A remote tool is never
  auto-registered into `agent.tools.registry.ToolRegistry` (pinned by an AST
  check), an empty per-server allowlist approves nothing, allowlist matching
  is exact, and a server's own metadata cannot widen its access.
- **`InitializeResult.instructions` is read and discarded.** The MCP spec
  describes it as telling a client how to use the server; feeding it to a
  model would be prompt injection with full pipeline reach.
- **The MCP subprocess gets no shell and no inherited environment.** This is
  the first production code here to spawn a subprocess. `env` is a complete
  replacement, so a third-party binary never receives `DB_PASSWORD`,
  `JWT_SECRET_KEY` or anything else from `.env`.
- **Progress events carry metadata only.** An event reaches the client before
  the result gate runs inside `execute_sql_node`, so it carries stage,
  attempt, duration, outcome and an error *category* — never SQL, a row, or an
  error message.
- Three copies of the SSRF blocklist now exist (`security/outbound_url.py`,
  `media_gen/download.py`, `bi_integration/outbound_safety.py`). Acceptable
  only because divergence cannot go unnoticed:
  `tests/test_outbound_url.py::TestBlocklistsAreIdentical` asserts all three
  match. Consolidating the two older copies is its own task, not done.
- Known, not fixed: the `ToolRegistry.execute` `caller_roles` override noted
  in the 2026-10-06 entry still stands. `mcp_client/` does not use that path.

### Migration and deployment

- **No database migration.** No schema change in this release.
- **No breaking change.** Every new setting defaults off, `AskRequest.sources`
  is optional, and `AskResponse.source_selection_notice` is additive with a
  default.
- To roll back any individual feature, unset its flag; no data is written that
  would need undoing.

### Verification

- Backend suite: **6,453 passed, 11 deselected** (the opt-in live tests), in
  755.93 s. Frontend: **1,079 passed** across 81 files. `tsc --noEmit` clean;
  `black --check` clean; `ruff` reports only the pre-existing `I001` in
  `agent/multiagent/agents.py`; `mypy` clean on every new module.
- Security controls were mutation-tested rather than asserted: the source
  filter (19 failures when turned into a union), three SSRF controls (3/9/1
  failures), and five MCP trust and transport controls (7/2/2/1/1 failures).
  Each mutation was applied to live code and reverted.
- **Not verified**: the MCP client has never met a real server, the REST
  connector has no live endpoint here, and no Microsoft 365 connector exists
  to test. See [`docs/LIVE_INTEGRATION_TESTING.md`](docs/LIVE_INTEGRATION_TESTING.md).

## 2026-10-06: AI analysis layers (Prompts 33-35)

All three layers are optional and off by default. None of them changes `/ask`.

### Added

- **AI Data Analyst** (`POST /analyst/investigate`, flag `ENABLE_DATA_ANALYST_AGENT`).
  A bounded, multi-step LangGraph loop that splits a business question, runs each
  part through the governed SQL pipeline, follows anomalies with fixed-template
  follow-ups, and returns an evidence-linked report. Budgets for steps,
  sub-questions, LLM calls, follow-ups and time are hard server-side stops.
  Contract: [`33_AI_DATA_ANALYST_AGENT_CONTRACT.md`](33_AI_DATA_ANALYST_AGENT_CONTRACT.md).
- **Multi-agent supervisor** (`POST /analyst/supervise`, flag `ENABLE_MULTI_AGENT_SUPERVISOR`).
  A deterministic supervisor over eight typed specialists. Each specialist has
  its own tool allowlist and truth-level ceiling. The tool gateway refuses
  identity fields in tool input. Circuit breakers are scoped per tenant. Claims
  must cite evidence the supervisor issued. Conflicts are resolved by a fixed rule.
  A benchmark (`eval/multiagent_benchmark.py`) confirms the supervisor makes the
  same SQL and planner calls as the analyst.
  Contract: [`34_MULTI_AGENT_SUPERVISOR_CONTRACT.md`](34_MULTI_AGENT_SUPERVISOR_CONTRACT.md).
- **Semantic intelligence** (`/semantic-intelligence/*`, flag `ENABLE_SEMANTIC_INTELLIGENCE`).
  Deterministic detection of synonym candidates, term clusters, ambiguous terms,
  conflicting metric definitions, candidate business rules, metric relationships
  and change impact over the governed catalog. Findings are versioned, carry an
  owner, and are ordered for review by risk rather than volume. Finding rollback
  and catalog-version rollback both create new versions and never rewrite history.
  Contract: [`35_SEMANTIC_INTELLIGENCE_CONTRACT.md`](35_SEMANTIC_INTELLIGENCE_CONTRACT.md).
- Database migration `a1f5c9e3b7d2` (additive): the `semantic_findings` table.
- Settings: 14 new variables, all documented in `docs/CONFIGURATION.md` and `.env.example`.

### Changed

- `rag/llm.py`: `call_ollama` accepts an optional `model` override. The default
  is unchanged for every existing caller.

### Security

- Every new route requires an authenticated local account with the relevant
  permission. Cross-tenant resources return 404, not 403.
- The analyst and the supervisor execute SQL only through the existing governed
  pipeline, under the caller's own roles.
- Findings and claims are always labelled `ai_inference` unless they come from a
  published catalog entry or an observed database value. Nothing inferred is
  presented as fact.
- Known, not fixed: `ToolRegistry.execute` lets tool input override
  `caller_roles`. The new supervisor gateway blocks this for everything it runs,
  but the shared registry still allows it. See the Prompt 34 contract's risk section.

### Verification

- Backend suite: 3,993 tests passing on the final code, including `tests/security/`.
- The three layers were verified with mocked LLM and SQL boundaries and with
  synthetic catalog data. They have not been run against a live model or a real
  tenant's catalog. See each contract's limitations section.
