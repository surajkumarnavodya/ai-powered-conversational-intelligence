import { isSqlResult } from './history'
import type { AskResponse, TruthLevel } from './types'

/**
 * Derives the `Question -> Source -> Tables -> Query -> Result -> Insight`
 * lineage for one answered question, from metadata the server already sent.
 *
 * **Nothing here is inferred, guessed, or computed.** Every field on every
 * returned source is read straight off `AskResponse`; a source appears only
 * when its own response field is actually populated, and a detail line
 * appears only when its own value is present. That is the whole point of
 * putting this in a pure module: "do not invent lineage" is checkable by
 * reading one function, rather than being spread across JSX.
 *
 * Three things are deliberately NOT derived, because the data to do it
 * honestly does not exist in this response:
 *
 * - **The database engine ("SQL Server", "PostgreSQL").** `DB_TYPE` is not
 *   exposed by any API schema -- `/health` reports a connection's `name`
 *   and status only (verified against `api/schemas.py`). The connection
 *   name is shown instead; printing an engine would be a guess.
 * - **Columns referenced by the SQL.** `sql_summary` carries
 *   `referenced_tables` but no columns, and re-parsing the SQL here would
 *   be a second, drifting implementation of something the server already
 *   does with sqlglot. What is shown instead is `result_columns` -- the
 *   columns the answer actually came back with, which is both real and
 *   already governed (see the masking note below).
 * - **A wall-clock timestamp per source.** No per-source timestamp exists
 *   anywhere in `AskResponse`; `answer_duration_ms` (how long the pipeline
 *   took) and `analytical_result.engine_version` are the only real
 *   time/version facts available, and are reported as exactly that.
 *
 * ### What is never exposed
 *
 * Connection strings, credentials, filesystem paths, prompts and
 * chain-of-thought are not in `AskResponse` at all, so there is nothing to
 * filter -- but two kinds of value that *are* present are still withheld
 * on purpose:
 *
 * - **Internal identifiers**: `Citation.document_id`,
 *   `AttachmentResult.used_attachment_ids` and
 *   `MediaGenerationResult.media_id` are opaque server-side handles. A
 *   citation is identified to the reader by filename and page; the ids stay
 *   with the components that actually need them to fetch bytes.
 * - **Full table DDL**: `SchemaTable.ddl` lists every column of a retrieved
 *   table, including ones this answer never touched. The lineage shows
 *   table *names*; the DDL already has a home in `SchemaContextPanel`, and
 *   a second renderer for it here would duplicate that panel.
 *
 * Restricted values never reach this module in the first place: the server
 * masks them before the row leaves the process
 * (`governance/result_policy.py`) and says so via
 * `restricted_field_notice`, which is surfaced as a real restriction note
 * rather than quietly dropped. Likewise `permission_denied_notice`, which
 * is how a source the caller may not use is reported -- such a source never
 * appears in `sources_used`, so the panel cannot list it.
 */

/** How a source's content was obtained. Each value corresponds to a real,
 * distinct retrieval path in this application -- never a generic guess. */
export type RetrievalType =
  /** A validated, read-only SQL statement executed against the database. */
  | 'direct_query'
  /** Top-k similarity search over embedded table DDL (`embeddings/`). */
  | 'vector_similarity'
  /** A published, human-approved definition from the semantic catalog. */
  | 'approved_definition'
  /** Agentic RAG: retrieve, grade for relevance, then answer with citations. */
  | 'graded_retrieval'
  /** A live call to an external web-search provider. */
  | 'live_web_search'
  /** Content-based search over the local media library. */
  | 'media_index'
  /** A file the user attached to this question. */
  | 'user_upload'
  /** Media produced by a generation provider for this question. */
  | 'generated'

/** One inspectable fact about a source. `detail` is secondary text shown
 * beside `label`; both are already-sanitised, display-ready strings. */
export interface LineageDetail {
  label: string
  value: string
}

export interface LineageSource {
  /** Stable key, used for React keys, test selectors and i18n lookup. */
  kind:
    | 'database'
    | 'schema_index'
    | 'business_glossary'
    | 'documents'
    | 'policy'
    | 'web'
    | 'media_library'
    | 'generated_media'
    | 'attachments'
  /** The specific instance this answer used -- a connection name, a
   * document count. Null when the source has no meaningful instance name
   * (the schema index is per-database, not separately named). */
  name: string | null
  retrieval: RetrievalType
  /** What kind of claim this source's contribution is. A query result is a
   * measured fact; an approved metric definition is confirmed business
   * truth; anything a model wrote or retrieved-and-summarised is an
   * inference. */
  truthLevel: TruthLevel
  details: LineageDetail[]
}

