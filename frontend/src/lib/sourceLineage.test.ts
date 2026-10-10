import { describe, expect, it } from 'vitest'
import { askState } from '@/test/analyticsFixtures'
import { buildLineage, sanitizeLineageText } from './sourceLineage'
import type { AskResponse, Citation, SourceAnswer } from './types'

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    filename: 'handbook.pdf',
    chunk_index: 0,
    page_number: 4,
    document_id: 'doc-internal-7f3a9c',
    has_pdf_bytes: true,
    ...overrides,
  }
}

function sourceAnswer(overrides: Partial<SourceAnswer> = {}): SourceAnswer {
  return { answer: 'Because the policy says so.', citations: [citation()], status: 'ok', ...overrides }
}

function step(state: AskResponse, key: string) {
  const found = buildLineage(state).steps.find((s) => s.key === key)
  if (!found) throw new Error(`no step ${key}`)
  return found
}

describe('buildLineage -- SQL attribution', () => {
  it('attributes a SQL answer to its own database by name', () => {
    const lineage = buildLineage(
      askState({
        database: 'CompanyMaster',
        sql_summary: { statement_type: 'SELECT', referenced_tables: ['dbo.Orders', 'dbo.Customers'] },
        result_columns: ['region', 'revenue'],
        row_count: 12,
      }),
    )

    const db = lineage.sources.find((source) => source.kind === 'database')
    expect(db).toMatchObject({
      name: 'CompanyMaster',
      retrieval: 'direct_query',
      truthLevel: 'database_fact',
    })
    expect(db?.details).toEqual([
      { label: 'statementType', value: 'SELECT' },
      { label: 'tablesUsed', value: 'dbo.Orders, dbo.Customers' },
      { label: 'columnsReturned', value: 'region, revenue' },
      { label: 'rowCount', value: '12' },
    ])
  })

  it('reports a query result as a measured fact, not an estimate', () => {
    const lineage = buildLineage(askState({ database: 'db1' }))
    expect(lineage.sources.find((s) => s.kind === 'database')?.truthLevel).toBe('database_fact')
  })

  it('omits a detail the response does not carry, rather than guessing it', () => {
    // No sql_summary, no result_columns, no row_count.
    const lineage = buildLineage(askState({ database: 'db1', row_count: null }))
    expect(lineage.sources.find((s) => s.kind === 'database')?.details).toEqual([])
  })

  it('treats an empty sources_used as the SQL path (router off), per this app\'s own convention', () => {
    const lineage = buildLineage(askState({ sources_used: [], database: 'db1' }))
    expect(lineage.sources.map((s) => s.kind)).toContain('database')
  })

  it('lists the retrieved schema tables as a similarity-search source', () => {
    const lineage = buildLineage(
      askState({
        schema_tables: [
          { table_name: 'Orders', ddl: 'CREATE TABLE Orders (id INT, secret_col VARCHAR)', similarity_score: 0.9 },
        ],
      }),
    )
    const index = lineage.sources.find((s) => s.kind === 'schema_index')
    expect(index).toMatchObject({ retrieval: 'vector_similarity' })
    expect(index?.details).toEqual([{ label: 'tablesRetrieved', value: 'Orders' }])
  })
})

describe('buildLineage -- knowledge sources', () => {
  it('attributes a governed metric as an approved definition, not an inference', () => {
    const lineage = buildLineage(
      askState({
        governing_metrics: [
          { business_name: 'Net Revenue', approved_expression: 'SUM(amount)', aggregation: 'sum', text: 'x' },
        ],
      }),
    )
    const glossary = lineage.sources.find((s) => s.kind === 'business_glossary')
    expect(glossary).toMatchObject({
      retrieval: 'approved_definition',
      truthLevel: 'confirmed_business_truth',
    })
    expect(glossary?.details).toEqual([{ label: 'metric', value: 'Net Revenue' }])
  })

  it('never exposes a metric\'s approved SQL expression in the lineage', () => {
    // The expression belongs to the governed-metrics panel, which already
    // shows it in context; repeating it here would duplicate that and widen
    // what a compact provenance panel discloses.
    const lineage = buildLineage(
      askState({
        governing_metrics: [
          { business_name: 'Net Revenue', approved_expression: 'SUM(o.amount - o.refunds)', aggregation: 'sum', text: 'x' },
        ],
      }),
    )
    expect(JSON.stringify(lineage)).not.toContain('o.refunds')
  })
})

