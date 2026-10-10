import { BarChart3, Pin, Table2, X } from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { isSqlResult, type QueryHistoryEntry } from '@/lib/history'
import { collapseQuestionWhitespace } from '@/lib/questionLabel'
import { useChatStore } from '@/store/chatStore'

const MAX_LABEL_CHARS = 70

/** The analyses the user pinned, as a compact worklist.
 *
 * A long conversation buries the two or three results that actually
 * matter. This keeps them one click away without copying them: an
 * artifact is a **reference** to a turn by its stable `entryId`, so the
 * rows, chart and insight shown when you jump to it are the live ones,
 * never a snapshot that can go stale or disagree with the turn above it.
 *
 * Jumping reuses the anchor `QuestionNavigator` already relies on
 * (`turn-${entryId}`), so there is one scroll mechanism in the chat
 * column, not two.
 *
 * Session-scoped, like everything else in `chatStore`: a reload clears
 * the pins. That is the same disclosed behaviour `queryHistory` has, and
 * persisting them would mean a new server-side artifact store -- a
 * larger decision than this surface needs.
 */
export interface ArtifactSummary {
  entryId: string
  question: string
  rowCount: number | null
  hasChart: boolean
  hasResult: boolean
}

/** Builds the worklist from the pinned ids and the live turns.
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

export function ArtifactWorkspace({ entries }: { entries: QueryHistoryEntry[] }) {
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
      aria-label={t('artifacts.title')}
      className="flex flex-col gap-2 rounded-lg border border-[var(--border)] bg-[var(--card)] p-3"
    >
      <h3 className="flex items-center gap-1.5 text-xs font-semibold">
        <Pin className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {t('artifacts.title')} ({artifacts.length})
      </h3>
      <ul className="flex flex-col gap-1">
        {artifacts.map((artifact) => (
          <li key={artifact.entryId} className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                document
                  .getElementById(`turn-${artifact.entryId}`)
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }}
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

/** The pin toggle shown on a turn. Kept here so the workspace and its
 * control share one definition of what pinning means. */
export function PinTurnButton({ entryId, question }: { entryId: string; question: string }) {
  const { t } = useTranslation()
  const pinnedEntryIds = useChatStore((state) => state.pinnedEntryIds)
  const togglePinned = useChatStore((state) => state.togglePinned)
  const pinned = pinnedEntryIds.has(entryId)

  return (
    <Button
      variant="ghost"
      aria-label={pinned ? t('artifacts.unpin', { question }) : t('artifacts.pin', { question })}
      aria-pressed={pinned}
      onClick={() => togglePinned(entryId)}
      title={pinned ? t('artifacts.unpinHint') : t('artifacts.pinHint')}
    >
      <Pin className={pinned ? 'h-4 w-4 fill-current' : 'h-4 w-4'} />
    </Button>
  )
}
