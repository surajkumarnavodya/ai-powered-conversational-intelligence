import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/lib/api'
import type { ModelsResponse } from '@/lib/types'
import { useSettingsStore } from '@/store/settingsStore'
import { ComposerModelPicker } from './ComposerModelPicker'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, getAvailableModels: vi.fn() }
})

const initialSettingsState = useSettingsStore.getState()

function renderPicker() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ComposerModelPicker />
    </QueryClientProvider>,
  )
}

function response(overrides: Partial<ModelsResponse> = {}): ModelsResponse {
  return {
    provider: 'ollama',
    default_model: 'llama3.1:8b',
    selection_enabled: true,
    models: [
      {
        id: 'llama3.1:8b',
        display_name: 'Llama 3.1 8B',
        is_default: true,
        enabled: true,
        installed: true,
        available: true,
        recommended: true,
        parameter_size: '8B',
        context_length: 128000,
        resource_level: 'medium',
        capabilities: ['text', 'sql'],
        description: '',
      },
      {
        id: 'qwen3.8:27b',
        display_name: 'Qwen 3.8 27B',
        is_default: false,
        enabled: true,
        installed: true,
        available: true,
        recommended: true,
        parameter_size: '27B',
        context_length: 262144,
        resource_level: 'high',
        capabilities: ['text', 'sql'],
        description: '',
      },
      {
        id: 'qwen2.5-coder:7b',
        display_name: 'Qwen 2.5 Coder 7B',
        is_default: false,
        enabled: true,
        installed: false,
        available: false,
        recommended: true,
        parameter_size: '7B',
        context_length: 32000,
        resource_level: 'medium',
        capabilities: ['text', 'sql'],
        description: '',
      },
    ],
    ...overrides,
  }
}

describe('ComposerModelPicker', () => {
  beforeEach(() => {
    useSettingsStore.setState(initialSettingsState, true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    useSettingsStore.setState(initialSettingsState, true)
  })

  it('shows the server default when the user has not picked a model', async () => {
    vi.mocked(api.getAvailableModels).mockResolvedValue(response())
    renderPicker()
    await waitFor(() => expect(screen.getByText('Llama 3.1 8B')).toBeInTheDocument())
  })

  it('writes the choice to the same settings store the Settings panel uses', async () => {
    // The whole point of reusing `useSettingsStore.selectedModel` rather
    // than holding a second copy here: both pickers are views of one
    // value, so they cannot drift apart, and `chatStore` keeps reading
    // the one field it always read when it builds `AskRequest.model`.
    vi.mocked(api.getAvailableModels).mockResolvedValue(response())
    renderPicker()
    await waitFor(() => expect(screen.getByText('Llama 3.1 8B')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button'))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Qwen 3\.8 27B/ }))

    expect(useSettingsStore.getState().selectedModel).toBe('qwen3.8:27b')
  })

  it('stores null rather than the default id when the default is chosen', async () => {
    // `null` means "use the server default", which is what lets
    // chatStore omit `AskRequest.model` entirely. Pinning the default's
    // id instead would start sending it explicitly and would survive an
    // operator changing OLLAMA_MODEL.
    vi.mocked(api.getAvailableModels).mockResolvedValue(response())
    useSettingsStore.setState({ selectedModel: 'qwen3.8:27b' } as never)
    renderPicker()
    await waitFor(() => expect(screen.getByText('Qwen 3.8 27B')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button'))
    await userEvent.click(await screen.findByRole('menuitem', { name: /Llama 3\.1 8B/ }))

    expect(useSettingsStore.getState().selectedModel).toBeNull()
  })

  it('offers a configured-but-not-installed model as disabled rather than hiding it', async () => {
    vi.mocked(api.getAvailableModels).mockResolvedValue(response())
    renderPicker()
    await waitFor(() => expect(screen.getByText('Llama 3.1 8B')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button'))
    const notInstalled = await screen.findByRole('menuitem', { name: /Qwen 2\.5 Coder 7B/ })
    expect(notInstalled).toHaveAttribute('data-disabled')
  })

  it('renders nothing when the server has model selection switched off', async () => {
    vi.mocked(api.getAvailableModels).mockResolvedValue(response({ selection_enabled: false }))
    const { container } = renderPicker()
    // Nothing to wait for -- assert it stays empty rather than that it is
    // empty on the first frame, which it would be either way.
    await waitFor(() => expect(api.getAvailableModels).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when the models query fails, leaving the composer usable', async () => {
    vi.mocked(api.getAvailableModels).mockRejectedValue(new Error('boom'))
    const { container } = renderPicker()
    await waitFor(() => expect(api.getAvailableModels).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