describe('buildLineage -- document, policy and web attribution', () => {
  it('attributes a document answer to its cited filenames and pages', () => {
    const lineage = buildLineage(
      askState({ sources_used: ['documents'], document_result: sourceAnswer() }),
    )
    const documents = lineage.sources.find((s) => s.kind === 'documents')
    expect(documents).toMatchObject({ retrieval: 'graded_retrieval', truthLevel: 'ai_inference' })
    expect(documents?.details).toEqual([{ label: 'citations', value: 'handbook.pdf (p. 4)' }])
  })

  it('attributes a web answer as a live search, and as an inference', () => {
    const lineage = buildLineage(
      askState({
        sources_used: ['web'],
        web_result: sourceAnswer({ citations: [citation({ filename: 'https://example.com/a', page_number: null })] }),
      }),
    )
    expect(lineage.sources.find((s) => s.kind === 'web')).toMatchObject({
      retrieval: 'live_web_search',
      truthLevel: 'ai_inference',
    })
  })

  it('de-duplicates repeated citations of the same page', () => {
    const lineage = buildLineage(
      askState({
        sources_used: ['documents'],
        document_result: sourceAnswer({ citations: [citation(), citation(), citation({ page_number: 9 })] }),
      }),
    )
    expect(lineage.sources.find((s) => s.kind === 'documents')?.details[0].value).toBe(
      'handbook.pdf (p. 4), handbook.pdf (p. 9)',
    )
  })

  it('reports a restricted policy match honestly: a source with no citations', () => {
    // rag/graph.py withholds a sensitivity-categorised chunk entirely, so
    // the answer arrives with zero citations. The source must still be
    // listed -- and must not acquire an invented document name.
    const lineage = buildLineage(
      askState({
        sources_used: ['policy'],
        policy_result: sourceAnswer({ answer: 'That information is restricted.', citations: [] }),
      }),
    )
    const policy = lineage.sources.find((s) => s.kind === 'policy')
    expect(policy).toBeDefined()
    expect(policy?.name).toBeNull()
    expect(policy?.details).toEqual([])
  })
})

describe('buildLineage -- multi-source', () => {
  it('lists every contributing source, in a stable order', () => {
    const lineage = buildLineage(
      askState({
        sources_used: ['sql', 'documents', 'web'],
        database: 'CompanyMaster',
        governing_metrics: [
          { business_name: 'Net Revenue', approved_expression: null, aggregation: null, text: 'x' },
        ],
        document_result: sourceAnswer(),
        web_result: sourceAnswer({ citations: [citation({ filename: 'https://example.com', page_number: null })] }),
      }),
    )
    expect(lineage.sources.map((s) => s.kind)).toEqual([
      'database',
      'business_glossary',
      'documents',
      'web',
    ])
  })

  it('counts the sources for the collapsed label', () => {
    const lineage = buildLineage(
      askState({
        sources_used: ['sql', 'web'],
        database: 'db1',
        web_result: sourceAnswer(),
      }),
    )
    expect(lineage.sources).toHaveLength(2)
    expect(step(askState({ sources_used: ['sql'], database: 'db1' }), 'source').summary).toBe('1')
  })

  it('attributes attached files by count, never by internal id', () => {
    const lineage = buildLineage(
      askState({
        sources_used: ['attachments'],
        attachment_result: {
          answer: 'The invoice totals 42.',
          status: 'ok',
          used_attachment_ids: ['att-9f21', 'att-3b74'],
          vision_unavailable: false,
        },
      }),
    )
    expect(lineage.sources.find((s) => s.kind === 'attachments')?.details).toEqual([
      { label: 'fileCount', value: '2' },
    ])
    expect(JSON.stringify(lineage)).not.toContain('att-9f21')
  })

  it('attributes generated media to its provider model', () => {
    const lineage = buildLineage(
      askState({
        sources_used: ['generation'],
        generation_result: {
          answer: 'Here is the image.',
          status: 'ok',
          media_id: 'media-internal-44ab',
          media_type: 'image',
          model: 'ima-studio-v2',
        } as AskResponse['generation_result'],
      }),
    )
    const generated = lineage.sources.find((s) => s.kind === 'generated_media')
    expect(generated?.details).toEqual([
      { label: 'mediaType', value: 'image' },
      { label: 'providerModel', value: 'ima-studio-v2' },
    ])
    expect(JSON.stringify(lineage)).not.toContain('media-internal-44ab')
  })
})

