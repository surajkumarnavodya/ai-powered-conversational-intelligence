import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/components/ui/toast'
import i18n from '@/i18n'
import * as api from '@/lib/api'
import * as askStream from '@/lib/askStream'
import type { AskResponse, HealthResponse } from '@/lib/types'
import { useChatStore } from '@/store/chatStore'
import { ChatInput } from './ChatInput'

function makeHealth(): HealthResponse {
  return {
    status: 'ok',
    databases: [],
    ollama: { status: 'ok', detail: null } as unknown as HealthResponse['ollama'],
    voice_enabled: false,
    media_search_enabled: false,
    local_auth_enabled: false,
    google_signin_enabled: false,
    google_client_id: null,
    vision_enabled: false,
    vision_provider: null,
    vision_model: null,
    vision_model_available: null,
    ocr_enabled: false,
  }
}

function makeAskResponse(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    session_id: 's1',
    conversation_id: null,
    message_id: null,
    status: 'succeeded',
    database: 'default',
    model: 'llama3.1:8b',
    sql: null,
    result_columns: null,
    result_rows: null,
    row_count: null,
    retry_count: 0,
    attempt_history: [],
    insight: null,
    cost_notice: null,
    low_confidence_notice: null,
    rejection_reason: null,
    rejection_message: null,
    rate_limit_message: null,
    clarification_message: null,
    failure_explanation: null,
    error_history: [],
    sources_used: ['attachments'],
    synthesized_answer: 'Here is what the file says.',
    document_result: null,
    policy_result: null,
    web_result: null,
    generation_result: null,
    media_search_result: null,
    attachment_result: null,
    query_plan: null,
    schema_tables: [],
    followup_classification: null,
    followup_resolved_against: null,
    permission_denied_notice: null,
    source_selection_notice: null,
    analytical_result: null,
    forecast_result: null,
    recommendations: [],
    analytical_intent: null,
    analytical_plan: null,
    governing_metrics: [],
    restricted_field_notice: null,
    ...overrides,
  }
}

function makeTextFile(name = 'notes.txt'): File {
  return new File(['hello world'], name, { type: 'text/plain' })
}

function renderChatInput() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ChatInput />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

async function attachAndUpload(filename: string) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(input, makeTextFile(filename))
  await waitFor(() => expect(screen.getByText(filename)).toBeInTheDocument())
  // Wait for the chip to leave its "uploading" state before the composer is
  // allowed to submit (ChatInput.submit() blocks while attachments.isUploading).
  await waitFor(() => expect(screen.queryByText(/uploading/i)).not.toBeInTheDocument())
}

const initialChatState = useChatStore.getState()

describe('ChatInput -- attachment composer lifecycle', () => {
  beforeEach(() => {
    useChatStore.setState(initialChatState, true)
    vi.spyOn(api, 'getHealth').mockResolvedValue(makeHealth())
    vi.spyOn(api, 'uploadAttachments').mockResolvedValue({
      attachments: [
        {
          attachment_id: 'att_1',
          filename: 'notes.txt',
          media_type: 'text/plain',
          size_bytes: 11,
          processing_status: 'succeeded',
          processing_error: null,
        },
      ],
      errors: [],
    })
    vi.spyOn(api, 'deleteAttachment').mockResolvedValue(undefined)
  })

  afterEach(() => {
    useChatStore.setState(initialChatState, true)
    vi.restoreAllMocks()
  })

  it('clears the pending attachment chip automatically once the backend accepts and answers the request', async () => {
    vi.spyOn(askStream, 'askQuestionStreaming').mockResolvedValue(makeAskResponse())
    const user = userEvent.setup()
    renderChatInput()

    await attachAndUpload('notes.txt')
    expect(screen.getByText('notes.txt')).toBeInTheDocument()

    await user.type(screen.getByLabelText(/ask a question/i), 'What does this file say?')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.queryByText('notes.txt')).not.toBeInTheDocument())

    // The actual server-assigned attachment id was sent, not a blob URL or
    // bare filename -- the composer wouldn't have anything else to send.
    expect(askStream.askQuestionStreaming).toHaveBeenCalledWith(
      expect.objectContaining({ attachment_ids: ['att_1'] }),
      expect.anything(),
      expect.anything(),
    )
  })

  it('clears the composer even when the agent itself fails to answer -- the request still reached the backend', async () => {
    vi.spyOn(askStream, 'askQuestionStreaming').mockResolvedValue(
      makeAskResponse({ status: 'failed', failure_explanation: 'The request failed.' }),
    )
    const user = userEvent.setup()
    renderChatInput()

    await attachAndUpload('notes.txt')
    await user.type(screen.getByLabelText(/ask a question/i), 'Describe it')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.queryByText('notes.txt')).not.toBeInTheDocument())
  })

  it('preserves the attachment for retry when the request never reaches the backend', async () => {
    vi.spyOn(askStream, 'askQuestionStreaming').mockRejectedValue(new Error('network down'))
    const user = userEvent.setup()
    renderChatInput()

    await attachAndUpload('notes.txt')
    await user.type(screen.getByLabelText(/ask a question/i), 'Describe it')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(askStream.askQuestionStreaming).toHaveBeenCalled())
    // Still there -- a client-side/network failure never reached the
    // backend, so the user shouldn't have to re-select the file to retry.
    expect(screen.getByText('notes.txt')).toBeInTheDocument()
  })

  it('clears the composer only once, even though the same file could otherwise still be resolved after clearing', async () => {
    vi.spyOn(askStream, 'askQuestionStreaming').mockResolvedValue(makeAskResponse())
    const user = userEvent.setup()
    renderChatInput()

    await attachAndUpload('notes.txt')
    await user.type(screen.getByLabelText(/ask a question/i), 'Describe it')
    await user.click(screen.getByRole('button', { name: /send/i }))

    await waitFor(() => expect(screen.queryByText('notes.txt')).not.toBeInTheDocument())
    expect(api.uploadAttachments).toHaveBeenCalledTimes(1)
    expect(askStream.askQuestionStreaming).toHaveBeenCalledTimes(1)
  })
})

