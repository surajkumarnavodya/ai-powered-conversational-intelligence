import { Loader2, Mic, Send, Square, Volume2 } from 'lucide-react'
import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
} from 'react'
import { useTranslation } from 'react-i18next'
import { ChatAttachmentChip } from '@/components/chat/ChatAttachmentChip'
import { ImageUploader } from '@/components/image/ImageUploader'
import { ComposerModelPicker } from '@/components/chat/ComposerModelPicker'
import { SourcePicker } from '@/components/chat/SourcePicker'
import { ImageViewer } from '@/components/image/ImageViewer'
import { OcrResultDialog } from '@/components/image/OcrResultDialog'
import { RemoveTextDialog } from '@/components/image/RemoveTextDialog'
import { ResizeImageDialog } from '@/components/image/ResizeImageDialog'
import { Button } from '@/components/ui/button'
import { useToast } from '@/components/ui/toast'
import { useChatAttachments } from '@/hooks/useChatAttachments'
import { useAttachmentCapabilities, useHealth } from '@/hooks/queries'
import { useVoiceConversation } from '@/hooks/useVoiceConversation'
import type { SentAttachmentPreview } from '@/lib/history'
import { useSettingsStore } from '@/store/settingsStore'
import { useChatStore } from '@/store/chatStore'

// Konva + react-konva are a genuinely large dependency (see
// docs/frontend-ui-audit.md's bundle-size note) that only the image editor
// needs -- lazy-loaded so opening the app, or attaching/viewing an image
// without editing it, never pays that cost. Only actually clicking "Edit"
// (setEditingImageId, below) triggers this import.
const ImageEditor = lazy(() =>
  import('@/components/image/ImageEditor').then((module) => ({ default: module.ImageEditor })),
)

const MAX_WORDS = 500
// Caps how tall the box can grow before it scrolls internally instead --
// otherwise a very long question could push the send button (and
// eventually the whole input bar) off-screen.
const MAX_HEIGHT_PX = 240

function countWords(text: string): number {
  const trimmed = text.trim()
  return trimmed === '' ? 0 : trimmed.split(/\s+/).length
}

/** Sizes the textarea to its content: one line when empty or short, growing
 * with each wrapped/entered line up to MAX_HEIGHT_PX, then scrolling. The
 * CSS `min-h-8` keeps the one-line minimum level with the 32px send and mic
 * buttons. Setting `auto` first lets the box also shrink again when text is
 * removed. */
