/**
 * Ports db/column_formatting.py's display-only transforms (human-readable
 * column labels, hiding surrogate key columns by default) so this table
 * matches the same choices the Python side makes. Pure display logic --
 * never touches the SQL that ran or the raw result data.
 *
 * Known, deliberate gap vs. the Python original: `is_probable_surrogate_key`
 * there also checks foreign-key constrained columns (via live schema
 * introspection); `/schema/tables` today only exposes each column's
 * `is_primary_key` flag, not FK info, so this port only uses the PK signal
 * plus the same name-suffix fallback. A column that's an FK but not a PK
 * anywhere may show up as "technical" here when it wouldn't on the Python
 * side -- a minor display-only discrepancy, not a data-safety one.
 */

const ABBREVIATION_EXPANSIONS: Record<string, string> = {
  cust: 'Customer',
  qty: 'Quantity',
  amt: 'Amount',
  desc: 'Description',
  num: 'Number',
}

const SURROGATE_KEY_SUFFIXES = new Set(['key', 'id'])

const CAMEL_BOUNDARY_RE = /(?<!^)(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])/g

function tokenize(rawName: string): string[] {
  const tokens: string[] = []
  for (const piece of rawName.split('_')) {
    if (!piece) continue
    tokens.push(...piece.replace(CAMEL_BOUNDARY_RE, ' ').split(/\s+/).filter(Boolean))
  }
  return tokens
}

function titleCase(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
}

export function formatColumnLabel(rawName: string): string {
  const tokens = tokenize(rawName)
  if (tokens.length === 0) {
    return rawName.replaceAll('_', ' ').replace(/\w\S*/g, titleCase)
  }
  return tokens.map((token) => ABBREVIATION_EXPANSIONS[token.toLowerCase()] ?? titleCase(token)).join(' ')
}

export function isProbableSurrogateKey(columnName: string, keyColumns: Set<string>): boolean {
  if (keyColumns.has(columnName.toLowerCase())) return true
  const tokens = tokenize(columnName)
  if (tokens.length === 0) return false
  return SURROGATE_KEY_SUFFIXES.has(tokens.at(-1)!.toLowerCase())
}

export function getDisplayColumns(
  columns: string[],
  keyColumns: Set<string>,
): { displayColumns: string[]; usedFallback: boolean } {
  const filtered = columns.filter((column) => !isProbableSurrogateKey(column, keyColumns))
  if (filtered.length === 0) return { displayColumns: columns, usedFallback: true }
  return { displayColumns: filtered, usedFallback: false }
}

// ---------------------------------------------------------------------------
// Result-cell display (Interactive Analytics Workspace)
// ---------------------------------------------------------------------------

/** What a result cell shows, and the two facts a table needs to lay it out.
 *
 * `isNull` exists because a SQL NULL and an empty string are different
 * answers -- "no value was recorded" versus "the recorded value is blank" --
 * and rendering both as an empty cell (what `String(value ?? '')` does)
 * silently merges them. */
export interface FormattedCell {
  text: string
  /** Right-align and use tabular figures, so a column of figures lines up
   * on its decimal point and can be compared down the column. */
  isNumeric: boolean
  /** Render the placeholder in a muted tone, and give assistive technology
   * the word rather than the glyph. */
  isNull: boolean
}

/** Shown for a SQL NULL. An em dash is the conventional "no value" mark in
 * a data table; `formatResultCell`'s `isNull` flag is what lets a caller
 * announce it as a word to a screen reader instead. */
export const NULL_DISPLAY = '—'

/** A plain decimal number: optional sign, digits, optional fractional part.
 * Deliberately strict -- no exponent, no leading `+`, no thousands
 * separators, no surrounding space -- so only something already written as
 * a plain number is ever re-formatted as one. */
const PLAIN_NUMBER_RE = /^-?\d+(\.\d+)?$/

/** Groups the integer part of an already-validated plain number string for
 * the active locale, leaving the fractional part byte-for-byte untouched.
 *
 * `BigInt` rather than `Number` on purpose: a SQL Server `decimal(38,10)`
 * arrives over JSON as a string precisely because it does not fit a float64,
 * and `Number('12345678901234567890')` would silently round it. Grouping the
 * integer digits through `BigInt` and then re-appending the original
 * fraction characters means no digit of the value the database returned is
 * ever recomputed -- only separators are inserted. */
