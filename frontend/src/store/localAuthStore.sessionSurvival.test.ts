import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import type { TokenResponse } from '@/lib/types'
import { handleUnauthorized, isSessionEndedError, useLocalAuthStore } from './localAuthStore'

/** A live session must not be discarded because the *question* "are you
 * still signed in?" could not be answered.
 *
 * ## The defect these tests were written against (`UI-002`)
 *
 * Both places that call `POST /auth/refresh` -- `initialize()` on app load
 * and `handleUnauthorized()` on any 401 -- used a bare `catch` and set
 * `status: 'unauthenticated'` for *every* rejection. `/auth/refresh` shares
 * `login_rate_limit_per_minute` (default 10 per minute, keyed by client IP)
 * and one full page load spends one refresh, so eleven direct URL entries
 * in roughly forty seconds returned 429 -- measured in Chrome 2026-10-29 --
 * and the eleventh dropped the user to the sign-in form while their
 * `HttpOnly` refresh cookie was still perfectly valid.
 *
 * `handleUnauthorized` is the worse of the two: an access token expires
 * every `access_token_expire_minutes` (default 15), so an ordinary session
 * refreshes repeatedly, and one rate-limited moment ended it mid-question.
 *
 * Each test here fails against the old `catch`-everything behaviour.
 */

vi.mock('@/lib/identityApi', () => ({
  refreshAccessToken: vi.fn(),
  getCurrentUser: vi.fn(),
  loginUser: vi.fn(),
  logoutUser: vi.fn(),
  logoutAllSessions: vi.fn(),
  googleSignIn: vi.fn(),
  registerUser: vi.fn(),
  isTokenResponse: (value: unknown) =>
    typeof value === 'object' && value !== null && 'access_token' in value,
}))

// Deliberately no `{ ...(await importOriginal()) }` spread: the note at
// the top of `localAuthStore.test.ts` records that the spread leaves the
// real implementation in the module record, which `restoreMocks: true`
// (vitest.config.ts) then restores before each test -- so a mock set in a
// `beforeEach` silently stops being a mock. Declaring only what the store
// imports, and setting each implementation inside the test body, avoids
// both halves of that.
vi.mock('@/lib/api', () => {
  class FakeApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  }
  return { ApiError: FakeApiError, getHealth: vi.fn() }
})

const { refreshAccessToken } = await import('@/lib/identityApi')
const { getHealth } = await import('@/lib/api')

function localAuthEnabled() {
  vi.mocked(getHealth).mockResolvedValue({ local_auth_enabled: true } as never)
}

const initialState = useLocalAuthStore.getState()

function fakeTokenResponse(): TokenResponse {
  return {
    access_token: 'fresh-token',
    token_type: 'bearer',
    expires_in: 900,
    user: {
      id: 'user-1',
      email: 'someone@example.com',
      username: null,
      display_name: 'Someone',
      needs_profile_completion: false,
      status: 'active',
      is_email_verified: true,
      roles: ['user'],
      created_at: '2026-10-29T00:00:00Z',
      last_login_at: null,
    },
  }
}

function signedIn() {
  useLocalAuthStore.setState({
    status: 'authenticated',
    user: fakeTokenResponse().user,
    accessToken: 'the-live-token',
    error: null,
  })
}

describe('isSessionEndedError', () => {
  it('treats 401 as the session having ended', () => {
    expect(isSessionEndedError(new ApiError('No refresh token was presented.', 401))).toBe(true)
  })

  it('treats 404 (local auth not enabled) as the session having ended', () => {
    expect(isSessionEndedError(new ApiError('Not found.', 404))).toBe(true)
  })

  it('does not treat a rate limit as the session having ended', () => {
    expect(isSessionEndedError(new ApiError('Too many requests.', 429))).toBe(false)
  })

  it('does not treat a server error as the session having ended', () => {
    expect(isSessionEndedError(new ApiError('Bad gateway.', 502))).toBe(false)
    expect(isSessionEndedError(new ApiError('Unavailable.', 503))).toBe(false)
  })

  it('does not treat a failed fetch as the session having ended', () => {
    expect(isSessionEndedError(new TypeError('Failed to fetch'))).toBe(false)
  })

  it('does not treat an unknown status as the session having ended', () => {
    // A narrow allowlist on purpose: a status this function has never
    // heard of must not be allowed to end a session.
    expect(isSessionEndedError(new ApiError('Teapot.', 418))).toBe(false)
  })
})

