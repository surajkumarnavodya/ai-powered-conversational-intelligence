import { buildConversationHistory, MAX_FOLLOWUP_EXCHANGES, type QueryHistoryEntry } from './history'
import { collapseQuestionWhitespace } from './questionLabel'
import type { TruthLevel } from './types'

/** Where one piece of active context came from, which is a different
 * question from how sure we are of it (`TruthLevel`) -- the same
 * orthogonality `EvidenceBadge`'s own docstring draws against
 * `TruthLevel`, and `briefing/models.py`'s `ClaimClass` draws server-side.
 *
 * - `user_provided` -- the person typed or attached it.
 * - `confirmed` -- the server recorded that it actually used this: the
 *   database it routed to, the tables it retrieved, a published metric
 *   definition a reviewer approved.
 * - `inferred` -- this platform decided it. The follow-up classification
 *   (a regex heuristic), the analytical intent, and the plan's filters
 *   and time window (LLM-proposed, then deterministically re-verified by
 *   `agent.plan_validator`, which makes them checked -- not confirmed).
 */
export type ContextProvenance = 'user_provided' | 'confirmed' | 'inferred'

/** Whether an item is literally re-sent with the next question, or
 * re-derived by the server from that question's own text.
 *
 * This distinction is load-bearing, and is why this module exists rather
 * than having the panel read fields off the last turn directly. Only
 * `question`/`sql`/`tables`/`status` travel in `conversation_history`
 * (see `agent.state.ConversationExchange`); the database is re-routed per
 * question by `embeddings.retriever.select_database`, and governing
 * metrics, intent and the analytical plan are rebuilt per question. An
 * item marked `rederived` describes the scope the conversation is
 * *currently operating in* -- it is not a value being carried forward,
 * and the next question can land somewhere else entirely.
 *
 * `influencing` is the narrower truth about what is carried: per
 * `agent.input_guard.sanitize_conversation_history`'s own docstring, only
 * the single most recent entry is ever functionally read downstream
 * (`classify_followup_node` checks only whether the list is non-empty;
 * `retrieve_schema_node`/`generate_sql` read `conversation_history[-1]`).
 * Showing every carried turn as equally active would overstate it.
 */
export type ContextLifetime = 'influencing' | 'carried' | 'rederived'

export type ContextItemKind =
  | 'question'
  | 'referencedAnswer'
  | 'database'
  | 'tables'
  | 'definition'
  | 'filter'
  | 'timeRange'
  | 'scope'
  | 'attachment'

export interface ContextItem {
  /** Stable across renders. For a removable item this is the owning
   * turn's `entryId`, which is what the store excludes by -- never an
   * array index, so removing one item cannot shift another's identity. */
  id: string
  kind: ContextItemKind
  /** Shown verbatim. Already whitespace-collapsed and length-capped. */
  value: string
  provenance: ContextProvenance
  lifetime: ContextLifetime
  /** Only where the server actually recorded one. `null` means no truth
   * level was reported, and renders as nothing rather than a guess. */
  truthLevel: TruthLevel | null
  /** True only for a carried turn -- the one class of context the client
   * genuinely controls, because it is the only part the client sends.
   * See `buildContextSnapshot`'s docstring. */
  removable: boolean
}

export interface ContextSnapshot {
  items: ContextItem[]
  /** Turns currently carried into the next question. */
  carriedCount: number
  /** Turns the person has explicitly removed from the window. */
  excludedCount: number
  /** Successful turns that exist but fall outside the window cap. */
  beyondWindowCount: number
  /** Turns not eligible at all (anything that did not succeed). */
  unsuccessfulCount: number
  maxExchanges: number
}

const MAX_VALUE_CHARS = 90

function cap(text: string): string {
  const collapsed = collapseQuestionWhitespace(text)
  return collapsed.length > MAX_VALUE_CHARS ? `${collapsed.slice(0, MAX_VALUE_CHARS)}…` : collapsed
}

/** Everything the next question will carry, plus the scope the
 * conversation is currently operating in, as one flat, typed, ordered
 * list.
 *
 * A pure function over state the client already holds -- it issues no
 * request and reads nothing the server did not already send. The panel
 * that renders it adds no context of its own.
 *
 * **Carried turns come from `buildConversationHistory`, the exact
 * function that builds the `conversation_history` request field**, with
 * the same exclusion set the store applies when asking. So the list
 * cannot drift from what is actually sent: if the cap or the
 * "succeeded turns only" rule changes, this changes with it.
 *
 * **Only carried turns are `removable`, and that is an architectural
 * fact, not a UI preference.** A turn is removable because the client is
 * what sends it, so removing one narrows the request. Nothing else here
 * is client-supplied -- the database, tables, metrics, intent and filters
 * are all server-derived from the question text, so a "remove" control on
 * them would be a button that cannot do what it claims. The `rederived`
 * lifetime is what says so in the UI instead.
 *
 * Scope items are read from the most recent **carried** turn, not simply
 * the last turn in the transcript: a failed or removed turn is not part
 * of the active scope, so its database and filters must not be presented
 * as though they were.
 */
