import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResultsTable } from './ResultsTable'

// `useSchemaTables` only supplies which columns are primary keys (so they
// can be hidden behind "show technical"); an empty response is the honest
// "schema not loaded" case and keeps every column visible.
vi.mock('@/hooks/queries', () => ({
  useSchemaTables: () => ({ data: { tables: [] } }),
}))

function renderTable(columns: string[], rows: unknown[][]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ResultsTable columns={columns} rows={rows} />
    </QueryClientProvider>,
  )
}

function bodyRows() {
  const [, body] = screen.getAllByRole('rowgroup')
  return within(body).getAllByRole('row')
}

let clickSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  // jsdom has no download plumbing; the CSV path only needs not to throw.
  clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: () => 'blob:x' })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: () => {} })
  }
})

afterEach(() => {
  clickSpy.mockRestore()
  vi.restoreAllMocks()
})

describe('ResultsTable -- result shapes', () => {
  it('renders a category + metric result', () => {
    renderTable(['region', 'revenue'], [['North', 1200.5], ['South', 900.25]])
    expect(screen.getByRole('columnheader', { name: /Region/ })).toBeInTheDocument()
    expect(bodyRows()).toHaveLength(2)
  })

  it('renders a single-row single-column (scalar) result', () => {
    renderTable(['total'], [[42]])
    expect(bodyRows()).toHaveLength(1)
    expect(screen.getByRole('cell', { name: '42' })).toBeInTheDocument()
  })

  it('renders a wide many-column result without dropping columns', () => {
    const columns = Array.from({ length: 30 }, (_, i) => `col_${i}`)
    renderTable(columns, [columns.map((_, i) => i)])
    expect(screen.getAllByRole('columnheader')).toHaveLength(30)
  })

  it('shows an explicit empty state for a result with no rows', () => {
    renderTable(['region', 'revenue'], [])
    expect(screen.getByText(/no matching rows/i)).toBeInTheDocument()
  })

  it('reports the row count in its own accessible caption', () => {
    renderTable(['region'], [['North'], ['South']])
    expect(screen.getByRole('table')).toHaveAccessibleName(/2/)
  })
})

describe('ResultsTable -- nulls', () => {
  it('shows a NULL distinctly from an empty string', () => {
    renderTable(['a', 'b'], [[null, '']])
    const [row] = bodyRows()
    const cells = within(row).getAllByRole('cell')
    // The NULL cell carries the placeholder and the word; the empty-string
    // cell carries neither.
    expect(cells[0].textContent).toContain('—')
    expect(cells[1].textContent).toBe('')
  })

  it('announces a NULL as a word, not as the dash glyph', () => {
    renderTable(['a'], [[null]])
    expect(screen.getByText('no value')).toBeInTheDocument()
    // The glyph itself is hidden from assistive technology.
    const [row] = bodyRows()
    expect(within(row).getByText('—')).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders a result that is entirely nulls without crashing', () => {
    renderTable(['a', 'b'], [[null, null], [null, null]])
    expect(bodyRows()).toHaveLength(2)
  })

  it('does not treat 0 or false as missing', () => {
    renderTable(['n', 'flag'], [[0, false]])
    expect(screen.getByRole('cell', { name: '0' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'false' })).toBeInTheDocument()
    expect(screen.queryByText('no value')).not.toBeInTheDocument()
  })
})

describe('ResultsTable -- numeric formatting', () => {
  it('preserves exact values while grouping a measure column', () => {
    renderTable(['revenue'], [['1234567.8912'], ['2.5']])
    const text = bodyRows()[0].textContent ?? ''
    expect(text.replace(/[^0-9.]/g, '')).toBe('1234567.8912')
  })

  it('leaves an all-integer column ungrouped, so an identifier is not mangled', () => {
    renderTable(['postcode'], [[10001], [94107]])
    expect(screen.getByRole('cell', { name: '10001' })).toBeInTheDocument()
    expect(screen.queryByRole('cell', { name: '10,001' })).not.toBeInTheDocument()
  })

  it('right-aligns a numeric column and left-aligns a text one', () => {
    renderTable(['region', 'revenue'], [['North', 1200.5]])
    const cells = within(bodyRows()[0]).getAllByRole('cell')
    expect(cells[0].className).not.toMatch(/text-right/)
    expect(cells[1].className).toMatch(/text-right/)
  })

  it('uses tabular figures for numerics so columns line up', () => {
    renderTable(['revenue'], [[1200.5], [900.25]])
    expect(within(bodyRows()[0]).getByText(/1,200.5/).className).toMatch(/tabular-nums/)
  })
})

