import { describe, expect, it } from 'vitest'
// Read as text through Vite's own `?raw` loader rather than a filesystem
// read: `tsconfig.app.json` types the app project with `vite/client`
// alone, so reading through `node:*` here would mean node globals across
// every app source file. `?raw` is also anchored to this file rather than
// to whichever directory the runner happened to start in.
import conversationContextSource from './conversationContext.ts?raw'
import { askState } from '@/test/analyticsFixtures'
import {
  buildContextSnapshot,
  itemsByProvenance,
  PROVENANCE_ORDER,
  type ContextItemKind,
} from './conversationContext'
import {
  buildConversationHistory,
  MAX_FOLLOWUP_EXCHANGES,
  newHistoryEntry,
  type QueryHistoryEntry,
} from './history'
import type { AskResponse } from './types'

function entry(
  question: string,
  overrides: Partial<QueryHistoryEntry> = {},
  state: Partial<AskResponse> = {},
): QueryHistoryEntry {
  const base = newHistoryEntry(question, askState({ status: 'succeeded', ...state }), 1000)
  return { ...base, agentStatus: 'succeeded', ...overrides }
}

function kinds(entries: QueryHistoryEntry[], excluded = new Set<string>()): ContextItemKind[] {
  return buildContextSnapshot(entries, excluded).items.map((item) => item.kind)
}

function valuesOfKind(
  entries: QueryHistoryEntry[],
  kind: ContextItemKind,
  excluded = new Set<string>(),
): string[] {
  return buildContextSnapshot(entries, excluded)
    .items.filter((item) => item.kind === kind)
    .map((item) => item.value)
}

