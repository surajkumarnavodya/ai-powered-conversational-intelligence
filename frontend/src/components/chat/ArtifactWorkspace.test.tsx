import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { newHistoryEntry, type QueryHistoryEntry } from '@/lib/history'
import type { ArtifactDetailOut, ArtifactOut } from '@/lib/types'
import { askState } from '@/test/analyticsFixtures'
import { useChatStore } from '@/store/chatStore'
import { useLocalAuthStore } from '@/store/localAuthStore'
import {
  ArtifactWorkspace,
  SaveArtifactButton,
  resolveArtifactAnchor,
  summarizeArtifacts,
} from './ArtifactWorkspace'

const listArtifacts = vi.fn()
const getArtifact = vi.fn()
const saveArtifact = vi.fn()
const refreshArtifact = vi.fn()
const deleteArtifact = vi.fn()

vi.mock('@/lib/artifactsApi', () => ({
  listArtifacts: (...args: unknown[]) => listArtifacts(...args),
  getArtifact: (...args: unknown[]) => getArtifact(...args),
  saveArtifact: (...args: unknown[]) => saveArtifact(...args),
  refreshArtifact: (...args: unknown[]) => refreshArtifact(...args),
  deleteArtifact: (...args: unknown[]) => deleteArtifact(...args),
}))

function entry(question: string, overrides: Partial<QueryHistoryEntry> = {}): QueryHistoryEntry {
  return { ...newHistoryEntry(question, askState({ sql: 'SELECT 1' }), 1000), ...overrides }
}

function artifact(overrides: Partial<ArtifactOut> = {}): ArtifactOut {
  return {
    id: 'art-1',
    artifact_type: 'analysis',
    title: 'Revenue by region?',
    created_at: '2026-10-10T09:00:00Z',
    updated_at: '2026-10-10T09:00:00Z',
    conversation_id: 'conv-1',
    prompt_id: 'prompt-1',
    message_id: 'msg-1',
    database: 'default',
    model: 'llama3.1:8b',
    sources_used: ['sql'],
    row_count: 3,
    freshness: {
      state: 'current',
      reasons: [],
      result_age_seconds: 60,
      result_as_of: '2026-10-10T09:00:00Z',
    },
    is_refreshable: true,
    refresh_blocked_reason: null,
    last_refresh_at: null,
    last_refresh_outcome: 'never_refreshed',
    last_refresh_error: null,
    ...overrides,
  }
}

function detail(overrides: Partial<ArtifactDetailOut> = {}): ArtifactDetailOut {
  return {
    ...artifact(),
    question: 'Revenue by region?',
    insight: 'Australia leads.',
    insight_numbers_checked: 2,
    tables_referenced: ['sales'],
    governed_metrics: [],
    truth_level: 'ai_inference',
    sql: 'SELECT region, revenue FROM sales',
    sql_withheld_reason: null,
    origin_result: {
      columns: ['region', 'revenue'],
      rows: [['Australia', 2300000]],
      row_count: 3,
      returned_rows: 1,
      truncated: false,
      column_types: null,
      chart_recommendation: null,
      captured_at: '2026-10-10T09:00:00Z',
    },
    refreshed_result: null,
    result_changed_since_creation: false,
    ...overrides,
  }
}

function renderWorkspace(entries: QueryHistoryEntry[] = []) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ArtifactWorkspace entries={entries} />
    </QueryClientProvider>,
  )
}

function signIn() {
  useLocalAuthStore.setState({
    status: 'authenticated',
    user: { id: 'u1', email: 'a@example.com', display_name: 'A', roles: ['user'] } as never,
  })
}

function signOut() {
  useLocalAuthStore.setState({ status: 'unauthenticated', user: null })
}

beforeEach(() => {
  useChatStore.setState({ pinnedEntryIds: new Set() })
  signOut()
  listArtifacts.mockReset().mockResolvedValue({ items: [], total: 0, limit_per_user: 200 })
  getArtifact.mockReset().mockResolvedValue(detail())
  saveArtifact.mockReset().mockResolvedValue(detail())
  refreshArtifact.mockReset().mockResolvedValue(detail())
  deleteArtifact.mockReset().mockResolvedValue(undefined)
})

