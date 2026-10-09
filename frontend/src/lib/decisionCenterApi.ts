import { request } from '@/lib/api'

/** Types and the one call for the Executive Decision Center (Prompt 52).
 *
 * Mirrors `api/decision_center_schemas.py`. Every field a headline needs
 * in order to be drilled into arrives on the headline itself, so opening
 * a drill-down never costs a second request.
 */

export type TruthLevel = 'database_fact' | 'ai_inference' | 'confirmed_business_truth'

export interface EvidenceItem {
  label: string
  value: string
  truth_level: TruthLevel
}

export interface Provenance {
  source: string
  source_record_id: string
  period: string
  metric_concept_key: string | null
  confidence: number | null
  database_id: string | null
  recorded_at: string
  engine_version: string
  limitations: string[]
  lineage_available: boolean
  metric_definition_available: boolean
}

export interface DecisionHeadline {
  headline_id: string
  kind: string
  title: string
  detail: string
  severity: number
  severity_tier: 'low' | 'medium' | 'high'
  status: string
  occurred_at: string
  truth_level: TruthLevel
  provenance: Provenance
  evidence: EvidenceItem[]
}

export interface HealthIndicator {
  key: string
  label: string
  value: number
  unit: 'count' | 'percent'
  direction: 'higher_is_better' | 'lower_is_better' | 'neutral'
  detail: string
  truth_level: TruthLevel
}

export interface BriefingSection {
  section: string
  title: string
  headlines: DecisionHeadline[]
  total_available: number
  empty_reason: string
}

export interface ChainLink {
  stage: string
  present: boolean
  headline_id: string | null
  summary: string
  /** `declared` is a real foreign key; `correlated` only means the two
   * records share a subject, which is never evidence that one followed
   * from the other. */
  link_type: 'declared' | 'correlated' | null
  absent_reason: string
}

export interface DecisionChain {
  chain_id: string
  is_complete: boolean
  furthest_stage: string | null
  break_reason: string
  links: ChainLink[]
  truth_level: TruthLevel
}

export interface MetricDefinition {
  concept_key: string
  business_name: string
  description: string
  status: string
  version: number
  owner: string | null
  dimensions: string[]
  confidence: number | null
}

export interface ExecutiveBriefing {
  tenant_id: string
  database_id: string | null
  profile_id: string
  generated_at: string
  health: HealthIndicator[]
  sections: BriefingSection[]
  chains: DecisionChain[]
  metric_definitions: Record<string, MetricDefinition>
  contributing_sources: string[]
  unavailable_sources: string[]
  engine_version: string
}

export function getExecutiveBriefing(databaseId?: string): Promise<ExecutiveBriefing> {
  const query = databaseId ? `?database_id=${encodeURIComponent(databaseId)}` : ''
  return request<ExecutiveBriefing>(`/decision-center/briefing${query}`)
}
