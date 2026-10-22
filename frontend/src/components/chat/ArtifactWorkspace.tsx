import { BarChart3, Pin, RefreshCw, Table2, Trash2, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArtifactDetail } from './ArtifactDetail'
import { ArtifactFreshnessBadge } from './ArtifactFreshnessBadge'
import { ActionTooltip } from '@/components/ui/action-tooltip'
import { Button } from '@/components/ui/button'
import {
  useArtifact,
  useArtifacts,
  useDeleteArtifact,
  useRefreshArtifact,
  useSaveArtifact,
} from '@/hooks/queries'
import { isSqlResult, type QueryHistoryEntry } from '@/lib/history'
import { collapseQuestionWhitespace } from '@/lib/questionLabel'
import type { ArtifactOut } from '@/lib/types'
import { useChatStore } from '@/store/chatStore'
import { useLocalAuthStore } from '@/store/localAuthStore'

const MAX_LABEL_CHARS = 70

/** The analysis workspace: durable work products made from conversation
 * turns, beside the conversation they came from.
 *
 * **An artifact is a reference, not a copy.** It points at the
 * `Prompt`/`AiOutput` rows this app already persists for a turn, and the
 * result it presents is that turn's own already-bounded
 * `result_snapshot` -- so saving a 50-row analysis stores three scalars,
 * not a second copy of the result. The only rows an artifact owns are the
 * ones a refresh produced, because those exist nowhere else.
 *
 * **Freshness is never decided here.** The server computes it on every
 * read (`artifacts/staleness.py`) from the schema fingerprint, the source
 * turn and the refresh history -- none of which the client can see. A
 * second opinion is how the same artifact ends up reading "current" in
 * one place and "dated" in another.
 *
 * **Two backing stores, one affordance.** For a signed-in local account,
 * saving writes a durable, tenant- and owner-scoped row that survives a
 * reload and can be refreshed. Without local auth there is no account to
 * attach one to (the same limitation chat history itself has), so the
 * button falls back to the pre-existing session-only pin and the panel
 * says so rather than offering a Save that would quietly 403.
 */
export interface ArtifactSummary {
  entryId: string
  question: string
  rowCount: number | null
  hasChart: boolean
  hasResult: boolean
}

/** The session-only worklist, for a caller with no local account.
 *
 * Pinned ids for turns that are no longer in `entries` (a different
 * conversation is open) are skipped rather than shown as dead rows.
 * Exported for its own test.
 */
export function summarizeArtifacts(
  entries: QueryHistoryEntry[],
  pinnedEntryIds: Set<string>,
): ArtifactSummary[] {
  return entries
    .filter((entry) => pinnedEntryIds.has(entry.entryId))
    .map((entry) => ({
      entryId: entry.entryId,
      question: collapseQuestionWhitespace(entry.question).slice(0, MAX_LABEL_CHARS),
      rowCount: entry.confirmedRows ? entry.confirmedRows.length : entry.rowCount,
      hasChart: entry.chartOptions !== null,
      hasResult: Boolean(entry.confirmedRows) || isSqlResult(entry.finalState.sources_used),
    }))
}

/** Which turn in the open conversation an artifact points at, or `null`
 * when that conversation is not the one on screen.
 *
 * Two ways to match, because a turn's `entryId` differs by how it got
 * there: a turn asked in this session carries a client-side id and the
 * server's `message_id` inside its response, while a turn restored from
 * saved history is keyed on its `Prompt` row id
 * (`serverMessagesToQueryHistory`) -- which is exactly the artifact's own
 * `prompt_id`. Matching both is what lets "Go to question" reuse the one
 * existing `turn-${entryId}` scroll anchor instead of a second mechanism.
 */
export function resolveArtifactAnchor(
  artifact: Pick<ArtifactOut, 'message_id' | 'prompt_id'>,
  entries: QueryHistoryEntry[],
): string | null {
  if (artifact.message_id) {
    const live = entries.find((entry) => entry.finalState.message_id === artifact.message_id)
    if (live) return live.entryId
  }
  if (artifact.prompt_id) {
    const restored = entries.find((entry) => entry.entryId === artifact.prompt_id)
    if (restored) return restored.entryId
  }
  return null
}