describe('summarizeArtifacts -- the session-only fallback', () => {
  it('includes only the pinned turns', () => {
    const a = entry('First?')
    const b = entry('Second?')
    const summaries = summarizeArtifacts([a, b], new Set([b.entryId]))
    expect(summaries.map((item) => item.question)).toEqual(['Second?'])
  })

  it('skips a pinned id that is not in the open conversation', () => {
    const a = entry('First?')
    expect(summarizeArtifacts([a], new Set(['some-other-entry']))).toEqual([])
  })

  it('prefers the confirmed row count over the agent-reported one', () => {
    const a = entry('First?', { confirmedColumns: ['c'], confirmedRows: [[1], [2]] })
    expect(summarizeArtifacts([a], new Set([a.entryId]))[0].rowCount).toBe(2)
  })

  it('references turns by id rather than copying their results', () => {
    const a = entry('First?', { confirmedColumns: ['c'], confirmedRows: [[1]] })
    const summary = summarizeArtifacts([a], new Set([a.entryId]))[0]
    expect(Object.keys(summary).sort()).toEqual(
      ['entryId', 'hasChart', 'hasResult', 'question', 'rowCount'].sort(),
    )
  })
})

describe('resolveArtifactAnchor -- navigating back to the source question', () => {
  it('matches a turn asked in this session by its server message id', () => {
    const live = entry('Revenue?', {
      finalState: askState({ sql: 'SELECT 1', message_id: 'msg-1' } as never),
    })
    expect(resolveArtifactAnchor({ message_id: 'msg-1', prompt_id: 'prompt-1' }, [live])).toBe(
      live.entryId,
    )
  })

  it('matches a turn restored from saved history by its prompt row id', () => {
    // A reloaded conversation keys each turn on its `Prompt` row id, which
    // is the artifact's own `prompt_id` -- which is exactly why "go to
    // question" can reuse the one existing scroll anchor.
    const restored = entry('Revenue?')
    restored.entryId = 'prompt-1'
    expect(resolveArtifactAnchor({ message_id: 'msg-1', prompt_id: 'prompt-1' }, [restored])).toBe(
      'prompt-1',
    )
  })

  it('is null when the artifact belongs to another conversation', () => {
    expect(resolveArtifactAnchor({ message_id: 'msg-9', prompt_id: 'p-9' }, [entry('Other?')])).toBe(
      null,
    )
  })

  it('is null when the artifact records neither id', () => {
    expect(resolveArtifactAnchor({ message_id: null, prompt_id: null }, [entry('Any?')])).toBe(null)
  })
})

