import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useNavigation } from '@/hooks/queries'

/** Shown for an in-app address that matches no route.
 *
 * **Why this exists.** `App.tsx`'s `<Routes>` had no `path="*"` entry and
 * no component like this one, and `api/main.py` serves the dashboard from
 * a catch-all `GET /{full_path:path}` -- so any mistyped or stale URL
 * returned 200 with `index.html`, the SPA booted, React Router matched
 * nothing, and `<Routes>` rendered `null`. Measured in Chrome 2026-10-29
 * (Phase 9, `UI-003`): `/this-route-does-not-exist` left `#root` with one
 * child and **zero characters** of body text -- a blank white page, for
 * every one of the seven roles tested, with no heading, no navigation and
 * no way out but the browser's Back button.
 *
 * It deliberately mirrors `RequireNavItem`'s `ForbiddenPage`: the same
 * layout, the same focus-the-heading behaviour so the change is announced,
 * and the same "link to the first screen this caller can actually open"
 * rule, which is what keeps the escape route from dead-ending on a screen
 * their role does not include.
 *
 * It renders *inside* `AppShell`, so the header and navigation stay
 * available -- a wrong address should not strand the user. */
export function NotFoundPage() {
  const { t } = useTranslation()
  const navigation = useNavigation()
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  const home = navigation.data?.items[0] ?? null

  return (
    <section aria-labelledby="not-found-title" className="mx-auto flex max-w-xl flex-col gap-3 p-6">
      <h1
        id="not-found-title"
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {t('routeGate.notFoundTitle')}
      </h1>
      <p className="text-sm text-[var(--muted-foreground)]">{t('routeGate.notFoundBody')}</p>
      {home && (
        <Link
          to={home.path}
          className="w-fit text-sm font-medium text-[var(--accent)] underline underline-offset-2"
        >
          {t('routeGate.goToPermitted')}
        </Link>
      )}
    </section>
  )
}
