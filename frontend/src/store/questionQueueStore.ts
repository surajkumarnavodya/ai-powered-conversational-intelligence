import { create } from 'zustand'
import {
  cancelQuestionJob,
  fetchQueueStats,
  fetchQuestionJob,
  isQueueFullError,
  isQueueUnavailableError,
  isTerminalStatus,
  listQuestionJobs,
  submitQuestion,
  type QuestionJob,
  type QueueStats,
  type SubmitQuestionRequest,
} from '@/lib/questionsApi'

/**
 * Many questions in flight at once, each tracked independently.
 *
 * **Why this is a separate store from `chatStore`.** `chatStore` holds a
 * single `pendingQuestion` and a single `currentAbortController`: one
 * question at a time, which is the right model for the synchronous
 * `POST /ask` flow it drives and which is not changed here. Widening it
 * to a collection would rewrite every component that reads
 * `pendingQuestion` for a flow those components do not participate in.
 * This store owns the asynchronous flow and nothing else (CLAUDE.md 4.7:
 * additive, not a rewrite; 4.8: one owner per fact).
 *
 * **Polling, not streaming.** One timer polls every tracked non-terminal
 * job, rather than a timer per job: at 100 concurrent questions a
 * per-job timer is 100 timers and 100 requests per tick. A single pass
 * that fetches only the jobs that can still change keeps the request
 * count proportional to *live* work, and stops entirely when nothing is
 * live. No WebSocket endpoint exists (CLAUDE.md 13), and `POST /ask/stream`
 * is for the synchronous path.
 *
 * **Duplicate submission is prevented twice.** Locally, a question whose
 * text is already in flight is refused before a request is made, so the
 * UI never shows two identical cards. Server-side, an idempotency key
 * collapses a resubmission onto the original job. The local check alone
 * would not survive a double-click racing two renders, and the server
 * check alone would still briefly show a second card -- so both.
 *
 * **Nothing sensitive is held here.** Job records carry the question, the
 * governed answer, source names and a model name. No token, no provider
 * detail, no routing rule. Nothing is persisted: on refresh the store
 * rehydrates from the server (`refresh()`), which re-governs every result
 * against the caller's *current* roles -- whereas a persisted copy in
 * `localStorage` would be a stale snapshot governed for whoever the user
 * was last time.
 */

/** How often the polling pass runs while anything is live. 1.5s is a
 * compromise: fast enough that a short question feels responsive, slow
 * enough that 100 live jobs do not saturate the connection pool the
 * browser gives one origin. */
const POLL_INTERVAL_MS = 1500

/** Jobs fetched when rehydrating after a refresh or a remount. */
const REHYDRATE_LIMIT = 50

export type QueueAvailability = 'unknown' | 'available' | 'unavailable'

interface QuestionQueueState {
  /** Tracked jobs, newest first. Keyed by `job_id` in `byId` for O(1)
   * updates; `order` holds the display order so a poll that updates one
   * job cannot reshuffle the list under the user. */
  byId: Record<string, QuestionJob>
  order: string[]
  stats: QueueStats | null
  /** Whether the deployment offers this feature at all. `unavailable`
   * covers both "queue switched off" and "auth mode cannot attribute a
   * job to a user" -- neither is an error to report, and both mean the
   * asynchronous UI should stay hidden. */
  availability: QueueAvailability
  /** Set when a submission was refused because the queue is full. This is
   * genuine backpressure and distinct from being over the concurrency
   * limit, which queues silently and is not an error. */
  queueFullNotice: string | null
  /** Set when a submission failed for any other reason. */
  submitError: string | null
  isSubmitting: boolean

  submit: (payload: SubmitQuestionRequest) => Promise<string | null>
  cancel: (jobId: string) => Promise<void>
  refresh: () => Promise<void>
  refreshStats: () => Promise<void>
  pollOnce: () => Promise<void>
  startPolling: () => void
  stopPolling: () => void
  dismiss: (jobId: string) => void
  clearNotices: () => void
  reset: () => void
}

let pollTimer: ReturnType<typeof setInterval> | null = null

/** Questions submitted but not yet acknowledged by the server, by
 * normalised text. Module-level rather than store state on purpose: it
 * must be consulted and written *synchronously* inside `submit`, before
 * any `await`, which is exactly the window a double-click exploits. A
 * value read from `get()` after an await would already be stale. */
const inFlightQuestions = new Set<string>()

function normalise(question: string): string {
  return question.trim().toLowerCase()
}

function liveJobIds(state: Pick<QuestionQueueState, 'byId' | 'order'>): string[] {
  return state.order.filter((id) => {
    const job = state.byId[id]
    return job && !isTerminalStatus(job.status)
  })
}

function mergeJob(
  state: Pick<QuestionQueueState, 'byId' | 'order'>,
  job: QuestionJob,
): Pick<QuestionQueueState, 'byId' | 'order'> {
  const order = state.order.includes(job.job_id) ? state.order : [job.job_id, ...state.order]
  return { byId: { ...state.byId, [job.job_id]: job }, order }
}

