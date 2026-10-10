import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { askState } from '@/test/analyticsFixtures'
import type { AskResponse } from '@/lib/types'
import { SqlIntelligencePanel } from './SqlIntelligencePanel'

function renderPanel(state: Partial<AskResponse>, showDatabase = false) {
  return render(
    <MemoryRouter>
      <SqlIntelligencePanel state={askState(state)} showDatabase={showDatabase} />
    </MemoryRouter>,
  )
}

describe('SqlIntelligencePanel', () => {
  it('renders nothing when there is no intelligence to show', () => {
    // A pure document/web answer has no plan, no retrieved schema and no
    // governed metric -- an empty expander would be pure noise.
    const { container } = renderPanel({
      query_plan: null,
      schema_tables: [],
      governing_metrics: [],
      database: null,
      model: null,
    })
    expect(container).toBeEmptyDOMElement()
  })

  it('counts the sections it actually has in the collapsed title', () => {
    renderPanel({
      query_plan: ['Step one', 'Step two'],
      schema_tables: [{ table_name: 'sales', ddl: 'CREATE TABLE sales ()', similarity_score: 0.9 }],
      governing_metrics: [],
      model: 'llama3.1:8b',
    })
    // plan + schema + details = 3
    expect(screen.getByText(/SQL intelligence \(3\)/)).toBeInTheDocument()
  })

  it('shows the query plan steps', () => {
    renderPanel({ query_plan: ['Aggregate revenue by category'] })
    expect(screen.getByText('Aggregate revenue by category')).toBeInTheDocument()
  })

  it('shows the retrieved schema with its similarity score', () => {
    renderPanel({
      schema_tables: [{ table_name: 'sales', ddl: 'CREATE TABLE sales (id int)', similarity_score: 0.87 }],
    })
    expect(screen.getByText('sales')).toBeInTheDocument()
    expect(screen.getByText(/similarity 0\.87/)).toBeInTheDocument()
    expect(screen.getByText(/CREATE TABLE sales/)).toBeInTheDocument()
  })

  it('names the governed metrics the SQL had to use', () => {
    renderPanel({
      governing_metrics: [
        { business_name: 'Net Revenue', approved_expression: 'SUM(amount)', aggregation: 'sum', text: 'x' },
      ],
    })
    expect(screen.getByText('Governed metrics applied')).toBeInTheDocument()
    expect(screen.getByText('Net Revenue')).toBeInTheDocument()
  })

  it('names the database only when more than one is configured', () => {
    renderPanel({ database: 'hr', model: 'llama3.1:8b' }, false)
    expect(screen.queryByText(/hr/)).not.toBeInTheDocument()

    renderPanel({ database: 'hr', model: 'llama3.1:8b' }, true)
    expect(screen.getByText('hr')).toBeInTheDocument()
  })

  it('links to the lineage explorer without pre-seeding its state', () => {
    renderPanel({ query_plan: ['Step one'] })
    const link = screen.getByRole('link', { name: /lineage explorer/i })
    // A plain link, not a deep link: that page resolves its own database
    // selection, so a query string would imply state it does not read.
    expect(link).toHaveAttribute('href', '/data-lineage')
  })

  it('exposes no prompt text or model reasoning', () => {
    const { container } = renderPanel({
      query_plan: ['Aggregate revenue'],
      schema_tables: [{ table_name: 'sales', ddl: 'CREATE TABLE sales ()', similarity_score: 0.9 }],
      model: 'llama3.1:8b',
    })
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/system prompt|you are an|chain of thought|reasoning:/i)
  })
})
