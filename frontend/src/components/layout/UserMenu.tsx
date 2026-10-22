import { LogOut, Settings as SettingsIcon, UserCircle } from 'lucide-react'
import type { Ref } from 'react'
import { useTranslation } from 'react-i18next'
import { ThemeToggle } from '@/components/settings/ThemeToggle'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useAvatarObjectUrl } from '@/hooks/useAvatarObjectUrl'
import { isOidcConfigured } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/authStore'
import { useLocalAuthStore } from '@/store/localAuthStore'

function initialsFor(name: string): string {
  const parts = name.trim().split(/\s+/)
  const first = parts[0]?.[0] ?? ''
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : ''
  return (first + last).toUpperCase() || '?'
}

/** The single, consolidated account-level menu: display name/email,
 * Settings, Theme, and Sign out. This is the ONE place any of those four
 * actions live in the app -- previously Settings and Sign out each had two
 * separate, independently-implemented copies (AppShell's header and
 * Sidebar's footer), including two separate mounted `SettingsDialog`
 * instances with two independent open/closed states. See
 * docs/navigation-and-actions.md for the full ownership table and
 * docs/ui-production-audit.md for what this replaced.
 *
 * Theme is the one deliberate exception to "one location per action" --
 * this menu's own `ThemeToggle` row is a quick-access shortcut; the same
 * control (identical component, identical store) also appears inside the
 * full Settings dialog's Appearance section. That's a documented,
 * intentional shortcut (same component/state, two access points), not a
 * duplicate implementation -- see docs/navigation-and-actions.md.
 *
 * `triggerRef` (optional) exposes the avatar button so `SettingsDialog`
 * can explicitly restore focus to it on close -- Radix Dialog's own
 * automatic "return focus to whatever was active when I mounted" doesn't
 * work reliably here, since Settings is opened from a `DropdownMenuItem`
 * that itself unmounts (closing this menu) before the Dialog's `open`
 * prop flips true, so nothing meaningful is focused at that instant. See
 * `docs/functional-ui-audit.md`'s Settings-focus finding. */
export function UserMenu({
  onOpenSettings,
  triggerRef,
}: {
  onOpenSettings: () => void
  triggerRef?: Ref<HTMLButtonElement>
}) {
  const { t } = useTranslation()
  const authStatus = useAuthStore((state) => state.status)
  const signOut = useAuthStore((state) => state.signOut)
  const localAuthStatus = useLocalAuthStore((state) => state.status)
  const localSignOut = useLocalAuthStore((state) => state.logout)
  const localUser = useLocalAuthStore((state) => state.user)

  const isLocalAuthenticated = localAuthStatus === 'authenticated'
  const canSignOut = isLocalAuthenticated || (isOidcConfigured && authStatus === 'authenticated')
  const handleSignOut = () => (isLocalAuthenticated ? void localSignOut() : void signOut())

  const displayName = localUser?.display_name ?? null
  // Fetched rather than used as a bare `<img src>`: `GET /auth/me/avatar`
  // needs the `Authorization` header a browser image request cannot send
  // (see `useAvatarObjectUrl`). Shared with the chat avatar and the
  // settings section -- the hook fetches each version once for all three,
  // so putting the picture here adds no extra request.
  const avatarUrl = useAvatarObjectUrl(localUser?.avatar_url)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          ref={triggerRef}
          type="button"
          aria-label={t('userMenu.trigger')}
          title={displayName ?? t('userMenu.trigger')}
          // The white ring is applied only when a *picture* is shown: it
          // exists to separate a photograph from the surface behind it,
          // and around initials or the generic icon it would be a stray
          // outline on a control that already reads clearly. `ring-inset`
          // is deliberately NOT used -- the ring belongs outside the
          // image, adjacent to it, not overlapping the face.
          className={cn(
            'flex h-8 w-8 items-center justify-center overflow-hidden rounded-full bg-[var(--accent-soft)] text-xs font-semibold text-[var(--accent)] transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]',
            avatarUrl && 'ring-2 ring-[var(--avatar-ring)]',
          )}
        >
          {/* Three states, most specific first: the user's own picture,
              then their initials, then the generic icon. `alt=""` because
              the button already carries `aria-label`/`title` -- labelling
              the image too would make a screen reader announce the account
              twice for one control.

              `overflow-hidden` on the button is what clips the square to
              the circle; without it a 256x256 image would spill out of a
              32px round button. */}
          {avatarUrl ? (
            <img src={avatarUrl} alt="" className="h-full w-full object-cover" />
          ) : displayName ? (
            initialsFor(displayName)
          ) : (
            <UserCircle className="h-4.5 w-4.5" />
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-64">
        {(displayName || localUser?.email) && (
          <>
            <DropdownMenuLabel className="flex flex-col gap-0.5 px-2 py-1.5">
              {displayName && (
                <span className="truncate text-sm font-medium text-[var(--foreground)]">{displayName}</span>
              )}
              {localUser?.email && <span className="truncate text-xs">{localUser.email}</span>}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
          </>
        )}

        <DropdownMenuItem onSelect={onOpenSettings}>
          <SettingsIcon className="h-4 w-4" />
          {t('userMenu.settings')}
        </DropdownMenuItem>

        {/* Not a DropdownMenuItem -- plain content, so clicking a theme
            option doesn't close the menu the way selecting an item would,
            letting the user preview more than one theme in one open. */}
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm">{t('userMenu.theme')}</span>
          <ThemeToggle />
        </div>

        {canSignOut && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={handleSignOut} className="text-[var(--danger)]">
              <LogOut className="h-4 w-4" />
              {t('auth.signOut')}
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