function scrollToTurn(entryId: string): void {
  document.getElementById(`turn-${entryId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function ArtifactRow({
  artifact,
  entries,
  isOpen,
  onToggle,
}: {
  artifact: ArtifactOut
  entries: QueryHistoryEntry[]
  isOpen: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const refresh = useRefreshArtifact()
  const remove = useDeleteArtifact()
  const detail = useArtifact(isOpen ? artifact.id : null)
  const anchor = resolveArtifactAnchor(artifact, entries)

  return (
    <li className="rounded border border-[var(--border)]" data-artifact-id={artifact.id}>
      <div className="flex items-center gap-2 px-1.5 py-1">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={isOpen}
          aria-controls={`artifact-detail-${artifact.id}`}
          className="flex min-w-0 flex-1 items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-[var(--muted)]"
        >
          {artifact.artifact_type === 'chart' ? (
            <BarChart3 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <Table2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          )}
          <span className="truncate">{artifact.title}</span>
          {artifact.row_count !== null && (
            <span className="shrink-0 text-[var(--muted-foreground)]">
              {t('artifacts.rows', { count: artifact.row_count })}
            </span>
          )}
          <ArtifactFreshnessBadge freshness={artifact.freshness} />
        </button>

        {/* Back to the question that produced it -- the whole point of an
            artifact being a reference. Disabled, with a reason, when its
            conversation is not the one on screen, rather than silently
            scrolling nowhere. */}
        <Button
          variant="ghost"
          aria-label={t('artifacts.goToQuestion', { title: artifact.title })}
          title={anchor ? t('artifacts.goToQuestion', { title: artifact.title }) : t('artifacts.otherConversation')}
          disabled={anchor === null}
          onClick={() => anchor && scrollToTurn(anchor)}
        >
          <Pin className="h-3.5 w-3.5" />
        </Button>

        <Button
          variant="ghost"
          aria-label={t('artifacts.refresh', { title: artifact.title })}
          title={artifact.refresh_blocked_reason ?? t('artifacts.refreshHint')}
          disabled={!artifact.is_refreshable || refresh.isPending}
          onClick={() => refresh.mutate(artifact.id)}
        >
          <RefreshCw className={refresh.isPending ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
        </Button>

        <Button
          variant="ghost"
          aria-label={t('artifacts.delete', { title: artifact.title })}
          disabled={remove.isPending}
          onClick={() => remove.mutate(artifact.id)}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {isOpen && (
        <div id={`artifact-detail-${artifact.id}`}>
          {detail.data ? (
            <ArtifactDetail artifact={detail.data} />
          ) : (
            <p className="border-t border-[var(--border)] px-3 py-2 text-xs text-[var(--muted-foreground)]">
              {detail.isError ? t('artifacts.openFailed') : t('artifacts.opening')}
            </p>
          )}
        </div>
      )}
    </li>
  )
}

/** The pre-existing session-only list, unchanged in behaviour. Reached
 * only when there is no local account to attach a durable artifact to. */
function SessionPinList({ entries }: { entries: QueryHistoryEntry[] }) {
  const { t } = useTranslation()
  const pinnedEntryIds = useChatStore((state) => state.pinnedEntryIds)
  const togglePinned = useChatStore((state) => state.togglePinned)
  const artifacts = useMemo(
    () => summarizeArtifacts(entries, pinnedEntryIds),
    [entries, pinnedEntryIds],
  )

  if (artifacts.length === 0) return null

  return (
    <section
      aria-label={t('artifacts.sessionTitle')}
      className="flex flex-col gap-2 rounded-lg border border-[var(--border)] bg-[var(--card)] p-3"
    >
      <h3 className="flex items-center gap-1.5 text-xs font-semibold">
        <Pin className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {t('artifacts.sessionTitle')} ({artifacts.length})
      </h3>
      {/* Said plainly rather than left to be discovered on reload. */}
      <p className="text-xs text-[var(--muted-foreground)]">{t('artifacts.sessionOnly')}</p>
      <ul className="flex flex-col gap-1">
        {artifacts.map((artifact) => (
          <li key={artifact.entryId} className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => scrollToTurn(artifact.entryId)}
              className="flex min-w-0 flex-1 items-center gap-2 rounded px-1.5 py-1 text-left text-xs hover:bg-[var(--muted)]"
            >
              {artifact.hasChart ? (
                <BarChart3 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              ) : (
                <Table2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              )}
              <span className="truncate">{artifact.question}</span>
              {artifact.rowCount !== null && (
                <span className="shrink-0 text-[var(--muted-foreground)]">
                  {t('artifacts.rows', { count: artifact.rowCount })}
                </span>
              )}
            </button>
            <Button
              variant="ghost"
              aria-label={t('artifacts.unpin', { question: artifact.question })}
              onClick={() => togglePinned(artifact.entryId)}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function ArtifactWorkspace({ entries }: { entries: QueryHistoryEntry[] }) {
  const { t } = useTranslation()
  const signedIn = useLocalAuthStore((state) => state.user !== null)
  const artifacts = useArtifacts()
  const [openId, setOpenId] = useState<string | null>(null)

  if (!signedIn) return <SessionPinList entries={entries} />

  const items = artifacts.data?.items ?? []
  if (items.length === 0) return null

  return (
    <section
      aria-label={t('artifacts.title')}
      className="flex flex-col gap-2 rounded-lg border border-[var(--border)] bg-[var(--card)] p-3"
    >
      <h3 className="flex items-center gap-1.5 text-xs font-semibold">
        <Pin className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {t('artifacts.title')} ({items.length})
      </h3>
      <ul className="flex flex-col gap-1">
        {items.map((artifact) => (
          <ArtifactRow
            key={artifact.id}
            artifact={artifact}
            entries={entries}
            isOpen={openId === artifact.id}
            onToggle={() => setOpenId(openId === artifact.id ? null : artifact.id)}
          />
        ))}
      </ul>
    </section>
  )
}

/** The save control shown on a turn. Kept here so the workspace and its
 * control share one definition of what saving means.
 *
 * Saves durably when there is a local account **and** the turn was
 * actually persisted (`message_id` is set -- a turn answered without
 * local auth, or whose persistence failed, has none). Otherwise it falls
 * back to the session pin, which is what a caller with no account has
 * always had. One button, and it says which it is doing.
 */
export function SaveArtifactButton({
  entry,
}: {
  entry: Pick<QueryHistoryEntry, 'entryId' | 'question' | 'finalState'>
}) {
  const { t } = useTranslation()
  const signedIn = useLocalAuthStore((state) => state.user !== null)
  const pinnedEntryIds = useChatStore((state) => state.pinnedEntryIds)
  const togglePinned = useChatStore((state) => state.togglePinned)
  const artifacts = useArtifacts()
  const save = useSaveArtifact()
  const remove = useDeleteArtifact()

  const conversationId = entry.finalState.conversation_id ?? null
  const messageId = entry.finalState.message_id ?? null
  const durable = signedIn && conversationId !== null && messageId !== null
  const existing = durable
    ? (artifacts.data?.items ?? []).find((item) => item.message_id === messageId)
    : undefined

  const pinned = durable ? existing !== undefined : pinnedEntryIds.has(entry.entryId)
  const label = pinned
    ? t('artifacts.unpin', { question: entry.question })
    : t('artifacts.pin', { question: entry.question })

  // The hint and the accessible name deliberately differ and are not
  // merged: `label` names *which* turn this pins (it interpolates the
  // question, which a screen-reader user needs to tell one pin from the
  // next), while the hint explains whether the pin is durable or
  // session-only. A tooltip repeating the whole question would be a
  // paragraph hanging off a 32px button.
  const hint = durable
    ? pinned
      ? t('artifacts.unpinHint')
      : t('artifacts.pinHint')
    : t('artifacts.sessionOnlyHint')

  return (
    <ActionTooltip label={hint}>
      <Button
        variant="ghost"
        size="icon"
        aria-label={label}
        aria-pressed={pinned}
        disabled={save.isPending || remove.isPending}
        onClick={() => {
          if (!durable) {
            togglePinned(entry.entryId)
            return
          }
          if (existing) remove.mutate(existing.id)
          else
            save.mutate({
              conversation_id: conversationId as string,
              message_id: messageId as string,
            })
        }}
      >
        <Pin className={pinned ? 'h-4 w-4 fill-current' : 'h-4 w-4'} />
      </Button>
    </ActionTooltip>
  )
}
