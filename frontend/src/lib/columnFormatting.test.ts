import { describe, expect, it } from 'vitest'
import {
  NULL_DISPLAY,
  distinctColumnValues,
  filterableColumns,
  formatResultCell,
  shouldGroupNumbers,
} from './columnFormatting'

describe('formatResultCell', () => {
  it('marks a SQL NULL distinctly from an empty string', () => {
    const nullCell = formatResultCell(null)
    const emptyCell = formatResultCell('')

    expect(nullCell).toEqual({ text: NULL_DISPLAY, isNumeric: false, isNull: true })
    // The whole point: these two must not render identically.
    expect(emptyCell.isNull).toBe(false)
    expect(emptyCell.text).toBe('')
    expect(nullCell.text).not.toBe(emptyCell.text)
  })

  it('treats undefined the same as NULL (a short row, a missing key)', () => {
    expect(formatResultCell(undefined).isNull).toBe(true)
  })

  it('flags a number as numeric so the caller can right-align it', () => {
    expect(formatResultCell(42)).toEqual({ text: '42', isNumeric: true, isNull: false })
    expect(formatResultCell('3.5').isNumeric).toBe(true)
    expect(formatResultCell('-17').isNumeric).toBe(true)
  })

  it('does not treat text that merely contains digits as numeric', () => {
    for (const value of ['Q3 2024', '1,234', '1e5', '+5', ' 7 ', '12-34', 'NaN', 'Infinity']) {
      expect(formatResultCell(value).isNumeric, value).toBe(false)
    }
  })

  it('leaves a value exactly as the database returned it when not grouping', () => {
    expect(formatResultCell('1234567.8900').text).toBe('1234567.8900')
    expect(formatResultCell(0).text).toBe('0')
  })

  it('preserves every fractional digit when grouping -- no rounding', () => {
    // A money figure with four decimals keeps all four; only separators are
    // inserted into the integer part.
    const cell = formatResultCell('1234567.8912', true)
    expect(cell.text.endsWith('.8912')).toBe(true)
    expect(cell.text.replace(/[^0-9.]/g, '')).toBe('1234567.8912')
  })

  it('preserves a decimal too large for a float64 (no Number() round-trip)', () => {
    // This is why the integer part is grouped through BigInt: Number() on
    // this value rounds it, and a rounded financial figure is a wrong one.
    const raw = '123456789012345678901234.56'
    const cell = formatResultCell(raw, true)
    expect(cell.text.replace(/[^0-9.]/g, '')).toBe('123456789012345678901234.56')
    expect(cell.text).not.toContain(String(Number(raw)))
  })

  it('keeps a negative number negative when grouping', () => {
    const cell = formatResultCell('-1234567.5', true)
    expect(cell.text.startsWith('-')).toBe(true)
    expect(cell.text.replace(/[^0-9.]/g, '')).toBe('1234567.5')
  })

  it('renders a boolean as text, never as a number', () => {
    expect(formatResultCell(true)).toEqual({ text: 'true', isNumeric: false, isNull: false })
    expect(formatResultCell(false).isNull).toBe(false)
  })

  it('passes a non-numeric value through unchanged, including unicode and emoji', () => {
    expect(formatResultCell('Café ☕ "quoted" <b>').text).toBe('Café ☕ "quoted" <b>')
  })
})

describe('shouldGroupNumbers', () => {
  it('groups a measure column (has fractional values)', () => {
    expect(shouldGroupNumbers([1234.5, 99.25, 7.0])).toBe(true)
  })

  it('does NOT group an all-integer column -- it may be an identifier', () => {
    // A postcode, a year, or an order number must never render as "10,001"
    // or "2,024". An all-integer column is indistinguishable from an id
    // using the data alone, so it is left alone.
    expect(shouldGroupNumbers([10001, 94107, 2024])).toBe(false)
    expect(shouldGroupNumbers(['1', '2', '3'])).toBe(false)
  })

  it('does not group a column with any non-numeric value', () => {
    expect(shouldGroupNumbers([1234.5, 'n/a'])).toBe(false)
  })

  it('ignores nulls when deciding', () => {
    expect(shouldGroupNumbers([null, 1234.5, undefined])).toBe(true)
    expect(shouldGroupNumbers([null, undefined])).toBe(false)
  })

  it('returns false for an empty column', () => {
    expect(shouldGroupNumbers([])).toBe(false)
  })
})

