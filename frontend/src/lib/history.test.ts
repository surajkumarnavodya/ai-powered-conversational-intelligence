import { describe, expect, it } from 'vitest'
import { askState } from '@/test/analyticsFixtures'
import {
  buildConversationHistory,
  buildSpokenAnswerText,
  conversationAnswerText,
  MAX_FOLLOWUP_EXCHANGES,
  newHistoryEntry,
  nlCacheKey,
  serverMessagesToQueryHistory,
  type QueryHistoryEntry,
} from './history'
import type { AgentStatus, ServerMessage, ServerMessageMetadata } from './types'

/** Universal conversation history fix (2026-09-27): these are the direct
 * regression tests for the reported bug ("selecting a previous question
 * from the history panel can show no assistant answer, an empty
 * generated-SQL editor with Confirm and Run, or [object Object]") -- traced
 * to `serverMessagesToQueryHistory` fabricating an almost-empty
 * `AskResponse` (in particular always hardcoding `sources_used: []`,
 * TurnCard.tsx's own "empty means SQL path" signal) regardless of a
 * reloaded turn's real route. */

let nextId = 0
function userRow(content: string, conversationId = 'c1'): ServerMessage {
  nextId += 1
  return {
    id: `u${nextId}`,
    conversation_id: conversationId,
    role: 'user',
    content,
    sequence_number: nextId,
    created_at: '2026-01-01T00:00:00Z',
    status: 'completed',
    model_name: null,
    error_code: null,
    metadata: null,
  }
}

function assistantRow(
  content: string,
  metadata: ServerMessageMetadata | null,
  overrides: Partial<ServerMessage> = {},
): ServerMessage {
  nextId += 1
  return {
    id: `a${nextId}`,
    conversation_id: 'c1',
    role: 'assistant',
    content,
    sequence_number: nextId,
    created_at: '2026-01-01T00:00:01Z',
    status: 'completed',
    model_name: 'llama3.1:8b',
    error_code: null,
    metadata,
    ...overrides,
  }
}

