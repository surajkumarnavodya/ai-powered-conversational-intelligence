import { Loader2, Trash2, Upload, User } from 'lucide-react'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api'
import { removeProfilePicture, uploadProfilePicture } from '@/lib/identityApi'
import { useLocalAuthStore } from '@/store/localAuthStore'

/** The accepted extensions, as the file picker's `accept` attribute.
 *
 * A convenience, never a control: the picker is trivially bypassed, so the
 * real allowlist is `identity/profile_picture.py::ALLOWED_EXTENSIONS`,
 * which also cross-checks the magic bytes. Keeping this list in sync is a
 * UX nicety -- a mismatch costs a clear server-side refusal, not a hole. */
const ACCEPT = 'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp'

/** Lets the signed-in user set, replace or remove their own profile
 * picture. Rendered inside `SettingsDialog` (one instance, one location --
 * see docs/navigation-and-actions.md).
 *
 * Nothing is cropped or resized here. The server stores one normalized
 * square (`Settings.profile_picture_size_px`) and this renders it in a
 * circle, so the browser and the backend cannot disagree about what the
 * avatar actually is -- a client-side crop would be a second, drifting
 * answer to the same question.
 *
 * Hidden entirely when there is no signed-in local account: an avatar
 * belongs to an `identity.users` row, and OIDC/unauthenticated callers
 * have none (the same limitation chat history and artifacts already carry
 * -- see CLAUDE.md 13). */
export function ProfilePictureSection() {
  const { t } = useTranslation()
  const user = useLocalAuthStore((state) => state.user)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The server's disclosure that it stored the picture *without* an
  // automated suitability check. Shown as information, not as a problem:
  // the upload succeeded (see identity/profile_picture.py's fail-open note).
  const [notice, setNotice] = useState<string | null>(null)

  if (!user) return null

  const avatarUrl = user.avatar_url ?? null

  async function handleFile(file: File) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await uploadProfilePicture(file)
      useLocalAuthStore.setState({ user: result.user })
      if (!result.vision_checked) {
        setNotice(
          t('profilePicture.notChecked', { reason: result.vision_skipped_reason ?? '' }),
        )
      }
    } catch (err) {
      // An ApiError's message is the server's own short reason, which is
      // exactly the "show a short reason and ask for another" text. Any
      // other failure gets the generic string rather than a raw stack.
      setError(err instanceof ApiError ? err.message : t('profilePicture.uploadFailed'))
    } finally {
      setBusy(false)
      // Always clear the input, including after a rejection: without this,
      // picking the *same* file again fires no change event and the retry
      // silently does nothing.
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  async function handleRemove() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const updated = await removeProfilePicture()
      useLocalAuthStore.setState({ user: updated })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('profilePicture.removeFailed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-[var(--muted-foreground)]">
        {t('profilePicture.title')}
      </p>
      <div className="flex items-center gap-3">
        <span className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt={t('profilePicture.alt')}
              className="h-full w-full object-cover"
            />
          ) : (
            <User className="h-6 w-6" aria-hidden="true" />
          )}
        </span>
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Upload className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {avatarUrl ? t('profilePicture.replace') : t('profilePicture.upload')}
            </Button>
            {avatarUrl && (
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => void handleRemove()}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                {t('profilePicture.remove')}
              </Button>
            )}
          </div>
          <p className="text-xs text-[var(--muted-foreground)]">{t('profilePicture.hint')}</p>
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        // `aria-hidden` is wrong here -- the visible Button is the control
        // and already labels the action; this input is just its mechanism.
        tabIndex={-1}
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void handleFile(file)
        }}
      />

      {busy && (
        <p className="text-xs text-[var(--muted-foreground)]" role="status">
          {t('profilePicture.checking')}
        </p>
      )}
      {error && (
        <p className="text-xs text-[var(--danger)]" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="text-xs text-[var(--warning)]">{notice}</p>}
    </div>
  )
}
