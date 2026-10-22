import { describe, expect, it } from 'vitest'
import {
  categoryColumns,
  createDefaultChartOptions,
  defaultSeriesForType,
  inferColumnRoles,
  looksLikeKeyColumn,
  measureColumns,
  prepareChart,
  type ServerColumnSemantics,
} from '@/lib/chartEngine'

/**
 * Axis binding: a key is never the y-axis.
 *
 * The reported bug: "total sales by product" charted **ProductKey** as
 * the measure. `defaultSeriesForType` took `numeric[0]`, and a surrogate
 * key is numeric, so whichever numeric column the SQL happened to return
 * first became the value axis.
 *
 * Each scenario below is asserted twice -- once with server semantics
 * (the real path, where live schema introspection knows the keys) and
 * once without (the fallback, where only name patterns are available) --
 * because a regression in either path reproduces the bug.
 */

function semantics(
  name: string,
  overrides: Partial<ServerColumnSemantics> = {},
): ServerColumnSemantics {
  return {
    name,
    label: name,
    data_type: 'numeric',
    semantic_type: 'measure',
    number_format: 'plain',
    suitable_for_x: false,
    suitable_for_y: true,
    hidden_by_default: false,
    measure_priority: 10,
    ...overrides,
  }
}

const KEY = (name: string): ServerColumnSemantics =>
  semantics(name, {
    semantic_type: 'key',
    suitable_for_x: false,
    suitable_for_y: false,
    hidden_by_default: true,
    measure_priority: 0,
  })

const DIMENSION = (name: string, label = name): ServerColumnSemantics =>
  semantics(name, {
    label,
    data_type: 'text',
    semantic_type: 'dimension',
    suitable_for_x: true,
    suitable_for_y: false,
    measure_priority: 0,
  })

const MEASURE = (name: string, label: string, priority: number): ServerColumnSemantics =>
  semantics(name, { label, measure_priority: priority })

describe('the reported bug: a key must never be the y-axis', () => {
  const columns = ['ProductKey', 'TotalSales']
  const rows: unknown[][] = [
    [310, 1234.5],
    [311, 987.25],
  ]
  const types = { ProductKey: 'numeric', TotalSales: 'numeric' }

  it('with server semantics, the y-axis is the measure, not the key', () => {
    const infos = inferColumnRoles(columns, rows, types, [KEY('ProductKey'), MEASURE('TotalSales', 'Total Sales', 100)])
    const series = defaultSeriesForType('bar', infos)
    expect(series.yColumns).toEqual(['TotalSales'])
    expect(series.yColumns).not.toContain('ProductKey')
  })

  it('without server semantics, the name fallback still excludes the key', () => {
    const infos = inferColumnRoles(columns, rows, types)
    const series = defaultSeriesForType('bar', infos)
    expect(series.yColumns).toEqual(['TotalSales'])
  })

  it('the key is not silently promoted to the x-axis either', () => {
    const infos = inferColumnRoles(columns, rows, types)
    expect(defaultSeriesForType('bar', infos).xColumn).not.toBe('ProductKey')
  })

  it('measureColumns never returns a key', () => {
    const infos = inferColumnRoles(columns, rows, types)
    expect(measureColumns(infos).map((c) => c.name)).toEqual(['TotalSales'])
  })
})

describe('"Total sales by product"', () => {
  const columns = ['Product', 'TotalSales']
  const rows: unknown[][] = [
    ['Mountain-200 Black, 46', 1234.5],
    ['Road-150 Red, 62', 987.25],
  ]
  const types = { Product: 'text', TotalSales: 'numeric' }
  const server = [DIMENSION('Product', 'Product'), MEASURE('TotalSales', 'Total Sales', 100)]

  it('maps product name to x and total sales to y', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    const series = defaultSeriesForType('bar', infos)
    expect(series.xColumn).toBe('Product')
    expect(series.yColumns).toEqual(['TotalSales'])
  })

  it('the y-axis title is the friendly measure label', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    expect(measureColumns(infos)[0].label).toBe('Total Sales')
  })

  it('chart values are the real measure values, not re-scaled', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    const options = createDefaultChartOptions(infos, rows)
    expect(options).not.toBeNull()
    const prepared = prepareChart(infos, rows, options!)
    expect(prepared).not.toBeNull()
    const values = prepared!.datasets[0].data
    expect(values).toEqual([1234.5, 987.25])
  })
})

