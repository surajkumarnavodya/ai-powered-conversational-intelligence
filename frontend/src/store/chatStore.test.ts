import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { newHistoryEntry, nlCacheKey, type QueryHistoryEntry } from '@/lib/history'
import type { AgentStatus } from '@/lib/types'
import { askState } from '@/test/analyticsFixtures'
import { useChatStore } from './chatStore'

const initialState = useChatStore.getState()

describe('chatStore askQuestion / cancelPendingQuestion', () => {
  let abortSignals: AbortSignal[]

  beforeEach(() => {
    useChatStore.setState(initialState, true)
    abortSignals = []
    // Simulates a genuinely in-flight, cancellable /ask request: never
    // resolves on its own, only rejects with a real DOMException AbortError
    // once its signal is aborted -- the same shape a real aborted `fetch()`
    // call produces, so chatStore's AbortError-detection code path is
    // exercised for real, not assumed.
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        return new Promise((_resolve, reject) => {
          const signal = init?.signal
          if (signal) {
            abortSignals.push(signal)
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
          }
        })
      }),
    )
  })

  afterEach(() => {
    useChatStore.setState(initialState, true)
    vi.unstubAllGlobals()
  })

  it('sets pendingQuestion while a request is in flight', () => {
    void useChatStore.getState().askQuestion('How many orders?')
    expect(useChatStore.getState().pendingQuestion?.question).toBe('How many orders?')
  })

  it('cancelPendingQuestion aborts the in-flight request and resolves with a cancelled entry', async () => {
    const promise = useChatStore.getState().askQuestion('How many orders?')
    expect(abortSignals).toHaveLength(1)

    useChatStore.getState().cancelPendingQuestion()
    const entry = await promise

    expect(entry.agentStatus).toBe('failed')
    expect(entry.finalState.failure_explanation).toBe('Cancelled.')
    expect(useChatStore.getState().pendingQuestion).toBeNull()
  })

  it('is a no-op when nothing is pending', () => {
    expect(() => useChatStore.getState().cancelPendingQuestion()).not.toThrow()
  })
})

function entry(question: string, status: AgentStatus = 'succeeded'): QueryHistoryEntry {
  const base = newHistoryEntry(question, askState({ status }), 1000)
  return { ...base, agentStatus: status }
}

