import { refreshAccessTokenOnce } from '@/lib/api'
import { getBearerToken } from '@/store/authStore'
import type { AskResponse } from '@/lib/types'

/**
 * Client for `POST /ask/stream` -- the SSE counterpart of `askQuestion`.
 *
 * Uses `fetch` + a `ReadableStream` rather than `EventSource`, because
 * `EventSource` can only issue GET and cannot send the `Authorization`
 * header this API requires. The trade-off is that SSE framing has to be
 * parsed here; `parseSseChunk` below is exported so that parsing is
 * testable on its own.
 *
 * **Errors arrive in-band.** Once the server has sent SSE headers the HTTP
 * status is already 200, so a rate-limit or validation failure comes back
 * as an `error` event carrying the status it *would* have been. A caller
 * must handle `onError`, not just a rejected promise.
 */

/** One node transition. Mirrors `agent/progress.py::ProgressEvent`.
 *
 * Metadata only, by design: a progress event is emitted before the result
 * gate has run, so it carries no SQL, no row and no error message -- see
 * that module's docstring. */
export interface AskProgressEvent {
  stage: string
  attempt: number
  duration_ms: number
  outcome: 'ok' | 'no_op' | 'error'
  error_category: string | null
}

export interface AskStreamHandlers {
  /** Called once per node transition, as it happens. */
  onProgress?: (event: AskProgressEvent) => void
  /** Called if the run failed. `status` is the HTTP status it would have
   * had on the non-streaming route. */
  onError?: (status: number, detail: string) => void
}

interface SseMessage {
  event: string
  data: unknown
}

/**
 * Splits a buffer into complete SSE messages plus the unparsed remainder.
 *
 * SSE messages are separated by a blank line, and a network read can end
 * mid-message -- so the remainder must be carried into the next read
 * rather than discarded. Comment lines (`: keepalive`) are ignored, which
 * is what keeps a proxy from closing a legitimately slow run.
 */
export function parseSseChunk(buffer: string): { messages: SseMessage[]; rest: string } {
  const messages: SseMessage[] = []
  const blocks = buffer.split('\n\n')
  // The last element is either an incomplete message or '' -- either way
  // it is not ready to emit yet.
  const rest = blocks.pop() ?? ''

  for (const block of blocks) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue
      if (line.startsWith('event:')) event = line.slice('event:'.length).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice('data:'.length).trim())
    }
    if (dataLines.length === 0) continue
    try {
      messages.push({ event, data: JSON.parse(dataLines.join('\n')) })
    } catch {
      // A malformed message is skipped rather than failing the stream: one
      // bad frame should not discard a run that is otherwise fine, and the
      // terminal `done` event still arrives.
    }
  }
  return { messages, rest }
}

/**
 * Runs one question with streaming progress.
 *
 * Resolves with the same `AskResponse` `askQuestion` returns. Rejects only
 * on a transport failure or an aborted request -- a *server-side* failure
 * arrives via `onError` and then resolves with whatever the server sent,
 * because the stream completing is not itself an error.
 */
export async function askQuestionStreaming(
  payload: unknown,
  handlers: AskStreamHandlers = {},
  signal?: AbortSignal,
): Promise<AskResponse | null> {
  const send = (bearer: string | null | undefined) =>
    fetch('/ask/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
      },
      body: JSON.stringify(payload),
      signal,
    })

  let response = await send(getBearerToken())

  // Access tokens are short-lived, so one going stale mid-session is
  // routine rather than exceptional. `request()` has always refreshed and
  // retried transparently; this route could not use it (it needs the raw
  // Response to read an SSE body), and so was the single authenticated
  // call in the app that turned a recoverable expiry into a hard failure
  // -- the user saw the question fail and had to sign in again, while any
  // other route would have carried on silently. Retried exactly once, via
  // the shared single-flight refresh, so a question asked alongside other
  // in-flight calls still triggers only one `/auth/refresh`.
  if (response.status === 401) {
    const refreshedToken = await refreshAccessTokenOnce()
    if (refreshedToken) response = await send(refreshedToken)
  }

  // A pre-stream rejection (422 from request validation, 401 from auth)
  // is a real HTTP status, because those happen before the generator runs.
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // Non-JSON body; keep the generic message.
    }
    handlers.onError?.(response.status, detail)
    return null
  }

  if (!response.body) {
    handlers.onError?.(500, 'The server returned no response body.')
    return null
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result: AskResponse | null = null

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const { messages, rest } = parseSseChunk(buffer)
      buffer = rest

      for (const message of messages) {
        if (message.event === 'progress') {
          handlers.onProgress?.(message.data as AskProgressEvent)
        } else if (message.event === 'result') {
          result = message.data as AskResponse
        } else if (message.event === 'error') {
          const payloadData = message.data as { status?: number; detail?: string }
          handlers.onError?.(
            payloadData.status ?? 500,
            payloadData.detail ?? 'The request could not be completed.',
          )
        }
        // 'open' and 'done' carry no caller-visible payload.
      }
    }
  } finally {
    // Releasing the lock lets an aborted request tear down promptly
    // instead of holding the connection until GC.
    reader.releaseLock()
  }

  return result
}
