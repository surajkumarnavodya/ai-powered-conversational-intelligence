import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Expander } from '@/components/ui/expander'
import { Select } from '@/components/ui/select'
import { TruthLevelBadge } from '@/components/analytics/TruthLevelBadge'
import { useHealth } from '@/hooks/queries'
import { ApiError } from '@/lib/api'
import {
  buildLineageGraph,
  traceLineage,
  type LineageGraph,
  type LineageNode,
  type LineageTraceResult,
} from '@/lib/lineageApi'

const NODE_TYPE_ORDER: LineageNode['type'][] = [
  'database',
  'table',
  'column',
  'metric',
  'dimension',
  'query',
  'visualization',
  'insight',
  'recommendation',
]

const NODE_TYPE_LABEL: Record<LineageNode['type'], string> = {
  database: 'Databases',
  table: 'Tables',
  column: 'Columns',
  metric: 'Metrics',
  dimension: 'Dimensions',
  query: 'Queries',
  visualization: 'Visualizations',
  insight: 'Insights',
  recommendation: 'Recommendations',
}

/** The Data Lineage Platform's own read-only explorer -- Prompt 42
 * (`42_DATA_LINEAGE_PLATFORM_CONTRACT.md`), the first frontend surface
 * for `lineage/`/`api/lineage.py`.
 *
 * Deliberately a lean table/list view, not a graph-visualization
 * widget -- this app's own stated conservatism against adding a new
 * rendering library for a chart/graph type ("do not introduce duplicate
 * chart libraries", `15_VISUALIZATION_ENGINE_CONTRACT.md`'s own
 * precedent for histogram-vs-box/map) applies equally to a dedicated
 * graph-layout library here; nodes/edges are shown as plain, grouped,
 * accessible lists instead.
 *
 * Every node shows its own truth level (`TruthLevelBadge`, reused, not
 * duplicated -- master rule 10: an inferred/draft/AI-narrated node must
 * never look the same as a declared fact or a published definition).
 * "Trace" calls `/lineage/trace` purely over the already-built graph in
 * state -- no second `/lineage/graph` call, matching that route's own
 * "never touches a database" contract.
 */
