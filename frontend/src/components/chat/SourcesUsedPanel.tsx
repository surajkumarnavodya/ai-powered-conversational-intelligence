import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Markdown } from '@/components/ui/markdown'
import type { AskResponse } from '@/lib/types'
import { AttachmentResultCard } from './AttachmentResultCard'
import { MediaResultCard } from './MediaResultCard'
import { MediaSearchResultCard } from './MediaSearchResultCard'
import { SourceAnswerCard } from './SourceAnswerCard'

const SOURCE_CHIP_LABELS: Record<string, string> = {
  sql: 'Database',
  documents: 'Documents',
  policy: 'Policy',
  web: 'Web',
  generation: 'Generated Media',
  media_search: 'Media Library',
  attachments: 'Attached Files',
  // A conversation turn saved before per-source metadata existed (see
  // frontend/src/lib/history.ts::askResponseFromPersistedMessage's own
  // "legacy record without recoverable structure" case) -- the answer text
  // itself was always preserved, only which source(s) produced it wasn't.
  legacy: 'Saved answer',
}

/** Shows which source(s) contributed, and either the synthesized combined
 * answer or each
 * non-SQL source's own card -- never both, and never a "not found in X"
 * aside once another source already answered (see agent/orchestrator/
 * nodes.py::synthesis_node, which already filters that out server-side).
 *
 * `generation_result`/`media_search_result` are the exceptions to "never
 * both": the generated image/video, and any retrieved media-library
 * hits, always render as their own component *in addition to* the
 * synthesized text, regardless of whether the router also picked another
 * source alongside "generation"/"media_search" (it does, often enough
 * that this must be handled, not just the common single-source case --
 * see synthesis_node's docstring). Rendering them inside the ternary
 * would mean a real user-reported bug: ask to "generate an image of X",
 * the router also picks "web", and the image silently never appears
 * because only the combined text branch rendered. */
/** The "Sources: [Database] [Web]" chip row on its own.
 *
 * Split out of `SourcesUsedPanel` so `TurnCard` can place it at the foot
 * of the turn, under the answer actions, instead of above the answer --
 * attribution reads better after the thing it attributes, and the old
 * position put a row of chips between the question and the first line of
 * the response. The labels stay in this file so `SOURCE_CHIP_LABELS` keeps
 * one owner.
 *
 * Returns `null` on an empty `sources_used` exactly as the panel does, so
 * a turn with no recorded source renders no stray "Sources:" label.
 */
export function SourcesUsedChips({ state }: { state: AskResponse }) {
  const { t } = useTranslation()
  if (state.sources_used.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
      <span>{t('chat.sources')}:</span>
      {state.sources_used.map((source) => (
        <Badge key={source} tone="accent">
          {SOURCE_CHIP_LABELS[source] ?? source}
        </Badge>
      ))}
    </div>
  )
}

export function SourcesUsedPanel({ state, entryId }: { state: AskResponse; entryId: string }) {
  // A no-retrieval route ("general_knowledge"/"conversation") answers
  // without running any source, so `sources_used` is legitimately empty
  // while `synthesized_answer` holds the whole answer -- gating on the
  // source list alone rendered nothing at all for those turns. The chip
  // row above still returns null here, because there is genuinely no
  // attribution to show.
  if (state.sources_used.length === 0 && !state.synthesized_answer) return null

  return (
    <div className="flex flex-col gap-2">
      {state.synthesized_answer ? (
        <Markdown>{state.synthesized_answer}</Markdown>
      ) : (
        <>
          {state.document_result && <SourceAnswerCard sourceKey="document_result" result={state.document_result} />}
          {state.policy_result && <SourceAnswerCard sourceKey="policy_result" result={state.policy_result} />}
          {state.web_result && <SourceAnswerCard sourceKey="web_result" result={state.web_result} />}
          {state.attachment_result && <AttachmentResultCard result={state.attachment_result} />}
        </>
      )}
      {state.generation_result && <MediaResultCard result={state.generation_result} entryId={entryId} />}
      {state.media_search_result && <MediaSearchResultCard result={state.media_search_result} />}
    </div>
  )
}