describe('ArtifactWorkspace -- durable artifacts', () => {
  beforeEach(signIn)

  it('renders nothing when there is nothing saved', async () => {
    const { container } = renderWorkspace()
    await waitFor(() => expect(listArtifacts).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('lists the saved analyses with a count and their freshness', async () => {
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    renderWorkspace()
    const section = await screen.findByRole('region', { name: 'Analysis' })
    expect(within(section).getByText(/Analysis \(1\)/)).toBeInTheDocument()
    expect(within(section).getByText('Revenue by region?')).toBeInTheDocument()
    expect(within(section).getByText('Current')).toBeInTheDocument()
  })

  it('shows the freshness state as words, not only as a colour', async () => {
    listArtifacts.mockResolvedValue({
      items: [
        artifact({
          freshness: {
            state: 'stale',
            reasons: ['aged_past_stale_budget'],
            result_age_seconds: 999999,
            result_as_of: '2026-10-01T09:00:00Z',
          },
        }),
      ],
      total: 1,
      limit_per_user: 200,
    })
    renderWorkspace()
    expect(await screen.findByText('Stale')).toBeInTheDocument()
  })

  it('opens one artifact on demand, because a list row carries no rows', async () => {
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    renderWorkspace()
    const row = await screen.findByRole('button', { name: /^Revenue by region/ })
    expect(getArtifact).not.toHaveBeenCalled()

    fireEvent.click(row)
    await waitFor(() => expect(getArtifact).toHaveBeenCalledWith('art-1'))
    expect(await screen.findByText('SELECT region, revenue FROM sales')).toBeInTheDocument()
  })

  it('wires the row toggle to its detail panel for assistive technology', async () => {
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    renderWorkspace()
    const row = await screen.findByRole('button', { name: /^Revenue by region/ })
    expect(row).toHaveAttribute('aria-expanded', 'false')
    expect(row).toHaveAttribute('aria-controls', 'artifact-detail-art-1')
    fireEvent.click(row)
    expect(row).toHaveAttribute('aria-expanded', 'true')
  })

  it('jumps to the source question, reusing the chat anchor', async () => {
    const live = entry('Revenue by region?', {
      finalState: askState({ sql: 'SELECT 1', message_id: 'msg-1' } as never),
    })
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })

    const anchor = document.createElement('div')
    anchor.id = `turn-${live.entryId}`
    const scrollIntoView = vi.fn()
    anchor.scrollIntoView = scrollIntoView
    document.body.append(anchor)

    renderWorkspace([live])
    fireEvent.click(await screen.findByRole('button', { name: /^Go to the question behind/ }))
    expect(scrollIntoView).toHaveBeenCalled()
    anchor.remove()
  })

  it('disables the jump when the artifact belongs to another conversation', async () => {
    // Rather than silently scrolling nowhere.
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    renderWorkspace([entry('A different question?')])
    expect(await screen.findByRole('button', { name: /^Go to the question behind/ })).toBeDisabled()
  })

  it('refreshes an artifact server-side, sending no SQL', async () => {
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: /^Re-run/ }))
    // React Query hands a mutationFn `(variables, context)`, so the
    // artifact id is the first argument; the rest is its own plumbing.
    await waitFor(() => expect(refreshArtifact).toHaveBeenCalled())
    expect(refreshArtifact.mock.calls[0][0]).toBe('art-1')
    // The client cannot turn a refresh into a less-governed execution --
    // nothing resembling SQL is sent at all.
    expect(JSON.stringify(refreshArtifact.mock.calls[0])).not.toMatch(/select/i)
  })

  it('offers no refresh when the server says it is not refreshable', async () => {
    listArtifacts.mockResolvedValue({
      items: [
        artifact({
          is_refreshable: false,
          refresh_blocked_reason: 'This artifact has no query to re-run.',
        }),
      ],
      total: 1,
      limit_per_user: 200,
    })
    renderWorkspace()
    expect(await screen.findByRole('button', { name: /^Re-run/ })).toBeDisabled()
  })

  it('deletes an artifact', async () => {
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: /^Delete/ }))
    await waitFor(() => expect(deleteArtifact).toHaveBeenCalled())
    expect(deleteArtifact.mock.calls[0][0]).toBe('art-1')
  })

  it('never decides freshness itself', async () => {
    // A deliberately contradictory row: a very old capture that the server
    // nonetheless calls current. The UI must report the server's verdict,
    // because only the server can see the schema fingerprint and the
    // refresh history.
    listArtifacts.mockResolvedValue({
      items: [
        artifact({
          created_at: '2020-01-01T00:00:00Z',
          freshness: {
            state: 'current',
            reasons: [],
            result_age_seconds: 1,
            result_as_of: '2026-10-10T09:00:00Z',
          },
        }),
      ],
      total: 1,
      limit_per_user: 200,
    })
    renderWorkspace()
    expect(await screen.findByText('Current')).toBeInTheDocument()
    expect(screen.queryByText('Stale')).not.toBeInTheDocument()
  })
})