describe('"Which customer bought frequently?"', () => {
  const columns = ['Customer', 'TotalOrders', 'TotalSales']
  const rows: unknown[][] = [
    ['Ann Lee', 25, 4000],
    ['Bo Chen', 18, 9000],
  ]
  const types = { Customer: 'text', TotalOrders: 'numeric', TotalSales: 'numeric' }

  it('defaults to the measure the query ranked by, not the richest-sounding one', () => {
    // The server boosts TotalOrders because the SQL said
    // `ORDER BY TotalOrders DESC` -- which is there because the question
    // asked who bought most *frequently*.
    const infos = inferColumnRoles(columns, rows, types, [
      DIMENSION('Customer', 'Customer'),
      MEASURE('TotalOrders', 'Total Orders', 1070),
      MEASURE('TotalSales', 'Total Sales', 100),
    ])
    const series = defaultSeriesForType('bar', infos)
    expect(series.xColumn).toBe('Customer')
    expect(series.yColumns).toEqual(['TotalOrders'])
  })

  it('both measures stay available for the user to switch between', () => {
    const infos = inferColumnRoles(columns, rows, types, [
      DIMENSION('Customer', 'Customer'),
      MEASURE('TotalOrders', 'Total Orders', 1070),
      MEASURE('TotalSales', 'Total Sales', 100),
    ])
    expect(measureColumns(infos).map((c) => c.name)).toEqual(['TotalOrders', 'TotalSales'])
  })

  it('no CustomerKey on the y-axis when the key is returned too', () => {
    const withKey = ['CustomerKey', ...columns]
    const withKeyRows = rows.map((r, i) => [100 + i, ...r])
    const infos = inferColumnRoles(withKey, withKeyRows, { ...types, CustomerKey: 'numeric' })
    expect(defaultSeriesForType('bar', infos).yColumns).not.toContain('CustomerKey')
  })
})

describe('"Year-wise sales"', () => {
  const columns = ['CalendarYear', 'TotalSales']
  const rows: unknown[][] = [
    [2023, 100],
    [2024, 150],
  ]
  const types = { CalendarYear: 'numeric', TotalSales: 'numeric' }

  it('a numeric year is the x-axis, never the measure', () => {
    const infos = inferColumnRoles(columns, rows, types)
    const series = defaultSeriesForType('bar', infos)
    expect(series.xColumn).toBe('CalendarYear')
    expect(series.yColumns).toEqual(['TotalSales'])
  })

  it('a year is excluded from measures even though it is numeric', () => {
    const infos = inferColumnRoles(columns, rows, types)
    expect(measureColumns(infos).map((c) => c.name)).toEqual(['TotalSales'])
    expect(categoryColumns(infos).map((c) => c.name)).toContain('CalendarYear')
  })
})

describe('"Top 10 products by revenue and quantity"', () => {
  const columns = ['Product', 'OrderQuantity', 'Revenue']
  const rows: unknown[][] = [
    ['A', 5, 500],
    ['B', 9, 300],
  ]
  const types = { Product: 'text', OrderQuantity: 'numeric', Revenue: 'numeric' }
  const server = [
    DIMENSION('Product', 'Product'),
    MEASURE('OrderQuantity', 'Order Quantity', 80),
    MEASURE('Revenue', 'Revenue', 100),
  ]

  it('defaults to revenue even though quantity is returned first', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    expect(defaultSeriesForType('bar', infos).yColumns).toEqual(['Revenue'])
  })

  it('offers both measures so the user can switch to quantity', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    expect(measureColumns(infos).map((c) => c.label)).toEqual(['Revenue', 'Order Quantity'])
  })

  it('switching the measure needs no new query -- only the options change', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    const options = createDefaultChartOptions(infos, rows)!
    const switched = { ...options, series: { ...options.series, yColumns: ['OrderQuantity'] } }
    const prepared = prepareChart(infos, rows, switched)
    expect(prepared!.datasets[0].data).toEqual([5, 9])
  })
})

describe('number formatting follows the plotted measure', () => {
  const columns = ['Product', 'TotalSales']
  const rows: unknown[][] = [['A', 10]]
  const types = { Product: 'text', TotalSales: 'numeric' }

  it('a currency measure defaults the chart to currency formatting', () => {
    const infos = inferColumnRoles(columns, rows, types, [
      DIMENSION('Product'),
      semantics('TotalSales', {
        label: 'Total Sales',
        semantic_type: 'currency',
        number_format: 'currency',
        measure_priority: 100,
      }),
    ])
    expect(createDefaultChartOptions(infos, rows)!.numberFormat).toBe('currency')
  })

  it("the server's 'integer' has no chart equivalent and renders plain", () => {
    const infos = inferColumnRoles(columns, rows, types, [
      DIMENSION('Product'),
      semantics('TotalSales', { number_format: 'integer', measure_priority: 70 }),
    ])
    expect(createDefaultChartOptions(infos, rows)!.numberFormat).toBe('plain')
  })

  it('without server semantics the format stays plain, as before', () => {
    const infos = inferColumnRoles(columns, rows, types)
    expect(createDefaultChartOptions(infos, rows)!.numberFormat).toBe('plain')
  })
})

