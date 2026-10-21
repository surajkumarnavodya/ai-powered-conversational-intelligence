/**
 * Typed client for the asynchronous question queue (`api/jobs.py`).
 *
 * Reuses `request()` from `./api`, which already carries the bearer
 * token, retries once on a stale token, and normalises every error body
 * shape into a readable string. Writing a second fetch wrapper here would
 * duplicate all four of those behaviours and drift from them.
 *
 * **Nothing in these types is internal configuration.** `modelUsed` is
 * the same model name `AskResponse.model` already returns, and
 * `sourcesUsed` the same source names the "Sources used" panel already
 * shows. No provider name, key, routing rule, raw prompt or system
 * instruction crosses this boundary -- the server does not send them, and
 * this module has nowhere to put them.
 */
import { ApiError, request } from './api'

/** The seven statuses the server publishes. Mirrors `jobs.models.JobStatus`
 * and `api.jobs_schemas.JobStatusOut`; a server-side change that added an
 * eighth would surface here as a TypeScript error at the switch sites
 * rather than as a silently unhandled state. */
export type QuestionJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout'
  | 'blocked_by_guardrail'

/** Statuses that will not change again, so polling can stop. */
export const TERMINAL_JOB_STATUSES: readonly QuestionJobStatus[] = [
  'completed',
  'failed',
  'cancelled',
  'timeout',
  'blocked_by_guardrail',
]

export function isTerminalStatus(status: QuestionJobStatus): boolean {
  return TERMINAL_JOB_STATUSES.includes(status)
}

export interface SubmitQuestionRequest {
  question: string
  conversation_history?: { question: string; sql?: string | null; tables?: string[]; status?: string }[]
  enable_insight?: boolean
  session_id?: string | null
  conversation_id?: string | null
  attachment_ids?: string[]
  model?: string | null
  forecast_horizon?: number | null
  sources?: string[] | null
  /** Makes a resubmission return the original job rather than starting a
   * second run. The server treats this as *input* to an owner- and
   * tenant-scoped hash, never as the stored key itself, so it cannot
   * collide with another user's. */
  idempotency_key?: string | null
}

export interface SubmitQuestionResponse {
  job_id: string
  status: QuestionJobStatus
  duplicate: boolean
  queued_for_caller: number
  running_for_caller: number
  max_concurrent_per_user: number
}

export interface QuestionJob {
  job_id: string
  status: QuestionJobStatus
  question: string
  agent_status: string
  attempts: number
  submitted_at: number
  queue_wait_seconds: number
  run_seconds: number | null
  /** The governed answer payload, shaped like `AskResponse`. Re-governed
   * against the reader's current roles on every fetch, so a value here
   * can legitimately change between polls if roles changed. */
  result: Record<string, unknown> | null
  error_message: string | null
  guardrail_reason: string | null
  sources_used: string[]
  model_used: string | null
  source_confidence: Record<string, number>
  correlation_id: string | null
}

export interface QuestionJobList {
  jobs: QuestionJob[]
  total: number
}

export interface QueueStats {
  running_global: number
  queued_global: number
  max_concurrent_global: number
  max_concurrent_per_user: number
  max_queue_depth: number
  running_for_caller: number
  queued_for_caller: number
  enabled: boolean
}

/** Submit one question. Resolves with a job handle, not an answer.
 *
 * A 429 means the queue is full (genuine backpressure), which is
 * different from being over the concurrency limit -- that *queues*. The
 * caller distinguishes them by catching `ApiError` with `status === 429`;
 * anything else is a real failure. */
export async function submitQuestion(
  payload: SubmitQuestionRequest,
): Promise<SubmitQuestionResponse> {
  return request<SubmitQuestionResponse>('/questions', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchQuestionJob(jobId: string): Promise<QuestionJob> {
  return request<QuestionJob>(`/questions/${encodeURIComponent(jobId)}`)
}

export async function listQuestionJobs(options?: {
  status?: QuestionJobStatus[]
  limit?: number
}): Promise<QuestionJobList> {
  const params = new URLSearchParams()
  if (options?.status?.length) params.set('status', options.status.join(','))
  if (options?.limit) params.set('limit', String(options.limit))
  const query = params.toString()
  return request<QuestionJobList>(`/questions${query ? `?${query}` : ''}`)
}

export async function cancelQuestionJob(jobId: string): Promise<QuestionJob> {
  return request<QuestionJob>(`/questions/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
  })
}

/** Queue capacity. Returns `enabled: false` rather than throwing when the
 * feature is off, so the UI can hide the asynchronous affordances instead
 * of showing an error for a deployment that simply does not use them. */
export async function fetchQueueStats(): Promise<QueueStats> {
  return request<QueueStats>('/questions/stats')
}

/** True when an error means "the queue is full", as opposed to any other
 * failure. Named so the distinction is made once rather than by comparing
 * a magic number at each call site. */
export function isQueueFullError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 429
}

/** True when the deployment cannot attribute a job to this caller (its
 * auth mode identifies no individual user) or the queue is switched off.
 * Both mean "do not offer this feature", and neither is a bug to report. */
export function isQueueUnavailableError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.status === 503)
}
