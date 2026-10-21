# Multi-Source Guide

How to turn on and use each optional source beyond the SQL database(s):
document RAG, policy RAG (with sensitivity gating), live web search, media
generation (image/video creation), and media search (finding existing
images/video in a local library). Everything in this document is **off by
default** — a fresh clone with an unmodified `.env` behaves identically to
the SQL-only app. See
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md#4-multi-source-orchestration) for
how this works internally, and [`CLAUDE.md`](../CLAUDE.md) for the design
decisions behind it.

## 1. Turn on the router

```env
ENABLE_MULTI_SOURCE_ROUTER=true
```

With this off, `api/main.py` calls `agent.graph.run_agent`
directly — nothing below matters. With it on and nothing else configured,
the router still only ever sees `sql` as available and short-circuits to
it with zero added latency or LLM calls — turning this on by itself
changes nothing observable until you configure at least one more source
below.

**`Settings` is a cached, process-wide singleton — restart the app after
any `.env` change.** This is the single most common cause of "I set the
key but it's still not using it": the running process is still holding the
`.env` snapshot from when it started. If a question that should clearly
route to a source you just configured instead gets rejected or answered
from the wrong source, check the terminal log for the `[router] available=...`
line before assuming something's broken — if your new source isn't in
`available`, restart the app.

## 2. Document RAG (general PDF uploads)

```env
ENABLE_DOCUMENT_RAG=true
RAG_STORE_CONNECTION_STRING=<see step 4>
```

Once both are set and the app is restarted, go to the **Knowledge Sources**
page (`frontend/src/pages/KnowledgeSources.tsx`, linked from the header
nav), the **📄 Documents** tab, and upload a PDF. You'll see a
progress bar, then a success/failure summary per file (chunk count, or the
error and any "may need OCR" warnings for near-empty pages). The document
appears in the management table below, status `ready` once indexed —
that's when it becomes queryable, no restart needed for a new upload.

Ask a question in the main chat page. If `documents` is the only
non-`sql` source configured, the router only needs to distinguish "is this
about the database or about the documents," which it does reliably; once
you also turn on `policy`/`web` (below), a question that's ambiguous
between sources may need to be phrased a bit more specifically.

## 3. Policy RAG (sensitive HR/company policy PDFs)

```env
ENABLE_POLICY_RAG=true
RAG_STORE_CONNECTION_STRING=<see step 4>
```

Same upload flow, on the **🔒 Policies** tab — but with one more field: a
**sensitivity category** selector (`None`, `Compensation & pay`,
`Disciplinary / HR case content`, `Legal / litigation`). Pick a category
if the document's content is genuinely restricted; leave it `None` for
ordinary policy content (leave policy, dress code, general HR process
docs, ...) that doesn't need gating.

**What the category actually does:** this application has no per-user
login or authorization system (see [`SECURITY.md`](../SECURITY.md)) — it
cannot check "is the person asking allowed to see this." So rather than
guess, a chunk from a categorized document is **never summarized into an
answer, ever** — the agent detects the category before it even calls the
LLM to generate a response, and instead returns a fixed message pointing
the user to HR/the policy owner directly. This is a real, tested gate, not
a prompt instruction the model could ignore. If you need real per-user
access control over policy content, that's a bigger feature (real
authentication + authorization) this project doesn't have — treat the
category selector as "block everyone equally," not "block the wrong
people."

Only categorize what's genuinely sensitive. Over-categorizing (e.g.
marking your whole leave policy "confidential" out of caution) just makes
the agent unable to answer normal questions about it.

## 4. The RAG store database

Both document and policy RAG share one SQL Server database (two
collections, one connection) — **SQL Server 2025+ or Azure SQL**, because
storage uses the native `VECTOR` column type
([`rag/store.py`](../rag/store.py)). Check your version before assuming
this works:

```sql
SELECT @@VERSION;
```

If you're not on 2025+/Azure SQL, this feature isn't usable as built —
that's a real constraint of the current implementation, not a config
option to work around.

**Use a dedicated database**, not one of your configured `DB_CONNECTIONS`
business databases — chunk/embedding storage isn't business data and
shouldn't share a schema with `AdventureWorksDW2025`/your HR database/etc.
The schema (`rag.documents`, `rag.chunks`) is created automatically on
first use (`rag.store.ensure_schema`) — you don't need to run any DDL by
hand, just point the connection string at an existing (possibly empty)
database:

```sql
-- One-time, if the database doesn't exist yet:
CREATE DATABASE RagKnowledgeStore;
```

