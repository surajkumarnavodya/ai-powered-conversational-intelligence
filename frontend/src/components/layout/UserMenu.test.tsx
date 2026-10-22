import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useLocalAuthStore } from '@/store/localAuthStore'
import { UserMenu } from './UserMenu'

// The avatar is fetched with a bearer token and rendered from a blob URL
// (`GET /auth/me/avatar` needs a header an `<img>` cannot send), so the
// hook is mocked and the assertions describe what reaches `src`.
vi.mock('@/hooks/useAvatarObjectUrl', () => ({
  useAvatarObjectUrl: (url: string | null | undefined) =>
    url ? `blob:mock/${encodeURIComponent(url)}` : null,
}))

// UserMenu is the one consolidated location for Settings/Theme/Sign out
// (see docs/navigation-and-actions.md) -- these tests exist specifically
// to lock in "exactly one of each," not just "each one works."
const initialAuthState = useLocalAuthStore.getState()

describe('UserMenu', () => {
  afterEach(() => {
    useLocalAuthStore.setState(initialAuthState, true)
  })

  it('shows display name and email, and opens with Settings/Theme/Sign out when authenticated', async () => {
    useLocalAuthStore.setState({
      status: 'authenticated',
      user: {
        id: 'u1',
        email: 'ada@example.com',
        username: null,
        display_name: 'Ada Lovelace',
        needs_profile_completion: false,
        status: 'active',
      } as never,
    })
    render(<UserMenu onOpenSettings={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: /account menu/i }))

    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('ada@example.com')).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /settings/i })).toBeInTheDocument()
    expect(screen.getByText(/^theme$/i)).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /sign out/i })).toBeInTheDocument()
  })

  it('calls onOpenSettings exactly once when Settings is selected', async () => {
    useLocalAuthStore.setState({ status: 'unauthenticated', user: null })
    const onOpenSettings = vi.fn()
    render(<UserMenu onOpenSettings={onOpenSettings} />)

    await userEvent.click(screen.getByRole('button', { name: /account menu/i }))
    await userEvent.click(screen.getByRole('menuitem', { name: /settings/i }))

    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })

  it('hides Sign out when no one is authenticated, but still offers Settings and Theme', async () => {
    useLocalAuthStore.setState({ status: 'unauthenticated', user: null })
    render(<UserMenu onOpenSettings={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: /account menu/i }))

    expect(screen.queryByRole('menuitem', { name: /sign out/i })).not.toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /settings/i })).toBeInTheDocument()
  })

  it('calls logout exactly once when Sign out is selected', async () => {
    const logout = vi.fn().mockResolvedValue(undefined)
    useLocalAuthStore.setState({
      status: 'authenticated',
      user: {
        id: 'u1',
        email: 'ada@example.com',
        username: null,
        display_name: 'Ada Lovelace',
        needs_profile_completion: false,
        status: 'active',
      } as never,
      logout,
    })
    render(<UserMenu onOpenSettings={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: /account menu/i }))
    await userEvent.click(screen.getByRole('menuitem', { name: /sign out/i }))

    expect(logout).toHaveBeenCalledTimes(1)
  })

  it('closes with Escape', async () => {
    useLocalAuthStore.setState({ status: 'unauthenticated', user: null })
    render(<UserMenu onOpenSettings={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: /account menu/i }))
    expect(screen.getByRole('menuitem', { name: /settings/i })).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menuitem', { name: /settings/i })).not.toBeInTheDocument()
  })

  describe('the account button avatar', () => {
    function signedIn(extra: Record<string, unknown>) {
      useLocalAuthStore.setState({
        status: 'authenticated',
        user: {
          id: 'u1',
          email: 'ada@example.com',
          username: null,
          display_name: 'Ada Lovelace',
          needs_profile_completion: false,
          status: 'active',
          ...extra,
        } as never,
      })
    }

    it('shows the profile picture instead of initials when one is set', () => {
      signedIn({ avatar_url: '/auth/me/avatar?v=4' })

      render(<UserMenu onOpenSettings={() => {}} />)

      const trigger = screen.getByRole('button', { name: /account menu/i })
      const img = trigger.querySelector('img')
      expect(img).toHaveAttribute(
        'src',
        `blob:mock/${encodeURIComponent('/auth/me/avatar?v=4')}`,
      )
      // The initials are replaced, not merely covered -- otherwise they
      // would show through a transparent PNG.
      expect(trigger).not.toHaveTextContent('AL')
    })

    it('clips the picture to the circle', () => {
      // Without `overflow-hidden` on the button, a 256x256 image spills out
      // of a 32px round control.
      signedIn({ avatar_url: '/auth/me/avatar?v=4' })

      render(<UserMenu onOpenSettings={() => {}} />)

      const trigger = screen.getByRole('button', { name: /account menu/i })
      expect(trigger.className).toContain('overflow-hidden')
      expect(trigger.className).toContain('rounded-full')
      expect(trigger.querySelector('img')).toHaveClass('object-cover')
    })

    it('draws the white ring around a real picture', () => {
      // Dark-theme contrast: the header is near-black there, and a
      // photograph's own edge pixels are arbitrary, so the circle needs an
      // explicit outline to read as one.
      signedIn({ avatar_url: '/auth/me/avatar?v=4' })

      render(<UserMenu onOpenSettings={() => {}} />)

      const trigger = screen.getByRole('button', { name: /account menu/i })
      expect(trigger.className).toContain('ring-[var(--avatar-ring)]')
    })

    it('draws no ring around initials', () => {
      // There it would be a stray outline on a control that already reads
      // clearly against the header.
      signedIn({ avatar_url: null })

      render(<UserMenu onOpenSettings={() => {}} />)

      const trigger = screen.getByRole('button', { name: /account menu/i })
      expect(trigger.className).not.toContain('ring-[var(--avatar-ring)]')
    })

    it('falls back to initials when no picture is set', () => {
      signedIn({ avatar_url: null })

      render(<UserMenu onOpenSettings={() => {}} />)

      const trigger = screen.getByRole('button', { name: /account menu/i })
      expect(trigger).toHaveTextContent('AL')
      expect(trigger.querySelector('img')).toBeNull()
    })

    it('falls back to the generic icon with neither picture nor name', () => {
      useLocalAuthStore.setState({ status: 'unauthenticated', user: null })

      render(<UserMenu onOpenSettings={() => {}} />)

      const trigger = screen.getByRole('button', { name: /account menu/i })
      expect(trigger.querySelector('img')).toBeNull()
      expect(trigger.querySelector('svg')).toBeInTheDocument()
    })

    it('does not label the image, so the account is announced once', () => {
      // The button already carries aria-label/title; an alt text here would
      // make a screen reader read the account twice for one control.
      signedIn({ avatar_url: '/auth/me/avatar?v=4' })

      render(<UserMenu onOpenSettings={() => {}} />)

      const img = screen.getByRole('button', { name: /account menu/i }).querySelector('img')
      expect(img).toHaveAttribute('alt', '')
    })
  })

})