function groupIntegerPart(raw: string): string {
  const [integerPart, fractionPart] = raw.split('.')
  let grouped: string
  try {
    grouped = new Intl.NumberFormat(undefined, { useGrouping: true }).format(BigInt(integerPart))
  } catch {
    // An integer part too malformed for BigInt should never reach here (the
    // regex has already vetted it), but a formatting failure must never cost
    // the user their value.
    return raw
  }
  return fractionPart === undefined ? grouped : `${grouped}.${fractionPart}`
}

/** Decides how one result cell is displayed.
 *
 * **Exact values are preserved.** No value is ever rounded, truncated, or
 * re-parsed into a float: the only change this makes to a number's own
 * characters is inserting locale thousands separators into its integer
 * part, and even that is limited (see `groupNumbers`). Anything that is not
 * a plain number is passed through as `String(value)` unchanged. */
export function formatResultCell(value: unknown, groupNumbers = false): FormattedCell {
  if (value === null || value === undefined) {
    return { text: NULL_DISPLAY, isNumeric: false, isNull: true }
  }
  if (typeof value === 'boolean') {
    return { text: String(value), isNumeric: false, isNull: false }
  }
  const text = String(value)
  if (!PLAIN_NUMBER_RE.test(text)) {
    return { text, isNumeric: false, isNull: false }
  }
  return { text: groupNumbers ? groupIntegerPart(text) : text, isNumeric: true, isNull: false }
}

/** Whether a column's values should get thousands separators.
 *
 * Only a column in which at least one value carries a fractional part --
 * a measure, in practice. An all-integer numeric column is left ungrouped
 * because this function cannot tell a quantity from an identifier, and
 * turning a postcode, year, or order number into "10,001"/"2,024" would
 * misrepresent it. A measure written with decimals is never an identifier,
 * which makes that the one safe signal available from the data alone.
 *
 * A column is only considered at all if **every** non-null value in it is a
 * plain number -- one stray non-numeric value means the column is text that
 * happens to contain digits. */
export function shouldGroupNumbers(values: unknown[]): boolean {
  let sawFraction = false
  let sawNumber = false
  for (const value of values) {
    if (value === null || value === undefined) continue
    const text = String(value)
    if (!PLAIN_NUMBER_RE.test(text)) return false
    sawNumber = true
    if (text.includes('.')) sawFraction = true
  }
  return sawNumber && sawFraction
}

/** Distinct non-null values of a column, capped so a wide result never costs
 * more than `limit + 1` entries of work per column. Returns `null` once the
 * cap is exceeded -- the caller's cue that this column has too many values
 * to offer as a filter. */
export function distinctColumnValues(values: unknown[], limit: number): string[] | null {
  const seen = new Set<string>()
  for (const value of values) {
    if (value === null || value === undefined) continue
    seen.add(String(value))
    if (seen.size > limit) return null
  }
  return [...seen].sort((a, b) => a.localeCompare(b))
}

/** The columns a result's own data actually supports filtering by, in column
 * order. A column qualifies only when it genuinely partitions these rows:
 *
 * - at least two distinct values (one value filters nothing);
 * - at most `maxDistinct` of them (a dropdown of hundreds is not a filter);
 * - fewer distinct values than rows, so a column unique per row -- an id, a
 *   timestamp, a free-text note -- is never offered;
 * - not numeric, since a numeric column wants a range, and offering
 *   equality on it would imply a comparison this control cannot express.
 *
 * Returning the empty array is a normal, expected answer: a result whose
 * shape supports no filter must show no filter controls at all, rather than
 * a disabled or empty one. */
export function filterableColumns(
  columns: string[],
  rows: unknown[][],
  maxDistinct = 25,
  maxColumns = 3,
): { column: string; values: string[] }[] {
  if (rows.length < 2) return []
  const result: { column: string; values: string[] }[] = []
  for (let index = 0; index < columns.length; index++) {
    const values = rows.map((row) => row[index])
    if (values.some((value) => value !== null && value !== undefined && PLAIN_NUMBER_RE.test(String(value)))) {
      continue
    }
    const distinct = distinctColumnValues(values, maxDistinct)
    if (distinct === null || distinct.length < 2 || distinct.length >= rows.length) continue
    result.push({ column: columns[index], values: distinct })
    if (result.length === maxColumns) break
  }
  return result
}
