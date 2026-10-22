import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import * as questionsApi from '@/lib/questionsApi'
import type { QuestionJob, QuestionJobStatus } from '@/lib/questionsApi'
import {
  selectJobs,
  selectLiveCount,
  useQuestionQueueStore,
} from '@/store/questionQueueStore'

/**
 * The asynchronous question store. The properties worth pinning:
 *
 *  - several questions are tracked at once, independently;
 *  - a duplicate submission never creates a second card;
 *  - polling stops once nothing can change, so an idle tab is silent;
 *  - a deployment without the feature produces no error, just no UI;
 *  - logout clears everything, so the next session in the same tab
 *    cannot see the previous user's questions.
 */

function makeJob(overrides: Partial<QuestionJob> = {}): QuestionJob {
  return {
    job_id: 'q_1',
    status: 'queued' as QuestionJobStatus,
    question: 'Show total sales by region',
    agent_status: 'pending',
    attempts: 0,
    submitted_at: 1_700_000_000,
    queue_wait_seconds: 0,
    run_seconds: null,
    result: null,
    error_message: null,
    guardrail_reason: null,
    sources_used: [],
    model_used: null,
    source_confidence: {},
    correlation_id: null,
    ...overrides,
  }
}

beforeEach(() => {
  useQuestionQueueStore.getState().reset()
  vi.restoreAllMocks()
})

afterEach(() => {
  useQuestionQueueStore.getState().reset()
  vi.useRealTimers()
})

describe('submitting', () => {
  it('tracks several questions at once, independently', async () => {
    let n = 0
    vi.spyOn(questionsApi, 'submitQuestion').mockImplementation(async () => {
      n += 1
      return {
        job_id: `q_${n}`,
        status: 'queued',
        duplicate: false,
        queued_for_caller: n,
        running_for_caller: 0,
        max_concurrent_per_user: 100,
      }
    })
    vi.spyOn(questionsApi, 'fetchQueueStats').mockResolvedValue({
      running_global: 0,
      queued_global: 3,
      max_concurrent_global: 500,
      max_concurrent_per_user: 100,
      max_queue_depth: 1000,
      running_for_caller: 0,
      queued_for_caller: 3,
      enabled: true,
    })

    const store = useQuestionQueueStore.getState()
    await store.submit({ question: 'first question about sales' })
    await store.submit({ question: 'second question about revenue' })
    await store.submit({ question: 'third question about margin' })

    const jobs = selectJobs(useQuestionQueueStore.getState())
    expect(jobs).toHaveLength(3)
    expect(new Set(jobs.map((j) => j.job_id)).size).toBe(3)
    expect(selectLiveCount(useQuestionQueueStore.getState())).toBe(3)
  })

  it('shows the question immediately, before the first poll', async () => {
    vi.spyOn(questionsApi, 'submitQuestion').mockResolvedValue({
      job_id: 'q_1',
      status: 'queued',
      duplicate: false,
      queued_for_caller: 1,
      running_for_caller: 0,
      max_concurrent_per_user: 100,
    })
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('not called'))

    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })
    expect(selectJobs(useQuestionQueueStore.getState())[0].question).toBe(
      'Show sales by region',
    )
  })

  it('never creates a second card for a question already in flight', async () => {
    const submit = vi.spyOn(questionsApi, 'submitQuestion').mockResolvedValue({
      job_id: 'q_1',
      status: 'queued',
      duplicate: false,
      queued_for_caller: 1,
      running_for_caller: 0,
      max_concurrent_per_user: 100,
    })
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    const store = useQuestionQueueStore.getState()
    await store.submit({ question: 'Show sales by region' })
    await store.submit({ question: 'Show sales by region' })

    expect(submit).toHaveBeenCalledTimes(1)
    expect(selectJobs(useQuestionQueueStore.getState())).toHaveLength(1)
  })

  it('collapses a double-click that races two submissions', async () => {
    const submit = vi.spyOn(questionsApi, 'submitQuestion').mockImplementation(
      async () =>
        new Promise((resolve) =>
          setTimeout(
            () =>
              resolve({
                job_id: 'q_1',
                status: 'queued',
                duplicate: false,
                queued_for_caller: 1,
                running_for_caller: 0,
                max_concurrent_per_user: 100,
              }),
            5,
          ),
        ),
    )
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    const store = useQuestionQueueStore.getState()
    // Both calls start before either resolves -- the window a real
    // double-click exploits, and why the local guard is checked before
    // any await.
    await Promise.all([
      store.submit({ question: 'Show sales by region' }),
      store.submit({ question: 'Show sales by region' }),
    ])
    expect(submit).toHaveBeenCalledTimes(1)
  })

  it('always sends a server-side idempotency key', async () => {
    const submit = vi.spyOn(questionsApi, 'submitQuestion').mockResolvedValue({
      job_id: 'q_1',
      status: 'queued',
      duplicate: false,
      queued_for_caller: 1,
      running_for_caller: 0,
      max_concurrent_per_user: 100,
    })
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })
    expect(submit.mock.calls[0][0].idempotency_key).toBeTruthy()
  })

  it('reports a full queue as a notice rather than a silent drop', async () => {
    vi.spyOn(questionsApi, 'submitQuestion').mockRejectedValue(
      new ApiError('There are too many questions waiting right now.', 429),
    )
    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })
    const state = useQuestionQueueStore.getState()
    expect(state.queueFullNotice).toContain('too many questions')
    expect(selectJobs(state)).toHaveLength(0)
  })

  it('marks the feature unavailable rather than erroring when it is off', async () => {
    vi.spyOn(questionsApi, 'submitQuestion').mockRejectedValue(
      new ApiError('Asynchronous questions are not enabled on this deployment.', 503),
    )
    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })
    const state = useQuestionQueueStore.getState()
    expect(state.availability).toBe('unavailable')
    expect(state.submitError).toBeNull()
  })

  it('marks the feature unavailable when the caller cannot be attributed', async () => {
    vi.spyOn(questionsApi, 'submitQuestion').mockRejectedValue(
      new ApiError('Asynchronous questions require an authenticated user account.', 403),
    )
    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })
    expect(useQuestionQueueStore.getState().availability).toBe('unavailable')
  })

  it('surfaces any other failure as a submit error', async () => {
    vi.spyOn(questionsApi, 'submitQuestion').mockRejectedValue(
      new ApiError('Something went wrong.', 500),
    )
    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })
    expect(useQuestionQueueStore.getState().submitError).toBe('Something went wrong.')
  })

  it('ignores an empty question without calling the server', async () => {
    const submit = vi.spyOn(questionsApi, 'submitQuestion')
    await useQuestionQueueStore.getState().submit({ question: '   ' })
    expect(submit).not.toHaveBeenCalled()
  })
})

