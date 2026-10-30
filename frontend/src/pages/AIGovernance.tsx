import { useEffect, useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Expander } from '@/components/ui/expander'
import { Select } from '@/components/ui/select'
import { useCapabilities } from '@/hooks/queries'
import { ApiError } from '@/lib/api'
import {
  approveGovernanceAssetVersion,
  createGovernanceAssetDraft,
  deployGovernanceAssetVersion,
  getGovernanceAssetEvents,
  getGovernanceAssetUsageSummary,
  listGovernanceAssetVersions,
  recordGovernanceEvaluationResults,
  recordGovernanceTestResults,
  rejectGovernanceAssetVersion,
  retireGovernanceAssetVersion,
  rollbackGovernanceAsset,
  submitGovernanceAssetForEvaluation,
  submitGovernanceAssetForTesting,
  type GovernanceAssetVersion,
  type GovernanceEvent,
  type GovernedAssetType,
  type UsageSummary,
} from '@/lib/governanceApi'

const ASSET_TYPES: GovernedAssetType[] = [
  'model',
  'provider',
  'prompt',
  'agent',
  'tool',
  'retrieval_source',
  'policy',
]

const STAGE_TONE: Record<
  GovernanceAssetVersion['stage'],
  'neutral' | 'success' | 'warning' | 'danger' | 'accent'
> = {
  draft: 'neutral',
  testing: 'warning',
  evaluating: 'warning',
  approved: 'accent',
  deployed: 'success',
  rolled_back: 'danger',
  rejected: 'danger',
  retired: 'neutral',
}

/** The AI Governance Center -- Prompt 44
 * (`44_AI_GOVERNANCE_CENTER_CONTRACT.md`): a reviewer/operator surface
 * over the governed Draft -> Test -> Evaluate -> Approve -> Deploy ->
 * Rollback lifecycle (`api/governance.py`) for models, providers,
 * prompts, agents, tools, retrieval sources and policies.
 *
 * Mirrors `SemanticReview.tsx`'s own shape (list + priority-free detail
 * panel + capability-gated action buttons) -- the server re-checks every
 * action regardless of which buttons this page shows (master
 * principle: no frontend-level restriction is a security boundary).
 *
 * **"Release" (the mission's own word) is this page's Deploy button;
 * "Rollback" targets a *specific prior version* of the same asset** --
 * the dropdown next to Rollback only ever lists that asset's own
 * `retired`/`rolled_back` versions (the real, server-enforced
 * eligibility rule -- see `governance_center.models
 * .ROLLBACK_ELIGIBLE_STAGES`), never an arbitrary version id.
 */
