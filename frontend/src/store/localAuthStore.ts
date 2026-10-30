import { create } from 'zustand'
import { ApiError, getHealth } from '@/lib/api'
import {
  getCurrentUser,
  googleSignIn,
  isTokenResponse,
  loginUser,
  logoutAllSessions,
  logoutUser,
  refreshAccessToken,
  registerUser,
} from '@/lib/identityApi'
import type { LocalUser, MessageResponse, TokenResponse } from '@/lib/types'

/** This app's own self-hosted accounts (`identity/`, `api/identity_auth.py`)
 * -- a sibling to `authStore.ts`'s OIDC flow, not a replacement. Mirrors
 * that store's shape (`status`/`user`/`error`, an `initialize()` that runs
 * once on app load) so `AuthGate.tsx` can treat both uniformly.
 *
 * - `"unconfigured"`: the backend's `GET /health` says `local_auth_enabled`
 *   is `false` -- render the app exactly as if this feature didn't exist.
 * - `"loading"`: checking availability, then attempting a silent refresh
 *   (the `HttpOnly` refresh-token cookie, if still valid) before ever
 *   falling back to `"unauthenticated"`.
 * - `"authenticated"` / `"unauthenticated"`: self-explanatory.
 * - `"unavailable"`: the silent refresh could not be *answered* -- a 429
 *   from the refresh route's own rate limiter, a 5xx, or no network at
 *   all. Deliberately **not** `"unauthenticated"`: see
 *   `isSessionEndedError` below for why conflating the two signed a user
 *   out of a session that was still perfectly valid.
 */
export type LocalAuthStatus =
  | 'unconfigured'
  | 'loading'
  | 'authenticated'
  | 'unauthenticated'
  | 'unavailable'

/** Which of the two forms `AuthGate.tsx` shows while `status ===
 * "unauthenticated"`. Lives here (not as local component state in
 * `AuthGate`) specifically so `logout`/`logoutAll` can reset it to
 * `"signIn"` -- otherwise a user who clicked through to the sign-up form
 * earlier in the session, then later signs out, would land back on the
 * sign-up form instead of a sign-in prompt. */
/** True only when a failed `/auth/refresh` actually means "this caller has
 * no session": the route answers **401** when the `HttpOnly` cookie is
 * missing, expired, already rotated or replayed, and **404** when local
 * auth is not enabled at all. Everything else -- `429` from the refresh
 * route's own per-IP rate limiter, any `5xx`, a `TypeError` from `fetch`
 * with no network -- means the question could not be answered, which is
 * not the same answer as "no".
 *
 * Measured 2026-10-29 (Phase 9, `UI-002`): `POST /auth/refresh` shares the
 * `login_rate_limit_per_minute` budget (default **10 per minute, keyed by
 * client IP**), and one full page load spends one refresh. Eleven direct
 * URL entries in roughly forty seconds returned `429`, and because both
 * refresh call sites treated *any* rejection as "not signed in", the live
 * session was discarded and the sign-in form appeared -- with no message,
 * and with the refresh cookie still valid. The same shape hit
 * `handleUnauthorized`, where it is worse: an access token expires every
 * `access_token_expire_minutes` (default 15), so an ordinary session
 * refreshes repeatedly, and a rate-limited moment would sign the user out
 * mid-question.
 *
 * Deliberately a narrow allowlist of "ended" statuses rather than a
 * blocklist of transient ones: a status this function has never heard of
 * must not end a session. */
export function isSessionEndedError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 404)
}

export type LocalAuthView = 'signIn' | 'register'

interface LocalAuthState {
  status: LocalAuthStatus
  user: LocalUser | null
  /** In-memory only, same posture `authStore.ts` already established for
   * OIDC access tokens -- never written to `localStorage`/`sessionStorage`. */
  accessToken: string | null
  error: string | null
  authView: LocalAuthView
  setAuthView: (view: LocalAuthView) => void
  initialize: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  /** Sign in, or sign up on first use, via a verified Google ID token
   * (`credential`, from `GoogleSignInButton`'s callback) -- one backend
   * flow decides which, this store's caller never has to say. Sets
   * exactly the same `TokenResponse`-derived state `login`/`register`
   * already do, so `AuthGate.tsx` treats a Google-authenticated session
   * identically to a local one. */
  loginWithGoogle: (credential: string) => Promise<void>
  /** Returns the raw response so the caller (the Register page) can tell
   * an immediate sign-in (`TokenResponse`) apart from "check your email"
   * (`MessageResponse`, when `REQUIRE_EMAIL_VERIFICATION` is on). */
  register: (
    email: string,
    password: string,
    displayName?: string,
  ) => Promise<TokenResponse | MessageResponse>
  logout: () => Promise<void>
  logoutAll: () => Promise<void>
  clearError: () => void
}

