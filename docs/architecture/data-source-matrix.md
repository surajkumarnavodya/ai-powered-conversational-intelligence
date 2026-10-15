# Data source matrix — capabilities, auth, and verification status

**Scope.** One row per *source type*, covering access pattern, auth model,
ACL fidelity, side effects, implementation status and test strategy. This
document owns the **capability and auth comparison across source types**,
including types that are not implemented.

It does **not** own per-source operator setup — for turning on and
configuring a source that already exists, read
[`../MULTI_SOURCE_GUIDE.md`](../MULTI_SOURCE_GUIDE.md), which is
authoritative for that. Status claims come from
[`feature-inventory.md`](feature-inventory.md).

**Date:** 2026-10-14. **Commit:** `b4d74d2`.

---

## 1. Rules this matrix follows

1. **No invented scopes.** Every permission below is either (a) cited to a
   Microsoft Learn page consulted on 2026-10-14, or (b) marked
   `VERIFY AT IMPLEMENTATION`. Master instruction #4 forbids inventing
   provider permissions. Scope lists change; a name verified today is still
   re-verified when the connector is built.
2. **"Implemented" means code + tests exist.** A configuration seam is
   *not* an implementation. See the Status column's vocabulary in
   [`feature-inventory.md`](feature-inventory.md) §1.
3. **Side effects are a separate capability.** Reading a mailbox and sending
   mail are different rows, and every write row is off by default.
4. **Source ACL fidelity is stated honestly**, including where it cannot be
   fully preserved.

---

## 2. Implemented sources

| | Text/paste | File upload | SQL database | Web search |
|---|---|---|---|---|
| **Status** | Partial | Flag-gated | **Live** | Flag-gated |
| **Evidence** | question text only; no distinct source type | `attachments/` (21 modules) | `db/`, `agent/graph.py` | `search/web_search.py` |
| **Access pattern** | inline in request | multipart upload → parse → index | SQLAlchemy, read-only role | HTTPS provider API (Tavily) |
| **Auth** | caller's session | caller's session | `DB_USER` (read-only, startup-enforced) | `WEB_SEARCH_API_KEY` |
| **User-delegated vs app-only** | delegated | delegated | **app-only** (shared service account) | app-only |
| **Data model** | raw text | file → chunks + metadata | tables/views/columns via live introspection | URL + title + snippet + freshness |
| **Search / read** | read | read + retrieve | introspect + query | search |
| **Incremental sync** | n/a | n/a (per-request) | n/a (live introspection) | n/a |
| **ACL preservation** | n/a | per-user attachment ownership | **none at row level** — the DB role is shared; isolation is by `governance/` + tenant `WHERE` folding, not by source ACL | n/a (public web) |
| **Rate limits** | `/ask` admission control | size + parser timeouts | pool cap + query timeout + row cap (enforced twice) | provider quota |
| **Writes / side effects** | none | none | **none** — SELECT-only allowlist | none |
| **Flag** | — | `ENABLE_CHAT_ATTACHMENTS` | always on | `ENABLE_WEB_SEARCH` (default `false`) |
| **Test strategy** | — | `tests/` + `tests/security/` | fully mocked suite | `tests/test_web_search.py` — 14 mocked tests |
| **Blocked?** | no | no | no | no |

| | Document RAG | Policy RAG | Media search | Media generation |
|---|---|---|---|---|
| **Status** | Flag-gated, **Blocked** | Flag-gated, **Blocked** | Flag-gated | Flag-gated |
| **Evidence** | `rag/` | `rag/` + sensitivity gate | `media/` | `media_gen/` |
| **Access pattern** | SQL Server 2025+/Azure SQL native `VECTOR` | same store, policy collection | local CLIP embeddings | provider API |
| **Auth** | `RAG_STORE_CONNECTION_STRING` | same | local filesystem | `IMA_API_KEY` |
| **ACL preservation** | `DocumentRecord.restricted_roles` (independent RBAC gate) | `restricted_roles` **plus** `sensitivity_category` — a categorised chunk is never summarised, fail-closed, regardless of role | none | n/a |
| **Writes** | ingestion only | ingestion only | none | generates media, served under opaque id |
| **Flag** | `ENABLE_DOCUMENT_RAG` (`false`) | `ENABLE_POLICY_RAG` (`false`) | `ENABLE_MEDIA_SEARCH` (`false`) | `ENABLE_MEDIA_GENERATION` (`false`) |
| **Blocked?** | **yes** — needs a SQL Server 2025+/Azure SQL vector store, absent here | **yes** — same | no (needs Tesseract for OCR; fails open) | no (confirmed against a real account per `media_gen/client.py`) |

---

## 3. Absent sources — Microsoft 365

**None of these exist in the codebase.** A keyword sweep returned 0 files
for `sharepoint`, `onedrive`, `dataverse`, `microsoft.graph`, `graph_api`,
`outlook`, `msal`, `teams`. Everything below is *planning input*, not a
description of behaviour.