export function AIGovernance() {
  const capabilities = useCapabilities()
  const canManage = capabilities.manage_governance ?? false
  const canReview = capabilities.review_governance ?? false

  const [assetTypeFilter, setAssetTypeFilter] = useState<string>('')
  const [versions, setVersions] = useState<GovernanceAssetVersion[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [events, setEvents] = useState<GovernanceEvent[]>([])
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const [showNewForm, setShowNewForm] = useState(false)
  const [newAssetType, setNewAssetType] = useState<GovernedAssetType>('model')
  const [newAssetKey, setNewAssetKey] = useState('')
  const [newDisplayName, setNewDisplayName] = useState('')
  const [newConfigText, setNewConfigText] = useState('{}')

  async function refresh() {
    setLoading(true)
    setLoadError(null)
    try {
      const result = await listGovernanceAssetVersions(
        assetTypeFilter ? { asset_type: assetTypeFilter } : undefined,
      )
      setVersions(result)
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : 'Failed to load asset versions.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetTypeFilter])

  const selected = useMemo(
    () => versions.find((v) => v.id === selectedId) ?? null,
    [versions, selectedId],
  )

  const rollbackTargets = useMemo(() => {
    if (!selected) return []
    return versions.filter(
      (v) =>
        v.asset_type === selected.asset_type &&
        v.asset_key === selected.asset_key &&
        v.id !== selected.id &&
        (v.stage === 'retired' || v.stage === 'rolled_back'),
    )
  }, [versions, selected])
  const [rollbackTargetId, setRollbackTargetId] = useState('')

  async function openDetail(version: GovernanceAssetVersion) {
    setSelectedId(version.id)
    setActionError(null)
    try {
      const [eventList, usageSummary] = await Promise.all([
        getGovernanceAssetEvents(version.id),
        getGovernanceAssetUsageSummary(version.id),
      ])
      setEvents(eventList)
      setUsage(usageSummary)
    } catch {
      setEvents([])
      setUsage(null)
    }
  }

  async function runAction(action: () => Promise<unknown>) {
    setActionError(null)
    try {
      await action()
      await refresh()
      if (selected) await openDetail(selected)
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : 'Action failed.')
    }
  }

  async function handleCreate() {
    let config: Record<string, unknown> = {}
    try {
      config = newConfigText.trim() ? JSON.parse(newConfigText) : {}
    } catch {
      setActionError('Config must be valid JSON.')
      return
    }
    await runAction(() =>
      createGovernanceAssetDraft({
        asset_type: newAssetType,
        asset_key: newAssetKey,
        display_name: newDisplayName,
        config,
      }),
    )
    setShowNewForm(false)
    setNewAssetKey('')
    setNewDisplayName('')
    setNewConfigText('{}')
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold">AI Governance Center</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          Draft → Test → Evaluate → Approve → Deploy → Rollback, for models, providers, prompts,
          agents, tools, retrieval sources and policies. Every version shown is an AI-estimate
          candidate until a human approval promotes it -- see each version's own truth-level
          badge.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Governed asset versions</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Select
              aria-label="Filter by asset type"
              value={assetTypeFilter}
              onChange={(e) => setAssetTypeFilter(e.target.value)}
            >
              <option value="">All asset types</option>
              {ASSET_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
            <Button onClick={() => void refresh()} disabled={loading} variant="ghost" size="sm">
              {loading ? 'Loading…' : 'Refresh'}
            </Button>
            {canManage && (
              <Button onClick={() => setShowNewForm((v) => !v)} size="sm">
                + New asset draft
              </Button>
            )}
          </div>
          {loadError && <p className="text-sm text-[var(--danger)]">{loadError}</p>}

          {showNewForm && canManage && (
            <div className="flex flex-col gap-2 rounded-md border border-[var(--border)] p-3">
              <div className="flex flex-wrap gap-2">
                <Select
                  aria-label="Asset type"
                  value={newAssetType}
                  onChange={(e) => setNewAssetType(e.target.value as GovernedAssetType)}
                >
                  {ASSET_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </Select>
                <input
                  className="h-9 rounded-md border border-[var(--border-control)] bg-[var(--input)] px-2.5 text-sm"
                  placeholder="asset key (e.g. llama3.1:8b)"
                  value={newAssetKey}
                  onChange={(e) => setNewAssetKey(e.target.value)}
                />
                <input
                  className="h-9 flex-1 rounded-md border border-[var(--border-control)] bg-[var(--input)] px-2.5 text-sm"
                  placeholder="display name"
                  value={newDisplayName}
                  onChange={(e) => setNewDisplayName(e.target.value)}
                />
              </div>
              <textarea
                className="h-20 rounded-md border border-[var(--border-control)] bg-[var(--input)] px-2.5 py-1.5 text-sm font-mono"
                placeholder="config JSON"
                value={newConfigText}
                onChange={(e) => setNewConfigText(e.target.value)}
              />
              <div>
                <Button
                  onClick={() => void handleCreate()}
                  disabled={!newAssetKey || !newDisplayName}
                  size="sm"
                >
                  Create draft
                </Button>
              </div>
            </div>
          )}

          <ul className="flex flex-col gap-1.5">
            {versions.map((v) => (
              <li key={v.id} className="flex flex-wrap items-center gap-2 text-sm">
                <Button
                  variant={selectedId === v.id ? 'primary' : 'ghost'}
                  size="sm"
                  onClick={() => void openDetail(v)}
                >
                  {v.asset_type}/{v.asset_key} v{v.version} -- {v.display_name}
                </Button>
                <Badge tone={STAGE_TONE[v.stage]}>{v.stage}</Badge>
              </li>
            ))}
            {versions.length === 0 && !loading && (
              <li className="text-sm text-[var(--muted-foreground)]">No asset versions yet.</li>
            )}
          </ul>
        </CardContent>
      </Card>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle>
              {selected.asset_type}/{selected.asset_key} v{selected.version}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={STAGE_TONE[selected.stage]}>{selected.stage}</Badge>
              <Badge tone={selected.truth_level === 'confirmed_business_truth' ? 'accent' : 'warning'}>
                {selected.truth_level === 'confirmed_business_truth'
                  ? 'Human-approved'
                  : 'Not yet confirmed'}
              </Badge>
              {selected.owner && <span className="text-sm">Owner: {selected.owner}</span>}
            </div>

            <Expander title="Config">
              <pre className="overflow-auto text-xs">{JSON.stringify(selected.config, null, 2)}</pre>
            </Expander>
            {selected.test_results && (
              <Expander title="Test results">
                <pre className="overflow-auto text-xs">
                  {JSON.stringify(selected.test_results, null, 2)}
                </pre>
              </Expander>
            )}
            {selected.evaluation_results && (
              <Expander title="Evaluation results">
                <pre className="overflow-auto text-xs">
                  {JSON.stringify(selected.evaluation_results, null, 2)}
                </pre>
              </Expander>
            )}
            {usage && (
              <Expander title="Usage/cost/latency (operator-reported)">
                <p className="text-xs text-[var(--muted-foreground)]">
                  {usage.sample_count} sample(s) -- {usage.total_requests} requests, $
                  {usage.total_cost.toFixed(2)} total cost
                  {usage.mean_p95_latency_ms !== null &&
                    `, mean p95 ${usage.mean_p95_latency_ms.toFixed(0)}ms`}
                  . Reported by operators, not auto-instrumented (no per-model cost capture
                  exists yet).
                </p>
              </Expander>
            )}
            <Expander title={`Audit trail (${events.length})`}>
              <ul className="flex flex-col gap-1 text-xs">
                {events.map((e) => (
                  <li key={e.id}>
                    <span className="font-mono">{e.event_type}</span>
                    {e.from_stage && e.to_stage && ` ${e.from_stage} → ${e.to_stage}`}
                    {e.reason && ` -- ${e.reason}`}
                  </li>
                ))}
              </ul>
            </Expander>

            {actionError && <p className="text-sm text-[var(--danger)]">{actionError}</p>}

            <div className="flex flex-wrap gap-2">
              {canManage && selected.stage === 'draft' && (
                <Button
                  size="sm"
                  onClick={() => void runAction(() => submitGovernanceAssetForTesting(selected.id))}
                >
                  Submit for testing
                </Button>
              )}
              {canManage && selected.stage === 'testing' && (
                <>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() =>
                      void runAction(() =>
                        recordGovernanceTestResults(selected.id, { passed: true }),
                      )
                    }
                  >
                    Record test results (passed)
                  </Button>
                  <Button
                    size="sm"
                    onClick={() =>
                      void runAction(() => submitGovernanceAssetForEvaluation(selected.id))
                    }
                  >
                    Submit for evaluation
                  </Button>
                </>
              )}
              {canManage && selected.stage === 'evaluating' && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    void runAction(() =>
                      recordGovernanceEvaluationResults(selected.id, { score: 1.0 }),
                    )
                  }
                >
                  Record evaluation results
                </Button>
              )}
              {canReview && selected.stage === 'evaluating' && (
                <Button
                  size="sm"
                  onClick={() => void runAction(() => approveGovernanceAssetVersion(selected.id))}
                >
                  Approve
                </Button>
              )}
              {canReview && !['rejected', 'rolled_back', 'retired'].includes(selected.stage) && (
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() =>
                    void runAction(() =>
                      rejectGovernanceAssetVersion(selected.id, 'Rejected by reviewer'),
                    )
                  }
                >
                  Reject
                </Button>
              )}
              {canManage && selected.stage === 'approved' && (
                <Button
                  size="sm"
                  onClick={() => void runAction(() => deployGovernanceAssetVersion(selected.id))}
                >
                  Deploy (release)
                </Button>
              )}
              {canManage && (selected.stage === 'approved' || selected.stage === 'deployed') && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void runAction(() => retireGovernanceAssetVersion(selected.id))}
                >
                  Retire
                </Button>
              )}
              {canManage && selected.stage === 'deployed' && rollbackTargets.length > 0 && (
                <div className="flex items-center gap-1.5">
                  <Select
                    aria-label="Roll back to version"
                    value={rollbackTargetId}
                    onChange={(e) => setRollbackTargetId(e.target.value)}
                  >
                    <option value="">Roll back to…</option>
                    {rollbackTargets.map((t) => (
                      <option key={t.id} value={t.id}>
                        v{t.version} ({t.stage})
                      </option>
                    ))}
                  </Select>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={!rollbackTargetId}
                    onClick={() =>
                      void runAction(() =>
                        rollbackGovernanceAsset(
                          selected.id,
                          rollbackTargetId,
                          'Manual rollback from AI Governance Center',
                        ),
                      )
                    }
                  >
                    Rollback
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