function resizeToFitContent(el: HTMLTextAreaElement): void {
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`
}

/** The composer: textarea + mic + send, always in the same layout -- voice
 * mode never replaces it with a separate card. The mic button toggles in
 * place to a stop button while listening (`voice.phase === 'listening'`),
 * so "stop" always sits exactly where "mic" was, and there is never a
 * second button for it. A finished voice turn writes its raw heard text
 * straight into the textarea, same as if it had been typed -- the user
 * reviews/edits it there and presses the existing Send button (or Enter)
 * to confirm; nothing is ever auto-submitted. */
export function ChatInput({ onSend }: { onSend?: () => void } = {}) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  // True only while `value` is (still) exactly what voice transcription
  // produced -- the one signal `submit()` uses to tag the question as
  // voice-originated (so `chatStore.askQuestion` synthesizes a spoken
  // answer) and to know whether to play that answer back automatically.
  // Cleared on submit and on any manual edit -- editing a voice transcript
  // still counts as voice-originated (the user is correcting what was
  // heard, not writing a fresh question), so it's only cleared once the
  // box is emptied and typed into from scratch.
  const [fromVoice, setFromVoice] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const health = useHealth()
  const voiceModeEnabled = useSettingsStore((state) => state.voiceModeEnabled)
  const showVoiceButton = Boolean(health.data?.voice_enabled) && voiceModeEnabled

  const pendingQuestion = useChatStore((state) => state.pendingQuestion)
  // Nothing asked yet in this conversation: the composer is the only
  // thing on screen, so its placeholder is the one place that can say
  // what to do with it. Once a turn exists the shorter prompt is enough
  // -- the instruction has been demonstrated by then. A question still
  // in flight counts as "asked", so the hint does not reappear while the
  // very first answer is being waited on.
  const hasConversation = useChatStore((state) => state.queryHistory.length > 0)
  const askQuestion = useChatStore((state) => state.askQuestion)
  const tryApplyChartTypeFollowup = useChatStore((state) => state.tryApplyChartTypeFollowup)
  const cancelPendingQuestion = useChatStore((state) => state.cancelPendingQuestion)
  // "A turn is in flight." Still gates the controls that belong to that
  // turn (attachments, the source picker, voice capture), which a queued
  // follow-up question does not participate in.
  const disabled = pendingQuestion !== null
  // Whether the primary button means Stop rather than Send. Only when a
  // question is pending *and* nothing is typed -- see the button itself.
  const showStop = disabled && !value.trim()
  const { toast } = useToast()

  const voice = useVoiceConversation((text) => {
    setValue(text)
    setFromVoice(true)
  })

  const attachments = useChatAttachments()
  const capabilities = useAttachmentCapabilities()
  const [editingImageId, setEditingImageId] = useState<string | null>(null)
  const [viewingImageId, setViewingImageId] = useState<string | null>(null)
  const [ocrImageId, setOcrImageId] = useState<string | null>(null)
  const [resizeImageId, setResizeImageId] = useState<string | null>(null)
  const [removeTextImageId, setRemoveTextImageId] = useState<string | null>(null)
  const [isDraggingOver, setIsDraggingOver] = useState(false)
  const editingImage = attachments.attachments.find((item) => item.id === editingImageId) ?? null
  const viewingImage = attachments.attachments.find((item) => item.id === viewingImageId) ?? null
  const ocrImage = attachments.attachments.find((item) => item.id === ocrImageId) ?? null
  const resizeImage = attachments.attachments.find((item) => item.id === resizeImageId) ?? null
  const removeTextImage = attachments.attachments.find((item) => item.id === removeTextImageId) ?? null

  // "Attach resized/edited image" replaces the source chip with the new,
  // server-derived one -- the user asked to edit *this* image, so the
  // composer should hold the edited result afterward, not both versions
  // (see useChatAttachments.ts's addProcessedResult docstring for why this
  // never re-uploads anything).
  const replaceWithProcessedResult = (
    sourceId: string,
    result: Parameters<typeof attachments.addProcessedResult>[0],
    filename: string,
  ) => {
    attachments.removeAttachment(sourceId)
    void attachments.addProcessedResult(result, filename)
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDraggingOver(false)
    if (event.dataTransfer.files.length > 0) void attachments.addFiles(event.dataTransfer.files)
  }

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const files = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null)
    if (files.length > 0) void attachments.addFiles(files)
  }

  // Once a finished transcript lands in `value` (voice.phase back to
  // 'idle' after having been 'transcribing'), focus the textarea for
  // editing, same as a user would expect after typing.
  useEffect(() => {
    if (voice.phase === 'idle' && fromVoice && textareaRef.current) {
      textareaRef.current.focus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to phase changes, not every value/fromVoice change
  }, [voice.phase])

  const submit = async () => {
    const trimmed = value.trim()
    // Blocked while any attachment is still uploading/processing -- sending
    // early would either omit it entirely or race the server still writing
    // its record, neither of which should silently happen.
    // `disabled` (a question already in flight) is deliberately NOT a
    // guard here any more. `chatStore.askQuestion` now routes a question
    // submitted while another is pending to the asynchronous queue
    // instead of overwriting the single pending slot, so submitting is
    // safe and is what lets a user ask several things at once. Still
    // blocked while an attachment is uploading: sending early would
    // either omit it or race the server still writing its record.
    // The word limit is enforced here, not by truncating as the user
    // types (see `handleChange`). This is the only place it can be
    // enforced without destroying text the user may still want to edit,
    // and it covers every route in: Enter, the send button, and a voice
    // transcript long enough to overrun the cap.
    if (!trimmed || attachments.isUploading || countWords(value) > MAX_WORDS) return

    // A "show this as a pie chart"/"switch to line" style follow-up is
    // answered instantly, client-side, against the most recent chartable
    // turn -- never sent to the agent as if it were a real question (see
    // chatStore.tryApplyChartTypeFollowup's own docstring for why: the SQL
    // agent has no way to act on it, and re-running SQL for a pure
    // visualization change would be wasted work the user never asked for).
    const chartFollowup = tryApplyChartTypeFollowup(trimmed)
    if (chartFollowup.handled) {
      setValue('')
      setFromVoice(false)
      toast({
        title: chartFollowup.message,
        variant: chartFollowup.applied ? 'success' : 'info',
      })
      return
    }

    const voiceOriginated = fromVoice
    setValue('')
    setFromVoice(false)
    // Clearing `value` below collapses the box back to one line (see the
    // layout effect above).

    // An immutable snapshot of what's attached right now, taken before the
    // request is built -- both for this question's own chat bubble (below)
    // and as the ids actually sent (`attachments.readyAttachmentIds` reads
    // live state, so it must be captured alongside this, not re-read after
    // the composer is cleared further down). A fresh `URL.createObjectURL`
    // (never the composer's own `previewUrl`) keeps this thumbnail alive
    // even after the composer's own chip is cleared/removed, which revokes
    // `previewUrl` itself.
    const submissionAttachmentIds = attachments.readyAttachmentIds
    const sentAttachments: SentAttachmentPreview[] = attachments.attachments
      .filter((item) => item.status === 'ready' && item.attachmentId)
      .map((item) => ({
        filename: item.file.name,
        kind: item.kind,
        previewUrl:
          item.kind !== 'image'
            ? null
            : (item.editedDataUrl ?? URL.createObjectURL(item.file)),
      }))

    // Start the request first, then let the host screen react (the shared
    // view navigates to the chat screen, which shows this pending question).
    const pendingAnswer = askQuestion(trimmed, {
      originatedFromVoice: voiceOriginated,
      attachmentIds: submissionAttachmentIds,
      sentAttachments,
    })
    onSend?.()
    const entry = await pendingAnswer

    // Clear the composer's pending attachment chips once the backend has
    // actually accepted and answered the request -- a non-empty
    // `session_id` is the signal for that (see AskResponse.session_id's own
    // docstring: askQuestion only ever returns an empty one when the call
    // never reached the backend at all -- aborted/cancelled, or genuinely
    // unreachable -- in which case the attachments are left in place so the
    // user can retry without re-selecting files). This clears regardless of
    // whether the agent's own answer succeeded or failed, since "the model
    // couldn't answer" still means the attachment was received and
    // processed -- the sent bubble above already carries its own read-only
    // reference, so nothing is lost by clearing the now-stale pending chips.
    //
    // `entry` is undefined when this question went to the asynchronous
    // queue instead of being answered inline (another was already in
    // flight). The attachment chips are cleared in that case too: the
    // server accepted the submission and already holds the attachment
    // ids, so leaving the chips would invite the user to send them twice.
    if (!entry || entry.finalState.session_id) {
      attachments.clearAll()
    }

    if (voiceOriginated && entry?.spokenAudioUrl) {
      await voice.playAnswer(entry.spokenAudioUrl)
    }
  }

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const next = event.target.value
    // Deliberately no longer truncated at `MAX_WORDS`. Silently dropping
    // the tail of a paste is the worst of the options: the user cannot
    // see what was lost, and an over-long draft they wanted to trim
    // themselves is destroyed by the act of pasting it. The limit is now
    // *shown and enforced at send* instead -- the box turns red, the
    // count explains why, and `submit()` refuses -- which leaves the text
    // in the user's hands to edit down.
    setValue(next)
    if (fromVoice && next.trim() === '') setFromVoice(false)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
    // Shift+Enter falls through to the textarea's default behavior --
    // inserts a newline, growing the box on the next change event.
  }

  const wordCount = countWords(value)
  // Strictly over the limit. `=== MAX_WORDS` is a valid, sendable draft:
  // the cap is "at most 500 words", so the warning starts at 501.
  const isOverWordLimit = wordCount > MAX_WORDS
  const isListening = voice.phase === 'listening'
  const isTranscribing = voice.phase === 'transcribing'
  const isSpeaking = voice.phase === 'speaking'
  // While listening, the box shows the live (interim, browser-native)
  // caption instead of `value` -- the composer visibly "types" what it
  // hears in real time. It's read-only during listening/transcribing so
  // the user can't type over words that are about to be replaced by the
  // authoritative local transcript.
  const displayValue = isListening ? voice.liveCaption : value
  const voiceLocked = isListening || isTranscribing || isSpeaking

  // Re-fit the box whenever the shown text changes by any route -- typing,
  // paste, a finished voice transcript, live captions, or being cleared
  // after send -- not only on a keystroke. Runs before paint, so there is no
  // one-frame height jump.
  useLayoutEffect(() => {
    if (textareaRef.current) resizeToFitContent(textareaRef.current)
  }, [displayValue])

  return (
    <div className="flex flex-col gap-1.5">
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setIsDraggingOver(true)
        }}
        onDragLeave={() => setIsDraggingOver(false)}
        onDrop={handleDrop}
        onPaste={handlePaste}
        // Over the word limit the whole box goes red, and stays red even
        // while focused -- hence the `focus-within:` override here rather
        // than letting the accent colour win back on the next keystroke,
        // which is exactly when the user is still typing into an
        // already-too-long draft.
        className={`flex flex-col gap-0.5 rounded-2xl border p-1.5 shadow-sm transition-shadow focus-within:shadow-md ${
          isOverWordLimit
            ? 'border-[var(--danger)] bg-[var(--card)] focus-within:border-[var(--danger)]'
            : isDraggingOver
              ? 'border-[var(--accent)] bg-[var(--accent-soft)] focus-within:border-[var(--accent)]'
              : 'border-[var(--border)] bg-[var(--card)] focus-within:border-[var(--accent)]'
        }`}
      >
        {attachments.attachments.length > 0 && (
          <div className="flex flex-col gap-1.5 border-b border-[var(--border)] pb-2">
            <div className="flex flex-wrap gap-2">
              {attachments.attachments.map((item) => (
                <ChatAttachmentChip
                  key={item.id}
                  attachment={item}
                  onEdit={item.kind === 'image' ? () => setEditingImageId(item.id) : undefined}
                  onRemove={() => attachments.removeAttachment(item.id)}
                  onView={item.kind === 'image' ? () => setViewingImageId(item.id) : undefined}
                  onExtractText={
                    item.kind === 'image' && capabilities.data?.ocr ? () => setOcrImageId(item.id) : undefined
                  }
                  onResize={
                    item.kind === 'image' && capabilities.data?.image_resize
                      ? () => setResizeImageId(item.id)
                      : undefined
                  }
                  onRemoveText={
                    item.kind === 'image' && capabilities.data?.image_text_removal
                      ? () => setRemoveTextImageId(item.id)
                      : undefined
                  }
                />
              ))}
            </div>
          </div>
        )}
        {/* One row at every width: attach + source sit to the LEFT of the
            text, mic + send to the right. Splitting the four buttons across
            the two ends is what lets a single row survive on a phone -- the
            previous layout put all four on the right, which left the
            textarea about 150px wide and wrapped the placeholder onto two
            lines, so it had to stack below `sm`. Two buttons per end leaves
            the text roughly 215px at 360px, so the stacked breakpoint is no
            longer needed and the composer keeps one shape everywhere.

            No gap between the icon buttons: each is already a 32-36px box
            around a 16px icon, so it carries its own breathing room, and a
            gap on top of that read as four separate controls rather than
            one toolbar. */}
        <div className="flex items-end">
          <div className="flex items-center">
            <ImageUploader onFilesSelected={(files) => void attachments.addFiles(files)} disabled={disabled} />
            <SourcePicker disabled={disabled} />
          </div>
          <textarea
            ref={textareaRef}
            // Lets the global `/` shortcut find the composer from
            // `AppShell`, which renders this several routes deep through
            // `<Outlet />`. See `lib/shortcuts.ts` on why an attribute
            // rather than a ref threaded up through context.
            data-chat-composer=""
            value={displayValue}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder={
              isListening && !voice.isSupported
                ? t('voice.unsupportedCaption')
                : hasConversation || pendingQuestion
                  ? t('chat.placeholder')
                  : t('chat.placeholderEmpty')
            }
            // Deliberately not the placeholder above: the control's
            // accessible name must stay the same whatever the
            // conversation currently holds, so a screen reader (and the
            // tests that find this box by name) never see it rename
            // itself after the first question.
            aria-label={t('chat.placeholder')}
            // Typing stays open while an answer is pending, so the next question
          // can be drafted. Only voice capture/playback locks the box.
          disabled={voiceLocked}
            readOnly={isListening || isTranscribing}
            rows={1}
            className="min-h-8 max-h-60 flex-1 resize-none overflow-y-auto bg-transparent px-1.5 py-1.5 text-sm leading-6 focus-visible:outline-none"
          />
          <div className="flex items-center">
          {showVoiceButton && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                if (isListening) voice.stopListening()
                else if (!voiceLocked) void voice.start()
              }}
              disabled={disabled || isTranscribing || isSpeaking}
              aria-label={isListening ? t('voice.stopConversation') : t('voice.startConversation')}
              title={isListening ? t('voice.stopConversation') : t('voice.startConversation')}
              className="rounded-xl"
            >
              {isListening ? (
                <Square className="h-4 w-4" />
              ) : isTranscribing || isSpeaking ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Mic className="h-4 w-4" />
              )}
            </Button>
          )}
          <Button
            variant={showStop ? 'secondary' : 'primary'}
            size="icon"
            onClick={() => (showStop ? cancelPendingQuestion() : void submit())}
            // Stop stays a real cancel of the one outstanding streaming
            // request (AbortController, chatStore.cancelPendingQuestion) --
            // there is no token stream to interrupt (see
            // docs/frontend-ui-audit.md), but the request itself genuinely
            // aborts.
            //
            // It is now shown only while a question is pending AND the box
            // is empty. With text typed, the button sends: that question
            // goes to the asynchronous queue rather than replacing the
            // one in flight, which is what lets several questions run at
            // once. Keeping Stop as the only option whenever anything was
            // pending is exactly what made the UI single-threaded.
            disabled={
              !showStop &&
              (voiceLocked || !value.trim() || attachments.isUploading || isOverWordLimit)
            }
            aria-label={showStop ? t('common.stop') : t('chat.send')}
            title={showStop ? t('common.stop') : t('chat.send')}
            className="rounded-xl"
          >
            {showStop ? <Square className="h-4 w-4" /> : <Send className="h-4 w-4" />}
          </Button>
          </div>
        </div>
        {editingImage && (
          // Suspense fallback is intentionally invisible (null): the editor
          // dialog itself isn't mounted/visible until the lazy chunk
          // resolves, so there's nothing on screen to show a spinner over
          // yet -- the "Edit" button's own disabled/pressed state is the
          // only loading affordance for the brief chunk-fetch window.
          <Suspense fallback={null}>
            <ImageEditor
              open={editingImageId !== null}
              onOpenChange={(open) => !open && setEditingImageId(null)}
              imageSrc={editingImage.editedDataUrl ?? editingImage.previewUrl ?? ''}
              fileName={editingImage.file.name}
              onSave={(dataUrl) => attachments.setEditedImage(editingImage.id, dataUrl)}
            />
          </Suspense>
        )}
        {viewingImage && (
          <ImageViewer
            open={viewingImageId !== null}
            onOpenChange={(open) => !open && setViewingImageId(null)}
            imageSrc={viewingImage.editedDataUrl ?? viewingImage.previewUrl ?? ''}
            altText={viewingImage.file.name}
          />
        )}
        {ocrImage?.attachmentId && (
          <OcrResultDialog
            open={ocrImageId !== null}
            onOpenChange={(open) => !open && setOcrImageId(null)}
            attachmentId={ocrImage.attachmentId}
            filename={ocrImage.file.name}
          />
        )}
        {resizeImage?.attachmentId && (
          <ResizeImageDialog
            open={resizeImageId !== null}
            onOpenChange={(open) => !open && setResizeImageId(null)}
            attachmentId={resizeImage.attachmentId}
            filename={resizeImage.file.name}
            previewUrl={resizeImage.editedDataUrl ?? resizeImage.previewUrl}
            presets={capabilities.data?.resize_presets ?? []}
            maxDimension={capabilities.data?.max_resize_dimension_px ?? 4096}
            onResized={(result) => resizeImageId && replaceWithProcessedResult(resizeImageId, result, resizeImage.file.name)}
          />
        )}
        {removeTextImage?.attachmentId && (
          <RemoveTextDialog
            open={removeTextImageId !== null}
            onOpenChange={(open) => !open && setRemoveTextImageId(null)}
            attachmentId={removeTextImage.attachmentId}
            filename={removeTextImage.file.name}
            previewUrl={removeTextImage.editedDataUrl ?? removeTextImage.previewUrl}
            maxRegions={capabilities.data?.max_text_removal_regions ?? 20}
            onEdited={(result) =>
              removeTextImageId && replaceWithProcessedResult(removeTextImageId, result, removeTextImage.file.name)
            }
          />
        )}
      </div>
      {voice.error && <p className="px-1 text-xs text-[var(--danger)]">{t(voice.error)}</p>}
      {isSpeaking && (
        <p className="flex items-center gap-1 px-1 text-xs text-[var(--muted-foreground)]">
          <Volume2 className="h-3 w-3" /> {t('voice.speaking')}
        </p>
      )}
      {/* Three columns, with the outer two sharing the leftover width
          equally (`1fr ... 1fr`). That is what centres the disclaimer on
          the *composer*, rather than merely centring it in whatever space
          the model picker and the warning happen to leave -- those two
          differ in width, and with a plain flex row the sentence would
          drift sideways as the warning appeared and disappeared.

          Below `sm` it stacks instead: three columns of this on a phone
          would squeeze the sentence into a narrow ribbon. The disclaimer
          is ordered last there so the controls stay nearest the box. */}
      <div className="flex flex-col items-center gap-1 px-1 sm:grid sm:grid-cols-[1fr_auto_1fr] sm:items-center sm:gap-3">
        <div className="order-1 flex w-full justify-start sm:order-none sm:w-auto sm:justify-self-start">
          <ComposerModelPicker disabled={disabled} />
        </div>
        <p className="order-3 text-center text-xs text-[var(--muted-foreground)] sm:order-none">
          {t('chat.disclaimer')}
        </p>
        {/* Nothing at all until the limit is actually exceeded -- the
            running "0 / 500 words" tally was noise on every empty
            composer, and a counter that is only ever interesting at one
            value may as well say what that value means. `role="status"`
            so the warning is announced when it appears; it is not an
            `alert`, because the draft is still intact and nothing has
            failed yet. */}
        <div className="order-2 flex w-full justify-end sm:order-none sm:w-auto sm:justify-self-end">
          {isOverWordLimit && (
            <span role="status" className="shrink-0 text-xs text-[var(--danger)]">
              {t('chat.wordLimitExceeded', { max: MAX_WORDS })}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