export function buildContextSnapshot(
  entries: QueryHistoryEntry[],
  excludedEntryIds: ReadonlySet<string> = new Set(),
  maxExchanges = MAX_FOLLOWUP_EXCHANGES,
): ContextSnapshot {
  const successful = entries.filter((entry) => entry.agentStatus === 'succeeded')
  const windowed = successful.slice(-maxExchanges)
  // The same list the request will carry, by the same function.
  const carriedQuestions = new Set(
    buildConversationHistory(entries, maxExchanges, excludedEntryIds).map(
      (exchange) => exchange.question,
    ),
  )
  const carried = windowed.filter(
    (entry) => !excludedEntryIds.has(entry.entryId) && carriedQuestions.has(entry.question),
  )

  const items: ContextItem[] = []

  // --- What the person asked (user-provided, actually carried) ---------
  carried.forEach((entry, index) => {
    items.push({
      id: entry.entryId,
      kind: 'question',
      value: cap(entry.question),
      provenance: 'user_provided',
      // The last carried turn is the one the server functionally reads.
      lifetime: index === carried.length - 1 ? 'influencing' : 'carried',
      truthLevel: null,
      removable: true,
    })
  })

  const latest = carried.length > 0 ? carried[carried.length - 1] : null
  const counts = {
    carriedCount: carried.length,
    excludedCount: windowed.length - carried.length,
    beyondWindowCount: successful.length - windowed.length,
    unsuccessfulCount: entries.length - successful.length,
    maxExchanges,
  }
  if (latest === null) return { items, ...counts }

  const state = latest.finalState

  // --- The prior answer a follow-up was resolved against ---------------
  // Server-reported, not positional guesswork: this is the turn the agent
  // itself said it read.
  if (state.followup_classification === 'followup' && state.followup_resolved_against) {
    items.push({
      id: `${latest.entryId}:referenced`,
      kind: 'referencedAnswer',
      value: cap(state.followup_resolved_against.question),
      provenance: 'inferred',
      lifetime: 'rederived',
      truthLevel: 'ai_inference',
      removable: false,
    })
  }

  // --- Active scope ----------------------------------------------------
  if (state.database) {
    items.push({
      id: `${latest.entryId}:database`,
      kind: 'database',
      value: state.database,
      provenance: 'confirmed',
      lifetime: 'rederived',
      truthLevel: 'database_fact',
      removable: false,
    })
  }

  // Tables are the one scope item that is *also* carried (they travel in
  // `ConversationExchange.tables`), so they pool across every carried
  // turn rather than being read off the latest one alone.
  const tables = [...new Set(carried.flatMap((entry) => entry.tables))].sort()
  if (tables.length > 0) {
    items.push({
      id: `${latest.entryId}:tables`,
      kind: 'tables',
      value: cap(tables.join(', ')),
      provenance: 'confirmed',
      lifetime: 'carried',
      truthLevel: 'database_fact',
      removable: false,
    })
  }

  // Published, reviewer-approved metric definitions the answer was
  // grounded in -- the only context here that is confirmed *business*
  // truth rather than an observed fact about what ran.
  for (const metric of state.governing_metrics) {
    items.push({
      id: `${latest.entryId}:definition:${metric.business_name}`,
      kind: 'definition',
      value: cap(metric.business_name),
      provenance: 'confirmed',
      lifetime: 'rederived',
      truthLevel: 'confirmed_business_truth',
      removable: false,
    })
  }

  const plan = state.analytical_plan
  if (plan) {
    for (const filter of plan.filters) {
      items.push({
        id: `${latest.entryId}:filter:${filter.column}:${filter.operator}:${filter.value}`,
        kind: 'filter',
        value: cap(`${filter.column} ${filter.operator} ${filter.value}`),
        provenance: 'inferred',
        lifetime: 'rederived',
        truthLevel: plan.truth_level,
        removable: false,
      })
    }
    if (plan.time_range) {
      items.push({
        id: `${latest.entryId}:timeRange`,
        kind: 'timeRange',
        value: cap(plan.time_range.description || plan.time_range.column),
        provenance: 'inferred',
        lifetime: 'rederived',
        truthLevel: plan.truth_level,
        removable: false,
      })
    }
  }

  // What kind of analysis the conversation is currently doing.
  if (state.analytical_intent) {
    items.push({
      id: `${latest.entryId}:scope`,
      kind: 'scope',
      value: state.analytical_intent.intent,
      provenance: 'inferred',
      lifetime: 'rederived',
      truthLevel: 'ai_inference',
      removable: false,
    })
  }

  // Files the person attached, named only -- never contents.
  for (const attachment of latest.sentAttachments) {
    items.push({
      id: `${latest.entryId}:attachment:${attachment.filename}`,
      kind: 'attachment',
      value: cap(attachment.filename),
      provenance: 'user_provided',
      lifetime: 'rederived',
      truthLevel: null,
      removable: false,
    })
  }

  return { items, ...counts }
}

/** Display order of the provenance groups. A group with no items is
 * dropped rather than rendered empty. */
export const PROVENANCE_ORDER: readonly ContextProvenance[] = [
  'user_provided',
  'confirmed',
  'inferred',
]

export function itemsByProvenance(
  snapshot: ContextSnapshot,
): { provenance: ContextProvenance; items: ContextItem[] }[] {
  return PROVENANCE_ORDER.map((provenance) => ({
    provenance,
    items: snapshot.items.filter((item) => item.provenance === provenance),
  })).filter((group) => group.items.length > 0)
}