/** One step of the `Question -> ... -> Insight` chain. `state` is what the
 * reader needs to know about this step, and is never "unknown": a step
 * either happened (`present`), genuinely did not apply to this answer
 * (`not_applicable`), or applied and produced nothing (`absent`). */
export interface LineageStep {
  key: 'question' | 'source' | 'tables' | 'query' | 'result' | 'insight'
  state: 'present' | 'absent' | 'not_applicable'
  /** Already-sanitised summary of what this step produced, or null when
   * there is nothing to say beyond `state`. */
  summary: string | null
}

export interface Lineage {
  sources: LineageSource[]
  steps: LineageStep[]
  /** Server-reported notices that explain a gap in the lineage. Surfaced
   * verbatim -- they are the honest account of what was withheld and why. */
  restrictionNotices: string[]
  /** Real time/version facts, where the response carries any. */
  metadata: LineageDetail[]
}

/** Caps a display string so a hostile or merely huge value can never blow
 * out the panel. Mirrors `lib/analyticsCharts.ts`'s own `sanitizeLabel`
 * intent; kept local so this module has no dependency on the chart layer.
 * Control characters are stripped because a database value is untrusted
 * content, exactly like a web-search snippet. */
export function sanitizeLineageText(value: string, max = 120): string {
  // eslint-disable-next-line no-control-regex
  const cleaned = value.replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ').replace(/\s+/g, ' ').trim()
  return cleaned.length <= max ? cleaned : `${cleaned.slice(0, max - 1)}…`
}

function joinNames(names: string[], max = 6): string {
  const shown = names.slice(0, max).map((name) => sanitizeLineageText(name, 60))
  const hidden = names.length - shown.length
  return hidden > 0 ? `${shown.join(', ')} +${hidden} more` : shown.join(', ')
}

/** Unique citation filenames, in first-seen order. A page number is kept
 * alongside the filename when the server sent one; `document_id` never
 * leaves this function. */
function citationNames(citations: { filename: string; page_number: number | null }[]): string[] {
  const seen = new Set<string>()
  const names: string[] = []
  for (const citation of citations) {
    const label =
      citation.page_number != null
        ? `${citation.filename} (p. ${citation.page_number})`
        : citation.filename
    if (seen.has(label)) continue
    seen.add(label)
    names.push(label)
  }
  return names
}

function buildSources(state: AskResponse): LineageSource[] {
  const sources: LineageSource[] = []
  const used = state.sources_used
  // An empty `sources_used` is this app's own "multi-source router off"
  // convention, not "unknown" -- see `lib/history.ts::isSqlResult`.
  const sqlPath = isSqlResult(used)

  if (sqlPath && (state.database || state.sql)) {
    const details: LineageDetail[] = []
    if (state.sql_summary?.statement_type) {
      details.push({ label: 'statementType', value: state.sql_summary.statement_type })
    }
    const tables = state.sql_summary?.referenced_tables ?? []
    if (tables.length > 0) {
      details.push({ label: 'tablesUsed', value: joinNames(tables) })
    }
    if (state.result_columns && state.result_columns.length > 0) {
      details.push({ label: 'columnsReturned', value: joinNames(state.result_columns, 10) })
    }
    if (state.row_count != null) {
      details.push({ label: 'rowCount', value: String(state.row_count) })
    }
    sources.push({
      kind: 'database',
      name: state.database ? sanitizeLineageText(state.database, 60) : null,
      retrieval: 'direct_query',
      truthLevel: 'database_fact',
      details,
    })
  }

  if (state.schema_tables.length > 0) {
    sources.push({
      kind: 'schema_index',
      name: null,
      retrieval: 'vector_similarity',
      truthLevel: 'database_fact',
      details: [
        {
          label: 'tablesRetrieved',
          value: joinNames(state.schema_tables.map((table) => table.table_name)),
        },
      ],
    })
  }

  if (state.governing_metrics.length > 0) {
    sources.push({
      kind: 'business_glossary',
      name: null,
      retrieval: 'approved_definition',
      // A published catalog metric is a human-approved definition, which is
      // exactly what `confirmed_business_truth` means in this codebase.
      truthLevel: 'confirmed_business_truth',
      details: state.governing_metrics.map((metric) => ({
        label: 'metric',
        value: sanitizeLineageText(metric.business_name, 80),
      })),
    })
  }

  const ragSources: { kind: 'documents' | 'policy'; answer: AskResponse['document_result'] }[] = [
    { kind: 'documents', answer: state.document_result },
    { kind: 'policy', answer: state.policy_result },
  ]
  for (const { kind, answer } of ragSources) {
    if (!answer) continue
    const names = citationNames(answer.citations)
    sources.push({
      kind,
      // A policy answer whose matches were all restricted carries no
      // citations at all (see rag/graph.py) -- reported as the real count,
      // never as a fabricated document name.
      name: names.length > 0 ? String(names.length) : null,
      retrieval: 'graded_retrieval',
      truthLevel: 'ai_inference',
      details: names.length > 0 ? [{ label: 'citations', value: joinNames(names, 8) }] : [],
    })
  }

  if (state.web_result) {
    const names = citationNames(state.web_result.citations)
    sources.push({
      kind: 'web',
      name: names.length > 0 ? String(names.length) : null,
      retrieval: 'live_web_search',
      truthLevel: 'ai_inference',
      // No `pages` detail. The cited URLs are owned by the answer's own
      // `SourcesSection`, which lists each one once with its real title
      // and a working link. Repeating a truncated, unlinked copy of the
      // same list here is how the same five URLs ended up on screen three
      // times in one turn (CLAUDE.md §4.8: one owner per fact).
      details: [],
    })
  }

  if (state.media_search_result) {
    sources.push({
      kind: 'media_library',
      name: null,
      retrieval: 'media_index',
      truthLevel: 'ai_inference',
      details: [{ label: 'matches', value: String(state.media_search_result.hits.length) }],
    })
  }

  if (state.generation_result) {
    const details: LineageDetail[] = []
    if (state.generation_result.media_type) {
      details.push({ label: 'mediaType', value: state.generation_result.media_type })
    }
    if (state.generation_result.model) {
      details.push({
        label: 'providerModel',
        value: sanitizeLineageText(state.generation_result.model, 60),
      })
    }
    sources.push({
      kind: 'generated_media',
      name: null,
      retrieval: 'generated',
      truthLevel: 'ai_inference',
      details,
    })
  }

  if (state.attachment_result) {
    sources.push({
      kind: 'attachments',
      name: String(state.attachment_result.used_attachment_ids.length),
      retrieval: 'user_upload',
      truthLevel: 'ai_inference',
      // Deliberately the *count*, never `used_attachment_ids` themselves.
      details: [
        { label: 'fileCount', value: String(state.attachment_result.used_attachment_ids.length) },
      ],
    })
  }

  return sources
}