describe('buildLineage -- the Question to Insight chain', () => {
  it('walks every step for a complete SQL answer', () => {
    const state = askState({
      database: 'db1',
      sql: 'SELECT 1',
      sql_summary: { statement_type: 'SELECT', referenced_tables: ['Orders'] },
      result_columns: ['a'],
      row_count: 3,
      insight: 'Revenue rose.',
    })
    expect(buildLineage(state).steps.map((s) => [s.key, s.state])).toEqual([
      ['question', 'present'],
      ['source', 'present'],
      ['tables', 'present'],
      ['query', 'present'],
      ['result', 'present'],
      ['insight', 'present'],
    ])
  })

  it('marks tables, query and result not-applicable for a web-only answer', () => {
    const state = askState({
      sources_used: ['web'],
      database: null,
      sql: null,
      row_count: null,
      web_result: sourceAnswer(),
    })
    expect(step(state, 'tables').state).toBe('not_applicable')
    expect(step(state, 'query').state).toBe('not_applicable')
    expect(step(state, 'result').state).toBe('not_applicable')
  })

  it('distinguishes "did not apply" from "applied but produced nothing"', () => {
    // A SQL turn that never got as far as generating SQL: the query step
    // applied and is missing, which is a different fact from a web answer
    // that never had a query step at all.
    const state = askState({ sources_used: ['sql'], database: 'db1', sql: null })
    expect(step(state, 'query').state).toBe('absent')
  })

  it('reports no insight as absent, never as present-but-empty', () => {
    expect(step(askState({ database: 'db1', insight: null }), 'insight').state).toBe('absent')
    expect(step(askState({ database: 'db1', insight: '' }), 'insight').state).toBe('absent')
  })

  it('falls back to the retrieved tables when the SQL named none', () => {
    const state = askState({
      database: 'db1',
      schema_tables: [{ table_name: 'Orders', ddl: 'CREATE TABLE Orders (id INT)', similarity_score: 0.8 }],
    })
    expect(step(state, 'tables')).toMatchObject({ state: 'present', summary: 'Orders' })
  })
})

describe('buildLineage -- restrictions and tenant isolation', () => {
  it('surfaces a masked-field notice rather than hiding that masking happened', () => {
    const lineage = buildLineage(
      askState({ database: 'db1', restricted_field_notice: 'Some values were masked for your role.' }),
    )
    expect(lineage.restrictionNotices).toEqual(['Some values were masked for your role.'])
  })

  it('surfaces a dropped-source notice, which is how an unpermitted source is disclosed', () => {
    const lineage = buildLineage(
      askState({
        database: 'db1',
        permission_denied_notice: 'Media generation is not available for your role.',
      }),
    )
    expect(lineage.restrictionNotices).toContain('Media generation is not available for your role.')
  })

  it('never lists a source the server did not report', () => {
    // Tenant/role isolation is enforced server-side: a source the caller
    // may not use never arrives in the response, so the lineage cannot
    // name it. This pins that the derivation is purely additive over what
    // was sent -- it never assumes a source "should" be there.
    const lineage = buildLineage(
      askState({ sources_used: ['sql'], database: 'TenantAMaster' }),
    )
    expect(lineage.sources.map((s) => s.kind)).toEqual(['database'])
    const serialised = JSON.stringify(lineage)
    for (const absent of ['documents', 'policy', 'web', 'media_library', 'generated_media']) {
      expect(serialised).not.toContain(absent)
    }
  })

  it('names only the database this answer was actually routed to', () => {
    const lineage = buildLineage(askState({ database: 'TenantAMaster' }))
    expect(JSON.stringify(lineage)).toContain('TenantAMaster')
    expect(JSON.stringify(lineage)).not.toContain('TenantB')
  })

  it('returns no sources at all for a rejected turn, rather than an empty shell', () => {
    const lineage = buildLineage(
      askState({
        status: 'rejected',
        database: null,
        sql: null,
        row_count: null,
        rejection_reason: 'off_topic',
      }),
    )
    expect(lineage.sources).toEqual([])
  })
})