describe('axis titles and legend entries use friendly labels', () => {
  const columns = ['Product', 'TotalSales']
  const rows: unknown[][] = [
    ['A', 10],
    ['B', 20],
  ]
  const types = { Product: 'text', TotalSales: 'numeric' }
  const server = [DIMENSION('Product', 'Product'), MEASURE('TotalSales', 'Total Sales', 100)]

  it('the y-axis title is "Total Sales", not the raw column name', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    const prepared = prepareChart(infos, rows, createDefaultChartOptions(infos, rows)!)
    expect(prepared!.yTitle).toBe('Total Sales')
    expect(prepared!.xTitle).toBe('Product')
  })

  it('the legend entry uses the friendly label too', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    const prepared = prepareChart(infos, rows, createDefaultChartOptions(infos, rows)!)
    expect(prepared!.datasets[0].label).toBe('Total Sales')
  })

  it('a pie chart labels its value series with the friendly measure name', () => {
    const infos = inferColumnRoles(columns, rows, types, server)
    const options = createDefaultChartOptions(infos, rows)!
    const prepared = prepareChart(infos, rows, { ...options, chartType: 'pie' })
    expect(prepared!.datasets[0].label).toBe('Total Sales')
    // Slice labels are the dimension, values are the measure.
    expect(prepared!.labels).toEqual(['A', 'B'])
    expect(prepared!.datasets[0].data).toEqual([10, 20])
  })

  it('falls back to the raw name when no semantics were sent', () => {
    const infos = inferColumnRoles(columns, rows, types)
    const prepared = prepareChart(infos, rows, createDefaultChartOptions(infos, rows)!)
    expect(prepared!.yTitle).toBe('TotalSales')
  })
})

describe('a result with only keys', () => {
  const columns = ['ProductKey', 'CustomerKey']
  const rows: unknown[][] = [
    [1, 10],
    [2, 20],
  ]
  const types = { ProductKey: 'numeric', CustomerKey: 'numeric' }

  it('offers no chart at all rather than charting identifiers', () => {
    const infos = inferColumnRoles(columns, rows, types)
    expect(measureColumns(infos)).toEqual([])
    expect(createDefaultChartOptions(infos, rows)).toBeNull()
  })
})

describe('key detection by name, for paths carrying no server semantics', () => {
  it.each(['ProductKey', 'CustomerKey', 'EmployeeKey', 'customer_id', 'OrderID'])(
    'treats %s as a key',
    (name) => {
      expect(looksLikeKeyColumn(name)).toBe(true)
    },
  )

  it.each(['TotalSales', 'Revenue', 'PostalCode', 'AccountNumber', 'Valid', 'Quantity'])(
    'does not treat %s as a key',
    (name) => {
      expect(looksLikeKeyColumn(name)).toBe(false)
    },
  )
})

describe('no regression for results that never had a key', () => {
  it('an ordinary category + measure result is unchanged', () => {
    const columns = ['Region', 'Total']
    const rows: unknown[][] = [
      ['East', 10],
      ['West', 20],
    ]
    const infos = inferColumnRoles(columns, rows, { Region: 'text', Total: 'numeric' })
    const series = defaultSeriesForType('bar', infos)
    expect(series.xColumn).toBe('Region')
    expect(series.yColumns).toEqual(['Total'])
  })

  it('scatter still plots one measure against another', () => {
    const columns = ['Revenue', 'Profit']
    const rows: unknown[][] = [
      [10, 1],
      [20, 3],
    ]
    const infos = inferColumnRoles(columns, rows, { Revenue: 'numeric', Profit: 'numeric' })
    const series = defaultSeriesForType('scatter', infos)
    expect(series.xColumn).toBe('Revenue')
    expect(series.yColumns).toEqual(['Profit'])
  })

  it('stacked bar still picks two measures, both non-key', () => {
    const columns = ['ProductKey', 'Product', 'Revenue', 'Profit']
    const rows: unknown[][] = [[1, 'A', 10, 2]]
    const infos = inferColumnRoles(columns, rows, {
      ProductKey: 'numeric',
      Product: 'text',
      Revenue: 'numeric',
      Profit: 'numeric',
    })
    const series = defaultSeriesForType('bar-stacked', infos)
    expect(series.yColumns).not.toContain('ProductKey')
    expect(series.yColumns).toEqual(['Revenue', 'Profit'])
    expect(series.xColumn).toBe('Product')
  })
})
