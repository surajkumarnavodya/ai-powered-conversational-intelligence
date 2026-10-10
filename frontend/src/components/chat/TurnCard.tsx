import { AlertTriangle, ShieldAlert, Sparkles } from 'lucide-react'
import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { ExecutionStatusBar } from '@/components/sql/ExecutionStatusBar'
import { ClaimEvidence } from '@/components/analytics/ClaimEvidence'
import { insightEvidenceSummary } from '@/lib/evidence'
import { QueryLifecycle } from '@/components/sql/QueryLifecycle'
import { SqlIntelligencePanel } from '@/components/sql/SqlIntelligencePanel'
import { GoldenFeedbackWidget } from '@/components/sql/GoldenFeedbackWidget'
import { ResultsTable } from '@/components/sql/ResultsTable'
import { Markdown } from '@/components/ui/markdown'
import { useHealth } from '@/hooks/queries'
import { buildAnswerMarkdown, buildSpokenAnswerText, isSqlResult, type QueryHistoryEntry } from '@/lib/history'
import { useChatStore } from '@/store/chatStore'
import { useSettingsStore } from '@/store/settingsStore'
import { ChatMessage } from './ChatMessage'
import { CopyAnswerButton } from './CopyAnswerButton'
import { DownloadAnswerButton } from './DownloadAnswerButton'
import { AgentExecutionTimeline } from './AgentExecutionTimeline'
import { PinTurnButton } from './ArtifactWorkspace'
import { FollowUpSuggestions } from './FollowUpSuggestions'
import { ReadAloudButton } from './ReadAloudButton'
import { ResponseFeedbackWidget } from './ResponseFeedbackWidget'
import { RetryTimeline } from './RetryTimeline'
import { SentAttachmentsPreview } from './SentAttachmentsPreview'
import { SourceLineagePanel } from './SourceLineagePanel'
import { SourcesUsedPanel } from './SourcesUsedPanel'
import { TimingBadge } from './TimingBadge'

// Chart.js is the largest remaining chunk in this app -- lazy-loaded so a
// turn never pays for it unless the user actually clicks "Visualize"
// (ChartSection.tsx itself imports ResultChart.tsx, so this one lazy
// boundary covers the whole opt-in chart flow: the picker, the customize
// panel, and the chart.js renderer). Chart generation is optional by
// design (see ChartSection.tsx's own docstring) -- this lazy boundary is
// what makes "optional" also mean "not even downloaded" for the common
// case of a turn nobody ever visualizes.
const ChartSection = lazy(() =>
  import('@/components/sql/ChartSection').then((m) => ({ default: m.ChartSection })),
)

// Prompt 30's analysis panels render through the same chart renderer
// (ResultChart -> Chart.js), so they get the identical lazy boundary --
// reading a turn must not pull Chart.js into the main bundle.
const AnalyticsSummary = lazy(() =>
  import('@/components/analytics/AnalyticsSummary').then((m) => ({ default: m.AnalyticsSummary })),
)

const BLOCKED_STATUSES = new Set([
  'failed',
  'needs_clarification',
  'rejected',
  'rate_limited',
  // A reconstructed history entry with no matching assistant row at all
  // (see history.ts::serverMessagesToQueryHistory's own doc comment on a
  // truncated/failed persistence) -- there is genuinely nothing to show,
  // and showing the SQL editor + "Confirm and Run" here would offer to run
  // a query that was never actually generated.
  'pending',
])

/** One full question+answer turn -- always fully rendered (never replaced
 * by a later turn), so the whole conversation stays visible and each
 * answer stays right next to the question that produced it. Every piece
 * of interactive state below (the SQL editor, Confirm-and-Run, golden
 * feedback) is scoped to *this* entry via its entryId, so editing one
 * turn's SQL box can never affect another's. */
/** When the question was asked, in the browser's locale -- e.g. "Oct 4, 2026, 10:32 AM".
 * Unparseable timestamps render as nothing rather than "Invalid Date". */