describe('buildContextSnapshot', () => {
  it('reports nothing for an empty conversation', () => {
    const snapshot = buildContextSnapshot([])
    expect(snapshot.items).toEqual([])
    expect(snapshot.carriedCount).toBe(0)
  })

  it('carries exactly what buildConversationHistory sends', () => {
    // The whole point of routing through that function: this list cannot
    // drift from the request payload.
    const entries = [entry('First?'), entry('Second?'), entry('Third?'), entry('Fourth?')]
    expect(valuesOfKind(entries, 'question')).toEqual(
      buildConversationHistory(entries).map((exchange) => exchange.question),
    )
  })

  it('carries only successful turns, and counts the rest', () => {
    const entries = [
      entry('Worked?'),
      entry('Broke?', { agentStatus: 'failed' }),
      entry('Rejected?', { agentStatus: 'rejected' }),
    ]
    const snapshot = buildContextSnapshot(entries)
    expect(valuesOfKind(entries, 'question')).toEqual(['Worked?'])
    expect(snapshot.unsuccessfulCount).toBe(2)
  })

  it('counts successful turns that fall outside the window', () => {
    const entries = Array.from({ length: MAX_FOLLOWUP_EXCHANGES + 2 }, (_, index) =>
      entry(`Q${index}?`),
    )
    const snapshot = buildContextSnapshot(entries)
    expect(snapshot.carriedCount).toBe(MAX_FOLLOWUP_EXCHANGES)
    expect(snapshot.beyondWindowCount).toBe(2)
  })

  describe('what actually influences the next answer', () => {
    it('marks only the most recent carried turn as read downstream', () => {
      // Per agent.input_guard.sanitize_conversation_history, only
      // conversation_history[-1] is functionally read; the rest contribute
      // only "history exists". Showing all three as equally active would
      // overstate their effect.
      const entries = [entry('Oldest?'), entry('Middle?'), entry('Newest?')]
      const questions = buildContextSnapshot(entries).items.filter(
        (item) => item.kind === 'question',
      )
      expect(questions.map((item) => item.lifetime)).toEqual([
        'carried',
        'carried',
        'influencing',
      ])
    })

    it('marks server-rederived scope separately from carried context', () => {
      // The database is re-routed per question and the plan is rebuilt per
      // question -- neither travels in conversation_history, so neither may
      // be presented as a value being carried forward.
      const entries = [entry('Revenue?', { tables: ['sales'] })]
      const byKind = new Map(
        buildContextSnapshot(entries).items.map((item) => [item.kind, item.lifetime]),
      )
      expect(byKind.get('database')).toBe('rederived')
      // Tables are the one scope item that genuinely is carried.
      expect(byKind.get('tables')).toBe('carried')
    })
  })

  describe('provenance classification', () => {
    it('classes the question as user-provided and the database as confirmed', () => {
      const snapshot = buildContextSnapshot([entry('Revenue?', { tables: ['sales'] })])
      const byKind = new Map(snapshot.items.map((item) => [item.kind, item.provenance]))
      expect(byKind.get('question')).toBe('user_provided')
      expect(byKind.get('database')).toBe('confirmed')
      expect(byKind.get('tables')).toBe('confirmed')
    })

    it('classes an approved metric definition as confirmed business truth', () => {
      const entries = [
        entry(
          'Revenue?',
          {},
          {
            governing_metrics: [
              {
                business_name: 'Net Revenue',
                approved_expression: 'SUM(amount)',
                aggregation: 'sum',
                text: 'Net Revenue',
              },
            ],
          },
        ),
      ]
      const definition = buildContextSnapshot(entries).items.find(
        (item) => item.kind === 'definition',
      )
      expect(definition?.provenance).toBe('confirmed')
      expect(definition?.truthLevel).toBe('confirmed_business_truth')
      expect(definition?.value).toBe('Net Revenue')
    })

    it('classes a plan filter and time range as inferred, never confirmed', () => {
      // LLM-proposed then deterministically re-verified by
      // agent.plan_validator -- checked, not confirmed.
      const entries = [
        entry(
          'Revenue FY2026?',
          {},
          {
            analytical_plan: {
              metrics: [],
              dimensions: [],
              filters: [
                { table: 'sales', column: 'fiscal_year', operator: '=', value: 'FY2026' },
              ],
              time_range: { table: 'sales', column: 'order_date', description: 'FY2026' },
              grain: null,
              comparison: null,
              ranking: null,
              sort: [],
              limit: null,
              required_operations: [],
              truth_level: 'ai_inference',
            },
          },
        ),
      ]
      const snapshot = buildContextSnapshot(entries)
      const filter = snapshot.items.find((item) => item.kind === 'filter')
      expect(filter?.provenance).toBe('inferred')
      expect(filter?.truthLevel).toBe('ai_inference')
      expect(filter?.value).toBe('fiscal_year = FY2026')
      expect(snapshot.items.find((item) => item.kind === 'timeRange')?.provenance).toBe('inferred')
    })

    it('groups items into the three classes, dropping empty groups', () => {
      const groups = itemsByProvenance(buildContextSnapshot([entry('Revenue?')]))
      expect(groups.map((group) => group.provenance)).toEqual([
        'user_provided',
        'confirmed',
      ])
      expect(PROVENANCE_ORDER).toEqual(['user_provided', 'confirmed', 'inferred'])
    })
  })

  describe('follow-ups', () => {
    it('names the prior question a follow-up was resolved against', () => {
      const entries = [
        entry('Revenue by company?'),
        entry(
          'And by region?',
          {},
          {
            followup_classification: 'followup',
            followup_resolved_against: {
              question: 'Revenue by company?',
              sql: 'SELECT 1',
              tables: ['sales'],
              status: 'succeeded',
            },
          },
        ),
      ]
      expect(valuesOfKind(entries, 'referencedAnswer')).toEqual(['Revenue by company?'])
    })

    it('shows no referenced answer for a standalone question', () => {
      const entries = [entry('Revenue?', {}, { followup_classification: 'standalone' })]
      expect(kinds(entries)).not.toContain('referencedAnswer')
    })
  })

  describe('removal', () => {
    it('only carried turns are removable -- nothing server-derived is', () => {
      // Removability is an architectural fact: a turn is removable because
      // the client is what sends it. A remove control on a re-derived item
      // would be a button that cannot do what it claims.
      const entries = [entry('Revenue?', { tables: ['sales'] })]
      for (const item of buildContextSnapshot(entries).items) {
        expect(item.removable).toBe(item.kind === 'question')
      }
    })

    it('a removed turn leaves the carried list and the count', () => {
      const entries = [entry('Keep?'), entry('Drop?')]
      const snapshot = buildContextSnapshot(entries, new Set([entries[1].entryId]))
      expect(valuesOfKind(entries, 'question', new Set([entries[1].entryId]))).toEqual(['Keep?'])
      expect(snapshot.carriedCount).toBe(1)
      expect(snapshot.excludedCount).toBe(1)
    })

    it('removing the newest turn promotes the next one to influencing', () => {
      const entries = [entry('Older?'), entry('Newest?')]
      const questions = buildContextSnapshot(entries, new Set([entries[1].entryId])).items.filter(
        (item) => item.kind === 'question',
      )
      expect(questions.map((item) => [item.value, item.lifetime])).toEqual([
        ['Older?', 'influencing'],
      ])
    })

    it('reads scope from the newest *carried* turn, not the newest turn', () => {
      // A removed turn is not part of the active scope, so its database
      // must not still be presented as the one in effect.
      const entries = [
        entry('Kept?', { tables: ['sales'] }, { database: 'SalesDB' }),
        entry('Removed?', { tables: ['hr'] }, { database: 'HrDB' }),
      ]
      const excluded = new Set([entries[1].entryId])
      expect(valuesOfKind(entries, 'database', excluded)).toEqual(['SalesDB'])
      // The removed turn's tables go with it.
      expect(valuesOfKind(entries, 'tables', excluded)).toEqual(['sales'])
    })

    it('removing every turn leaves no context at all', () => {
      const entries = [entry('One?'), entry('Two?')]
      const all = new Set(entries.map((item) => item.entryId))
      const snapshot = buildContextSnapshot(entries, all)
      expect(snapshot.items).toEqual([])
      expect(snapshot.carriedCount).toBe(0)
      expect(snapshot.excludedCount).toBe(2)
    })

    it('does not backfill an older turn into a freed window slot', () => {
      // Narrowing only: pulling an older turn in to replace a removed one
      // would silently re-add context the person just removed.
      const entries = Array.from({ length: MAX_FOLLOWUP_EXCHANGES + 1 }, (_, index) =>
        entry(`Q${index}?`),
      )
      const windowed = entries.slice(-MAX_FOLLOWUP_EXCHANGES)
      const snapshot = buildContextSnapshot(entries, new Set([windowed[0].entryId]))
      expect(snapshot.carriedCount).toBe(MAX_FOLLOWUP_EXCHANGES - 1)
      expect(valuesOfKind(entries, 'question', new Set([windowed[0].entryId]))).not.toContain('Q0?')
    })
  })

  describe('duplicate questions', () => {
    it('keeps duplicate questions as separate items with separate ids', () => {
      // Two identical questions are two different turns. Deduplicating by
      // text would make one of them unremovable.
      const entries = [entry('Revenue?'), entry('Revenue?')]
      const questions = buildContextSnapshot(entries).items.filter(
        (item) => item.kind === 'question',
      )
      expect(questions).toHaveLength(2)
      expect(questions[0].id).not.toBe(questions[1].id)
      expect(questions.map((item) => item.id)).toEqual(entries.map((item) => item.entryId))
    })

    it('removes only the duplicate that was actually selected', () => {
      const entries = [entry('Revenue?'), entry('Revenue?')]
      const snapshot = buildContextSnapshot(entries, new Set([entries[0].entryId]))
      const questions = snapshot.items.filter((item) => item.kind === 'question')
      expect(questions).toHaveLength(1)
      expect(questions[0].id).toBe(entries[1].entryId)
    })
  })

  describe('database changes mid-conversation', () => {
    it('reports the database of the newest carried turn, not the first', () => {
      // The router re-picks per question (embeddings.retriever.select_database),
      // so a conversation can genuinely move between databases.
      const entries = [
        entry('Sales?', { tables: ['sales'] }, { database: 'SalesDB' }),
        entry('Headcount?', { tables: ['employee'] }, { database: 'HrDB' }),
      ]
      expect(valuesOfKind(entries, 'database')).toEqual(['HrDB'])
    })

    it('pools tables across carried turns even when the database changed', () => {
      // Tables genuinely travel in conversation_history for every carried
      // turn, so this reports the union -- not just the latest turn's.
      const entries = [
        entry('Sales?', { tables: ['sales'] }, { database: 'SalesDB' }),
        entry('Headcount?', { tables: ['employee'] }, { database: 'HrDB' }),
      ]
      expect(valuesOfKind(entries, 'tables')).toEqual(['employee, sales'])
    })

    it('omits the database entirely when the server reported none', () => {
      expect(kinds([entry('Anything?', {}, { database: null })])).not.toContain('database')
    })
  })

  describe('historical conversations', () => {
    it('describes a reloaded conversation from its persisted fields alone', () => {
      // A reloaded turn has no analytics fields (they are not persisted)
      // and no DDL -- so the panel shows what survived and claims nothing
      // more, rather than rendering an empty or invented scope.
      const reloaded = entry(
        'What were Q1 sales?',
        { tables: ['sales'] },
        {
          database: 'SalesDB',
          analytical_plan: null,
          analytical_intent: null,
          governing_metrics: [],
        },
      )
      const snapshot = buildContextSnapshot([reloaded])
      expect(snapshot.items.map((item) => item.kind)).toEqual(['question', 'database', 'tables'])
      expect(snapshot.carriedCount).toBe(1)
    })

    it('carries a reloaded turn with no recorded tables without inventing any', () => {
      const snapshot = buildContextSnapshot([entry('Legacy?', { tables: [] })])
      expect(snapshot.items.map((item) => item.kind)).not.toContain('tables')
    })
  })

  describe('what is never exposed', () => {
    it('never includes the SQL, which each turn already shows itself', () => {
      const entries = [
        entry('Revenue?', { sql: 'SELECT SUM(amount) FROM sales' }, { database: 'SalesDB' }),
      ]
      const text = buildContextSnapshot(entries)
        .items.map((item) => item.value)
        .join(' ')
      expect(text).not.toContain('SELECT')
    })

    it('never includes retrieved DDL', () => {
      const entries = [
        entry(
          'Revenue?',
          { tables: ['sales'] },
          {
            schema_tables: [
              { table_name: 'sales', ddl: 'CREATE TABLE sales (id INT)', similarity_score: 0.9 },
            ],
          },
        ),
      ]
      const text = buildContextSnapshot(entries)
        .items.map((item) => item.value)
        .join(' ')
      expect(text).not.toContain('CREATE TABLE')
    })

    it('names an attached file without its contents', () => {
      const entries = [
        entry('What is in this?', {
          sentAttachments: [{ filename: 'invoice.pdf', kind: 'document', previewUrl: null }],
        }),
      ]
      const attachment = buildContextSnapshot(entries).items.find(
        (item) => item.kind === 'attachment',
      )
      expect(attachment?.value).toBe('invoice.pdf')
      expect(attachment?.provenance).toBe('user_provided')
    })

    it('caps a long value rather than letting it run', () => {
      const long = `Why did ${'revenue '.repeat(40)}drop?`
      const value = valuesOfKind([entry(long)], 'question')[0]
      expect(value.length).toBeLessThanOrEqual(91)
      expect(value.endsWith('…')).toBe(true)
    })
  })

  describe('isolation', () => {
    it('reads only the entries it is given -- it fetches nothing', () => {
      // Structural, mirroring this repo's tests/security/test_*_never_*
      // precedent: a context view that could issue its own request could
      // reach data the caller's own conversation never contained. Tenant
      // isolation itself is the server's (conversations are tenant-scoped,
      // and conversation_history is treated as untrusted caller input);
      // what is pinned here is that this module adds no second path to
      // data at all.
      const source = conversationContextSource
      for (const forbidden of ['lib/api', './api', 'fetch(', 'XMLHttpRequest', 'chatStore']) {
        expect(source).not.toContain(forbidden)
      }
    })

    it('is a pure function of its arguments', () => {
      const entries = [entry('Revenue?', { tables: ['sales'] })]
      const first = buildContextSnapshot(entries)
      const second = buildContextSnapshot(entries)
      expect(second).toEqual(first)
    })

    it('does not mutate the entries it was given', () => {
      const entries = [entry('Revenue?', { tables: ['sales'] })]
      const before = JSON.stringify(entries)
      buildContextSnapshot(entries, new Set([entries[0].entryId]))
      expect(JSON.stringify(entries)).toBe(before)
    })
  })
})