function buildSteps(state: AskResponse, sources: LineageSource[]): LineageStep[] {
  const sqlPath = isSqlResult(state.sources_used)
  const tables = state.sql_summary?.referenced_tables ?? []
  const retrieved = state.schema_tables.map((table) => table.table_name)
  const tableNames = tables.length > 0 ? tables : retrieved

  const hasResult =
    (state.result_columns != null && state.result_columns.length > 0) || state.row_count != null

  return [
    { key: 'question', state: 'present', summary: null },
    {
      key: 'source',
      state: sources.length > 0 ? 'present' : 'absent',
      summary: sources.length > 0 ? String(sources.length) : null,
    },
    {
      key: 'tables',
      // A web/document-only answer touches no tables at all -- that is a
      // real "not applicable", not a missing step.
      state: !sqlPath ? 'not_applicable' : tableNames.length > 0 ? 'present' : 'absent',
      summary: tableNames.length > 0 ? joinNames(tableNames) : null,
    },
    {
      key: 'query',
      state: !sqlPath ? 'not_applicable' : state.sql ? 'present' : 'absent',
      // The SQL text itself is shown by the existing SQL panel/editor; the
      // chain reports only that a query exists, and its shape.
      summary: state.sql_summary?.statement_type ?? null,
    },
    {
      key: 'result',
      state: !sqlPath ? 'not_applicable' : hasResult ? 'present' : 'absent',
      summary: state.row_count != null ? String(state.row_count) : null,
    },
    {
      key: 'insight',
      state: state.insight ? 'present' : 'absent',
      summary: null,
    },
  ]
}

export function buildLineage(state: AskResponse): Lineage {
  const sources = buildSources(state)

  const restrictionNotices: string[] = []
  // Both notices are server-authored sentences explaining a real gap. They
  // are the mechanism by which "a source/field you may not see" is
  // disclosed rather than silently missing.
  if (state.restricted_field_notice) {
    restrictionNotices.push(sanitizeLineageText(state.restricted_field_notice, 300))
  }
  if (state.permission_denied_notice) {
    restrictionNotices.push(sanitizeLineageText(state.permission_denied_notice, 300))
  }

  const metadata: LineageDetail[] = []
  if (state.model) {
    metadata.push({ label: 'model', value: sanitizeLineageText(state.model, 60) })
  }
  if (state.answer_duration_ms != null) {
    metadata.push({ label: 'duration', value: `${Math.round(state.answer_duration_ms)} ms` })
  }
  if (state.analytical_result?.engine_version) {
    metadata.push({
      label: 'analyticsEngineVersion',
      value: sanitizeLineageText(state.analytical_result.engine_version, 40),
    })
  }

  return { sources, steps: buildSteps(state, sources), restrictionNotices, metadata }
}