describe('buildLineage -- no credential or internal-path leakage', () => {
  const FORBIDDEN = [
    /password\s*=/i,
    /pwd\s*=/i,
    /\bserver\s*=/i,
    /\buid\s*=/i,
    /trusted_connection/i,
    /postgres(ql)?:\/\//i,
    /mssql\+pyodbc/i,
    /driver=\{/i,
    /[A-Za-z]:\\\\?Users\\\\?/i,
    /\/home\/[a-z]/i,
    /\/var\/lib\//i,
    /secret|api[_-]?key|bearer /i,
  ]

  it('carries nothing credential- or path-shaped, even when such text is planted in the response', () => {
    // Every string field a hostile or misconfigured server could populate.
    // None of these should survive into the lineage; those that legitimately
    // do (a database *name*) are asserted separately below.
    const lineage = buildLineage(
      askState({
        database: 'db1',
        model: 'llama3.1:8b',
        sql: "SELECT * FROM t WHERE conn = 'Server=10.0.0.1;Uid=sa;Password=hunter2'",
        sql_summary: { statement_type: 'SELECT', referenced_tables: ['t'] },
        schema_tables: [
          {
            table_name: 'Orders',
            // DDL is deliberately never rendered by this panel.
            ddl: "CREATE TABLE Orders (path VARCHAR DEFAULT 'C:\\Users\\Suraj\\secret.txt')",
            similarity_score: 0.9,
          },
        ],
        insight: 'Revenue rose.',
      }),
    )

    const serialised = JSON.stringify(lineage)
    for (const pattern of FORBIDDEN) {
      expect(serialised, String(pattern)).not.toMatch(pattern)
    }
  })

  it('never includes the SQL text itself', () => {
    // The chain reports that a query ran and its statement type; the SQL
    // body has its own panel, where it is editable and re-validated.
    const lineage = buildLineage(
      askState({ database: 'db1', sql: 'SELECT secret FROM vault', sql_summary: { statement_type: 'SELECT', referenced_tables: ['vault'] } }),
    )
    expect(JSON.stringify(lineage)).not.toContain('SELECT secret FROM vault')
  })

  it('never includes table DDL', () => {
    const lineage = buildLineage(
      askState({
        schema_tables: [{ table_name: 'Orders', ddl: 'CREATE TABLE Orders (hidden_col INT)', similarity_score: 0.5 }],
      }),
    )
    expect(JSON.stringify(lineage)).not.toContain('hidden_col')
    expect(JSON.stringify(lineage)).not.toContain('CREATE TABLE')
  })

  it('never includes a prompt, plan step, or model reasoning', () => {
    const lineage = buildLineage(
      askState({
        database: 'db1',
        query_plan: ['Step 1: think about joining Orders to Customers', 'Step 2: aggregate'],
        attempt_history: [],
      }),
    )
    expect(JSON.stringify(lineage)).not.toContain('think about')
    expect(JSON.stringify(lineage)).not.toContain('Step 1')
  })

  it('never includes a raw error string from the retry history', () => {
    const lineage = buildLineage(
      askState({
        database: 'db1',
        error_history: ["pyodbc.ProgrammingError: login failed for user 'sa'"],
      }),
    )
    expect(JSON.stringify(lineage)).not.toContain('login failed')
  })
})

describe('sanitizeLineageText', () => {
  it('collapses whitespace and strips control characters', () => {
    expect(sanitizeLineageText('a\u0000b\n\n  c')).toBe('a b c')
  })

  it('truncates a very long value with an ellipsis', () => {
    const result = sanitizeLineageText('x'.repeat(500), 20)
    expect(result).toHaveLength(20)
    expect(result.endsWith('…')).toBe(true)
  })

  it('leaves a short value untouched, including unicode', () => {
    expect(sanitizeLineageText('Café ☕ Münster')).toBe('Café ☕ Münster')
  })
})