describe('polling', () => {
  it('fetches only the jobs that can still change', async () => {
    useQuestionQueueStore.setState({
      byId: {
        q_live: makeJob({ job_id: 'q_live', status: 'running' }),
        q_done: makeJob({ job_id: 'q_done', status: 'completed' }),
      },
      order: ['q_live', 'q_done'],
    })
    const fetchJob = vi
      .spyOn(questionsApi, 'fetchQuestionJob')
      .mockResolvedValue(makeJob({ job_id: 'q_live', status: 'running' }))

    await useQuestionQueueStore.getState().pollOnce()

    expect(fetchJob).toHaveBeenCalledTimes(1)
    expect(fetchJob).toHaveBeenCalledWith('q_live')
  })

  it('stops polling once every job is terminal', async () => {
    useQuestionQueueStore.setState({
      byId: { q_1: makeJob({ status: 'running' }) },
      order: ['q_1'],
    })
    vi.spyOn(questionsApi, 'fetchQuestionJob').mockResolvedValue(
      makeJob({ status: 'completed' }),
    )
    const stats = vi.spyOn(questionsApi, 'fetchQueueStats').mockResolvedValue({
      running_global: 0,
      queued_global: 0,
      max_concurrent_global: 500,
      max_concurrent_per_user: 100,
      max_queue_depth: 1000,
      running_for_caller: 0,
      queued_for_caller: 0,
      enabled: true,
    })

    await useQuestionQueueStore.getState().pollOnce()

    expect(selectLiveCount(useQuestionQueueStore.getState())).toBe(0)
    // Capacity is refreshed exactly once on the transition to idle, so
    // the panel's "n of m running" is correct without continuing to poll.
    expect(stats).toHaveBeenCalledTimes(1)
  })

  it('a poll with nothing live makes no requests at all', async () => {
    const fetchJob = vi.spyOn(questionsApi, 'fetchQuestionJob')
    await useQuestionQueueStore.getState().pollOnce()
    expect(fetchJob).not.toHaveBeenCalled()
  })

  it('one failed fetch does not discard the other jobs updates', async () => {
    useQuestionQueueStore.setState({
      byId: {
        q_1: makeJob({ job_id: 'q_1', status: 'running' }),
        q_2: makeJob({ job_id: 'q_2', status: 'running' }),
      },
      order: ['q_1', 'q_2'],
    })
    vi.spyOn(questionsApi, 'fetchQuestionJob').mockImplementation(async (id) => {
      if (id === 'q_1') throw new ApiError('transient', 500)
      return makeJob({ job_id: 'q_2', status: 'completed' })
    })
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    await useQuestionQueueStore.getState().pollOnce()

    const state = useQuestionQueueStore.getState()
    expect(state.byId.q_2.status).toBe('completed')
    expect(state.byId.q_1.status).toBe('running')
  })
})

