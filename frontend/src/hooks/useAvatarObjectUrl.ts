import { useEffect, useState } from 'react'
import { fetchAvatarBlobUrl } from '@/lib/api'

/** One in-flight//completed fetch per avatar *version*, shared by every
 * consumer.
 *
 * The cache is the point of this module, not an optimisation detail.
 * `ChatMessage` renders once per message, so a plain per-component
 * `useEffect` fetch would issue one authenticated request per visible turn
 * for a single ~15 KB image -- forty messages, forty requests. Keying on
 * the URL (which carries the `v=` cache-buster from
 * `users.avatar_updated_at`) means a replaced picture is a different key
 * and fetched once more, while the old entry is revoked below.
 */
const cache = new Map<string, Promise<string>>()

function load(avatarUrl: string): Promise<string> {
  const existing = cache.get(avatarUrl)
  if (existing) return existing
  const pending = fetchAvatarBlobUrl(avatarUrl).catch((error: unknown) => {
    // A failed fetch must not be cached as a permanent failure -- the token
    // may simply have been mid-refresh. Dropping the entry lets the next
    // consumer (or the next render after a successful refresh) retry.
    cache.delete(avatarUrl)
    throw error
  })
  cache.set(avatarUrl, pending)
  return pending
}

/** Revokes every cached object URL except `keep`.
 *
 * Called when the avatar version changes, so replacing a picture does not
 * leak the previous blob for the life of the tab. Browsers hold an object
 * URL until it is revoked or the document unloads, and this app is a
 * long-lived SPA, so "the tab will close eventually" is not a lifecycle. */
function revokeOthers(keep: string): void {
  for (const [url, pending] of cache) {
    if (url === keep) continue
    cache.delete(url)
    void pending.then(URL.revokeObjectURL).catch(() => {
      // Already rejected and removed above; nothing to revoke.
    })
  }
}

/** The signed-in user's profile picture as a blob URL, or null.
 *
 * Null covers every case where there is nothing to show -- no picture set,
 * still loading, or the fetch failed -- because each one renders the same
 * way: the caller falls back to its default icon. Distinguishing them in
 * the avatar would mean showing an error where a person expects a face.
 *
 * Deliberately not react-query, despite this app using it elsewhere: the
 * value here is a resource with a manual revoke step, and the cache above
 * exists to own exactly that. Putting it behind a query cache would leave
 * two owners for one blob's lifetime. */
export function useAvatarObjectUrl(avatarUrl: string | null | undefined): string | null {
  const [objectUrl, setObjectUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!avatarUrl) {
      setObjectUrl(null)
      revokeOthers('')
      return
    }

    let cancelled = false
    load(avatarUrl)
      .then((url) => {
        if (cancelled) return
        setObjectUrl(url)
        revokeOthers(avatarUrl)
      })
      .catch(() => {
        if (!cancelled) setObjectUrl(null)
      })

    return () => {
      // Only this subscription is cancelled. The object URL itself is
      // shared and stays cached for the other consumers still rendering
      // it -- revoking on unmount would blank the avatar in every other
      // mounted ChatMessage.
      cancelled = true
    }
  }, [avatarUrl])

  return objectUrl
}
