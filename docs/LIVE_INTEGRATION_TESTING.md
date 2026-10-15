# Live integration testing

How to validate the parts of this platform that cannot be verified with
mocks, and what each one needs before it can run.

This document owns **the opt-in live-test runbook**. The mocked suite and
its rules are [`CLAUDE.md`](../CLAUDE.md) §7; what state each capability is
in is [`docs/architecture/feature-inventory.md`](architecture/feature-inventory.md).

---

## 1. How they run

Live tests live in [`tests/live/`](../tests/live/), are marked `live`, and
are **deselected by default**:

```toml
# pyproject.toml
addopts = "-m 'not live'"
```

So `pytest` — and CI — stays fully mocked: no real database, no Ollama, no
network. Opt in explicitly:

```bash
pytest -m live tests/live/                      # everything configured
pytest -m live tests/live/ -k WebSearch         # one provider
pytest -m live tests/live/ -rs                  # show why things skipped
```

Each test **skips by name** when its configuration is absent:

```
SKIPPED tests/live/conftest.py:40: live test needs WEB_SEARCH_API_KEY
  -- see docs/LIVE_INTEGRATION_TESTING.md
```

That matters more than it looks: the failure mode to avoid is a live test
that *passes* because the thing it was checking silently degraded to a
no-op.

**Every live test is read-only.** No test here writes to, sends from, or
modifies anything in an external system. A live test that mutated a
tenant's data would be one nobody could safely run.

### A note on environment

[`tests/conftest.py`](../tests/conftest.py) strips every env var a
`Settings` field could read, so a mocked test never inherits a developer's
real `.env`. That is right for the mocked suite and wrong here, so
`tests/live/conftest.py` snapshots the real environment at import and
reads from that snapshot.

---

## 2. What is covered today

| Area | Needs | What it proves |
|---|---|---|
| Web search | `WEB_SEARCH_API_KEY` | Tavily returns well-formed results and honours `WEB_SEARCH_MAX_RESULTS` — a provider ignoring the cap is a cost surprise only a live call reveals |
| Document / policy RAG | `RAG_STORE_CONNECTION_STRING` | the store is reachable **and the server supports the `VECTOR` type** — the one thing that cannot be mocked, and the reason these are "blocked" rather than merely "off" |
| REST connector | `REST_ALLOWED_HOSTS`, `LIVE_REST_TEST_URL` | a declared endpoint is reachable, and the SSRF guard refuses an internal host **against real DNS** |
| MCP client | `LIVE_MCP_COMMAND` (± `LIVE_MCP_ARGS`) | the handshake completes against a real server, and an un-allowlisted tool is still refused |
| Ollama | `OLLAMA_HOST` | the configured model is actually installed |

The MCP handshake test is the highest-value one here. The protocol
revision that client implements is **spec-verified but never exercised
against a real server** — a live run either agrees or raises a clear
version error.

---

## 3. Microsoft 365 — not covered, because it is not built

**There are no SharePoint, OneDrive, Dataverse, Outlook or Teams
connectors in this repository.** A keyword sweep returns zero
implementation files; see
[`feature-inventory.md`](architecture/feature-inventory.md) §4. So there is
nothing to integration-test yet, and this document does not pretend
otherwise.

What this section *is* for: making the work config-shaped rather than
design-shaped when credentials arrive.

### 3.1 Tenant prerequisites (operator, not code)

1. An Entra tenant and an app registration.
2. **Delegated** permissions, not application ones. This is a design
   decision, not a preference — the Graph Search API runs as the signed-in
   user and honours item-level access control, so the provider enforces
   ACLs instead of this platform re-implementing them. App-only access
   bypasses user ACLs entirely and is explicitly unsupported. See
   [`data-source-matrix.md`](architecture/data-source-matrix.md) §5.
3. Admin consent where the scope requires it.
4. **A test site/drive with non-trivial ACLs, and two identities with
   different access to it.** Without this, the only test that really
   matters cannot be written.

### 3.2 Scopes to verify before writing any connector

Every scope below was cited to Microsoft Learn on 2026-10-14 and is marked
`VERIFY AT IMPLEMENTATION` — permission tables change, and several
findings came from pages of mixed age.

| Source | Candidate scopes | Note |
|---|---|---|
| SharePoint / OneDrive | `Files.Read.All`, `Sites.Read.All` (delegated) | `Sites.*` is unsupported for personal Microsoft accounts |
| Dataverse | `{environmentUrl}/user_impersonation` | the scope must target the **specific environment URL**; a generic admin host returns 401 |
| Outlook | `Mail.Read` (delegated only) | app-only grants every mailbox in the tenant |
| Teams | `ChannelMessage.Read.All`, `Chat.Read.All` | ⚠ see below |

### 3.3 Teams is harder than it looks

Not scheduled, and not merely deprioritised:

1. Org-wide message reads are **application-permission only** — delegated
   is not supported — so the provider cannot enforce ACLs for us.
2. It is a **protected API** needing Microsoft validation beyond admin
   consent: a request-and-wait process outside our control.
3. **Licensing is unresolved.** Sources conflict; the current
   `chatMessage` reference shows a `402` when an E5 or Communications
   service plan requirement is unmet.

Revisit only if an owner accepts re-implementing membership checks and
completes the approval process.

### 3.4 The shape each connector's live tests must take

When a connector is built, it gets a class in
[`tests/live/test_live_connectors.py`](../tests/live/test_live_connectors.py)
following the existing pattern:

```python
@pytest.mark.live
class TestLiveSharePoint:
    def test_connection_is_healthy(self): ...
    def test_search_returns_evidence_with_provenance(self): ...
    def test_a_document_the_caller_cannot_see_is_not_returned(self): ...
```

**The third is the one that matters**, and it is the one that cannot be
faked: it needs the two identities from §3.1. Everything else — request
shape, pagination, throttling, token refresh — is verifiable with mocked
Graph responses and belongs in the ordinary suite, not here.

A connector is **not** "production ready" until that third test passes
against a real tenant. Until then its status is "configuration and adapter
seam only", and it should be reported that way.

---

## 4. Setting it up

Put live-test variables in your real `.env` (never in `.env.example`,
which carries placeholders only):

```bash
# Web search
WEB_SEARCH_API_KEY=tvly-...

# Document/policy RAG
RAG_STORE_CONNECTION_STRING=mssql+pyodbc:///?odbc_connect=...

# REST connector
ENABLE_REST_CONNECTOR=true
REST_ALLOWED_HOSTS=api.example.com
LIVE_REST_TEST_URL=https://api.example.com/health

# MCP client
LIVE_MCP_COMMAND=mcp-server-filesystem
LIVE_MCP_ARGS=--root,/tmp/mcp-sandbox
```

Then:

```bash
pytest -m live tests/live/ -rs
```

`-rs` prints a skip reason for everything not configured, which doubles as
a checklist of what is still unverified.

---

## 5. What a passing live run does and does not mean

**Does**: the credentialed path works end to end against the real
provider, today, from this host.

**Does not**: anything about ACL fidelity for a source whose
access-control test is not written yet (§3.4), performance under load, or
behaviour against a provider's future API version.

Report a live-verified capability as verified **for the specific tests
that ran**, and name the ones that skipped. A capability with every live
test skipped is unverified, however green the run looks.