describe('ArtifactWorkspace -- the session-only fallback', () => {
  it('falls back to the pinned list when nobody is signed in', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    renderWorkspace([a])
    const section = screen.getByRole('region', { name: 'Pinned analyses' })
    expect(within(section).getByText('Revenue by region?')).toBeInTheDocument()
    // Said plainly rather than left to be discovered on reload.
    expect(within(section).getByText(/only/i)).toBeInTheDocument()
  })

  it('does not call the artifact API at all without an account', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    renderWorkspace([a])
    expect(listArtifacts).not.toHaveBeenCalled()
  })

  it('unpins from the session list', () => {
    const a = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([a.entryId]) })
    renderWorkspace([a])
    fireEvent.click(screen.getByRole('button', { name: /^Remove/ }))
    expect(useChatStore.getState().pinnedEntryIds.has(a.entryId)).toBe(false)
  })
})

describe('SaveArtifactButton', () => {
  function renderButton(e: QueryHistoryEntry) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(
      <QueryClientProvider client={queryClient}>
        <SaveArtifactButton entry={e} />
      </QueryClientProvider>,
    )
  }

  it('saves durably when the turn was persisted and an account is signed in', async () => {
    signIn()
    const e = entry('Revenue by region?', {
      finalState: askState({
        sql: 'SELECT 1',
        conversation_id: 'conv-1',
        message_id: 'msg-1',
      } as never),
    })
    renderButton(e)
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(saveArtifact).toHaveBeenCalled())
    expect(saveArtifact.mock.calls[0][0]).toEqual({
      conversation_id: 'conv-1',
      message_id: 'msg-1',
    })
    // No rows, no columns, no result of any kind: the server reads those
    // from the turn it already persisted.
    expect(Object.keys(saveArtifact.mock.calls[0][0]).sort()).toEqual([
      'conversation_id',
      'message_id',
    ])
  })

  it('shows itself as pressed once the turn already has an artifact', async () => {
    signIn()
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    const e = entry('Revenue by region?', {
      finalState: askState({
        sql: 'SELECT 1',
        conversation_id: 'conv-1',
        message_id: 'msg-1',
      } as never),
    })
    renderButton(e)
    await waitFor(() =>
      expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true'),
    )
  })

  it('removes the existing artifact when pressed again', async () => {
    signIn()
    listArtifacts.mockResolvedValue({ items: [artifact()], total: 1, limit_per_user: 200 })
    const e = entry('Revenue by region?', {
      finalState: askState({
        sql: 'SELECT 1',
        conversation_id: 'conv-1',
        message_id: 'msg-1',
      } as never),
    })
    renderButton(e)
    await waitFor(() =>
      expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true'),
    )
    fireEvent.click(screen.getByRole('button'))
    await waitFor(() => expect(deleteArtifact).toHaveBeenCalled())
    expect(deleteArtifact.mock.calls[0][0]).toBe('art-1')
    expect(saveArtifact).not.toHaveBeenCalled()
  })

  it('falls back to a session pin when nobody is signed in', () => {
    signOut()
    const e = entry('Revenue by region?')
    renderButton(e)
    fireEvent.click(screen.getByRole('button'))
    expect(useChatStore.getState().pinnedEntryIds.has(e.entryId)).toBe(true)
    expect(saveArtifact).not.toHaveBeenCalled()
  })

  it('falls back to a session pin when the turn was never persisted', () => {
    // Signed in, but this turn has no `message_id` -- persistence is
    // fail-open, so a turn can legitimately have none. Offering a durable
    // Save here would quietly 404.
    signIn()
    // `askState` always supplies both ids, so they are cleared explicitly
    // here -- this is the case persistence genuinely failed, or local auth
    // was off when the question was asked.
    const e = entry('Revenue by region?', {
      finalState: askState({ sql: 'SELECT 1', conversation_id: null, message_id: null }),
    })
    renderButton(e)
    fireEvent.click(screen.getByRole('button'))
    expect(useChatStore.getState().pinnedEntryIds.has(e.entryId)).toBe(true)
    expect(saveArtifact).not.toHaveBeenCalled()
  })

  it('exposes its pressed state to assistive technology', () => {
    signOut()
    const e = entry('Revenue by region?')
    useChatStore.setState({ pinnedEntryIds: new Set([e.entryId]) })
    renderButton(e)
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })
})