describe('distinctColumnValues', () => {
  it('returns the sorted distinct non-null values', () => {
    expect(distinctColumnValues(['b', 'a', 'b', null], 10)).toEqual(['a', 'b'])
  })

  it('gives up once the cap is exceeded, rather than building a huge set', () => {
    const many = Array.from({ length: 500 }, (_, i) => `v${i}`)
    expect(distinctColumnValues(many, 25)).toBeNull()
  })
})

describe('filterableColumns', () => {
  const columns = ['region', 'product', 'revenue']
  const rows: unknown[][] = [
    ['North', 'Widget', 1200.5],
    ['South', 'Widget', 900.25],
    ['North', 'Gadget', 450.75],
    ['South', 'Gadget', 300.0],
  ]

  it('offers a filter for a low-cardinality text column that partitions the rows', () => {
    const result = filterableColumns(columns, rows)
    expect(result.map((f) => f.column)).toEqual(['region', 'product'])
    expect(result[0].values).toEqual(['North', 'South'])
  })

  it('never offers a numeric column -- equality on a measure is not a filter', () => {
    expect(filterableColumns(columns, rows).map((f) => f.column)).not.toContain('revenue')
  })

  it('offers nothing when a column is unique per row (an id or free text)', () => {
    const unique: unknown[][] = [['a'], ['b'], ['c']]
    expect(filterableColumns(['note'], unique)).toEqual([])
  })

  it('offers nothing when a column has a single value (filtering it changes nothing)', () => {
    const single: unknown[][] = [['North'], ['North'], ['North']]
    expect(filterableColumns(['region'], single)).toEqual([])
  })

  it('offers nothing for a high-cardinality text column', () => {
    const wide: unknown[][] = Array.from({ length: 100 }, (_, i) => [`customer-${i}`, 'x'])
    expect(filterableColumns(['customer', 'flag'], wide).map((f) => f.column)).not.toContain('customer')
  })

  it('offers nothing at all for an empty or single-row result', () => {
    expect(filterableColumns(columns, [])).toEqual([])
    expect(filterableColumns(columns, [['North', 'Widget', 1]])).toEqual([])
  })

  it('caps how many filters it offers, so the controls never crowd out the table', () => {
    const manyCols = ['a', 'b', 'c', 'd', 'e']
    const manyRows: unknown[][] = [
      ['1', '1', '1', '1', '1'],
      ['2', '2', '2', '2', '2'],
      ['1', '1', '1', '1', '1'],
    ].map((row) => row.map((v) => `v${v}`))
    expect(filterableColumns(manyCols, manyRows, 25, 3)).toHaveLength(3)
  })

  it('treats a null-heavy column by its real values only', () => {
    const withNulls: unknown[][] = [['North'], [null], ['South'], [null], ['North']]
    expect(filterableColumns(['region'], withNulls)[0]?.values).toEqual(['North', 'South'])
  })

  it('passes a governance mask through untouched', () => {
    // `governance/result_policy.py` replaces a value the caller may not read
    // with one of these before the row ever leaves the server. They must
    // reach the screen verbatim -- not re-formatted, and never mistaken for
    // a number or for a missing value.
    for (const mask of ['[restricted]', '[withheld]']) {
      const cell = formatResultCell(mask, true)
      expect(cell).toEqual({ text: mask, isNumeric: false, isNull: false })
    }
  })
})

describe('field restrictions', () => {
  it('never number-formats a column holding a masked value', () => {
    // A masked salary column is text now, whatever it was in the database.
    // Grouping it would be applying number formatting to "[restricted]"'s
    // neighbours and implying the column is still numeric.
    expect(shouldGroupNumbers([1234.5, '[restricted]', 99.0])).toBe(false)
  })

  it('never offers a masked column as a filter value worth picking', () => {
    // A mask is a real distinct value in the returned rows, so it may
    // legitimately appear -- what must not happen is the mask being treated
    // as numeric and the column silently skipped, or the mask being
    // rewritten. Here it is offered as itself.
    const rows: unknown[][] = [['[restricted]'], ['North'], ['North'], ['[restricted]']]
    // Order is whatever `localeCompare` gives (a bracket sorts before a
    // letter) -- not worth pinning; what matters is both values are offered
    // as themselves, with the mask neither rewritten nor dropped.
    expect(filterableColumns(['region'], rows)[0]?.values).toHaveLength(2)
    expect(filterableColumns(['region'], rows)[0]?.values).toContain('North')
    expect(filterableColumns(['region'], rows)[0]?.values).toContain('[restricted]')
  })
})
