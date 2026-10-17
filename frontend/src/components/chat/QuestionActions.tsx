import { Share2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { CopyButton } from '@/components/ui/copy-button'
import { useChatStore } from '@/store/chatStore'
import { ShareModal } from './ShareModal'

/** Copy and Share for one question, for `ChatMessage`'s `actions` slot.
 *
 * Kept out of `ChatMessage` deliberately. That component is also rendered
 * by `SharedConversation`, which a viewer holding no roles can open, and
 * by `PendingTurn`; giving it a chat-store dependency and a modal would
 * push both of those into carrying machinery they must not offer. This
 * lives beside `TurnCard`, the one caller that has a conversation to act
 * on.
 *
 * **Share shares the conversation, not the turn.** There is no per-turn
 * share in this app and this does not invent one: it opens the same
 * `ShareModal` the history list already opens, against the active
 * conversation, so there stays one sharing path with one set of access
 * controls and one re-governing read-back (`api/shares.py`, and
 * `tests/security/test_shared_results_are_regoverned.py`). The button is
 * simply absent when the active conversation is not one the server knows
 * about -- an unsaved local conversation has nothing to share, and
 * `conversations` is the same map the sidebar's own share action reads.
 */
export function QuestionActions({ question }: { question: string }) {
  const { t } = useTranslation()
  const activeConversationId = useChatStore((state) => state.activeConversationId)
  const conversation = useChatStore((state) => state.conversations[state.activeConversationId])
  const [isSharing, setIsSharing] = useState(false)

  return (
    <>
      {/* Plain-text copy, not the markdown dual-write `CopyAnswerButton`
          uses: a question is what the person typed, so it should come
          back exactly as typed. */}
      <CopyButton
        getText={() => question}
        label={t('chat.copyQuestion')}
        className="h-7 w-7 sm:h-7 sm:w-7"
      />
      {conversation && (
        <Button
          size="icon"
          variant="ghost"
          onClick={() => setIsSharing(true)}
          aria-label={t('share.menuItem')}
          title={t('share.menuItem')}
          className="h-7 w-7 sm:h-7 sm:w-7"
        >
          <Share2 className="h-3.5 w-3.5" />
        </Button>
      )}
      {isSharing && conversation && (
        <ShareModal
          conversationId={activeConversationId}
          conversationTitle={conversation.title}
          onClose={() => setIsSharing(false)}
        />
      )}
    </>
  )
}