All four rows share one hard blocker: **no Entra tenant, app registration
or admin consent exists in this environment**, so none can be verified
end to end. Master instruction #13 applies — build the configuration and
adapter seam, then mark live verification blocked.

### 3.1 SharePoint and OneDrive

| Aspect | Finding |
|---|---|
| **API** | Microsoft Graph. Two usable shapes: the **Microsoft Search API** (`/search/query`, entity types `driveItem`/`listItem`) for cross-tenant content search, and **per-drive search** (`/me/drive/root/search`) for a known drive |
| **Candidate scopes — SharePoint/OneDrive content via Search API** | `driveItem` requires one of `Files.Read.All`, `Files.ReadWrite.All`, `Sites.Read.All`, `Sites.ReadWrite.All`; `listItem` requires `Sites.Read.All` or `Sites.ReadWrite.All`. Cited to Learn, 2026-10-14 — `VERIFY AT IMPLEMENTATION` |
| **Candidate scopes — per-drive search** | least-privileged delegated for work/school: `Files.Read`; higher-privileged: `Files.Read.All`, `Sites.Read.All`. Application least-privileged: `Files.Read.All`. **Does not support `Sites.Selected`** |
| **Delegated vs app-only** | **Prefer delegated.** The Search API runs *as the signed-in user* and results respect item-level access control — this is the single most valuable property for this platform, because it makes source ACL fidelity the provider's job rather than ours |
| **Admin consent** | Delegated `Files.Read.All` / `Sites.Read.All`: no admin consent. **Application** `Files.Read.All` / `Sites.Read.All`: admin consent required |
| **Personal accounts** | `Sites.*` is **not supported** for personal Microsoft accounts — work/school tenants only |
| **ACL fidelity** | **High, if delegated.** App-only access bypasses user ACLs entirely and would require us to re-implement authorization — a far worse posture. Recommend delegated-only for v1 and document app-only as unsupported |
| **Incremental sync** | Graph `delta` exists for drives. Treat as a later batch; v1 should be search-on-demand, which sidesteps sync, cursors, deletion propagation and re-index entirely |
| **Side effects** | none required (read-only) |
| **Test strategy** | mocked Graph HTTP responses + a conformance suite; **no live test possible here** |

### 3.2 Dataverse

| Aspect | Finding |
|---|---|
| **API** | Dataverse Web API, `{environment}/api/data/v9.2/`, OData v4, Bearer token |
| **Scope format** | Public client: `{environmentUrl}/user_impersonation`. Confidential client: `{environmentUrl}/.default`. Cited to Learn, 2026-10-14 — `VERIFY AT IMPLEMENTATION` |
| **Critical detail** | the scope must target **the specific environment URL**, not a generic admin host — a generic host returns 401 |
| **App registration** | Dataverse appears under "APIs my organization uses"; `user_impersonation` is the delegated permission; admin consent then granted. Dataverse's own application ID is `00000007-0000-0000-c000-000000000000` |
| **ACL fidelity** | **Good, if delegated** — Dataverse enforces its own row/column/record security for the impersonated user. This is *not* SharePoint's model and must not be assumed equivalent |
| **Data model** | environments → tables/entities → attributes, with its own metadata service. Needs its own discovery implementation; cannot reuse the SharePoint one |
| **Side effects** | writes exist and are **out of scope** — separate capability, off by default, approval-gated |
| **Blocked?** | **yes** — no environment available |

### 3.3 Outlook / email