describe('ChatInput -- composer layout', () => {
  const DISCLAIMER = 'AI may make mistakes. Please verify results before relying on them.'

  it('shows the AI disclaimer below the composer', () => {
    renderChatInput()
    expect(screen.getByText(DISCLAIMER)).toBeInTheDocument()
  })

  it('centre-aligns the disclaimer', () => {
    renderChatInput()
    expect(screen.getByText(DISCLAIMER).className).toMatch(/text-center/)
  })

  describe('word limit', () => {
    // `MAX_WORDS` is 500 in ChatInput.tsx. Built by repetition rather
    // than typed through userEvent: 500+ keystrokes per test would
    // dominate the suite's runtime for no extra coverage.
    const words = (n: number) => Array.from({ length: n }, (_, i) => `w${i}`).join(' ')

    function typeIntoComposer(text: string) {
      const textarea = screen.getByLabelText(/ask a question/i) as HTMLTextAreaElement
      fireEvent.change(textarea, { target: { value: text } })
      return textarea
    }

    it('shows no running word tally on an empty composer', () => {
      renderChatInput()
      // The old "0 / 500 words" readout was present from the first
      // render; nothing about the limit should be on screen until it
      // actually matters.
      expect(screen.queryByText(/\/\s*500/)).not.toBeInTheDocument()
      expect(screen.queryByText(/maximum 500 words/i)).not.toBeInTheDocument()
    })

    it('stays quiet at exactly the limit, which is still a sendable draft', () => {
      renderChatInput()
      typeIntoComposer(words(500))
      expect(screen.queryByText(/maximum 500 words/i)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /send/i })).not.toBeDisabled()
    })

    it('warns, reddens the box and blocks sending once the limit is exceeded', () => {
      renderChatInput()
      const textarea = typeIntoComposer(words(501))

      expect(screen.getByText(/maximum 500 words allowed/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /send/i })).toBeDisabled()
      // The red outline is on the whole composer box, not the textarea
      // itself -- that is the bordered element the user sees.
      const box = textarea.closest('.rounded-2xl') as HTMLElement
      expect(box.className).toMatch(/border-\[var\(--danger\)\]/)
    })

    it('keeps an over-long draft intact instead of truncating it', () => {
      // The previous behaviour silently dropped everything past word 500
      // on paste. The text has to survive for the user to be able to
      // edit it down to the limit themselves.
      renderChatInput()
      const textarea = typeIntoComposer(words(520))
      expect(textarea.value.trim().split(/\s+/)).toHaveLength(520)
    })

    it('clears the warning again once the draft is trimmed back', () => {
      renderChatInput()
      typeIntoComposer(words(501))
      expect(screen.getByText(/maximum 500 words allowed/i)).toBeInTheDocument()

      typeIntoComposer(words(10))
      expect(screen.queryByText(/maximum 500 words/i)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /send/i })).not.toBeDisabled()
    })
  })

  it('starts as a single-line textarea', () => {
    renderChatInput()
    const textarea = screen.getByRole('textbox', { name: /ask|question|placeholder/i }) as HTMLTextAreaElement
    expect(textarea.rows).toBe(1)
  })
})