describe('serverMessagesToQueryHistory -- rich (schema_version 2) records', () => {
  it('restores a SQL-path turn with no confirmed result: SQL shows, editor is actionable, nothing pre-confirmed', () => {
    const rows = [
      userRow('How many orders are there?'),
      assistantRow('Query succeeded.', {
        schema_version: 2,
        sources_used: [],
        sql: 'SELECT COUNT(*) FROM orders',
        database: 'default',
        model: 'llama3.1:8b',
        result_snapshot: null,
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.sql).toBe('SELECT COUNT(*) FROM orders')
    expect(entry.editableSql).toBe('SELECT COUNT(*) FROM orders')
    expect(entry.finalState.sources_used).toEqual([])
    expect(entry.confirmedColumns).toBeNull()
    expect(entry.confirmedRows).toBeNull()
  })

  it('restores a confirmed-and-run SQL turn with its rows and chart, without needing to re-run anything', () => {
    const rows = [
      userRow('How many orders are there?'),
      assistantRow('42 orders.', {
        schema_version: 2,
        sources_used: [],
        sql: 'SELECT COUNT(*) FROM orders',
        insight: '42 orders.',
        result_snapshot: {
          columns: ['cnt'],
          rows: [[42]],
          row_count: 1,
          returned_rows: 1,
          truncated: false,
          column_types: { cnt: 'numeric' },
          chart_recommendation: { chart_type: 'kpi', reason: 'single value', x_column: null, y_column: 'cnt' },
          normalized_sql: 'SELECT TOP 1000 COUNT(*) FROM orders',
          duration_ms: 12.3,
          captured_at: '2026-01-01T00:00:02Z',
        },
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.confirmedColumns).toEqual(['cnt'])
    expect(entry.confirmedRows).toEqual([[42]])
    expect(entry.confirmedSql).toBe('SELECT TOP 1000 COUNT(*) FROM orders')
    expect(entry.confirmedChartRecommendation?.chart_type).toBe('kpi')
    expect(entry.finalState.insight).toBe('42 orders.')
  })

  it('restores a web-only turn: real sources_used, no SQL editor material, citations intact', () => {
    const rows = [
      userRow('Why is the sky blue?'),
      assistantRow('Because of Rayleigh scattering.', {
        schema_version: 2,
        sources_used: ['web'],
        sql: null,
        web_result: {
          answer: 'Because of Rayleigh scattering.',
          citations: [
            { filename: 'https://example.com/sky', chunk_index: 0, page_number: null, document_id: 'w1', has_pdf_bytes: false },
          ],
          status: 'succeeded',
        },
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.finalState.sources_used).toEqual(['web'])
    expect(entry.sql).toBeNull()
    expect(entry.editableSql).toBe('')
    expect(entry.finalState.web_result?.answer).toBe('Because of Rayleigh scattering.')
    expect(entry.finalState.web_result?.citations[0].filename).toBe('https://example.com/sky')
  })

  it('restores a documents-only turn', () => {
    const rows = [
      userRow('What time does onboarding start?'),
      assistantRow('9am on day one.', {
        schema_version: 2,
        sources_used: ['documents'],
        document_result: { answer: '9am on day one.', citations: [], status: 'succeeded' },
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.finalState.sources_used).toEqual(['documents'])
    expect(entry.finalState.document_result?.answer).toBe('9am on day one.')
    expect(entry.sql).toBeNull()
  })

  it('restores a policy-only turn', () => {
    const rows = [
      userRow('What is the leave policy?'),
      assistantRow('1.5 days per month.', {
        schema_version: 2,
        sources_used: ['policy'],
        policy_result: { answer: '1.5 days per month.', citations: [], status: 'succeeded' },
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.finalState.sources_used).toEqual(['policy'])
    expect(entry.finalState.policy_result?.answer).toBe('1.5 days per month.')
  })

  it('restores an attachment-only turn including used_attachment_ids and vision_unavailable', () => {
    const rows = [
      userRow('What does this image show?'),
      assistantRow('A dashboard screenshot.', {
        schema_version: 2,
        sources_used: ['attachments'],
        attachment_result: {
          answer: 'A dashboard screenshot.',
          status: 'succeeded',
          used_attachment_ids: ['att-1'],
          vision_unavailable: false,
        },
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.finalState.attachment_result?.used_attachment_ids).toEqual(['att-1'])
    expect(entry.finalState.attachment_result?.vision_unavailable).toBe(false)
  })

  it('restores which files were sent with a question from attachment_refs, never fabricating a preview', () => {
    const rows = [
      userRow('Summarize this file.'),
      assistantRow('It is an invoice for $500.', {
        schema_version: 2,
        sources_used: ['attachments'],
        attachment_refs: [{ attachment_id: 'att-1', filename: 'invoice.pdf', media_type: 'application/pdf' }],
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.sentAttachments).toEqual([{ filename: 'invoice.pdf', kind: 'document', previewUrl: null }])
  })

  it('restores a mixed sql+web turn with both fields intact', () => {
    const rows = [
      userRow('Compare our sales with industry trends.'),
      assistantRow('Combined answer.', {
        schema_version: 2,
        sources_used: ['sql', 'web'],
        sql: 'SELECT * FROM sales',
        synthesized_answer: 'Combined answer.',
        web_result: { answer: 'Web context.', citations: [], status: 'succeeded' },
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.finalState.sources_used).toEqual(['sql', 'web'])
    expect(entry.sql).toBe('SELECT * FROM sales')
    expect(entry.finalState.synthesized_answer).toBe('Combined answer.')
    expect(entry.finalState.web_result?.answer).toBe('Web context.')
  })

  it('restores a failed/rejected turn with its real status and message, never [object Object]', () => {
    const rows = [
      userRow("What's the weather on Mars?"),
      assistantRow("That question isn't about your data.", {
        schema_version: 2,
        status: 'rejected',
        sources_used: [],
        rejection_message: "That question isn't about your data.",
      }, { status: 'failed' }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.agentStatus).toBe('rejected')
    expect(entry.finalState.rejection_message).toBe("That question isn't about your data.")
    expect(typeof entry.finalState.rejection_message).toBe('string')
  })
})

describe('serverMessagesToQueryHistory -- legacy records (no schema_version)', () => {
  it('a legacy SQL record (old {sql: ...} shape) still shows its SQL', () => {
    const rows = [
      userRow('How many rows?'),
      assistantRow('Query succeeded.', { sql: 'SELECT 1' } as ServerMessageMetadata),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.sql).toBe('SELECT 1')
    expect(entry.finalState.sources_used).toEqual([])
  })

  it('a legacy record with no sql and no structure surfaces the preserved answer text via a synthetic source, not nothing', () => {
    const rows = [userRow('Why is the sky blue?'), assistantRow('Because of scattering.', null)]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.finalState.sources_used).toEqual(['legacy'])
    expect(entry.finalState.synthesized_answer).toBe('Because of scattering.')
    expect(entry.sql).toBeNull()
  })

  it('a legacy record with no sql and no structure never shows an empty SQL editor', () => {
    const rows = [userRow('Why is the sky blue?'), assistantRow('Because of scattering.', null)]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.editableSql).toBe('')
    expect(entry.confirmedColumns).toBeNull()
  })
})

describe('serverMessagesToQueryHistory -- truncated/missing persistence', () => {
  it('a user message with no matching assistant row renders as pending, never a fabricated answer', () => {
    const rows = [userRow('Orphaned question')]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.agentStatus).toBe('pending')
    expect(entry.finalState.synthesized_answer).toBeNull()
    expect(entry.sql).toBeNull()
  })
})

describe('serverMessagesToQueryHistory -- answer timing and time', () => {
  it('restores the saved answer duration and the turn time, so a reopened chat shows the real figure', () => {
    const rows = [
      userRow('How many orders are there?'),
      assistantRow('Query succeeded.', {
        schema_version: 2,
        sources_used: [],
        sql: 'SELECT COUNT(*) FROM orders',
        answer_duration_ms: 4321.5,
      }),
    ]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.answerDurationMs).toBe(4321.5)
    expect(entry.timestamp).toBe('2026-01-01T00:00:00Z')
  })

  it('a turn saved before timing existed has no duration, not a fabricated 0', () => {
    const rows = [userRow('Old question?'), assistantRow('Old answer.', { schema_version: 2, sources_used: [] })]
    const [entry] = serverMessagesToQueryHistory(rows)
    expect(entry.answerDurationMs).toBeNull()
  })
})

function historyEntry(question: string, status: AgentStatus = 'succeeded'): QueryHistoryEntry {
  const base = newHistoryEntry(question, askState({ status }), 1000)
  return { ...base, agentStatus: status }
}

describe('buildConversationHistory context exclusions', () => {
  it('sends every windowed successful turn when nothing is excluded', () => {
    // The default path must be unchanged by the exclusion parameter
    // existing at all -- follow-up behaviour is preserved, not altered.
    const entries = [historyEntry('One?'), historyEntry('Two?')]
    expect(buildConversationHistory(entries).map((exchange) => exchange.question)).toEqual([
      'One?',
      'Two?',
    ])
    expect(buildConversationHistory(entries, MAX_FOLLOWUP_EXCHANGES, new Set())).toEqual(
      buildConversationHistory(entries),
    )
  })

  it('omits an excluded turn', () => {
    const entries = [historyEntry('Keep?'), historyEntry('Drop?')]
    const sent = buildConversationHistory(
      entries,
      MAX_FOLLOWUP_EXCHANGES,
      new Set([entries[1].entryId]),
    )
    expect(sent.map((exchange) => exchange.question)).toEqual(['Keep?'])
  })

  it('does not backfill an older turn into the freed slot', () => {
    // Exclusion is applied after the window slice on purpose: filtering
    // first would pull an older turn in, silently re-adding context the
    // person just removed.
    const entries = Array.from({ length: MAX_FOLLOWUP_EXCHANGES + 1 }, (_, index) =>
      historyEntry(`Q${index}?`),
    )
    const windowed = entries.slice(-MAX_FOLLOWUP_EXCHANGES)
    const sent = buildConversationHistory(
      entries,
      MAX_FOLLOWUP_EXCHANGES,
      new Set([windowed[0].entryId]),
    )
    expect(sent).toHaveLength(MAX_FOLLOWUP_EXCHANGES - 1)
    expect(sent.map((exchange) => exchange.question)).not.toContain('Q0?')
  })

  it('can only ever narrow -- never add, edit or reorder a turn', () => {
    // The authorization-relevant property: whatever the exclusion set
    // says, the output is always a subsequence of the un-excluded output.
    const entries = [historyEntry('One?'), historyEntry('Two?'), historyEntry('Three?')]
    const full = buildConversationHistory(entries)
    for (const excluded of [
      new Set<string>(),
      new Set([entries[0].entryId]),
      new Set([entries[1].entryId]),
      new Set(entries.map((entry) => entry.entryId)),
      new Set(['an-id-from-no-turn-at-all']),
    ]) {
      const sent = buildConversationHistory(entries, MAX_FOLLOWUP_EXCHANGES, excluded)
      expect(sent.length).toBeLessThanOrEqual(full.length)
      // A subsequence by value (fresh objects each call, so not by
      // identity): order preserved, and no entry the full output lacked.
      const remaining = full.map((exchange) => JSON.stringify(exchange))
      for (const exchange of sent.map((item) => JSON.stringify(item))) {
        const at = remaining.indexOf(exchange)
        expect(at).toBeGreaterThanOrEqual(0)
        remaining.splice(0, at + 1)
      }
    }
  })

  it('excluding an unknown id changes nothing', () => {
    const entries = [historyEntry('One?')]
    expect(
      buildConversationHistory(entries, MAX_FOLLOWUP_EXCHANGES, new Set(['nope'])),
    ).toEqual(buildConversationHistory(entries))
  })

  it('excluding everything yields an empty history, so the next ask is standalone', () => {
    const entries = [historyEntry('One?'), historyEntry('Two?')]
    expect(
      buildConversationHistory(
        entries,
        MAX_FOLLOWUP_EXCHANGES,
        new Set(entries.map((entry) => entry.entryId)),
      ),
    ).toEqual([])
  })
})

describe('nlCacheKey', () => {
  it('distinguishes two asks of the same question with different exclusions', () => {
    const withNone = nlCacheKey('revenue?', ['prior?'], true, new Set())
    const withOne = nlCacheKey('revenue?', ['prior?'], true, new Set(['entry-1']))
    expect(withOne).not.toBe(withNone)
  })

  it('is stable regardless of set iteration order', () => {
    expect(nlCacheKey('q', [], true, new Set(['b', 'a']))).toBe(
      nlCacheKey('q', [], true, new Set(['a', 'b'])),
    )
  })

  it('defaults to no exclusions', () => {
    expect(nlCacheKey('q', [], true)).toBe(nlCacheKey('q', [], true, new Set()))
  })
})

describe('conversationAnswerText', () => {
  it('prefers a grounded insight, the SQL path answer', () => {
    const entry = newHistoryEntry('Q?', askState({ insight: 'Sales grew 12%.' }), 1000)
    expect(conversationAnswerText(entry)).toBe('Sales grew 12%.')
  })

  it('falls back to the multi-source synthesized answer', () => {
    const entry = newHistoryEntry(
      'Q?',
      askState({ insight: null, synthesized_answer: 'The policy allows 12 days.' }),
      1000,
    )
    expect(conversationAnswerText(entry)).toBe('The policy allows 12 days.')
  })

  it('returns null rather than a row count when there is no answer text', () => {
    // "Found 12 rows." is not context a refinement can act on, so it is
    // deliberately not carried -- unlike buildSpokenAnswerText, which has
    // to say something out loud.
    const entry = newHistoryEntry('Q?', askState({ insight: null, row_count: 12 }), 1000)
    expect(conversationAnswerText(entry)).toBeNull()
  })

  it('still gives buildSpokenAnswerText something to say', () => {
    const entry = newHistoryEntry('Q?', askState({ insight: null, row_count: 12 }), 1000)
    expect(buildSpokenAnswerText(entry)).toContain('12')
  })
})

describe('buildConversationHistory carries the prior answer', () => {
  it('sends the answer text alongside the question', () => {
    const base = newHistoryEntry('Tell me about the Pixel 10a', askState({
      status: 'succeeded',
      insight: 'It is a mid-range phone.',
    }), 1000)
    const entries = [{ ...base, agentStatus: 'succeeded' as AgentStatus }]
    expect(buildConversationHistory(entries)[0].answer).toBe('It is a mid-range phone.')
  })

  it('omits the field entirely when the turn produced no answer text', () => {
    // `undefined`, not null, so the key is simply absent from the request
    // body -- the server treats a missing answer and an explicit null
    // identically, and absent is the smaller request.
    const base = newHistoryEntry('Q?', askState({ status: 'succeeded', insight: null }), 1000)
    const entries = [{ ...base, agentStatus: 'succeeded' as AgentStatus }]
    expect(buildConversationHistory(entries)[0].answer).toBeUndefined()
  })
})
