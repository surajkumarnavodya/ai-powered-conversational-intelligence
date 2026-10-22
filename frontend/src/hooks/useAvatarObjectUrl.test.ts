import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAvatarObjectUrl } from './useAvatarObjectUrl'

/** Regression guard for a bug found in live use, not by a test.
 *
 * `GET /auth/me/avatar` requires the `Authorization` header, and this
 * app's access token lives in JS state rather than a cookie. The first
 * version of this feature put the server URL straight into an `<img src>`,
 * so the browser issued a header-less request, got 401, and the settings
 * page rendered a broken-image icon with its alt text showing. The
 * `TestClient` API tests could not catch it: they set the header
 * explicitly, which a real `<img>` can never do.
 *
 * So the properties pinned here are (1) the fetch carries the token and
 * (2) one fetch is shared across consumers -- `ChatMessage` renders once
 * per message, and a fetch per turn would be dozens of requests for one
 * ~15 KB image.
 */

const getBearerToken = vi.fn<() => string | undefined>()
vi.mock('@/store/authStore', () => ({ getBearerToken: () => getBearerToken() }))

describe('useAvatarObjectUrl', () => {
  let fetchMock: ReturnType<typeof vi.fn>
  let created: string[]

  // The cache in the module under test is deliberately process-wide (one
  // signed-in user per tab), so it survives between tests -- `vi
  // .resetModules()` cannot clear state in an already-hoisted import. Each
  // test therefore uses its own version string instead of a shared one, so
  // no test can be satisfied by another test's cached entry. Exporting a
  // reset hatch purely for tests would put test-only API in production
  // code for the same result.
  let version = 0
  const freshUrl = () => `/auth/me/avatar?v=test${++version}`

  beforeEach(() => {
    vi.resetModules()
    created = []
    getBearerToken.mockReturnValue('test-token')
    fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['bytes'])))
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => {
        const url = `blob:avatar/${created.length}`
        created.push(url)
        return url
      }),
      revokeObjectURL: vi.fn(),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('returns null when no picture is set, and fetches nothing', () => {
    const { result } = renderHook(() => useAvatarObjectUrl(null))

    expect(result.current).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('fetches the avatar with the Authorization header', async () => {
    const target = freshUrl()
    renderHook(() => useAvatarObjectUrl(target))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(target)
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer test-token')
  })

  it('returns a blob URL rather than the server path', async () => {
    // The whole point: an `<img src>` must receive something the browser
    // can load without a header.
    // Hoisted out of the render callback deliberately: calling `freshUrl()`
    // inside it would mint a new URL on every render, so the effect's
    // dependency would change every time and never settle.
    const target = freshUrl()
    const { result } = renderHook(() => useAvatarObjectUrl(target))

    await waitFor(() => expect(result.current).not.toBeNull())
    expect(result.current).toMatch(/^blob:/)
  })

  it('fetches once for many consumers of the same version', async () => {
    // The property that keeps a 40-message conversation from issuing 40
    // requests for one avatar.
    const shared = freshUrl()
    const first = renderHook(() => useAvatarObjectUrl(shared))
    await waitFor(() => expect(first.result.current).not.toBeNull())

    const second = renderHook(() => useAvatarObjectUrl(shared))
    await waitFor(() => expect(second.result.current).not.toBeNull())

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(second.result.current).toBe(first.result.current)
  })

  it('refetches when the version changes', async () => {
    const before1 = freshUrl()
    const after1 = freshUrl()
    const { result, rerender } = renderHook(({ url }) => useAvatarObjectUrl(url), {
      initialProps: { url: before1 },
    })
    await waitFor(() => expect(result.current).not.toBeNull())
    const before = result.current

    rerender({ url: after1 })

    await waitFor(() => expect(result.current).not.toBe(before))
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('falls back to null when the fetch fails', async () => {
    // A 401 mid-token-refresh must degrade to the default icon, not to a
    // broken image or a thrown render.
    fetchMock.mockResolvedValue(new Response('nope', { status: 401 }))

    const target = freshUrl()
    const { result } = renderHook(() => useAvatarObjectUrl(target))

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(result.current).toBeNull()
  })

  it('does not cache a failure permanently', async () => {
    // The token may simply have been mid-refresh, so the next consumer
    // must be allowed to try again.
    const target = freshUrl()
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 401 }))
    const failed = renderHook(() => useAvatarObjectUrl(target))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(failed.result.current).toBeNull()

    fetchMock.mockResolvedValue(new Response(new Blob(['bytes'])))
    const retried = renderHook(() => useAvatarObjectUrl(target))

    await waitFor(() => expect(retried.result.current).not.toBeNull())
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
