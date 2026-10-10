import { useTranslation } from 'react-i18next'
import { Expander } from '@/components/ui/expander'
import type { SchemaTable } from '@/lib/types'

/** The panel's body, without its own expander chrome.
 *
 * Exported so `SqlIntelligencePanel` can render it as one section of a
 * single consolidated expander rather than nesting <details> inside
 * <details> or re-implementing the DDL rendering. The standalone
 * `SchemaContextPanel` below is unchanged for every other caller. */
export function SchemaContextContent({ tables }: { tables: SchemaTable[] }) {
  if (tables.length === 0) return null

  return (
    <div className="flex flex-col gap-3 text-xs">
        {tables.map((table) => (
          <div key={table.table_name}>
            <p className="mb-1 font-medium">
              {table.table_name}{' '}
              <span className="text-[var(--muted-foreground)]">
                (similarity {table.similarity_score.toFixed(2)})
              </span>
            </p>
            <pre className="overflow-x-auto rounded bg-[var(--muted)] p-2 font-mono">{table.ddl}</pre>
          </div>
      ))}
    </div>
  )
}

export function SchemaContextPanel({ tables }: { tables: SchemaTable[] }) {
  const { t } = useTranslation()
  if (tables.length === 0) return null

  return (
    <Expander title={`🔍 ${t('sql.schemaContext')}`}>
      <SchemaContextContent tables={tables} />
    </Expander>
  )
}