export const useQuestionQueueStore = create<QuestionQueueState>()((set, get) => ({
  byId: {},
  order: [],
  stats: null,
  availability: 'unknown',
  queueFullNotice: null,
  submitError: null,
  isSubmitting: false,

  async submit(payload) {
    const key = normalise(payload.question)
    if (!key) return null

    // Local duplicate guard, before any await -- see `inFlightQuestions`.
    if (inFlightQuestions.has(key)) return null
    const alreadyTracked = get().order.some((id) => {
      const job = get().byId[id]
      return job && !isTerminalStatus(job.status) && normalise(job.question) === key
    })
    if (alreadyTracked) return null
    inFlightQuestions.add(key)

    set({ isSubmitting: true, submitError: null, queueFullNotice: null })
    try {
      const response = await submitQuestion({
        ...payload,
        // Server-side duplicate prevention, independent of the local
        // guard above. Derived from the question text so a double-click
        // that races two renders still collapses onto one job.
        idempotency_key: payload.idempotency_key ?? `ui:${key}`,
      })
      // The submit response is a handle, not a full job, so the card is
      // seeded from it and filled in by the first poll. Seeding rather
      // than waiting means the question appears instantly.
      const seeded: QuestionJob = {
        job_id: response.job_id,
        status: response.status,
        question: payload.question,
        agent_status: 'pending',
        attempts: 0,
        submitted_at: Date.now() / 1000,
        queue_wait_seconds: 0,
        run_seconds: null,
        result: null,
        error_message: null,
        guardrail_reason: null,
        sources_used: [],
        model_used: null,
        source_confidence: {},
        correlation_id: null,
      }
      set((state) => ({
        ...mergeJob(state, seeded),
        availability: 'available',
        isSubmitting: false,
      }))
      get().startPolling()
      void get().refreshStats()
      return response.job_id
    } catch (error) {
      if (isQueueUnavailableError(error)) {
        set({ availability: 'unavailable', isSubmitting: false })
        return null
      }
      if (isQueueFullError(error)) {
        set({
          queueFullNotice: error instanceof Error ? error.message : null,
          isSubmitting: false,
        })
        return null
      }
      set({
        submitError: error instanceof Error ? error.message : 'Submission failed.',
        isSubmitting: false,
      })
      return null
    } finally {
      inFlightQuestions.delete(key)
    }
  },

  async cancel(jobId) {
    try {
      const job = await cancelQuestionJob(jobId)
      set((state) => mergeJob(state, job))
      void get().refreshStats()
    } catch {
      // A cancel that lost the race with completion is a 409, and a job
      // that is not ours is a 404. Neither is worth an error banner --
      // the next poll shows the real status either way, which is the
      // honest thing to display.
      void get().pollOnce()
    }
  },

  async refresh() {
    try {
      const { jobs } = await listQuestionJobs({ limit: REHYDRATE_LIMIT })
      set({
        byId: Object.fromEntries(jobs.map((job) => [job.job_id, job])),
        order: jobs.map((job) => job.job_id),
        availability: 'available',
      })
      if (jobs.some((job) => !isTerminalStatus(job.status))) get().startPolling()
    } catch (error) {
      if (isQueueUnavailableError(error)) set({ availability: 'unavailable' })
    }
  },

  async refreshStats() {
    try {
      const stats = await fetchQueueStats()
      set({ stats, availability: stats.enabled ? 'available' : 'unavailable' })
    } catch (error) {
      if (isQueueUnavailableError(error)) set({ availability: 'unavailable' })
    }
  },

  async pollOnce() {
    const ids = liveJobIds(get())
    if (ids.length === 0) {
      get().stopPolling()
      return
    }
    // Only the jobs that can still change are fetched, so the request
    // count tracks live work rather than history length.
    const results = await Promise.allSettled(ids.map((id) => fetchQuestionJob(id)))
    const fetched = results
      .filter((r): r is PromiseFulfilledResult<QuestionJob> => r.status === 'fulfilled')
      .map((r) => r.value)
    if (fetched.length > 0) {
      set((state) => {
        const byId = { ...state.byId }
        for (const job of fetched) byId[job.job_id] = job
        return { byId }
      })
    }
    if (liveJobIds(get()).length === 0) {
      get().stopPolling()
      void get().refreshStats()
    }
  },

  startPolling() {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      void get().pollOnce()
    }, POLL_INTERVAL_MS)
  },

  stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  },

  dismiss(jobId) {
    // Removes the card from this view only. The job itself is untouched:
    // dismissing a *running* question must not silently cancel it, which
    // is why this is separate from `cancel`.
    set((state) => {
      const byId = { ...state.byId }
      delete byId[jobId]
      return { byId, order: state.order.filter((id) => id !== jobId) }
    })
  },

  clearNotices() {
    set({ queueFullNotice: null, submitError: null })
  },

  reset() {
    // Called on logout. Clears every tracked job and stops the timer, so
    // one user's questions are never visible to the next session in the
    // same tab.
    get().stopPolling()
    inFlightQuestions.clear()
    set({
      byId: {},
      order: [],
      stats: null,
      availability: 'unknown',
      queueFullNotice: null,
      submitError: null,
      isSubmitting: false,
    })
  },
}))

/** Jobs in display order, derived rather than stored, so `byId` and
 * `order` stay the single owners and cannot disagree with a denormalised
 * copy.
 *
 * **Not safe to pass directly to `useQuestionQueueStore(...)`.** It
 * builds a new array each call, and zustand compares selector results by
 * reference -- subscribing with it re-renders on every render until
 * React's update-depth guard fires (a real bug this comment exists to
 * prevent recurring). A component subscribes to `byId` and `order` and
 * joins them with `useMemo`; see `QuestionQueuePanel`. This export is for
 * reading a snapshot outside React, and for tests. */
export function selectJobs(state: QuestionQueueState): QuestionJob[] {
  return state.order.map((id) => state.byId[id]).filter(Boolean)
}

export function selectLiveCount(state: QuestionQueueState): number {
  return liveJobIds(state).length
}