describe('chatStore conversation context', () => {
  let bodies: Record<string, unknown>[]

  beforeEach(() => {
    useChatStore.setState(initialState, true)
    bodies = []
    // Captures the real request body so what reaches /ask is asserted,
    // not inferred from the store's own fields.
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init?: RequestInit) => {
        if (typeof init?.body === 'string') bodies.push(JSON.parse(init.body))
        return Promise.resolve(
          new Response(JSON.stringify(askState()), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )
  })

  afterEach(() => {
    useChatStore.setState(initialState, true)
    vi.unstubAllGlobals()
  })

  it('sends every carried turn when nothing is excluded', async () => {
    useChatStore.setState({ queryHistory: [entry('One?'), entry('Two?')] })
    await useChatStore.getState().askQuestion('And by region?')
    expect(
      (bodies[0].conversation_history as { question: string }[]).map((item) => item.question),
    ).toEqual(['One?', 'Two?'])
  })

  it('omits an excluded turn from the request it actually sends', async () => {
    const entries = [entry('Keep?'), entry('Drop?')]
    useChatStore.setState({
      queryHistory: entries,
      excludedContextEntryIds: new Set([entries[1].entryId]),
    })
    await useChatStore.getState().askQuestion('And by region?')
    expect(
      (bodies[0].conversation_history as { question: string }[]).map((item) => item.question),
    ).toEqual(['Keep?'])
  })

  it('clearConversationContext makes the next ask carry no history at all', async () => {
    useChatStore.setState({ queryHistory: [entry('One?'), entry('Two?')] })
    useChatStore.getState().clearConversationContext()
    await useChatStore.getState().askQuestion('And by region?')
    // An empty history is what makes classify_followup_node treat the
    // question as standalone -- the same state a first question is in.
    expect(bodies[0].conversation_history).toEqual([])
  })

  it('clearConversationContext keeps the transcript on screen', () => {
    useChatStore.setState({ queryHistory: [entry('One?')] })
    useChatStore.getState().clearConversationContext()
    expect(useChatStore.getState().queryHistory).toHaveLength(1)
  })

  it('toggleContextEntryExcluded restores a turn on a second call', () => {
    const entries = [entry('One?')]
    useChatStore.setState({ queryHistory: entries })
    useChatStore.getState().toggleContextEntryExcluded(entries[0].entryId)
    expect(useChatStore.getState().excludedContextEntryIds.size).toBe(1)
    useChatStore.getState().toggleContextEntryExcluded(entries[0].entryId)
    expect(useChatStore.getState().excludedContextEntryIds.size).toBe(0)
  })

  it("startNewChat drops the previous conversation's removals", () => {
    const entries = [entry('One?')]
    useChatStore.setState({
      queryHistory: entries,
      excludedContextEntryIds: new Set([entries[0].entryId]),
    })
    useChatStore.getState().startNewChat()
    expect(useChatStore.getState().excludedContextEntryIds.size).toBe(0)
  })

  it('does not reuse a cached answer across a context change', async () => {
    // Seeded with the key the *un-excluded* ask would use, then asked with
    // a turn removed. Correct behaviour is a cache miss (a real request);
    // if the key ignored the exclusion set this would be served from cache
    // and never reach the server. Seeded directly rather than by asking
    // twice: a real first ask appends its own turn to `queryHistory`, which
    // changes `priorQuestions` and so changes the key regardless -- that
    // would make this pass whether the fix is present or not.
    const entries = [entry('One?')]
    const question = 'Same question?'
    useChatStore.setState({
      queryHistory: entries,
      excludedContextEntryIds: new Set([entries[0].entryId]),
      nlQuestionCache: new Map([
        [
          nlCacheKey(question, entries.map((item) => item.question), true, new Set()),
          askState(),
        ],
      ]),
      enableInsight: true,
    })

    await useChatStore.getState().askQuestion(question)
    expect(bodies).toHaveLength(1)
  })
})

// Regression coverage for a failure a user actually hit: an expired
// session produced "Agent could not be reached" in 0.0s. The stream had
// the real 401 and its detail in hand, but `askQuestion` passed no
// `onError`, so the status was dropped and every stream failure collapsed
// into one blanket message -- which pointed at connectivity for a problem
// that was nothing of the kind.
describe('chatStore surfaces why a streamed question failed', () => {
  beforeEach(() => {
    useChatStore.setState(initialState, true)
  })

  afterEach(() => {
    useChatStore.setState(initialState, true)
    vi.unstubAllGlobals()
  })

  /** Responds to /ask/stream with a pre-stream HTTP failure. */
  function failingStream(status: number, detail: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail }), { status })),
    )
  }

  function lastAnswer(): string {
    const history = useChatStore.getState().queryHistory
    return history[history.length - 1]?.finalState.failure_explanation ?? ''
  }

  it('tells an expired session to sign in, not that the agent is unreachable', async () => {
    failingStream(401, 'Missing or invalid Authorization header.')

    await useChatStore.getState().askQuestion('Tell me the sick leave policy.')

    expect(lastAnswer()).toBe('Your session has expired. Please sign in again.')
    expect(lastAnswer()).not.toMatch(/could not be reached/i)
  })

  it("passes the server's own wording through for other failures", async () => {
    failingStream(422, 'Unknown source: policies.')

    await useChatStore.getState().askQuestion('anything')

    expect(lastAnswer()).toBe('Unknown source: policies.')
  })

  it('still says "could not be reached" when the stream reported nothing', async () => {
    // A genuinely unreachable server -- the one case that message fits.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await useChatStore.getState().askQuestion('anything')

    expect(lastAnswer()).toBe('The agent could not be reached.')
  })
})