describe('initialize() when the refresh cannot be answered', () => {
  beforeEach(() => {
    useLocalAuthStore.setState(initialState, true)
  })

  afterEach(() => {
    useLocalAuthStore.setState(initialState, true)
  })

  it('reports "unavailable", not "unauthenticated", on a 429', async () => {
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(new ApiError('Too many requests.', 429))
    await useLocalAuthStore.getState().initialize()
    expect(useLocalAuthStore.getState().status).toBe('unavailable')
  })

  it('reports "unavailable" on a 503', async () => {
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(new ApiError('Unavailable.', 503))
    await useLocalAuthStore.getState().initialize()
    expect(useLocalAuthStore.getState().status).toBe('unavailable')
  })

  it('reports "unavailable" when the network is gone', async () => {
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(new TypeError('Failed to fetch'))
    await useLocalAuthStore.getState().initialize()
    expect(useLocalAuthStore.getState().status).toBe('unavailable')
  })

  it('still reports "unauthenticated" when there genuinely is no session', async () => {
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(
      new ApiError('No refresh token was presented.', 401),
    )
    await useLocalAuthStore.getState().initialize()
    expect(useLocalAuthStore.getState().status).toBe('unauthenticated')
  })

  it('recovers to "authenticated" when a retry succeeds', async () => {
    localAuthEnabled()
    localAuthEnabled()
    vi.mocked(refreshAccessToken)
      .mockRejectedValueOnce(new ApiError('Too many requests.', 429))
      .mockResolvedValueOnce(fakeTokenResponse())
    await useLocalAuthStore.getState().initialize()
    expect(useLocalAuthStore.getState().status).toBe('unavailable')
    await useLocalAuthStore.getState().initialize()
    expect(useLocalAuthStore.getState().status).toBe('authenticated')
    expect(useLocalAuthStore.getState().accessToken).toBe('fresh-token')
  })
})

describe('handleUnauthorized() when the refresh cannot be answered', () => {
  beforeEach(() => {
    useLocalAuthStore.setState(initialState, true)
  })

  afterEach(() => {
    useLocalAuthStore.setState(initialState, true)
  })

  it('keeps the session on a 429 and returns no token', async () => {
    signedIn()
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(new ApiError('Too many requests.', 429))
    await expect(handleUnauthorized()).resolves.toBeNull()
    // The request that provoked this still fails, but the session stands:
    // the next 401 retries the refresh, which is what lets a rate-limit
    // window recover by itself.
    expect(useLocalAuthStore.getState().status).toBe('authenticated')
    expect(useLocalAuthStore.getState().accessToken).toBe('the-live-token')
  })

  it('keeps the session on a 500', async () => {
    signedIn()
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(new ApiError('Boom.', 500))
    await expect(handleUnauthorized()).resolves.toBeNull()
    expect(useLocalAuthStore.getState().status).toBe('authenticated')
  })

  it('ends the session on a 401, because that one really does mean no session', async () => {
    signedIn()
    localAuthEnabled()
    vi.mocked(refreshAccessToken).mockRejectedValue(new ApiError('Refresh token reused.', 401))
    await expect(handleUnauthorized()).resolves.toBeNull()
    expect(useLocalAuthStore.getState().status).toBe('unauthenticated')
    expect(useLocalAuthStore.getState().accessToken).toBeNull()
  })

  it('returns the fresh token and stays authenticated on success', async () => {
    signedIn()
    vi.mocked(refreshAccessToken).mockResolvedValue(fakeTokenResponse())
    await expect(handleUnauthorized()).resolves.toBe('fresh-token')
    expect(useLocalAuthStore.getState().status).toBe('authenticated')
  })

  it('does nothing at all when local auth is unconfigured', async () => {
    useLocalAuthStore.setState({ status: 'unconfigured' })
    await expect(handleUnauthorized()).resolves.toBeNull()
    expect(refreshAccessToken).not.toHaveBeenCalled()
    expect(useLocalAuthStore.getState().status).toBe('unconfigured')
  })
})
