import { useTranslation } from 'react-i18next'
import { preparedChartToTable, type NumberFormat, type PreparedChart } from '@/lib/chartEngine'

/** The data behind a chart, as a real `<table>`.
 *
 * Requirement: "use accessible chart alternatives or data tables". A
 * Chart.js `<canvas>` carries an accessible *name* ("Revenue by region
 * chart") and nothing else -- every value it draws is pixels, so a screen
 * reader reaches none of them. This is the text alternative for that
 * canvas, and `ResultChart` renders it beneath every chart in the app, so
 * no chart can ship without one.
 *
 * Collapsed by default behind a native `<details>`/`<summary>`: a sighted
 * reader already has the chart, the markup stays in the accessibility tree
 * either way, and `<details>` is keyboard-operable with no JS state. The
 * table is additionally announced via `aria-describedby` on the summary so
 * the relationship to the chart is explicit rather than merely positional.
 *
 * Deliberately NOT a second copy of the chart's data preparation: it reads
 * the same `PreparedChart` the canvas is drawn from (see
 * `preparedChartToTable`), so the two can never disagree about a value, a
 * sort order, or a top-N cut. */
export function ChartDataTable({
  prepared,
  numberFormat,
  caption,
}: {
  prepared: PreparedChart
  numberFormat: NumberFormat
  caption?: string
}) {
  const { t } = useTranslation()
  const { headers, rows } = preparedChartToTable(prepared, numberFormat)

  // A chart with no plotted values has no data to offer as an alternative;
  // an empty table would be noise, not an aid.
  if (rows.length === 0 || headers.length === 0) return null

  return (
    <details className="group mt-2 rounded-md border border-[var(--border)]">
      <summary className="cursor-pointer list-none px-2.5 py-1.5 text-xs text-[var(--muted-foreground)] marker:content-none hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
        {t('chart.showDataTable', { count: rows.length })}
      </summary>
      <div className="scroll-x max-h-64 border-t border-[var(--border)]">
        <table className="w-full min-w-max text-left text-xs">
          <caption className="sr-only">{caption || t('chart.dataTableCaption')}</caption>
          <thead className="sticky top-0 bg-[var(--muted)]">
            <tr>
              {headers.map((header, index) => (
                <th
                  key={`${header}-${index}`}
                  scope="col"
                  className={`whitespace-nowrap px-2.5 py-1.5 font-medium ${index === 0 ? '' : 'text-right'}`}
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-t border-[var(--border)]">
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className={`whitespace-nowrap px-2.5 py-1 ${
                      cellIndex === 0 ? '' : 'text-right tabular-nums'
                    }`}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}