Same LocalDB-style connection-string pattern as a `DB_<NAME>_CONNECTION_STRING`
override, if you're on a named instance (no plain `host:port`):

```env
RAG_STORE_CONNECTION_STRING=mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+17+for+SQL+Server%7D%3BSERVER%3D%28localdb%29%5CMSSQLLocalDB%3BDATABASE%3DRagKnowledgeStore%3BUID%3D<user>%3BPWD%3D<password>%3BTrustServerCertificate%3Dyes%3B
```

For a standard host:port SQL Server, a plain SQLAlchemy connection string
works too:

```env
RAG_STORE_CONNECTION_STRING=mssql+pyodbc://<user>:<password>@<host>:1433/RagKnowledgeStore?driver=ODBC+Driver+17+for+SQL+Server
```

Tuning knobs (all optional, sensible defaults):

| Variable | Default | Purpose |
|---|---|---|
| `RAG_TOP_K` | `4` | Chunks retrieved per question, per collection. |
| `RAG_MAX_RETRIES` | `2` | Query-rewrite retries before "insufficient information." |
| `RAG_CHUNK_SIZE` | `1200` | Target chunk length in characters. |
| `RAG_CHUNK_OVERLAP` | `150` | Overlap between consecutive chunks. |
| `RAG_EMBEDDING_MODEL_NAME` | *(blank = reuse `EMBEDDING_MODEL_NAME`)* | Only set if documents genuinely need a different embedding model than schema retrieval. |

## 5. Live web search (Tavily)

```env
ENABLE_WEB_SEARCH=true
WEB_SEARCH_PROVIDER=tavily
WEB_SEARCH_API_KEY=tvly-...
```

