import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import type { LocalUser } from '@/lib/types'
import { useLocalAuthStore } from '@/store/localAuthStore'
import { ProfilePictureSection } from './ProfilePictureSection'

const uploadProfilePicture = vi.fn()
const removeProfilePicture = vi.fn()

vi.mock('@/lib/identityApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/identityApi')>()),
  uploadProfilePicture: (file: File) => uploadProfilePicture(file),
  removeProfilePicture: () => removeProfilePicture(),
}))

// `GET /auth/me/avatar` needs a bearer token an `<img>` cannot send, so
// the stored URL is fetched and turned into a blob URL
// (`useAvatarObjectUrl`). Mocked here so the component test does not need
// a real fetch, and so the assertions below describe what actually reaches
// `src` -- a blob URL, not the server path.
vi.mock('@/hooks/useAvatarObjectUrl', () => ({
  useAvatarObjectUrl: (url: string | null | undefined) =>
    url ? `blob:mock/${encodeURIComponent(url)}` : null,
}))

function user(overrides: Partial<LocalUser> = {}): LocalUser {
  return {
    id: 'u1',
    email: 'alice@example.com',
    username: null,
    display_name: 'Alice',
    needs_profile_completion: false,
    status: 'active',
    is_email_verified: true,
    roles: ['user'],
    created_at: '2026-01-01T00:00:00Z',
    last_login_at: null,
    avatar_url: null,
    ...overrides,
  }
}

function pick(): File {
  return new File([new Uint8Array([1, 2, 3])], 'me.png', { type: 'image/png' })
}

async function choose(file: File = pick()) {
  // The visible control is a Button; the input is its hidden mechanism, so
  // the file is handed to the input directly rather than clicking through.
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await userEvent.upload(input, file)
}

