import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SourceOut, SourcesOut } from '@/lib/types'
import { useSettingsStore } from '@/store/settingsStore'
import { SourcePicker } from './SourcePicker'

const mockGetSources = vi.fn<() => Promise<SourcesOut>>()

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  getSources: () => mockGetSources(),
}))

function source(overrides: Partial<SourceOut> = {}): SourceOut {
  return {
    name: 'sql',
    kind: 'structured_sql',
    description: 'the company databases',
    capabilities: {
      operations: ['search'],
      supports_citations: false,
      supports_pagination: false,
      supports_incremental_sync: false,
      has_side_effects: false,
      preserves_source_acl: false,
    },
    configured: true,
    authorized: true,
    required_permission: null,
    per_request: false,
    ...overrides,
  }
}

async function renderPicker(body: Partial<SourcesOut> = {}) {
  mockGetSources.mockResolvedValue({
    sources: [source()],
    usable: ['sql'],
    roles: ['admin'],
    ...body,
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <SourcePicker />
    </QueryClientProvider>,
  )
  // The list only exists once the popover is open.
  fireEvent.click(await screen.findByLabelText('Choose data sources'))
  return utils
}

beforeEach(() => {
  mockGetSources.mockReset()
  useSettingsStore.getState().setSelectedSources(null)
})

describe('SourcePicker', () => {
  it('offers a configured, authorized source', async () => {
    await renderPicker()
    expect(await screen.findByText('sql')).toBeInTheDocument()
  })

  it('selecting a source stores it', async () => {
    await renderPicker()
    fireEvent.click(await screen.findByRole('checkbox'))
    expect(useSettingsStore.getState().selectedSources).toEqual(['sql'])
  })

  it('deselecting the last source normalizes back to null, not an empty array', async () => {
    // `[]` is rejected by AskRequest.sources -- it would be ambiguous
    // server-side between "unrestricted" and "no sources at all".
    await renderPicker()
    const box = await screen.findByRole('checkbox')
    fireEvent.click(box)
    fireEvent.click(box)
    expect(useSettingsStore.getState().selectedSources).toBeNull()
  })

  it('clearing the selection resets to null', async () => {
    await renderPicker()
    fireEvent.click(await screen.findByRole('checkbox'))
    fireEvent.click(screen.getByText('Clear selection'))
    expect(useSettingsStore.getState().selectedSources).toBeNull()
  })

  it('does not offer an unconfigured source, and says why', async () => {
    await renderPicker({
      sources: [source(), source({ name: 'web', kind: 'web', configured: false })],
    })
    expect(await screen.findByText('web')).toBeInTheDocument()
    expect(screen.getByText('not set up')).toBeInTheDocument()
    // Listed as unavailable, so it is not a selectable checkbox.
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
  })

  it('does not offer an unauthorized source, and says why', async () => {
    await renderPicker({
      sources: [
        source(),
        source({ name: 'policy', kind: 'policy', authorized: false, required_permission: 'policy_rag_query' }),
      ],
    })
    expect(await screen.findByText('policy')).toBeInTheDocument()
    expect(screen.getByText('no access')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
  })

  it('distinguishes not-configured from not-permitted', async () => {
    // Two causes, two remedies -- an operator fixes one, an administrator
    // the other, so the UI must not collapse them into one message.
    await renderPicker({
      sources: [
        source({ name: 'web', kind: 'web', configured: false }),
        source({ name: 'policy', kind: 'policy', authorized: false }),
      ],
    })
    expect(await screen.findByText('not set up')).toBeInTheDocument()
    expect(screen.getByText('no access')).toBeInTheDocument()
  })

  it('excludes a per-request source from the standing choices', async () => {
    // `attachments` depends on whether a file is attached to that single
    // question, so offering it as a persistent choice would be misleading.
    await renderPicker({
      sources: [source(), source({ name: 'attachments', kind: 'attachment', per_request: true })],
    })
    await screen.findByText('sql')
    expect(screen.queryByText('attachments')).not.toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
  })

  it('reports a load failure instead of silently showing nothing', async () => {
    mockGetSources.mockRejectedValue(new Error('boom'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <SourcePicker />
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByLabelText('Choose data sources'))
    expect(await screen.findByText('Sources could not be loaded.')).toBeInTheDocument()
  })

  it('handles an empty source list', async () => {
    await renderPicker({ sources: [], usable: [] })
    expect(await screen.findByText('No sources are available to choose from.')).toBeInTheDocument()
  })
})