describe('ChatInput -- typing while an answer is pending', () => {
  afterEach(() => {
    useChatStore.setState({ pendingQuestion: null } as never)
  })

  it('offers stop while the agent works and nothing is typed', async () => {
    useChatStore.setState({ pendingQuestion: { question: 'first', startedAt: Date.now() } } as never)
    renderChatInput()

    const textarea = screen.getByRole('textbox', { name: /chat|ask|placeholder/i }) as HTMLTextAreaElement
    expect(textarea).toBeEnabled()
    expect(textarea).not.toHaveAttribute('readonly')

    // With an empty box the button is still a real cancel of the one
    // outstanding streaming request.
    expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument()
  })

  it('offers send again once a next question is typed, so it can be queued', async () => {
    // Behaviour change, deliberate: this used to keep showing Stop even
    // with text typed, which is what made the composer single-threaded --
    // the only thing a user could do while waiting was abandon the
    // question in flight. Now a typed question is submitted and, because
    // one is already pending, `chatStore.askQuestion` routes it to the
    // asynchronous queue instead of replacing the running one. Stop is
    // not lost: it returns the moment the box is empty again (the test
    // above).
    useChatStore.setState({ pendingQuestion: { question: 'first', startedAt: Date.now() } } as never)
    renderChatInput()

    const textarea = screen.getByRole('textbox', { name: /chat|ask|placeholder/i }) as HTMLTextAreaElement
    await userEvent.type(textarea, 'next question')
    expect(textarea.value).toBe('next question')

    // `/send/i`, not `/^send$/i`: i18n renders raw keys in this test
    // environment, so the accessible name is "chat.send" and an anchored
    // pattern can never match it -- which is why the original negative
    // assertion here passed vacuously.
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /stop/i })).not.toBeInTheDocument()
  })
})

describe('ChatInput -- first-question placeholder', () => {
  beforeEach(() => {
    useChatStore.setState(initialChatState, true)
    vi.spyOn(api, 'getHealth').mockResolvedValue(makeHealth())
  })

  afterEach(() => {
    useChatStore.setState(initialChatState, true)
    vi.restoreAllMocks()
  })

  /** The composer carries the full instruction in every conversation
   * state. It used to switch to a shorter prompt once a turn existed,
   * which is why this block was originally written one test per state;
   * the product decision is now a single placeholder, served by one
   * `chat.placeholder` key with no branch left in the component.
   *
   * So these tests assert *invariance* rather than a per-state string.
   * That distinction is the whole point: asserting the same literal in
   * three separate states passes whichever branch the component takes,
   * which is what made the previous version of this block vacuous for
   * the fallback it was named after. The expected value is read from the
   * translation bundle (the test setup runs a real i18next init) rather
   * than hard-coded, so a copy change needs no edit here. */
  const PLACEHOLDER = i18n.t('chat.placeholder')

  function placeholderOf() {
    const textarea = screen.getByRole('textbox', { name: /ask a question/i }) as HTMLTextAreaElement
    return textarea.placeholder
  }

  function placeholderInState(state: Record<string, unknown>) {
    useChatStore.setState(state as never)
    const { unmount } = renderChatInput()
    const value = placeholderOf()
    unmount()
    return value
  }

  it('resolves a real instruction, not a bare translation key', () => {
    // Guards the fixture every other test here compares against: if
    // `chat.placeholder` went missing, i18next would hand back the key
    // itself and each comparison below would still "pass" by matching it
    // on both sides.
    expect(PLACEHOLDER).not.toBe('chat.placeholder')
    expect(PLACEHOLDER).toMatch(/ask a question/i)
  })

  it('shows the same placeholder in every conversation state', () => {
    const seen = {
      empty: placeholderInState({ queryHistory: [], pendingQuestion: null }),
      firstAnswerPending: placeholderInState({
        queryHistory: [],
        pendingQuestion: { question: 'first', startedAt: Date.now() },
      }),
      oneTurnDone: placeholderInState({
        queryHistory: [{ entryId: 'e1', question: 'first' }],
        pendingQuestion: null,
      }),
      laterTurnPending: placeholderInState({
        queryHistory: [{ entryId: 'e1', question: 'first' }],
        pendingQuestion: { question: 'second', startedAt: Date.now() },
      }),
    }

    // One assertion over all four states, so a reintroduced branch names
    // the state it diverged in rather than just failing somewhere.
    expect(seen).toEqual({
      empty: PLACEHOLDER,
      firstAnswerPending: PLACEHOLDER,
      oneTurnDone: PLACEHOLDER,
      laterTurnPending: PLACEHOLDER,
    })
  })

  it('keeps one stable accessible name whatever the conversation holds', () => {
    const { unmount } = renderChatInput()
    expect(screen.getByLabelText(PLACEHOLDER)).toBeInTheDocument()
    unmount()

    useChatStore.setState({ queryHistory: [{ entryId: 'e1', question: 'first' }] } as never)
    renderChatInput()
    expect(screen.getByLabelText(PLACEHOLDER)).toBeInTheDocument()
  })

  it('still renders the placeholder from the translation bundle, not a fixed attribute', () => {
    // Non-vacuity guard for the invariance test above. "Always
    // PLACEHOLDER" would also hold if the component hard-coded the
    // string, or if `placeholder` were dropped and the name came only
    // from `aria-label`. Switching the active language has to move the
    // rendered placeholder with it.
    const original = i18n.language
    try {
      i18n.changeLanguage('fr')
      const french = i18n.t('chat.placeholder')
      expect(french).not.toBe(PLACEHOLDER)

      useChatStore.setState({ queryHistory: [], pendingQuestion: null } as never)
      renderChatInput()
      const textarea = screen.getByRole('textbox', {
        name: french,
      }) as HTMLTextAreaElement
      expect(textarea.placeholder).toBe(french)
    } finally {
      i18n.changeLanguage(original)
    }
  })
})
