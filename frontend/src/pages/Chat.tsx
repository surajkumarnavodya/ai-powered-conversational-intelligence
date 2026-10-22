import { ChatInput } from '@/components/chat/ChatInput'
import { ArtifactWorkspace } from '@/components/chat/ArtifactWorkspace'
import { ConversationContextPanel } from '@/components/chat/ConversationContextPanel'
import { ChatLiveRegion } from '@/components/chat/ChatLiveRegion'
import { PendingTurn } from '@/components/chat/PendingTurn'
import { QuestionQueuePanel } from '@/components/sql/QuestionQueuePanel'
import { QuestionNavigator } from '@/components/chat/QuestionNavigator'
import { ScrollToBottomButton } from '@/components/chat/ScrollToBottomButton'
import { TurnCard } from '@/components/chat/TurnCard'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useHealth } from '@/hooks/queries'
import { useScrollToBottom } from '@/hooks/useScrollToBottom'
import { useChatStore } from '@/store/chatStore'

export function Chat() {
  const health = useHealth()

  const queryHistory = useChatStore((state) => state.queryHistory)
  const pendingQuestion = useChatStore((state) => state.pendingQuestion)
  const activeConversationId = useChatStore((state) => state.activeConversationId)

  const isMultiDb = (health.data?.databases.length ?? 0) > 1
  const isEmptyConversation = queryHistory.length === 0 && pendingQuestion === null

  const scrollRef = useRef<HTMLDivElement>(null)
  // Single scroll-state source of truth, shared by the auto-follow effect
  // below and the floating "Jump to Bottom" button -- never a second,
  // independent scroll system (see useScrollToBottom.ts's own docstring).
  const { isAtBottom, isAtBottomRef, scrollToBottom } = useScrollToBottom(scrollRef)

  // Keep the newest turn in view -- but only when the user was already at
  // (or very near) the bottom before this turn arrived: scrolling up to
  // reread an earlier answer must never get yanked back down just because
  // a new question/answer lands elsewhere in the session. The floating
  // button below is how the user gets back to the latest content once
  // they've scrolled away from it. Switching to a *different* conversation
  // is a fresh view, not new content arriving while reading the current
  // one, so that case always jumps to the latest turn instantly,
  // unconditionally -- the same behavior this effect used to have for
  // every case before the "don't yank the user down" fix above.
  const prevConversationIdRef = useRef(activeConversationId)
  useEffect(() => {
    const switchedConversation = prevConversationIdRef.current !== activeConversationId
    prevConversationIdRef.current = activeConversationId
    if (switchedConversation) {
      isAtBottomRef.current = true
      scrollToBottom('auto')
      return
    }
    if (!isAtBottomRef.current) return
    scrollToBottom()
  }, [activeConversationId, queryHistory.length, pendingQuestion, isAtBottomRef, scrollToBottom])

  // Live-measured composer height -- so the floating button (below) sits
  // just above it regardless of how tall the composer currently is (a
  // grown multi-line textarea, an attachment preview row, a narrow mobile
  // viewport), never covering the input box.
  const composerRef = useRef<HTMLDivElement>(null)
  const [composerHeight, setComposerHeight] = useState(0)
  useLayoutEffect(() => {
    const el = composerRef.current
    if (!el) return
    const measure = () => setComposerHeight(el.offsetHeight)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    // Full-width, single-scrollbar layout (matches ChatGPT): this outer
    // column never scrolls itself -- the middle region below is the only
    // scroll container, spanning the full remaining width so its
    // scrollbar sits flush against the browser's right edge, with the
    // actual message content centered inside it via max-w-chat/mx-auto.
    //
    // Every turn ever asked this session renders here, in order, each
    // fully expanded (question directly above its own answer) -- not just
    // the latest one -- so scrolling up always reveals the full prior
    // conversation, and each turn keeps its own editable SQL/confirmed
    // result independently (see TurnCard).
    // `relative` makes this the positioning context for QuestionNavigator
    // (an absolutely-positioned overlay, below) -- self-contained, rather
    // than relying on AppShell's own <main> happening to be `position:
    // relative` for an ancestor this component doesn't own.
    <div className="relative flex h-full min-h-0 flex-col">
      <ChatLiveRegion
        pendingQuestion={pendingQuestion}
        latestEntryId={queryHistory.at(-1)?.entryId ?? null}
      />
      <QuestionNavigator entries={queryHistory} scrollContainerRef={scrollRef} />
      <ScrollToBottomButton
        visible={!isAtBottom}
        onClick={() => scrollToBottom()}
        bottomOffsetPx={composerHeight + 16}
      />
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {/* `max-w-chat` (48rem, --chat-column in index.css) is the one
            owner of the conversation's measure -- the composer below
            sits in this same box, so the two can never drift apart. The
            gutter grows with the viewport instead of being a fixed 16px
            at every size: edge-tight on a phone, roomier from `sm` up. */}
        <div className="mx-auto flex min-h-full w-full max-w-chat flex-col px-3 pb-4 pt-4 sm:px-6 sm:pt-6">
          <div className="flex flex-col gap-8">
            {queryHistory.map((entry) => (
              <TurnCard key={entry.entryId} entry={entry} isMultiDb={isMultiDb} />
            ))}
            {pendingQuestion && <PendingTurn pendingQuestion={pendingQuestion} />}
            {/* Questions submitted while another was already in flight.
                Renders nothing at all when there are none, and nothing on
                a deployment whose auth mode cannot attribute a job to a
                user -- so a single-question session looks exactly as it
                did before. It deliberately does NOT render answers:
                `TurnCard` already owns rendering a result, a chart, an
                insight and citations, and a second renderer for those
                would be a second owner of every one of them. */}
            <QuestionQueuePanel />
          </div>

          {/* Sticky at the bottom of the scroll region: the composer stays in view
              while the messages scroll beneath it, and the scrollbar itself runs
              all the way to the bottom edge of the page.

              It spans the full reading column, with no left inset. It used
              to carry `pl-10` to line up with the answer text's own
              avatar-width indent, but that pushed the composer 40px right
              of centre -- unmissable on an empty conversation, where the
              box is the only thing on screen. Matching the column the
              turns sit in gives it equal left and right margins; the
              answer's indent stays a property of the answer. */}
          {/* `my-auto` on an empty conversation, `mt-auto` once there is
              one: with nothing to read, pinning the box to the bottom
              left the whole screen empty above it, so it centres in the
              space instead and drops to the bottom as soon as the first
              turn gives it something to sit under. */}
          <div
            ref={composerRef}
            className={`sticky bottom-0 bg-[var(--background)] pb-3 pt-2 sm:pb-4 ${
              isEmptyConversation ? 'my-auto' : 'mt-auto'
            }`}
          >
            {/* Above the composer on purpose: it describes what the
                *next* question will carry, so it belongs with the input
                rather than with any one answer. Collapsed by default --
                it answers a question the reader only sometimes has. */}
            {/* The pinned worklist sits with the composer: it is a
                place to return to, not part of any one answer. */}
            <ArtifactWorkspace entries={queryHistory} />
            <ConversationContextPanel entries={queryHistory} />
            <ChatInput />
          </div>
        </div>
      </div>
    </div>
  )
}
