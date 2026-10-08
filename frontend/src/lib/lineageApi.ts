import { request } from './api'
import type { TruthLevel } from './types'

/** Thin wrappers around `POST /lineage/*` (`api/lineage.py`, Prompt 42) --
 * the Data Lineage Platform. Reuses `lib/api.ts`'s `request()` for the
 * same fetch/auth/error-handling logic every other endpoint in this app
 * already goes through, the identical convention `lib/onboardingApi.ts`/
 * `lib/scenarioApi.ts`-shaped modules already establish.
 *
 * `/graph` and `/stale-check` require a local account server-side
 * (`Depends(require_local_user)`) -- `request()`'s existing bearer-token
 * handling already covers this with no special case here. */

export type LineageNodeType =
  | 'database'
  | 'table'
  | 'column'
  | 'metric'
  | 'dimension'
  | 'query'
  | 'visualization'
  | 'insight'
  | 'recommendation'

export type LineageEdgeType =
  | 'reads'
  | 'joins_via'
  | 'derives_from'
  | 'governed_by'
  | 'visualizes'
  | 'explains'
  | 'supported_by'

export interface LineageNode {
  id: string
  type: LineageNodeType
  label: string
  database_id: string | null
  table: string | null
  column: string | null
  concept_key: string | null
  concept_version: number | null
  truth_level: TruthLevel
  extra: Record<string, unknown>
}

export interface LineageEdge {
  source_id: string
  target_id: string
  type: LineageEdgeType
  truth_level: TruthLevel
  detail: string
  is_inferred: boolean
}

export interface LineageGraph {
  database_id: string | null
  nodes: LineageNode[]
  edges: LineageEdge[]
  schema_fingerprint: string | null
  catalog_versions: Record<string, number>
  suppressed_restricted_nodes: string[]
  limitations: string[]
  engine_version: string
}

export interface LineageQuerySpec {
  sql: string
  fingerprint?: string
  visualization?: { title: string; type: string }
  insight?: { text: string; grounded_fact_labels: string[] }
  recommendations?: { action: string; evidence_count: number }[]
}

export interface LineageGraphRequest {
  database_id: string
  include_inferred_relationships?: boolean
  queries?: LineageQuerySpec[]
}

export function buildLineageGraph(payload: LineageGraphRequest): Promise<LineageGraph> {
  return request<LineageGraph>('/lineage/graph', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export interface LineageTraceStep {
  node: LineageNode
  via_edge: LineageEdge | null
  depth: number
}

export interface LineageTraceResult {
  start_node_id: string
  direction: 'upstream' | 'downstream'
  max_depth: number
  steps: LineageTraceStep[]
  truncated: boolean
}

export function traceLineage(
  graph: LineageGraph,
  startNodeId: string,
  direction: 'upstream' | 'downstream',
  maxDepth?: number,
): Promise<LineageTraceResult> {
  return request<LineageTraceResult>('/lineage/trace', {
    method: 'POST',
    body: JSON.stringify({ graph, start_node_id: startNodeId, direction, max_depth: maxDepth }),
  })
}

export interface LineageStalenessFinding {
  kind: string
  detail: string
  node_id: string | null
}

export interface LineageStaleCheckResult {
  is_stale: boolean
  findings: LineageStalenessFinding[]
  engine_version: string
}

export function checkLineageStaleness(
  databaseId: string,
  recordedSchemaFingerprint: string | null,
  recordedCatalogVersions: Record<string, number>,
): Promise<LineageStaleCheckResult> {
  return request<LineageStaleCheckResult>('/lineage/stale-check', {
    method: 'POST',
    body: JSON.stringify({
      database_id: databaseId,
      recorded_schema_fingerprint: recordedSchemaFingerprint,
      recorded_catalog_versions: recordedCatalogVersions,
    }),
  })
}
