import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Maximize2,
  Search,
  Table2,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select } from '@/components/ui/select'
import { useSchemaTables } from '@/hooks/queries'
import {
  filterableColumns,
  formatColumnLabel,
  formatResultCell,
  getDisplayColumns,
  shouldGroupNumbers,
} from '@/lib/columnFormatting'
import { downloadCsv, rowsToCsv } from '@/lib/csv'

type Row = Record<string, unknown>

/** A "fully functional datatable" (sortable columns, a global search
 * filter, and pagination) in the spirit of jQuery DataTables' feature set
 * -- built on @tanstack/react-table (headless, so it's styled to match the
 * rest of the app) rather than the jQuery library itself, which doesn't
 * integrate with React's rendering model.
 *
 * Three things the Interactive Analytics Workspace adds here:
 *
 * - **A SQL NULL is visibly distinct from an empty string.** Rendering both
 *   as a blank cell (what `String(value ?? '')` did) merges "no value was
 *   recorded" with "the recorded value is blank" -- two different answers.
 *   See `formatResultCell`.
 * - **Numeric columns are right-aligned with tabular figures**, and a
 *   column of measures gets thousands separators, so figures can be
 *   compared down a column. No value is rounded or re-parsed -- see
 *   `formatResultCell`/`shouldGroupNumbers` for how exactness is kept.
 * - **Per-column filters, but only where this result's own data supports
 *   one** (`filterableColumns`): a column that does not actually partition
 *   these rows produces no control at all, rather than an empty dropdown.
 *   They compose with the existing global search rather than replacing it.
 *
 * Pagination (already present) is what keeps a large result usable: only
 * one page of rows is ever in the DOM, whatever the row cap returned. */