export function DataLineage() {
  const { data: health } = useHealth()
  const databases = useMemo(() => health?.databases.map((d) => d.name) ?? [], [health])
  const [databaseId, setDatabaseId] = useState('')
  const [includeInferred, setIncludeInferred] = useState(false)
  const [graph, setGraph] = useState<LineageGraph | null>(null)
  const [buildError, setBuildError] = useState<string | null>(null)
  const [building, setBuilding] = useState(false)

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [direction, setDirection] = useState<'upstream' | 'downstream'>('upstream')
  const [trace, setTrace] = useState<LineageTraceResult | null>(null)
  const [traceError, setTraceError] = useState<string | null>(null)
  const [tracing, setTracing] = useState(false)

  const effectiveDatabaseId = databaseId || databases[0] || ''

  async function handleBuild() {
    if (!effectiveDatabaseId) return
    setBuilding(true)
    setBuildError(null)
    setGraph(null)
    setSelectedNodeId(null)
    setTrace(null)
    try {
      const result = await buildLineageGraph({
        database_id: effectiveDatabaseId,
        include_inferred_relationships: includeInferred,
      })
      setGraph(result)
    } catch (error) {
      setBuildError(error instanceof ApiError ? error.message : 'Failed to build the lineage graph.')
    } finally {
      setBuilding(false)
    }
  }

  async function handleTrace() {
    if (!graph || !selectedNodeId) return
    setTracing(true)
    setTraceError(null)
    setTrace(null)
    try {
      const result = await traceLineage(graph, selectedNodeId, direction)
      setTrace(result)
    } catch (error) {
      setTraceError(error instanceof ApiError ? error.message : 'Failed to trace lineage.')
    } finally {
      setTracing(false)
    }
  }

  const nodesByType = useMemo(() => {
    const groups = new Map<LineageNode['type'], LineageNode[]>()
    for (const node of graph?.nodes ?? []) {
      const bucket = groups.get(node.type) ?? []
      bucket.push(node)
      groups.set(node.type, bucket)
    }
    return groups
  }, [graph])

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold">Data lineage</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          End-to-end lineage from source tables/columns through governed metrics to SQL,
          visualizations, insights and recommendations -- deterministically parsed wherever
          possible; inferred lineage is labeled separately.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Build a lineage graph</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Select value={effectiveDatabaseId} onChange={(e) => setDatabaseId(e.target.value)}>
              {databases.length === 0 && <option value="">No database configured</option>}
              {databases.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={includeInferred}
                onChange={(e) => setIncludeInferred(e.target.checked)}
              />
              Include inferred (unconfirmed) relationships
            </label>
            <Button onClick={handleBuild} disabled={!effectiveDatabaseId || building}>
              {building ? 'Building…' : 'Build graph'}
            </Button>
          </div>
          {buildError && <p className="text-sm text-[var(--danger)]">{buildError}</p>}
        </CardContent>
      </Card>

      {graph && (
        <>
          {graph.limitations.length > 0 && (
            <Expander title="Scope & limitations">
              <ul className="list-disc space-y-1 pl-5 text-sm text-[var(--muted-foreground)]">
                {graph.limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            </Expander>
          )}

          {graph.suppressed_restricted_nodes.length > 0 && (
            <p className="text-sm text-[var(--warning)]">
              {graph.suppressed_restricted_nodes.length} restricted column(s) were withheld from
              this graph -- you do not hold permission to view them.
            </p>
          )}

          <Card>
            <CardHeader>
              <CardTitle>
                Nodes ({graph.nodes.length}) &amp; edges ({graph.edges.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-2">
              {NODE_TYPE_ORDER.filter((type) => nodesByType.has(type)).map((type) => (
                <Expander key={type} title={`${NODE_TYPE_LABEL[type]} (${nodesByType.get(type)?.length ?? 0})`}>
                  <ul className="flex flex-col gap-1.5">
                    {(nodesByType.get(type) ?? []).map((node) => (
                      <li key={node.id} className="flex flex-wrap items-center gap-2 text-sm">
                        <Button
                          variant={selectedNodeId === node.id ? 'primary' : 'ghost'}
                          size="sm"
                          onClick={() => {
                            setSelectedNodeId(node.id)
                            setTrace(null)
                          }}
                        >
                          {node.label}
                        </Button>
                        <TruthLevelBadge level={node.truth_level} />
                        {node.concept_version !== null && (
                          <Badge tone="neutral">v{node.concept_version}</Badge>
                        )}
                      </li>
                    ))}
                  </ul>
                </Expander>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Trace</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm">
                  Selected node:{' '}
                  <strong>
                    {graph.nodes.find((n) => n.id === selectedNodeId)?.label ?? 'none -- click a node above'}
                  </strong>
                </span>
                <Select
                  value={direction}
                  onChange={(e) => setDirection(e.target.value as 'upstream' | 'downstream')}
                >
                  <option value="upstream">Upstream (what feeds this)</option>
                  <option value="downstream">Downstream (what depends on this)</option>
                </Select>
                <Button onClick={handleTrace} disabled={!selectedNodeId || tracing}>
                  {tracing ? 'Tracing…' : 'Trace'}
                </Button>
              </div>
              {traceError && <p className="text-sm text-[var(--danger)]">{traceError}</p>}
              {trace && (
                <>
                  {trace.truncated && (
                    <p className="text-sm text-[var(--warning)]">
                      Truncated at depth {trace.max_depth} -- more reachable nodes may exist.
                    </p>
                  )}
                  {trace.steps.length === 0 ? (
                    <p className="text-sm text-[var(--muted-foreground)]">
                      Nothing found in that direction.
                    </p>
                  ) : (
                    <ol className="flex flex-col gap-2">
                      {trace.steps.map((step) => (
                        <li
                          key={step.node.id}
                          style={{ marginLeft: `${(step.depth - 1) * 1.25}rem` }}
                          className="flex flex-wrap items-center gap-2 text-sm"
                        >
                          <Badge tone="neutral">depth {step.depth}</Badge>
                          <span className="font-medium">{step.node.label}</span>
                          <TruthLevelBadge level={step.node.truth_level} />
                          {step.via_edge && (
                            <span className="text-[var(--muted-foreground)]">
                              via {step.via_edge.type}
                              {step.via_edge.is_inferred ? ' (inferred)' : ''} --{' '}
                              {step.via_edge.detail}
                            </span>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