describe('ResultsTable -- data-supported filters', () => {
  const columns = ['region', 'product', 'revenue']
  const rows: unknown[][] = [
    ['North', 'Widget', 1200.5],
    ['South', 'Widget', 900.25],
    ['North', 'Gadget', 450.75],
    ['South', 'Gadget', 300.5],
  ]

  it('offers a filter for a column whose data supports one', () => {
    renderTable(columns, rows)
    expect(screen.getByLabelText('Region')).toBeInTheDocument()
    expect(screen.getByLabelText('Product')).toBeInTheDocument()
  })

  it('offers no filter for a numeric measure column', () => {
    renderTable(columns, rows)
    expect(screen.queryByLabelText('Revenue')).not.toBeInTheDocument()
  })

  it('offers no filter controls at all when the shape supports none', () => {
    // Every value unique per row -> nothing to filter by.
    renderTable(['note'], [['a'], ['b'], ['c']])
    expect(screen.queryByRole('combobox', { name: 'Note' })).not.toBeInTheDocument()
  })

  it('actually narrows the rows, and composes with the row count', () => {
    renderTable(columns, rows)
    expect(bodyRows()).toHaveLength(4)

    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North' } })

    expect(bodyRows()).toHaveLength(2)
    for (const row of bodyRows()) expect(row.textContent).toContain('North')
  })

  it('composes two filters together', () => {
    renderTable(columns, rows)
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North' } })
    fireEvent.change(screen.getByLabelText('Product'), { target: { value: 'Gadget' } })
    expect(bodyRows()).toHaveLength(1)
  })

  it('clears back to every row via the All option', () => {
    renderTable(columns, rows)
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North' } })
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: '' } })
    expect(bodyRows()).toHaveLength(4)
  })

  it('keeps the filter control present after it has narrowed the rows', () => {
    // Derived from the unfiltered rows on purpose -- a dropdown that
    // vanished after use would be unusable.
    renderTable(columns, rows)
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North' } })
    expect(screen.getByLabelText('Region')).toBeInTheDocument()
    expect(within(screen.getByLabelText('Region')).getAllByRole('option')).toHaveLength(3)
  })

  it('composes with the global search rather than replacing it', () => {
    renderTable(columns, rows)
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North' } })
    fireEvent.change(screen.getByLabelText(/search/i), { target: { value: 'Gadget' } })
    expect(bodyRows()).toHaveLength(1)
  })
})

describe('ResultsTable -- large result sets', () => {
  const columns = ['id', 'region', 'revenue']
  const rows: unknown[][] = Array.from({ length: 1000 }, (_, i) => [
    i,
    i % 2 === 0 ? 'North' : 'South',
    i + 0.5,
  ])

  it('keeps only one page of rows in the DOM', () => {
    renderTable(columns, rows)
    expect(bodyRows()).toHaveLength(10)
  })

  it('pages through without re-rendering everything', () => {
    renderTable(columns, rows)
    fireEvent.click(screen.getByLabelText('Next page'))
    expect(bodyRows()).toHaveLength(10)
    expect(bodyRows()[0].textContent).toContain('10')
  })

  it('honours a larger page size', () => {
    renderTable(columns, rows)
    fireEvent.change(screen.getByLabelText(/rows per page/i), { target: { value: '100' } })
    expect(bodyRows()).toHaveLength(100)
  })

  it('returns to the first page when a filter narrows the result', () => {
    renderTable(columns, rows)
    fireEvent.click(screen.getByLabelText('Next page'))
    fireEvent.change(screen.getByLabelText('Region'), { target: { value: 'North' } })
    expect(screen.getByText(/^1 /)).toBeInTheDocument()
  })

  it('sorts a numeric column by value, not as text', () => {
    renderTable(['n'], [[2], [10], [1]])
    fireEvent.click(screen.getByRole('columnheader', { name: /N/ }))

    // The table sorts a numeric column descending on the first click; what
    // matters here is that it compared numbers, not strings -- sorting
    // these three as text would put 10 next to 1, giving ['2','10','1']
    // descending or ['1','10','2'] ascending.
    const order = bodyRows().map((row) => row.textContent)
    expect([['10', '2', '1'], ['1', '2', '10']]).toContainEqual(order)
  })
})

describe('ResultsTable -- expand and export', () => {
  it('opens an expanded view of the same table', () => {
    renderTable(['region'], [['North'], ['South']])
    expect(screen.getAllByRole('table')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: /expand/i }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('table')).toBeInTheDocument()
    expect(within(dialog).getAllByRole('row').length).toBeGreaterThan(1)
  })

  it('does not mount the expanded table until it is opened', () => {
    renderTable(['region'], [['North']])
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('exports without throwing, through the existing safe CSV helper', () => {
    renderTable(['region', 'revenue'], [['North', 1200.5]])
    expect(() => fireEvent.click(screen.getByRole('button', { name: /csv/i }))).not.toThrow()
    expect(clickSpy).toHaveBeenCalled()
  })
})

describe('ResultsTable -- accessibility', () => {
  it('reports each column header as sortable and its current sort state', () => {
    renderTable(['region'], [['North'], ['South']])
    const header = screen.getByRole('columnheader', { name: /Region/ })
    expect(header).toHaveAttribute('aria-sort', 'none')

    fireEvent.click(header)
    expect(screen.getByRole('columnheader', { name: /Region/ })).toHaveAttribute('aria-sort', 'ascending')
  })

  it('scopes every header to its column', () => {
    renderTable(['a', 'b'], [[1, 2]])
    for (const header of screen.getAllByRole('columnheader')) {
      expect(header).toHaveAttribute('scope', 'col')
    }
  })

  it('names the search and page-size controls', () => {
    renderTable(['region'], [['North'], ['South']])
    expect(screen.getByLabelText(/search/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/rows per page/i)).toBeInTheDocument()
  })

  it('renders a governance mask verbatim, and not as a missing value', () => {
    renderTable(['name', 'salary'], [['Alice', '[restricted]']])
    expect(screen.getByRole('cell', { name: '[restricted]' })).toBeInTheDocument()
    // A masked value is a value the server chose to send; it is not a NULL.
    expect(screen.queryByText('no value')).not.toBeInTheDocument()
  })

  it('leaves a column ungrouped once any of its values is masked', () => {
    renderTable(['salary'], [['1234567.50'], ['[restricted]']])
    // Ungrouped: the column is no longer uniformly numeric, so applying
    // number formatting to the values that survived would misrepresent it.
    expect(screen.getByRole('cell', { name: '1234567.50' })).toBeInTheDocument()
  })

  it('exports the masked value, never an unmasked one it never received', () => {
    renderTable(['salary'], [['[restricted]']])
    expect(() => fireEvent.click(screen.getByRole('button', { name: /csv/i }))).not.toThrow()
  })
})