| Aspect | Finding |
|---|---|
| **API** | Microsoft Graph messages |
| **Candidate scope (read)** | `Mail.Read` — listed as the least-privileged option for message resources; available delegated and application. Cited to Learn, 2026-10-14 — `VERIFY AT IMPLEMENTATION` |
| **Delegated vs app-only** | **delegated only.** App-only `Mail.Read` grants every mailbox in the tenant; master instruction rule and Prompt 3 §6 both forbid broad mailbox access without explicit admin approval |
| **ACL fidelity** | high when delegated (the user's own mailbox) |
| **Untrusted content** | **maximum caution.** Body, quoted chains, links and attachments are all attacker-controllable and must be framed as data, per [`CLAUDE.md`](../../CLAUDE.md) §5.5 |
| **Retention** | minimise; redact in logs |
| **Side effects** | send / reply / forward / delete / move are **separate capabilities, off by default**, each requiring confirmation + audit |
| **Blocked?** | **yes** |

### 3.4 Microsoft Teams — harder than the pack assumes

| Aspect | Finding |
|---|---|
| **API** | Graph `chatMessage`; org-wide reads via `channel: getAllMessages` |
| **Candidate scopes** | `ChannelMessage.Read.All` (admin consent required for **both** delegated and application); `Chat.Read.All` (application, admin consent required). Cited to Learn, 2026-10-14 — `VERIFY AT IMPLEMENTATION` |
| **Delegated support** | the org-wide "all messages" endpoints are listed **application-permission only; delegated not supported** |
| **⚠ Protected API gate** | Teams APIs reaching sensitive data are **protected APIs**: they need an **additional Microsoft validation/approval process beyond permissions and admin consent**. This is a request-and-wait process, not a config step |
| **⚠ Licensing** | **unresolved.** Sources conflict: Teams export preview docs gate on licensing; the current `chatMessage` update reference shows a `402` when an E5 or Microsoft Communications service plan requirement is unmet. No authoritative single table was found |
| **ACL fidelity** | **poor.** App-only org-wide read bypasses membership, so we would have to re-implement channel/chat membership checks ourselves before returning content — exactly the "don't rely on our own re-implementation of a source's ACL" risk |
| **Recommendation** | **lowest priority of the four, and do not commit to it.** It is blocked on a Microsoft approval process *and* an unresolved license question *and* has the weakest ACL story. Document these as constraints rather than scheduling it |
| **Blocked?** | **yes, doubly** — credentials *and* protected-API approval |

---

## 4. Absent sources — other

| | REST / OpenAPI | MCP |
|---|---|---|
| **Status** | **Flag-gated** (2026-10-15) — `rest/`, off by default, not routable yet | **Partial** — `agent/tools/` is MCP-*shaped*, not an MCP client |
| **Existing seam** | `agent/tools/types.py::Tool` already carries name, description, schema, permission, `timeout_seconds`, `RetryPolicy`, audit | same |
| **What's missing** | OpenAPI spec parsing, per-endpoint auth headers, and a `sources/descriptors.py` entry (so nothing can route to it yet). Host allowlist, redirect/payload validation and read-vs-side-effect classification are **done** — `security/outbound_url.py` | MCP transport, remote server client, trust process |
| **Security requirements** | SSRF validation re-run **on every use** (not once at registration), destination allowlist, payload cap | tool descriptions and results are **untrusted data**; an LLM must never register a remote tool — administrator-controlled trust only |
| **Side effects** | must be declared and approval-gated | same |
| **Blocked?** | no — built and tested here | no |

---

## 5. What this matrix changes about the proposed plan

1. **Delegated auth is not a preference, it is the design.** For
   SharePoint/OneDrive/Dataverse/Outlook, delegated access makes the
   provider enforce ACLs. App-only would force us to re-implement source
   authorization — more code, worse security, and contrary to
   [`CLAUDE.md`](../../CLAUDE.md) §5.2's "a UI-level restriction is never a
   boundary" spirit. **Recommend: delegated-only for v1; document app-only
   as explicitly unsupported.**
2. **Search-on-demand before sync.** Choosing the Search API for v1 removes
   sync jobs, cursors, deduplication, deletion propagation and re-indexing
   from the first connector batch — a very large scope reduction for a
   small capability loss.
3. **Teams should be dropped from the committed scope**, not merely
   deprioritised (§3.4).
4. **`search/` is the pattern to copy, and it is already tested.**
   Every new source in §3–4 is an outbound HTTP integration, and
   `search/web_search.py` + `tests/test_web_search.py` are the existing
   worked example of that shape — mocked transport, typed result mapping,
   secret non-leakage, provider dispatch. New connector tests should mirror
   it rather than invent a convention.
   (An earlier draft of this audit recorded `search/` as untested on
   [`CLAUDE.md`](../../CLAUDE.md) §13's authority. That entry is stale — see
   [`feature-inventory.md`](feature-inventory.md) §7 R3.)

---

## 6. Sources consulted (2026-10-14)

- [Microsoft Graph permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Use the Microsoft Search API to query data](https://learn.microsoft.com/en-us/graph/api/resources/search-api-overview?view=graph-rest-1.0)
- [Search for files (driveItem: search)](https://learn.microsoft.com/en-us/graph/api/driveitem-search?view=graph-rest-1.0)
- [Understanding OneDrive API permission scopes](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/permissions_reference?view=odsp-graph-online)
- [List sites](https://learn.microsoft.com/en-us/graph/api/site-list?view=graph-rest-1.0)
- [Use OAuth authentication with Microsoft Dataverse](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/authenticate-oauth)
- [Tutorial: Register an app with Microsoft Entra ID (Dataverse)](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/walkthrough-register-app-azure-active-directory)
- [channel: getAllMessages](https://learn.microsoft.com/en-us/graph/api/channel-getallmessages?view=graph-rest-1.0)
- [Update chatMessage](https://learn.microsoft.com/en-us/graph/api/chatmessage-update?view=graph-rest-1.0)
- [Create subscription](https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions?view=graph-rest-1.0)

**Every scope above is marked `VERIFY AT IMPLEMENTATION`.** Permission
tables change, and several findings came from pages of mixed age plus
community threads. Re-verify against the live tables before writing any
connector.