export function ResultsTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  const { t } = useTranslation()
  const [showTechnical, setShowTechnical] = useState(false)
  const [sorting, setSorting] = useState<SortingState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({})
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 })
  const [expanded, setExpanded] = useState(false)
  const schemaTables = useSchemaTables()

  const keyColumns = useMemo(() => {
    const set = new Set<string>()
    for (const table of schemaTables.data?.tables ?? []) {
      for (const column of table.columns) {
        if (column.is_primary_key) set.add(column.name.toLowerCase())
      }
    }
    return set
  }, [schemaTables.data])

  const { displayColumns, usedFallback } = useMemo(
    () => getDisplayColumns(columns, keyColumns),
    [columns, keyColumns],
  )
  const activeColumns = showTechnical ? columns : displayColumns
  const columnIndexes = useMemo(
    () => activeColumns.map((col) => columns.indexOf(col)),
    [activeColumns, columns],
  )

  const tableRows = useMemo<Row[]>(
    () =>
      rows.map((row) =>
        Object.fromEntries(activeColumns.map((col, i) => [col, row[columnIndexes[i]]])),
      ),
    [rows, activeColumns, columnIndexes],
  )

  // Whether each visible column's figures should carry thousands
  // separators, decided once from the column's own values rather than
  // per cell. Keyed by column name so a sort or a page change never
  // re-derives it.
  const groupedColumns = useMemo(() => {
    const set = new Set<string>()
    for (const col of activeColumns) {
      if (shouldGroupNumbers(tableRows.map((row) => row[col]))) set.add(col)
    }
    return set
  }, [activeColumns, tableRows])

  // Only the columns this result's own data genuinely supports filtering by.
  // Computed from the *unfiltered* rows so the set of controls stays stable
  // as filters are applied -- a dropdown must not vanish because the user
  // just narrowed the rows with it.
  const availableFilters = useMemo(
    () => filterableColumns(activeColumns, tableRows.map((row) => activeColumns.map((col) => row[col]))),
    [activeColumns, tableRows],
  )

  const columnDefs = useMemo<ColumnDef<Row>[]>(
    () =>
      activeColumns.map((col) => ({
        accessorKey: col,
        header: showTechnical ? col : formatColumnLabel(col),
        cell: (info) => {
          const { text, isNumeric, isNull } = formatResultCell(info.getValue(), groupedColumns.has(col))
          if (isNull) {
            // The glyph alone reads as nothing (or as "em dash") to a screen
            // reader; the word is what carries the meaning.
            return (
              <span className="text-[var(--muted-foreground)]">
                <span aria-hidden="true">{text}</span>
                <span className="sr-only">{t('results.nullValue')}</span>
              </span>
            )
          }
          return <span className={isNumeric ? 'tabular-nums' : undefined}>{text}</span>
        },
        meta: { isNumericColumn: tableRows.some((row) => formatResultCell(row[col]).isNumeric) },
      })),
    [activeColumns, showTechnical, groupedColumns, tableRows, t],
  )

  // Column filters are applied before the table sees the data, so they
  // compose with the global search and the row count below reflects both.
  const visibleRows = useMemo(() => {
    const active = Object.entries(columnFilters).filter(([, value]) => value !== '')
    if (active.length === 0) return tableRows
    return tableRows.filter((row) => active.every(([col, value]) => String(row[col] ?? '') === value))
  }, [tableRows, columnFilters])

  const table = useReactTable({
    data: visibleRows,
    columns: columnDefs,
    state: { sorting, globalFilter, pagination },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })

  const filteredRowCount = table.getFilteredRowModel().rows.length

  // Exports exactly the rows currently in view (column filters + global
  // search applied, pagination ignored), from the raw values rather than
  // the formatted cells -- `rowsToCsv` already neutralises spreadsheet
  // formula injection, and an exported figure must stay the database's own
  // value, ungrouped and unrounded.
  const handleExportCsv = () => {
    const csv = rowsToCsv(
      activeColumns,
      table.getFilteredRowModel().rows.map((row) => activeColumns.map((col) => row.original[col])),
    )
    downloadCsv(csv, 'results.csv')
  }

  // One table renderer, used by both the inline view and the expanded
  // dialog -- a second copy of this markup is how the two would drift.
  // `heightClass` is the only difference between them.
  const grid = (heightClass: string) => (
    <div className={`scroll-x rounded-md border border-[var(--border)] ${heightClass}`}>
      <table className="w-full min-w-max text-left text-sm">
        <caption className="sr-only">
          {t('results.title')} ({filteredRowCount} {t('common.rows')})
        </caption>
        <thead className="sticky top-0 bg-[var(--muted)]">
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const sortState = header.column.getIsSorted()
                const numeric = (header.column.columnDef.meta as { isNumericColumn?: boolean } | undefined)
                  ?.isNumericColumn
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={
                      sortState === 'asc' ? 'ascending' : sortState === 'desc' ? 'descending' : 'none'
                    }
                    onClick={header.column.getToggleSortingHandler()}
                    className="cursor-pointer select-none whitespace-nowrap px-3 py-2 font-medium hover:bg-[var(--card)]"
                  >
                    <span className={`flex items-center gap-1 ${numeric ? 'justify-end' : ''}`}>
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sortState === 'asc' && <ArrowUp className="h-3 w-3" aria-hidden="true" />}
                      {sortState === 'desc' && <ArrowDown className="h-3 w-3" aria-hidden="true" />}
                      {!sortState && <ArrowUpDown className="h-3 w-3 opacity-30" aria-hidden="true" />}
                    </span>
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 && (
            <tr>
              <td
                colSpan={activeColumns.length}
                className="px-3 py-4 text-center text-[var(--muted-foreground)]"
              >
                {t('common.noResults')}
              </td>
            </tr>
          )}
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id} className="border-t border-[var(--border)]">
              {row.getVisibleCells().map((cell) => {
                const numeric = (cell.column.columnDef.meta as { isNumericColumn?: boolean } | undefined)
                  ?.isNumericColumn
                return (
                  <td
                    key={cell.id}
                    className={`whitespace-nowrap px-3 py-1.5 ${numeric ? 'text-right' : ''}`}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <Table2 className="h-4 w-4 text-[var(--muted-foreground)]" />
          {t('results.title')} ({filteredRowCount} {t('common.rows')})
        </h3>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <input
              type="checkbox"
              checked={showTechnical}
              onChange={(event) => setShowTechnical(event.target.checked)}
            />
            {t('results.showTechnical')}
          </label>
          <Button size="sm" variant="secondary" onClick={() => setExpanded(true)}>
            <Maximize2 className="h-3.5 w-3.5" />
            {t('results.expand')}
          </Button>
          <Button size="sm" variant="secondary" onClick={handleExportCsv}>
            <Download className="h-3.5 w-3.5" />
            {t('common.exportCsv')}
          </Button>
        </div>
      </div>
      {!showTechnical && usedFallback && (
        <p className="text-xs text-[var(--muted-foreground)]">Only identifier columns were returned.</p>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <input
            value={globalFilter}
            onChange={(event) => setGlobalFilter(event.target.value)}
            placeholder={t('common.search')}
            aria-label={t('common.search')}
            className="h-8 w-full rounded-md border border-[var(--border-control)] bg-[var(--input)] pl-8 pr-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
          />
        </div>
        {/* Rendered only for the columns this result's data actually
            supports filtering by -- an unsupported shape shows nothing
            here at all, never a disabled or empty control. */}
        {availableFilters.map(({ column, values }) => (
          <label key={column} className="flex flex-col gap-1 text-xs text-[var(--muted-foreground)]">
            {showTechnical ? column : formatColumnLabel(column)}
            <Select
              value={columnFilters[column] ?? ''}
              onChange={(event) => {
                const next = event.target.value
                setColumnFilters((prev) => ({ ...prev, [column]: next }))
                // A narrower result may have fewer pages than the page the
                // user is currently on.
                setPagination((prev) => ({ ...prev, pageIndex: 0 }))
              }}
              className="h-8 py-0 text-xs"
            >
              <option value="">{t('results.filterAll')}</option>
              {values.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </label>
        ))}
      </div>

      {grid('max-h-[420px]')}

      {filteredRowCount > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
          <div className="flex items-center gap-2">
            <span>{t('common.rowsPerPage')}</span>
            <Select
              value={pagination.pageSize}
              onChange={(event) => table.setPageSize(Number(event.target.value))}
              className="h-7 py-0 text-xs"
              aria-label={t('common.rowsPerPage')}
            >
              {[10, 25, 50, 100].map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span>
              {table.getState().pagination.pageIndex + 1} {t('common.of')} {table.getPageCount()}
            </span>
            <Button
              size="icon"
              variant="ghost"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              aria-label="Previous page"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              aria-label="Next page"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {/* Expanded view -- the same grid at viewport width, for a wide
          result that the chat column clips. It renders the identical
          `grid()` output, so there is one table implementation and the
          expanded copy cannot drift from the inline one. Mounted only
          while open. */}
      <Dialog open={expanded} onOpenChange={setExpanded}>
        {expanded && (
          <DialogContent className="max-w-[min(95vw,90rem)]">
            <DialogHeader>
              <DialogTitle>
                {t('results.title')} ({filteredRowCount} {t('common.rows')})
              </DialogTitle>
            </DialogHeader>
            {grid('max-h-[75vh]')}
          </DialogContent>
        )}
      </Dialog>
    </div>
  )
}