export const useLocalAuthStore = create<LocalAuthState>((set) => ({
  status: 'loading',
  user: null,
  accessToken: null,
  error: null,
  authView: 'signIn',
  setAuthView: (view) => set({ authView: view }),

  initialize: async () => {
    // Re-entrant: `AuthGate`'s "Try again" on the `"unavailable"` state
    // calls this again, and that retry must show the spinner rather than
    // leave the error panel up while the request is in flight.
    set({ status: 'loading' })
    let enabled = false
    try {
      const health = await getHealth()
      enabled = health.local_auth_enabled
    } catch {
      // Health check itself failed -- treat local auth as unavailable
      // rather than surfacing a login screen for a backend that may be
      // down entirely; AuthGate falls through to its existing OIDC/static
      // behavior in that case.
      set({ status: 'unconfigured' })
      return
    }
    if (!enabled) {
      set({ status: 'unconfigured' })
      return
    }

    try {
      const token = await refreshAccessToken()
      set({
        status: 'authenticated',
        user: token.user,
        accessToken: token.access_token,
        error: null,
      })
    } catch (error) {
      if (isSessionEndedError(error)) {
        // No live refresh-token cookie (never signed in, or it expired) --
        // not an error state, just "not signed in yet."
        set({ status: 'unauthenticated', user: null, accessToken: null })
      } else {
        // The refresh could not be answered. Showing the sign-in form here
        // would be a lie: the session may be entirely valid and the server
        // merely rate-limited or briefly down.
        set({ status: 'unavailable', user: null, accessToken: null })
      }
    }
  },

  login: async (email, password) => {
    const token = await loginUser(email, password)
    set({ status: 'authenticated', user: token.user, accessToken: token.access_token, error: null })
  },

  loginWithGoogle: async (credential) => {
    const token = await googleSignIn(credential)
    set({ status: 'authenticated', user: token.user, accessToken: token.access_token, error: null })
  },

  register: async (email, password, displayName) => {
    const result = await registerUser({ email, password, display_name: displayName })
    if (isTokenResponse(result)) {
      set({
        status: 'authenticated',
        user: result.user,
        accessToken: result.access_token,
        error: null,
      })
    }
    return result
  },

  logout: async () => {
    try {
      await logoutUser()
    } finally {
      set({ status: 'unauthenticated', user: null, accessToken: null, authView: 'signIn' })
    }
  },

  logoutAll: async () => {
    try {
      await logoutAllSessions()
    } finally {
      set({ status: 'unauthenticated', user: null, accessToken: null, authView: 'signIn' })
    }
  },

  clearError: () => set({ error: null }),
}))

/** The bearer token `authStore.ts`'s unified `getBearerToken()` checks
 * first, ahead of OIDC and the static fallback -- a signed-in local
 * account is this app's own, most-specific credential. `undefined`
 * whenever local auth is unconfigured or not currently signed in. */
export function getLocalBearerToken(): string | undefined {
  return useLocalAuthStore.getState().accessToken ?? undefined
}

// Re-exported so callers that only need the current user (e.g. a nav
// avatar) don't need to know local auth's refresh mechanics.
export async function refreshCurrentLocalUser(): Promise<LocalUser> {
  const user = await getCurrentUser()
  useLocalAuthStore.setState({ user })
  return user
}

/** Registered with `lib/api.ts` via `setUnauthorizedHandler` (see
 * `AuthGate.tsx`'s mount effect) -- `request()` calls this on any 401
 * (except `/auth/*` itself) and retries the original call once if a fresh
 * token comes back. A real gap found via live use, not a hypothetical:
 * `access_token_expire_minutes` defaults to 15 minutes
 * (`config/settings.py`), and nothing previously noticed when this
 * in-memory `accessToken` went stale mid-session -- every subsequent call
 * failed with a bare "Missing or invalid Authorization header." until the
 * user manually reloaded the page, which was the only thing that re-ran
 * `initialize()`'s own refresh-via-cookie check.
 *
 * Returns `null` (never throws) whenever there's truly no live session to
 * recover -- local auth unconfigured, or the `HttpOnly` refresh-token
 * cookie itself is gone/expired -- and, in that case, flips `status` to
 * `"unauthenticated"` so `AuthGate` shows the sign-in screen instead of
 * leaving the user staring at a wall of failed requests. */
export async function handleUnauthorized(): Promise<string | null> {
  if (useLocalAuthStore.getState().status === 'unconfigured') return null
  try {
    const token = await refreshAccessToken()
    useLocalAuthStore.setState({
      status: 'authenticated',
      user: token.user,
      accessToken: token.access_token,
      error: null,
    })
    return token.access_token
  } catch (error) {
    if (isSessionEndedError(error)) {
      useLocalAuthStore.setState({ status: 'unauthenticated', user: null, accessToken: null })
    }
    // Otherwise the session is left exactly as it was. The one request that
    // provoked this still fails with its own error, and the next 401 tries
    // the refresh again -- which is what makes a rate-limit window recover
    // by itself instead of ending the session.
    return null
  }
}