describe('ProfilePictureSection', () => {
  // Removal is guarded by `window.confirm`, matching Sidebar.tsx's own
  // conversation-delete guard. Stubbed rather than left to jsdom, which
  // returns false and would make every removal test silently a no-op.
  const confirmSpy = vi.fn<() => boolean>()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('confirm', confirmSpy)
    confirmSpy.mockReturnValue(true)
    useLocalAuthStore.setState({ user: user() })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders nothing without a signed-in local account', () => {
    useLocalAuthStore.setState({ user: null })

    const { container } = render(<ProfilePictureSection />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the default icon and an upload action when no picture is set', () => {
    render(<ProfilePictureSection />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument()
  })

  it('shows the picture plus replace and remove once one is set', () => {
    useLocalAuthStore.setState({ user: user({ avatar_url: '/auth/me/avatar?v=7' }) })

    render(<ProfilePictureSection />)

    expect(screen.getByRole('img')).toHaveAttribute(
      'src',
      `blob:mock/${encodeURIComponent('/auth/me/avatar?v=7')}`,
    )
    expect(screen.getByRole('button', { name: /replace/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /remove/i })).toBeInTheDocument()
  })

  it('stores the returned user so every avatar updates at once', async () => {
    uploadProfilePicture.mockResolvedValue({
      user: user({ avatar_url: '/auth/me/avatar?v=99' }),
      vision_checked: true,
      vision_skipped_reason: null,
    })
    render(<ProfilePictureSection />)

    await choose()

    await waitFor(() =>
      expect(useLocalAuthStore.getState().user?.avatar_url).toBe('/auth/me/avatar?v=99'),
    )
  })

  it("shows the server's own reason when a picture is refused", async () => {
    // The point of the feature: a refusal explains itself and asks for
    // another picture, rather than failing silently.
    uploadProfilePicture.mockRejectedValue(new ApiError('Face is too blurry to use.', 400))
    render(<ProfilePictureSection />)

    await choose()

    expect(await screen.findByRole('alert')).toHaveTextContent('Face is too blurry to use.')
    expect(useLocalAuthStore.getState().user?.avatar_url).toBeNull()
  })

  it('discloses when the automated check did not run', async () => {
    uploadProfilePicture.mockResolvedValue({
      user: user({ avatar_url: '/auth/me/avatar?v=1' }),
      vision_checked: false,
      vision_skipped_reason: 'no vision model is configured',
    })
    render(<ProfilePictureSection />)

    await choose()

    expect(await screen.findByText(/automated photo check didn't run/i)).toBeInTheDocument()
  })

  it('says nothing about the check when it did run', async () => {
    uploadProfilePicture.mockResolvedValue({
      user: user({ avatar_url: '/auth/me/avatar?v=1' }),
      vision_checked: true,
      vision_skipped_reason: null,
    })
    render(<ProfilePictureSection />)

    await choose()

    await waitFor(() => expect(useLocalAuthStore.getState().user?.avatar_url).toBeTruthy())
    expect(screen.queryByText(/automated photo check didn't run/i)).not.toBeInTheDocument()
  })

  it('removes the picture and returns to the default icon', async () => {
    useLocalAuthStore.setState({ user: user({ avatar_url: '/auth/me/avatar?v=3' }) })
    removeProfilePicture.mockResolvedValue(user({ avatar_url: null }))
    confirmSpy.mockReturnValue(true)
    render(<ProfilePictureSection />)

    await userEvent.click(screen.getByRole('button', { name: /remove/i }))

    await waitFor(() => expect(useLocalAuthStore.getState().user?.avatar_url).toBeNull())
  })

  describe('removal confirmation', () => {
    it('asks before removing', async () => {
      useLocalAuthStore.setState({ user: user({ avatar_url: '/auth/me/avatar?v=3' }) })
      confirmSpy.mockReturnValue(true)
      removeProfilePicture.mockResolvedValue(user({ avatar_url: null }))
      render(<ProfilePictureSection />)

      await userEvent.click(screen.getByRole('button', { name: /remove/i }))

      expect(confirmSpy).toHaveBeenCalledTimes(1)
      await waitFor(() => expect(removeProfilePicture).toHaveBeenCalled())
    })

    it('removes nothing when the confirmation is declined', async () => {
      // The property that matters: declining must leave the picture alone,
      // not merely skip the dialog.
      useLocalAuthStore.setState({ user: user({ avatar_url: '/auth/me/avatar?v=3' }) })
      confirmSpy.mockReturnValue(false)
      render(<ProfilePictureSection />)

      await userEvent.click(screen.getByRole('button', { name: /remove/i }))

      expect(confirmSpy).toHaveBeenCalledTimes(1)
      expect(removeProfilePicture).not.toHaveBeenCalled()
      expect(useLocalAuthStore.getState().user?.avatar_url).toBe('/auth/me/avatar?v=3')
    })

    it('leaves no spinner behind when declined', async () => {
      // The confirm sits before `setBusy`, so declining must not disable
      // the controls or leave the "checking" row showing.
      useLocalAuthStore.setState({ user: user({ avatar_url: '/auth/me/avatar?v=3' }) })
      confirmSpy.mockReturnValue(false)
      render(<ProfilePictureSection />)

      await userEvent.click(screen.getByRole('button', { name: /remove/i }))

      expect(screen.getByRole('button', { name: /remove/i })).not.toBeDisabled()
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    })

    it('does not ask when uploading', async () => {
      // Replacing is not destructive -- the confirmation belongs to
      // removal alone, or it becomes a click tax on the common path.
      uploadProfilePicture.mockResolvedValue({
        user: user({ avatar_url: '/auth/me/avatar?v=2' }),
        vision_checked: true,
        vision_skipped_reason: null,
      })
      render(<ProfilePictureSection />)

      await choose()

      await waitFor(() => expect(uploadProfilePicture).toHaveBeenCalled())
      expect(confirmSpy).not.toHaveBeenCalled()
    })
  })

  describe('the white ring', () => {
    it('is drawn around a real picture, for dark-theme contrast', () => {
      useLocalAuthStore.setState({ user: user({ avatar_url: '/auth/me/avatar?v=7' }) })

      render(<ProfilePictureSection />)

      const frame = screen.getByRole('img').parentElement
      expect(frame?.className).toContain('ring-[var(--avatar-ring)]')
    })

    it('is absent when there is no picture to separate', () => {
      // Around the default icon the ring would be a stray outline.
      useLocalAuthStore.setState({ user: user({ avatar_url: null }) })

      const { container } = render(<ProfilePictureSection />)

      expect(container.innerHTML).not.toContain('ring-[var(--avatar-ring)]')
    })
  })

  it('lets the same file be retried after a refusal', async () => {
    // Without clearing the input, re-picking the identical file fires no
    // change event and the retry silently does nothing -- so "ask for
    // another" would be a dead end for anyone who fixes and re-exports to
    // the same filename.
    uploadProfilePicture.mockRejectedValueOnce(new ApiError('Too blurry.', 400))
    render(<ProfilePictureSection />)

    await choose()
    await screen.findByRole('alert')

    uploadProfilePicture.mockResolvedValueOnce({
      user: user({ avatar_url: '/auth/me/avatar?v=2' }),
      vision_checked: true,
      vision_skipped_reason: null,
    })
    await choose()

    await waitFor(() => expect(uploadProfilePicture).toHaveBeenCalledTimes(2))
  })
})