**Where to get a key:** [tavily.com](https://tavily.com) — sign up, a free
tier is available, copy the API key from the dashboard into
`WEB_SEARCH_API_KEY` above. Both `ENABLE_WEB_SEARCH=true` **and** a real
key are required before the router will ever offer `web` as a destination
— leaving the key blank with the flag on is safe, web search just stays
unavailable (same pattern as document/policy RAG with no store connection
configured).

Ask something clearly outside your database/documents — current events,
general knowledge, anything external. The router should pick `web` on its
own; the answer always opens with "According to a live web search:" and
lists the source URLs it drew from, so it's never confused with your own
data. Swapping providers later (Bing, SerpAPI, ...) is a
`search/web_search.py::SUPPORTED_SEARCH_PROVIDERS` addition, not something
`.env` alone can do yet — only `tavily` is implemented today.

### What the answer looks like

`web_search_node` asks the model for the shape a modern AI search
assistant produces, not a snippet dump: a direct 1–3 sentence answer
first, then depth under short `##` headings with lists where the content
is enumerable and a table only where several items share the same few
attributes. It is told to merge what several results say about one point
into a single statement rather than giving each result its own paragraph,
to keep figures, dates, names and versions rather than rounding them, to
cite the supporting result inline as a markdown link under that source's
own title, and to say plainly when results disagree, when a claim rests
on a single result, or when they do not cover part of the question.

The answer carries **no trailing "Sources" section** by design — the
dashboard's `SourceAnswerCard` already renders the source list from the
structured `citations`, and this repo keeps one owner per fact. The inline
links carry what that list cannot: which source supports which claim.

This is an instruction to a local model, not a guarantee. A small model
(`llama3.1:8b`) follows the shape loosely; a larger `OLLAMA_MODEL` follows
it more closely. The prompt is in `agent/orchestrator/nodes.py` and is
**not** benchmark-pinned, unlike the SQL generation and insight prompts.

**Inline citations are the least reliable part.** Across live runs here on
2026-10-16, `llama3.1:8b` produced the lead answer, headings and lists
every time, but inline links only sometimes -- and when it did, often as
`[1](url)` rather than the source title the prompt asks for. Numbering the
results in the prompt block is not the cause: an A/B of `[n] [Title](url)`
against `[Title](url)` produced no inline links in either arm for the same
question. The structured source list the dashboard renders comes from
`citations`, not from the answer text, so it is complete either way.

### How much of each result reaches the model

Usually all of it. Tavily is called at `advanced` depth; measured here on
2026-10-16, five ordinary web pages came back at 1,547–2,363 characters
each (10,979 total) and all five were passed whole. Long-form sources are
the exception: five academic pages for one medical question exceeded
18,000 characters between them, and the top two were used — with the
answer saying so.

What bounds this is `WEB_SEARCH_CONTEXT_MAX_CHARS` (default 18,000), and
it exists for a specific reason: **removing a limit is not the same as
removing truncation.** Ollama does not reject an oversized prompt — it
truncates from the start, where the system prompt lives, and reports
nothing. Measured here on 2026-10-16 with `llama3.1:8b`: served without
`num_ctx`, a long prompt came back evaluated at 2,050 tokens with the
system prompt gone; at `OLLAMA_NUM_CTX=16384` the same prompt was
evaluated at 12,043 tokens with it intact. Until this change
`rag/llm.py` — the helper behind this node — set no `num_ctx` at all, so
web answers were drafted against a 4,096-token served window while the
SQL path ran at 16,384.

So the budget is set below the usable window, and when it binds it:

- drops **whole results**, lowest-ranked first (half a source reads as a
  complete one, and the model would cite it for a claim its missing half
  contradicts);
- never drops the top-ranked result, even if it alone exceeds the budget;
- **says so in the answer** ("_Note: this answer is based on the N most
  relevant of M search results…_"), while the operator-facing log line
  names the settings to change;
- **does not cite** a dropped result — the citation list is what the model
  actually saw.

Set `WEB_SEARCH_CONTEXT_MAX_CHARS=0` to disable the budget and send every
result. If you raise `WEB_SEARCH_MAX_RESULTS`, raise `OLLAMA_NUM_CTX` and
this together — raising this alone just moves truncation back inside
Ollama, where it is silent. A larger `OLLAMA_NUM_CTX` costs VRAM
(roughly 1 GB of KV cache per doubling per concurrent slot on an 8B
model).

**Limitation.** Over-budget handling is rank-based selection only. There
is no map-reduce pass that summarizes each dropped result separately and
merges the summaries, and no per-result chunking — both would cost extra
LLM calls per question, and neither is implemented. So for a question
whose answer is spread across five long-form sources, the answer rests on
the top-ranked ones and says so, rather than covering all five.

## 6. Media generation (image/video creation)

```env
ENABLE_MULTI_SOURCE_ROUTER=true
ENABLE_MEDIA_GENERATION=true
IMA_API_KEY=<your IMA Studio key>
```

Both the flag and a real key are required before the router will ever
offer `generation` as a destination. Ask for something to be *created* —
"generate an image of monthly spend by category," "create a short video
of a factory production line" — and the router distinguishes this from an
ordinary "show me the data" question, even one that mentions a picture in
passing (see `agent/orchestrator/nodes.py`'s
`_GENERATION_FEW_SHOT_GUIDANCE` for exactly how that line is drawn).

**This is the one source that spends real, metered money**, so it never
fires automatically: `generation_node` only proposes what would be
generated (`status="pending_approval"`, nothing charged) until a human
clicks the dashboard's "▶ Generate image"/"▶ Generate video" button or
calls `POST /generate/confirm` directly (`Settings.require_generation_approval`,
default `true` — see [`SECURITY.md`](../SECURITY.md)'s "Media generation"
section before turning this off). Image generation is confirmed working
end-to-end against a live IMA account; video generation shares the same
code path but hasn't been separately confirmed with a live call yet, and
any provider clip-length limit (typically 5–15 seconds) applies regardless
of `MEDIA_GEN_VIDEO_DURATION_SECONDS`.

Generated media is served through this app's own `GET /media/{media_id}`
route — never the provider's raw URL — from a bounded, in-memory,
process-lifetime cache (a restart loses in-flight generated media, an
accepted tradeoff, not a bug).

## 7. Media search (finding existing images/video)

```env
ENABLE_MULTI_SOURCE_ROUTER=true
ENABLE_MEDIA_SEARCH=true
MEDIA_LIBRARY_PATH=/path/to/your/media
```

Both the flag and a real, existing folder are required before the router
offers `media_search` as a destination. Unlike every other source here,
the default embedding path runs entirely on-device (local CLIP via
`sentence-transformers` — no API key, no per-item cost, nothing about your
media ever sent anywhere) — see `CLAUDE.md`'s "Media search" section for
why that was chosen over a hosted multimodal embedding API.

**Build the index before asking anything** — nothing in the folder is
searchable until you run:

```bash
python scripts/build_media_index.py           # skip files already indexed
python scripts/build_media_index.py --force    # re-index every file
```

Images are embedded directly. Each video is split into scene-detected
segments (`PySceneDetect`, not fixed intervals), and each segment is
transcribed (reusing voice mode's own Whisper model — no second model
load), OCR'd (needs the system Tesseract binary — see `CLAUDE.md`'s
Windows-specific notes; a missing binary just means reduced accuracy, not
a hard failure), and — if `MEDIA_VISION_MODEL` names a pulled Ollama
vision model (e.g. `llava`) — captioned. A blank `MEDIA_VISION_MODEL` (the
default) skips captioning; the segment is still searchable via its
transcript/OCR text alone.

Ask something like *"find the photo of the site inspection"* or *"do we
have a video of the ribbon-cutting ceremony"* in the main chat, or use the
standalone **"🖼️ Media Search"** page (`frontend/src/pages/MediaSearch.tsx`,
also reachable directly via `POST /search/media` — see
[`docs/API.md`](API.md)) to search outside the conversational flow. A
video hit returns a representative keyframe and a timestamp range, never a
streamed clip.

**"generation" vs. "media_search":** if both are configured, the router
also gets extra guidance disambiguating "make a picture of X" (generation)
from "find a picture of X" (media_search) — see
`agent/orchestrator/nodes.py`'s `_MEDIA_SEARCH_VS_GENERATION_GUIDANCE`.
When in doubt, favor an explicit "find"/"do we have" verb for search and
"create"/"generate"/"make" for generation.

**No sensitivity classification for library content.** Unlike policy RAG
(step 3), there is no per-item tagging mechanism for the media library —
treat anything placed in the configured folder as visible to anyone who
can use this app.

## 8. Multi-source questions

With 2+ sources configured, one extra LLM call classifies which source(s)
a question needs — visible in the terminal as
`[router] available=[...] sources=[...] reasoning=...`. A question that
genuinely needs two sources ("compare our leave policy with what's in the
database") fans out to both in parallel and the answer shows each source's
contribution under its own labeled heading, never blended together.

**Known limitation:** each routed source gets the *same, full, un-split*
question text. A single-topic multi-source question retrieves fine on
every side; a question that's really two separate, unrelated asks mashed
into one sentence can retrieve poorly on both sides even though each half
would work fine asked separately. If a multi-source answer looks wrong or
incomplete, try asking the two parts as separate questions first before
assuming something's broken.

## 9. Troubleshooting

- **"I set `WEB_SEARCH_API_KEY`/`RAG_STORE_CONNECTION_STRING` and it's
  still not offered as a source."** Restart the app — see step 1's note on
  `Settings` caching. Then check the `[router] available=[...]` log line
  to confirm the source is actually in the list before digging further.
- **A question about a topic you *know* a source covers gets answered by
  the wrong source, or rejected.** Check the same `[router]` log line —
  `reasoning=` shows what the classifier saw and picked. If the response
  was unparseable, the router falls back to *every* available source
  (logged as `classification unparseable (...)`), not to dropping the
  question.
- **Upload succeeds but the document never seems to get used.** Confirm
  its status is `ready` (not `processing`/`failed`) on the Knowledge
  Sources page, and that you asked a question the document actually
  answers — check the "Sources: ..." citation line under a document/
  policy/web answer to see which file(s) it actually drew from.
- **A document is clearly in the store, but a question about it finds
  nothing.** Check which *collection* it was uploaded into. `documents`
  and `policies` are separate collections searched by separate sources,
  and the router picks the source from the question's wording — so a
  policy PDF uploaded as a plain document is invisible to a question
  phrased as a policy question, and vice versa. The `[router]` log line
  shows which source ran, and `[rag.retrieve] collection=... retrieved=0`
  shows which collection was searched and came back empty. Either
  re-upload it into the matching collection, or narrow the question to
  the source that holds it with the composer's source picker.
- **A policy question always gets refused.** Check whether the retrieved
  chunk(s) are tagged with a sensitivity category on the Knowledge Sources
  page — that's a hard gate (see step 3), not a bug. If the content
  shouldn't be restricted, re-upload it with `None` selected (delete the
  old version first via the 🗑️ button).
- **Native `VECTOR` errors, or `RagStoreNotConfiguredError`.** See step 4
  — confirm your SQL Server version supports `VECTOR`/`VECTOR_DISTANCE`
  (`SELECT @@VERSION`), and that `RAG_STORE_CONNECTION_STRING` points at a
  reachable database (`scripts/test_db_connection.py` doesn't check this
  connection — it's not one of `DB_CONNECTIONS` — so verify it manually or
  just try an upload and read the error shown).
