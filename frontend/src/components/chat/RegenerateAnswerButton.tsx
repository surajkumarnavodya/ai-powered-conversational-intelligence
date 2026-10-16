import { RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { useChatStore } from '@/store/chatStore'

/** Asks this turn's question again, unedited, appending a fresh turn.
 *
 * The store action and the label this renders both predate the button:
 * `chatStore.rerunEntry` was implemented and `chat.rerun` was translated
 * into all five locales, but nothing ever called either, so the feature
 * existed in the codebase and not in the product. This is the caller.
 *
 * **Why re-asking rather than replacing.** `rerunEntry` routes through
 * `askQuestion`, so the new answer arrives as its own turn and the
 * original stays on screen. That is deliberate and not merely simpler: the
 * transcript is append-only everywhere else in this app, `conversation_history`
 * is built from it, and silently overwriting a turn would change what the
 * *next* question is resolved against without the person seeing it happen.
 * Two visible answers to the same question is also the honest rendering of
 * what a non-zero-temperature model just did.
 *
 * **Why it disables while a question is pending.** A second `/ask` started
 * before the first settles would race for the same `pendingQuestion` slot.
 * `ChatInput`'s send button and `FollowUpSuggestions`' chips already gate
 * on exactly this; reusing the same flag keeps one answer to "is the app
 * busy" rather than three.
 *
 * Nothing here is a security surface: a re-run is an ordinary `/ask` with
 * the same question text, so validation, authorization, governance and the
 * row limits all apply exactly as they did the first time.
 */
export function RegenerateAnswerButton({ entryId }: { entryId: string }) {
  const { t } = useTranslation()
  const rerunEntry = useChatStore((state) => state.rerunEntry)
  const pendingQuestion = useChatStore((state) => state.pendingQuestion)
  const isBusy = pendingQuestion !== null

  return (
    <Button
      size="sm"
      variant="ghost"
      disabled={isBusy}
      onClick={() => void rerunEntry(entryId)}
      title={t('chat.rerun')}
    >
      <RotateCcw className="h-3.5 w-3.5" />
      {t('chat.rerun')}
    </Button>
  )
}