describe('cancelling and dismissing', () => {
  it('cancelling updates the job in place', async () => {
    useQuestionQueueStore.setState({
      byId: { q_1: makeJob({ status: 'running' }) },
      order: ['q_1'],
    })
    vi.spyOn(questionsApi, 'cancelQuestionJob').mockResolvedValue(
      makeJob({ status: 'cancelled' }),
    )
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    await useQuestionQueueStore.getState().cancel('q_1')
    expect(useQuestionQueueStore.getState().byId.q_1.status).toBe('cancelled')
  })

  it('a lost cancel race re-polls instead of showing an error', async () => {
    useQuestionQueueStore.setState({
      byId: { q_1: makeJob({ status: 'running' }) },
      order: ['q_1'],
    })
    vi.spyOn(questionsApi, 'cancelQuestionJob').mockRejectedValue(
      new ApiError('This question already finished (completed).', 409),
    )
    const fetchJob = vi
      .spyOn(questionsApi, 'fetchQuestionJob')
      .mockResolvedValue(makeJob({ status: 'completed' }))
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    await useQuestionQueueStore.getState().cancel('q_1')

    expect(fetchJob).toHaveBeenCalled()
    expect(useQuestionQueueStore.getState().submitError).toBeNull()
  })

  it('dismissing removes the card without cancelling the job', async () => {
    const cancel = vi.spyOn(questionsApi, 'cancelQuestionJob')
    useQuestionQueueStore.setState({
      byId: { q_1: makeJob({ status: 'running' }) },
      order: ['q_1'],
    })
    useQuestionQueueStore.getState().dismiss('q_1')
    expect(selectJobs(useQuestionQueueStore.getState())).toHaveLength(0)
    expect(cancel).not.toHaveBeenCalled()
  })
})

describe('rehydration and session boundaries', () => {
  it('refresh replaces the tracked set from the server', async () => {
    vi.spyOn(questionsApi, 'listQuestionJobs').mockResolvedValue({
      jobs: [makeJob({ job_id: 'q_a', status: 'completed' })],
      total: 1,
    })
    await useQuestionQueueStore.getState().refresh()
    const jobs = selectJobs(useQuestionQueueStore.getState())
    expect(jobs.map((j) => j.job_id)).toEqual(['q_a'])
  })

  it('refresh on a deployment without the feature is not an error', async () => {
    vi.spyOn(questionsApi, 'listQuestionJobs').mockRejectedValue(
      new ApiError('not enabled', 503),
    )
    await useQuestionQueueStore.getState().refresh()
    expect(useQuestionQueueStore.getState().availability).toBe('unavailable')
  })

  it('reset clears every job, so the next session sees nothing', () => {
    useQuestionQueueStore.setState({
      byId: { q_1: makeJob({ result: { answer: 'sensitive' } }) },
      order: ['q_1'],
      stats: {
        running_global: 1,
        queued_global: 0,
        max_concurrent_global: 500,
        max_concurrent_per_user: 100,
        max_queue_depth: 1000,
        running_for_caller: 1,
        queued_for_caller: 0,
        enabled: true,
      },
    })
    useQuestionQueueStore.getState().reset()
    const state = useQuestionQueueStore.getState()
    expect(selectJobs(state)).toHaveLength(0)
    expect(state.stats).toBeNull()
    expect(state.availability).toBe('unknown')
  })

  it('a question can be resubmitted after a reset', async () => {
    const submit = vi.spyOn(questionsApi, 'submitQuestion').mockResolvedValue({
      job_id: 'q_1',
      status: 'queued',
      duplicate: false,
      queued_for_caller: 1,
      running_for_caller: 0,
      max_concurrent_per_user: 100,
    })
    vi.spyOn(questionsApi, 'fetchQueueStats').mockRejectedValue(new Error('ignored'))

    const store = useQuestionQueueStore.getState()
    await store.submit({ question: 'Show sales by region' })
    useQuestionQueueStore.getState().reset()
    await useQuestionQueueStore.getState().submit({ question: 'Show sales by region' })

    expect(submit).toHaveBeenCalledTimes(2)
  })
})

describe('terminal status helper', () => {
  it('classifies every published status', () => {
    const statuses: QuestionJobStatus[] = [
      'queued',
      'running',
      'completed',
      'failed',
      'cancelled',
      'timeout',
      'blocked_by_guardrail',
    ]
    const live = statuses.filter((s) => !questionsApi.isTerminalStatus(s))
    expect(live).toEqual(['queued', 'running'])
  })
})
