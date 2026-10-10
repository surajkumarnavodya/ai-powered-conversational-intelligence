import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { preparedChartToTable, type PreparedChart } from '@/lib/chartEngine'
import { ChartDataTable } from './ChartDataTable'

function prepared(overrides: Partial<PreparedChart> = {}): PreparedChart {
  return {
    chartJsType: 'bar',
    labels: ['North', 'South'],
    datasets: [{ label: 'Revenue', data: [1200.5, 900.25], color: '#000' }],
    xTitle: 'Region',
    yTitle: 'Revenue',
    notices: [],
    ...overrides,
  }
}

describe('preparedChartToTable', () => {
  it('derives one row per chart label, with the axis title as the first header', () => {
    expect(preparedChartToTable(prepared())).toEqual({
      headers: ['Region', 'Revenue'],
      rows: [
        ['North', '1,200.5'],
        ['South', '900.25'],
      ],
    })
  })

  it('names the label column generically when the chart has no x-axis title', () => {
    expect(preparedChartToTable(prepared({ xTitle: '' })).headers[0]).toBe('Category')
  })

  it('gives every dataset its own column, for a multi-series chart', () => {
    const table = preparedChartToTable(
      prepared({
        datasets: [
          { label: '2024', data: [10, 20], color: '#1' },
          { label: '2025', data: [11, 22], color: '#2' },
        ],
      }),
    )
    expect(table.headers).toEqual(['Region', '2024', '2025'])
    expect(table.rows).toEqual([
      ['North', '10', '11'],
      ['South', '20', '22'],
    ])
  })

  it('leaves a gap in the series empty rather than inventing a zero', () => {
    const table = preparedChartToTable(
      prepared({
        labels: ['North', 'South', 'East'],
        datasets: [{ label: 'Revenue', data: [10, Number.NaN, 30], color: '#1' }],
      }),
    )
    expect(table.rows[1]).toEqual(['South', ''])
  })

  it('formats with the same formatter the chart axes use, so the two agree', () => {
    const asCurrency = preparedChartToTable(prepared(), 'currency')
    expect(asCurrency.rows[0][1]).toContain('1,200.5')
    expect(asCurrency.rows[0][1]).not.toBe('1200.5')
  })

  it('handles a scatter chart, whose points carry their own x', () => {
    const table = preparedChartToTable(
      prepared({
        chartJsType: 'scatter',
        labels: [],
        xTitle: 'Units',
        datasets: [
          {
            label: 'Revenue',
            data: [
              { x: 1, y: 100 },
              { x: 2, y: 200 },
            ],
            color: '#1',
          },
        ],
      }),
    )
    expect(table.headers).toEqual(['Units', 'Revenue'])
    expect(table.rows).toEqual([
      ['1', '100'],
      ['2', '200'],
    ])
  })

  it('returns no rows for a chart with no plotted values', () => {
    expect(preparedChartToTable(prepared({ labels: [], datasets: [] })).rows).toEqual([])
  })

  it('stays in step with a top-N cut already applied to the prepared chart', () => {
    // The table reads the prepared data, not the raw rows -- so whatever
    // top-N/sort/grouping the chart applied is already reflected and cannot
    // drift into a second implementation here.
    const table = preparedChartToTable(
      prepared({ labels: ['A', 'B'], datasets: [{ label: 'v', data: [2, 1], color: '#1' }] }),
    )
    expect(table.rows).toHaveLength(2)
  })
})

describe('ChartDataTable', () => {
  it('renders the chart data as a real table, so a screen reader can reach the values', () => {
    render(<ChartDataTable prepared={prepared()} numberFormat="plain" />)

    const table = screen.getByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'Region' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Revenue' })).toBeInTheDocument()
    expect(within(table).getByRole('cell', { name: 'North' })).toBeInTheDocument()
    expect(within(table).getByRole('cell', { name: '1,200.5' })).toBeInTheDocument()
  })

  it('gives the table an accessible caption tying it to the chart above', () => {
    render(<ChartDataTable prepared={prepared()} numberFormat="plain" caption="Revenue by region" />)
    expect(screen.getByRole('table')).toHaveAccessibleName('Revenue by region')
  })

  it('falls back to a generic caption when the chart has no title', () => {
    render(<ChartDataTable prepared={prepared()} numberFormat="plain" />)
    expect(screen.getByRole('table').querySelector('caption')?.textContent).toBeTruthy()
  })

  it('is collapsed by default but still present in the accessibility tree', () => {
    const { container } = render(<ChartDataTable prepared={prepared()} numberFormat="plain" />)
    const details = container.querySelector('details')
    // Not `open` -- a sighted reader already has the chart.
    expect(details).not.toHaveAttribute('open')
    // The markup is real, not conditionally mounted, so assistive
    // technology and in-page find both reach the values.
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('uses a native details/summary, so it is keyboard-operable with no JS', () => {
    const { container } = render(<ChartDataTable prepared={prepared()} numberFormat="plain" />)
    expect(container.querySelector('details > summary')).not.toBeNull()
  })

  it('renders nothing at all when there is no data to describe', () => {
    const { container } = render(
      <ChartDataTable prepared={prepared({ labels: [], datasets: [] })} numberFormat="plain" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('marks value columns as headers scoped to their column', () => {
    render(<ChartDataTable prepared={prepared()} numberFormat="plain" />)
    for (const header of screen.getAllByRole('columnheader')) {
      expect(header).toHaveAttribute('scope', 'col')
    }
  })

  it('stays readable for a large series without crashing', () => {
    const labels = Array.from({ length: 500 }, (_, i) => `L${i}`)
    render(
      <ChartDataTable
        prepared={prepared({
          labels,
          datasets: [{ label: 'v', data: labels.map((_, i) => i), color: '#1' }],
        })}
        numberFormat="plain"
      />,
    )
    expect(screen.getAllByRole('row')).toHaveLength(501) // 500 data + 1 header
  })
})
