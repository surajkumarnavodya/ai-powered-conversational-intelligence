import { beforeEach, describe, expect, it, vi } from 'vitest'
import { askQuestionStreaming, parseSseChunk } from './askStream'

/** Builds a Response whose body streams `chunks` in order, so a test can
 * split an SSE message across reads exactly as a network would. */
function streamingResponse(chunks: string[], init: ResponseInit = {}): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(body, { status: 200, ...init })
}

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('parseSseChunk', () => {
  it('parses one complete message', () => {
    const { messages, rest } = parseSseChunk(sse('progress', { stage: 'a' }))
    expect(messages).toEqual([{ event: 'progress', data: { stage: 'a' } }])
    expect(rest).toBe('')
  })

  it('carries an incomplete trailing message into the remainder', () => {
    // The failure this prevents: a network read ending mid-message, and
    // the partial frame being discarded instead of completed.
    const buffer = sse('progress', { stage: 'a' }) + 'event: progress\ndata: {"sta'
    const { messages, rest } = parseSseChunk(buffer)
    expect(messages).toHaveLength(1)
    expect(rest).toBe('event: progress\ndata: {"sta')
  })

  it('ignores comment lines', () => {
    const { messages } = parseSseChunk(`: keepalive\n\n${sse('progress', { stage: 'a' })}`)
    expect(messages).toEqual([{ event: 'progress', data: { stage: 'a' } }])
  })

  it('skips a malformed message rather than failing the stream', () => {
    const buffer = 'event: progress\ndata: not json\n\n' + sse('done', { dropped: 0 })
    const { messages } = parseSseChunk(buffer)
    expect(messages).toEqual([{ event: 'done', data: { dropped: 0 } }])
  })

  it('handles several messages in one chunk', () => {
    const { messages } = parseSseChunk(
      sse('progress', { stage: 'a' }) + sse('progress', { stage: 'b' }),
    )
    expect(messages.map((m) => (m.data as { stage: string }).stage)).toEqual(['a', 'b'])
  })
})

describe('askQuestionStreaming', () => {
  it('forwards each progress event and resolves with the result', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamingResponse([
          sse('open', { status: 'running' }),
          sse('progress', { stage: 'retrieve_schema', attempt: 1, duration_ms: 12, outcome: 'ok' }),
          sse('progress', { stage: 'generate_sql', attempt: 1, duration_ms: 90, outcome: 'ok' }),
          sse('result', { session_id: 's1', status: 'succeeded' }),
          sse('done', { dropped: 0 }),
        ]),
      ),
    )

    const seen: string[] = []
    const result = await askQuestionStreaming(
      { question: 'q' },
      { onProgress: (e) => seen.push(e.stage) },
    )

    expect(seen).toEqual(['retrieve_schema', 'generate_sql'])
    expect(result?.status).toBe('succeeded')
  })

  it('reassembles a message split across two reads', async () => {
    const full = sse('result', { session_id: 's1', status: 'succeeded' })
    const split = Math.floor(full.length / 2)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(streamingResponse([full.slice(0, split), full.slice(split)])),
    )

    const result = await askQuestionStreaming({ question: 'q' })
    expect(result?.status).toBe('succeeded')
  })

  it('reports an in-band error event with its status', async () => {
    // Once the stream opens the HTTP status is already 200, so a refusal
    // has to arrive as an event -- a caller that only checked the status
    // would think this succeeded.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamingResponse([
          sse('open', { status: 'running' }),
          sse('error', { status: 429, detail: 'Too many requests.' }),
          sse('done', { dropped: 0 }),
        ]),
      ),
    )

    const errors: Array<[number, string]> = []
    const result = await askQuestionStreaming(
      { question: 'q' },
      { onError: (status, detail) => errors.push([status, detail]) },
    )

    expect(errors).toEqual([[429, 'Too many requests.']])
    expect(result).toBeNull()
  })

  it('reports a pre-stream HTTP failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Unknown source.' }), { status: 422 }),
      ),
    )

    const errors: Array<[number, string]> = []
    const result = await askQuestionStreaming(
      { question: 'q' },
      { onError: (status, detail) => errors.push([status, detail]) },
    )

    expect(errors).toEqual([[422, 'Unknown source.']])
    expect(result).toBeNull()
  })

  it('returns null when the run produced no result event', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([sse('done', {})])))
    expect(await askQuestionStreaming({ question: 'q' })).toBeNull()
  })

  it('sends the payload as JSON to /ask/stream', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([sse('done', {})]))
    vi.stubGlobal('fetch', fetchMock)

    await askQuestionStreaming({ question: 'q', sources: ['sql'] })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/ask/stream')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ question: 'q', sources: ['sql'] })
  })

  it('passes the abort signal through', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamingResponse([sse('done', {})]))
    vi.stubGlobal('fetch', fetchMock)

    const controller = new AbortController()
    await askQuestionStreaming({ question: 'q' }, {}, controller.signal)
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal)
  })
})
