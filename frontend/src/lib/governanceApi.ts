import { request } from './api'

/** Thin wrappers around `POST/GET /governance/*` (`api/governance.py`,
 * Prompt 44) -- the AI Governance Center's own Draft -> Test ->
 * Evaluate -> Approve -> Deploy -> Rollback lifecycle for models,
 * providers, prompts, agents, tools, retrieval sources and policies.
 * Reuses `lib/api.ts`'s `request()` for the same fetch/auth/error-
 * handling logic every other endpoint in this app already goes through,
 * the identical convention `lib/onboardingApi.ts`/`lib/lineageApi.ts`
 * already establish.
 *
 * Every one of these requires a local account
 * (`Depends(require_local_user)` server-side) -- `request()`'s existing
 * bearer-token handling already covers this with no special case here. */

export type GovernedAssetType =
  | 'model'
  | 'provider'
  | 'prompt'
  | 'agent'
  | 'tool'
  | 'retrieval_source'
  | 'policy'

export type GovernanceStage =
  | 'draft'
  | 'testing'
  | 'evaluating'
  | 'approved'
  | 'deployed'
  | 'rolled_back'
  | 'rejected'
  | 'retired'

export interface GovernanceAssetVersion {
  id: string
  tenant_id: string
  asset_type: GovernedAssetType
  asset_key: string
  display_name: string
  description: string
  owner: string | null
  version: number
  stage: GovernanceStage
  truth_level: 'database_fact' | 'ai_inference' | 'confirmed_business_truth'
  config: Record<string, unknown>
  test_results: Record<string, unknown> | null
  evaluation_results: Record<string, unknown> | null
  approval_notes: string | null
  rejection_reason: string | null
  supersedes_id: string | null
  created_by_user_id: string | null
  tested_by_user_id: string | null
  tested_at: string | null
  evaluated_by_user_id: string | null
  evaluated_at: string | null
  approved_by_user_id: string | null
  approved_at: string | null
  deployed_by_user_id: string | null
  deployed_at: string | null
  retired_by_user_id: string | null
  retired_at: string | null
  created_at: string
  updated_at: string
}

export interface GovernanceEvent {
  id: string
  asset_version_id: string
  event_type: 'stage_change' | 'rollback' | 'usage_recorded' | 'note'
  from_stage: string | null
  to_stage: string | null
  actor_user_id: string | null
  actor_label: string | null
  reason: string | null
  payload: Record<string, unknown> | null
  created_at: string
}

export interface UsageSummary {
  sample_count: number
  total_requests: number
  total_cost: number
  mean_p95_latency_ms: number | null
}

export interface CreateGovernanceAssetRequest {
  asset_type: GovernedAssetType
  asset_key: string
  display_name: string
  description?: string
  config?: Record<string, unknown>
  owner?: string | null
}

export function createGovernanceAssetDraft(
  payload: CreateGovernanceAssetRequest,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>('/governance/assets', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listGovernanceAssetVersions(filters?: {
  asset_type?: string
  asset_key?: string
  stage?: string
}): Promise<GovernanceAssetVersion[]> {
  const params = new URLSearchParams()
  if (filters?.asset_type) params.set('asset_type', filters.asset_type)
  if (filters?.asset_key) params.set('asset_key', filters.asset_key)
  if (filters?.stage) params.set('stage', filters.stage)
  const query = params.toString()
  return request<GovernanceAssetVersion[]>(`/governance/assets${query ? `?${query}` : ''}`)
}

export function getGovernanceAssetVersion(versionId: string): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}`)
}

export function getGovernanceAssetEvents(versionId: string): Promise<GovernanceEvent[]> {
  return request<GovernanceEvent[]>(`/governance/assets/${versionId}/events`)
}

export function getGovernanceAssetUsageSummary(versionId: string): Promise<UsageSummary> {
  return request<UsageSummary>(`/governance/assets/${versionId}/usage-summary`)
}

export function updateGovernanceAssetDraft(
  versionId: string,
  payload: Partial<{
    display_name: string
    description: string
    config: Record<string, unknown>
    owner: string | null
  }>,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function submitGovernanceAssetForTesting(versionId: string): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/submit-for-testing`, {
    method: 'POST',
  })
}

export function recordGovernanceTestResults(
  versionId: string,
  testResults: Record<string, unknown>,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/test-results`, {
    method: 'POST',
    body: JSON.stringify({ test_results: testResults }),
  })
}

export function submitGovernanceAssetForEvaluation(
  versionId: string,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/submit-for-evaluation`, {
    method: 'POST',
  })
}

export function recordGovernanceEvaluationResults(
  versionId: string,
  evaluationResults: Record<string, unknown>,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/evaluation-results`, {
    method: 'POST',
    body: JSON.stringify({ evaluation_results: evaluationResults }),
  })
}

export function approveGovernanceAssetVersion(
  versionId: string,
  notes?: string,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ notes: notes ?? null }),
  })
}

export function rejectGovernanceAssetVersion(
  versionId: string,
  reason: string,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export interface DeployResult {
  deployed: GovernanceAssetVersion
  retired: GovernanceAssetVersion | null
}

export function deployGovernanceAssetVersion(versionId: string): Promise<DeployResult> {
  return request<DeployResult>(`/governance/assets/${versionId}/deploy`, { method: 'POST' })
}

export interface RollbackResult {
  rolled_back: GovernanceAssetVersion
  redeployed: GovernanceAssetVersion
}

export function rollbackGovernanceAsset(
  currentDeployedVersionId: string,
  targetVersionId: string,
  reason: string,
): Promise<RollbackResult> {
  return request<RollbackResult>(`/governance/assets/${currentDeployedVersionId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ target_version_id: targetVersionId, reason }),
  })
}

export function retireGovernanceAssetVersion(
  versionId: string,
  reason?: string,
): Promise<GovernanceAssetVersion> {
  return request<GovernanceAssetVersion>(`/governance/assets/${versionId}/retire`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason ?? null }),
  })
}

export function recordGovernanceAssetUsage(
  versionId: string,
  sample: {
    request_count?: number
    total_cost?: number
    p50_latency_ms?: number
    p95_latency_ms?: number
    window_start?: string
    window_end?: string
  },
): Promise<GovernanceEvent> {
  return request<GovernanceEvent>(`/governance/assets/${versionId}/usage`, {
    method: 'POST',
    body: JSON.stringify(sample),
  })
}