function formatTurnTime(timestamp: string): string {
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function TurnCard({ entry, isMultiDb }: { entry: QueryHistoryEntry; isMultiDb: boolean }) {
  const { t } = useTranslation()
  const setEditableSql = useChatStore((state) => state.setEditableSql)
  const confirmAndRun = useChatStore((state) => state.confirmAndRun)
  const confirmingEntryId = useChatStore((state) => state.confirmingEntryId)
  const setChartOptions = useChatStore((state) => state.setChartOptions)
  const askQuestion = useChatStore((state) => state.askQuestion)
  // A follow-up chip is disabled while another question is in flight,
  // so a click cannot queue a second run behind the first.
  const pendingQuestion = useChatStore((state) => state.pendingQuestion)
  // Same two-flag gate ChatInput.tsx's own mic button already uses --
  // the server's infra flag (GET /health) and the user's own per-device
  // opt-in. Read Aloud is a manual, on-demand TTS call (never automatic),
  // so it needs nothing voice-input-specific -- it works identically for
  // a typed question, a voice question, or a turn reloaded from a past
  // conversation, since all it ever needs is the answer text already on
  // screen. See ReadAloudButton.tsx's own docstring for why this is not
  // the same thing as `entry.spokenAudioUrl`'s auto-replay element below.
  const health = useHealth()
  const voiceModeEnabled = useSettingsStore((state) => state.voiceModeEnabled)
  const showReadAloud = Boolean(health.data?.voice_enabled) && voiceModeEnabled

  const state = entry.finalState
  const showSqlPanel = isSqlResult(state.sources_used) && !BLOCKED_STATUSES.has(entry.agentStatus)
  // The editor + "Confirm and Run" specifically (as opposed to the
  // read-only schema/plan context above it) require actual SQL text to
  // edit or a result already confirmed -- `showSqlPanel` alone used to be
  // treated as "safe to show an editor," but a reconstructed history entry
  // (or, in principle, any SQL-path turn that produced no candidate SQL at
  // all) has neither, and showing an empty box with a working "Confirm and
  // Run" button next to it offered to run nothing. See CLAUDE.md's chat-
  // history section for the reload-specific case this was found from.
  const hasEditableSql = entry.editableSql.trim().length > 0
  const showSqlEditor = showSqlPanel && (hasEditableSql || entry.confirmedColumns !== null)
  const isConfirming = confirmingEntryId === entry.entryId
  const answerMarkdown = buildAnswerMarkdown(entry)

  return (
    <div id={`turn-${entry.entryId}`} className="flex scroll-mt-4 flex-col gap-3">
      <SentAttachmentsPreview attachments={entry.sentAttachments} />
      <div className="flex justify-end">
        <ChatMessage message={{ role: 'user', content: entry.question }} />
      </div>

      <div className="flex flex-col gap-3 pl-10">
        <div className="flex flex-wrap items-center gap-2">
          <TimingBadge mode="done" durationMs={entry.answerDurationMs} />
          <time dateTime={entry.timestamp} className="text-xs text-[var(--muted-foreground)]">
            {formatTurnTime(entry.timestamp)}
          </time>
          {answerMarkdown && (
            <>
              <CopyAnswerButton answer={answerMarkdown} />
              <DownloadAnswerButton question={entry.question} answer={answerMarkdown} />
              <PinTurnButton entryId={entry.entryId} question={entry.question} />
              <ResponseFeedbackWidget entryId={entry.entryId} />
              {showReadAloud && <ReadAloudButton text={buildSpokenAnswerText(entry)} />}
            </>
          )}
          {entry.spokenAudioUrl && (
            // Playback itself already happened once, automatically, via
            // `useVoiceConversation`'s own `<audio>` element as part of the
            // hands-free conversation loop -- this is a manual-replay
            // control only (no `autoPlay`, or the answer would be spoken
            // twice: once here, once by the hook). Kept small and visible
            // so the user can replay/pause/mute it. Deliberately not
            // revoked on unmount: switching conversations and back must
            // not break replay, and one blob URL per voice turn is a
            // bounded, accepted cost for the life of the tab.
            <audio src={entry.spokenAudioUrl} controls className="h-8 max-w-[200px]" />
          )}
        </div>

        {state.followup_classification === 'followup' && state.followup_resolved_against && (
          <p className="text-xs text-[var(--muted-foreground)]">
            ↪ {t('chat.followingUpOn')}: "{state.followup_resolved_against.question}"
          </p>
        )}

        {entry.agentStatus === 'rate_limited' && (
          <p className="text-sm text-[var(--warning)]">
            {state.rate_limit_message ?? 'Too many questions -- please wait a moment.'}
          </p>
        )}
        {entry.agentStatus === 'rejected' && (
          <p className="text-sm text-[var(--warning)]">
            {state.rejection_message ?? 'That question could not be processed.'}
          </p>
        )}
        {entry.agentStatus === 'needs_clarification' && (
          <p className="text-sm text-[var(--warning)]">
            Needs clarification: {state.clarification_message}
          </p>
        )}
        {state.permission_denied_notice && (
          <p className="text-sm text-[var(--warning)]">{state.permission_denied_notice}</p>
        )}
        {entry.agentStatus === 'pending' && (
          <p className="text-sm text-[var(--muted-foreground)]">
            {t('chat.noRecordedAnswer')}
          </p>
        )}
        {entry.agentStatus === 'failed' && (
          <div className="flex flex-col gap-2">
            {state.restricted_field_notice && (
              <p className="flex items-center gap-1.5 text-sm text-[var(--warning)]">
                <ShieldAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
                {state.restricted_field_notice}
              </p>
            )}
            <p className="text-sm text-[var(--danger)]">
              {isSqlResult(state.sources_used) ? (
                <>
                  Agent could not produce a working query:{' '}
                  {state.failure_explanation ?? state.error_history.at(-1) ?? 'Unknown error.'}
                </>
              ) : (
                (state.failure_explanation ?? state.error_history.at(-1) ?? 'The request failed.')
              )}
            </p>
            {state.sql && (
              <pre className="whitespace-pre-wrap break-words rounded bg-[var(--muted)] p-2 font-mono text-xs">
                {state.sql}
              </pre>
            )}
          </div>
        )}

        {isSqlResult(state.sources_used) && (
          <QueryLifecycle entry={entry} isConfirming={isConfirming} />
        )}
        {showSqlPanel && <ExecutionStatusBar entry={entry} />}
        <FollowUpSuggestions
          recommendations={state.recommendations}
          onAsk={(question) => void askQuestion(question)}
          disabled={pendingQuestion !== null}
        />
        {state.attempt_history.length > 0 && <RetryTimeline attempts={state.attempt_history} />}
        <AgentExecutionTimeline timings={state.stage_timings ?? []} />
        <SourcesUsedPanel state={state} entryId={entry.entryId} />
        {/* Cross-source provenance for this answer. Sits after the
            answer body and before the SQL-specific panel, because it
            describes every source -- not just the SQL path -- and is
            the only provenance view a document/web/media answer has. */}
        <SourceLineagePanel state={state} />
        {showSqlPanel && (
          <>
            {/* One panel for everything about this SQL: the collapsed
                title is the status line, and the editor, Confirm and Run,
                metadata, plan and schema sit inside. It replaces the four
                sibling expanders this used to render (query information,
                schema context, query plan, SQL) -- same components, one
                disclosure. See SqlIntelligencePanel's own docstring. */}
            <SqlIntelligencePanel
              entry={entry}
              showDatabase={isMultiDb}
              onSqlChange={(sql) => setEditableSql(entry.entryId, sql)}
              onConfirmAndRun={() => void confirmAndRun(entry.entryId)}
              isConfirming={isConfirming}
              showEditor={showSqlEditor}
            />

            {entry.confirmedError && (
              <p className="rounded-md border border-[var(--danger)]/30 bg-[var(--danger)]/10 p-3 text-sm text-[var(--danger)]">
                {entry.confirmedError}
              </p>
            )}

            {entry.confirmedColumns && entry.confirmedRows && (
              <div className="flex flex-col gap-4">
                <GoldenFeedbackWidget entryId={entry.entryId} />
                {state.low_confidence_notice && entry.editableSql === state.sql && (
                  <p className="flex items-center gap-1.5 text-xs text-[var(--warning)]">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                    {state.low_confidence_notice}
                  </p>
                )}
                {state.insight && entry.editableSql === state.sql && (
                  <div className="rounded-lg border-l-4 border-[var(--accent)] bg-[var(--accent-soft)] p-3">
                    {/* The insight is marked `estimate`, not `computed`,
                        and that is the deliberate conservative call. Every
                        number in it did pass `agent.insight
                        .is_insight_grounded` server-side -- an insight with
                        an unsupported number is dropped and never reaches
                        here at all -- but the *wording* and any causal or
                        qualitative framing are the model's. Labelling the
                        whole sentence "Computed from data" would extend the
                        numbers' verification to an interpretation nobody
                        verified. The expandable evidence states what was
                        actually checked, so the conservative badge costs
                        the reader nothing. */}
                    <ClaimEvidence
                      status="estimate"
                      claim={
                        <span className="flex gap-2">
                          <Sparkles className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                          <Markdown className="inline">{state.insight}</Markdown>
                        </span>
                      }
                      summary={insightEvidenceSummary(
                        state.insight_numbers_checked ?? null,
                        entry.confirmedRows?.length ?? state.row_count ?? null,
                      )}
                      details={
                        <p className="text-[var(--muted-foreground)]">
                          {t('evidence.insightDetail')}
                        </p>
                      }
                    />
                  </div>
                )}
                {entry.editableSql === state.sql && (
                  <Suspense
                    fallback={<p className="text-xs text-[var(--muted-foreground)]">{t('common.loading')}</p>}
                  >
                    <AnalyticsSummary
                      state={state}
                      cacheStatus={entry.confirmedCacheStatus ?? null}
                      onAsk={(question) => void askQuestion(question)}
                    />
                  </Suspense>
                )}
                <ResultsTable columns={entry.confirmedColumns} rows={entry.confirmedRows} />
                <Suspense
                  fallback={<p className="text-xs text-[var(--muted-foreground)]">{t('common.loading')}</p>}
                >
                  <ChartSection
                    columns={entry.confirmedColumns}
                    rows={entry.confirmedRows}
                    columnTypes={entry.confirmedColumnTypes ?? {}}
                    chartRecommendation={entry.confirmedChartRecommendation}
                    truncated={entry.confirmedTruncated}
                    chartOptions={entry.chartOptions}
                    onChartOptionsChange={(options) => setChartOptions(entry.entryId, options)}
                  />
                </Suspense>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
