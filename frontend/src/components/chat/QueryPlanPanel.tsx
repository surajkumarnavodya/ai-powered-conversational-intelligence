import { useTranslation } from 'react-i18next'
import { Expander } from '@/components/ui/expander'

/** The plan steps, without expander chrome -- see
 * `SchemaContextContent` for why this split exists. */
export function QueryPlanContent({ plan }: { plan: string[] | null }) {
  if (!plan || plan.length === 0) return null

  return (
    <ol className="list-decimal space-y-1 pl-4 text-xs">
      {plan.map((step, index) => (
        // eslint-disable-next-line react/no-array-index-key -- plan steps have no stable id
        <li key={index}>{step}</li>
      ))}
    </ol>
  )
}

export function QueryPlanPanel({ plan }: { plan: string[] | null }) {
  const { t } = useTranslation()
  if (!plan || plan.length === 0) return null

  return (
    <Expander title={`🧭 ${t('sql.queryPlan')}`}>
      <QueryPlanContent plan={plan} />
    </Expander>
  )
}
