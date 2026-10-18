import {
  BookOpen,
  Building2,
  ClipboardCheck,
  ImageIcon,
  Lightbulb,
  Gauge,
  MessageSquare,
  Plug,
  ShieldAlert,
  ShieldCheck,
  Workflow,
} from 'lucide-react'
import { useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Outlet, useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useGlobalShortcuts } from '@/hooks/useGlobalShortcuts'
import { useNavigation } from '@/hooks/queries'
import { focusChatComposer } from '@/lib/shortcuts'
import { useChatStore } from '@/store/chatStore'
import { useSettingsStore } from '@/store/settingsStore'
import { MobileNav } from './MobileNav'
import { ScreenMenu, type ScreenMenuGroup } from './ScreenMenu'
import { SettingsDialog } from './SettingsDialog'
import { ShortcutHelpDialog } from './ShortcutHelpDialog'
import { Sidebar } from './Sidebar'
import { SidebarToggle } from './SidebarToggle'
import { UserMenu } from './UserMenu'

const SIDEBAR_ID = 'app-sidebar'
/** Target of the skip link, and the element focus is moved to. */
const MAIN_CONTENT_ID = 'main-content'

/** How each screen the server may list is drawn: an icon and a translated label.
 * Presentation only. Whether a screen appears at all is the server's decision
 * (`GET /navigation`). A screen with no entry here is simply not drawn. */
const SCREEN_PRESENTATION: Record<string, { icon: typeof MessageSquare; labelKey: string }> = {
  chat: { icon: MessageSquare, labelKey: 'nav.chat' },
  decision_center: { icon: Gauge, labelKey: 'nav.decisionCenter' },
  knowledge_sources: { icon: BookOpen, labelKey: 'nav.knowledgeSources' },
  media_search: { icon: ImageIcon, labelKey: 'nav.mediaSearch' },
  data_lineage: { icon: Workflow, labelKey: 'nav.dataLineage' },
  recommendations: { icon: Lightbulb, labelKey: 'nav.recommendations' },
  db_onboarding: { icon: Plug, labelKey: 'nav.dbOnboarding' },
  semantic_review: { icon: ShieldCheck, labelKey: 'nav.semanticReview' },
  ai_governance: { icon: ClipboardCheck, labelKey: 'nav.aiGovernance' },
  tenant_admin: { icon: Building2, labelKey: 'nav.tenantAdmin' },
  platform_admin: { icon: ShieldAlert, labelKey: 'nav.platformAdmin' },
}

/** Screens the header never shows, neither as a tab nor in the AI Workspace
 * menu. Chat is still the default route ("/"); it is reached from the
 * sidebar's New chat action and history, not from the header. */
const HIDDEN_SCREEN_IDS = new Set(['chat'])

/** Menu sections, in display order, keyed by the server's `group` field. */
const MENU_GROUPS: { key: 'workspace' | 'review' | 'administration'; labelKey: string }[] = [
  { key: 'workspace', labelKey: 'nav.groupWorkspace' },
  { key: 'review', labelKey: 'nav.groupReview' },
  { key: 'administration', labelKey: 'nav.groupAdministration' },
]

/** Full-viewport application shell: a persistent left conversation-history
 * sidebar (>=lg viewports, collapsible to an icon rail) + main workspace.
 * Below `lg`, the sidebar is replaced by a hamburger-triggered slide-in
 * drawer (MobileNav) rendering the exact same Sidebar content, so there is
 * one history implementation, not two.
 *
 * Action ownership (see docs/navigation-and-actions.md for the full table
 * and docs/ui-production-audit.md for what this replaced): this header
 * owns exactly one account-level control, `UserMenu` -- Settings, Theme,
 * and Sign out all live inside it now, not scattered across the header and
 * the sidebar footer as two independent copies each. `SettingsDialog`'s
 * open/closed state is owned here, as the single instance in the whole
 * app -- `UserMenu`'s "Settings" item just calls `setIsSettingsOpen(true)`,
 * it does not own or mount its own dialog. */
export function AppShell({ children }: { children?: ReactNode } = {}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const startNewChat = useChatStore((state) => state.startNewChat)

  /** Moves focus to `<main>` without letting the `#main-content` fragment
   * reach the router.
   *
   * The `href` is kept so the control is a real link -- it is announced as
   * one, and it still works if this handler never runs. But this is a
   * client-routed SPA: letting the default fire would append `#main-content`
   * to the current URL, which then rides along in anything the user copies
   * or shares, and react-router would treat the location as changed. The
   * explicit `focus()` is also what makes the jump actually work for a
   * keyboard user -- see `<main tabIndex={-1}>` below. */
  const handleSkipToContent = (event: ReactMouseEvent<HTMLAnchorElement>) => {
    const main = document.getElementById(MAIN_CONTENT_ID)
    if (!main) return
    event.preventDefault()
    main.focus()
    main.scrollIntoView({ block: 'start' })
  }

  // The product name doubles as "home": clicking it starts a fresh
  // conversation and returns to the chat route. Exactly what the
  // sidebar's "New chat" button does (`Sidebar.tsx::handleNewChat`) --
  // the same two calls in the same order, so the two entry points cannot
  // drift into meaning different things.
  const handleHome = () => {
    startNewChat()
    navigate('/')
  }
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [isShortcutHelpOpen, setIsShortcutHelpOpen] = useState(false)
  const toggleSidebarCollapsed = useSettingsStore((state) => state.toggleSidebarCollapsed)

  // The one global keyboard layer in the app, mounted here because this
  // is the one component that is always present and already owns the
  // actions these bind to. Component-level handlers (Enter in the
  // composer, arrows in the question navigator) are untouched: the hook
  // refuses to fire while a text field has focus or a modal is open.
  useGlobalShortcuts({
    newChat: handleHome,
    toggleSidebar: toggleSidebarCollapsed,
    openSettings: () => setIsSettingsOpen(true),
    focusComposer: focusChatComposer,
    showHelp: () => setIsShortcutHelpOpen(true),
  })
  // Role-based navigation (Prompt 32): the screens this caller may open come
  // from the server (`GET /navigation`, `security/navigation.py`), never from a
  // role name read in the browser. While that loads, no review or admin tab is
  // shown rather than guessed at.
  const navigation = useNavigation()
  const screens = navigation.data?.items ?? []
  // Only screens the server listed AND this file knows how to draw. Anything
  // the server doesn't list is never present here, so it can't appear in the
  // header or the menu.
  const drawableScreens = screens.flatMap((screen) => {
    const presentation = SCREEN_PRESENTATION[screen.id]
    if (!presentation || HIDDEN_SCREEN_IDS.has(screen.id)) return []
    return [{ id: screen.id, path: screen.path, group: screen.group, icon: presentation.icon, label: t(presentation.labelKey) }]
  })
  const menuGroups: ScreenMenuGroup[] = MENU_GROUPS.map(({ key, labelKey }) => ({
    key,
    labelKey,
    items: drawableScreens.filter((screen) => !HIDDEN_SCREEN_IDS.has(screen.id) && screen.group === key),
  })).filter((group) => group.items.length > 0)
  const sidebarCollapsed = useSettingsStore((state) => state.sidebarCollapsed)
  // Passed to both UserMenu (attaches it to the avatar button) and
  // SettingsDialog (restores focus there on close) -- see
  // SettingsDialog's own docstring for why this is necessary rather than
  // relying on Radix Dialog's automatic focus restoration.
  const userMenuTriggerRef = useRef<HTMLButtonElement>(null)

  return (
    <div className="h-screen w-screen overflow-hidden bg-[var(--background)] text-[var(--foreground)]">
      {/* `inert` while Settings is open: removes the entire app shell (sidebar,
          header, chat) from the accessibility tree and from tab/pointer
          interaction, on top of (not instead of) the opaque backdrop above --
          the backdrop handles *visual* occlusion, this handles keyboard/AT
          reachability. SettingsDialog itself renders through a Radix Portal
          (see ui/dialog.tsx), so it's not a descendant of this div and is
          never affected by its own inert flag. */}
      <div className="flex h-full" inert={isSettingsOpen || isShortcutHelpOpen || undefined}>
        {/* Bypass block (WCAG 2.4.1, Level A). Every page repeats the same
            sidebar and primary nav, so without this a keyboard or
            switch-device user tabs through all of it before reaching the
            answer they came for -- on every single navigation.

            It must be *seeable* once tabbed to, not merely present, or a
            sighted keyboard user cannot tell what focus landed on. It is
            the first child of the shell so it is the first thing Tab
            reaches.

            Parked off-screen with a transform rather than `sr-only` +
            `focus-visible:not-sr-only`: that pair looks tidier but is
            broken here. `not-sr-only` resets `padding` to 0, and Tailwind
            emits variant utilities after plain ones, so on focus it wins
            over `px-4 py-2` and the link renders as a cramped, unreadable
            pill. A transform touches only `translate`, which nothing else
            here sets. (Verified against the compiled CSS, not assumed.)

            It sits inside the `inert` wrapper deliberately: while Settings
            is open the whole shell is inert, and a skip link that still
            took focus would jump the user out of the open dialog. */}
        <a
          href={`#${MAIN_CONTENT_ID}`}
          onClick={handleSkipToContent}
          className="fixed left-4 top-4 z-50 -translate-y-24 rounded-md bg-[var(--accent)] px-4 py-2 text-sm font-medium text-[var(--accent-foreground)] shadow-lg transition-transform duration-[var(--duration-base)] ease-out focus-visible:translate-y-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--background)] motion-reduce:transition-none"
        >
          {t('a11y.skipToContent')}
        </a>
        <aside
          id={SIDEBAR_ID}
          className={cn(
            'hidden shrink-0 border-r border-[var(--border)] bg-[var(--sidebar)] text-[var(--sidebar-foreground)] transition-[width] duration-[var(--duration-base)] ease-out lg:flex lg:flex-col',
            sidebarCollapsed ? 'lg:w-14' : 'lg:w-72',
          )}
        >
          <Sidebar collapsed={sidebarCollapsed} />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center gap-1.5 border-b border-[var(--border)] bg-[var(--header)] px-2 sm:gap-2 sm:px-4">
            <MobileNav />
            <SidebarToggle controls={SIDEBAR_ID} className="hidden lg:inline-flex" />
            {/* min-w-0 so the long product title can truncate instead of
                pushing the nav and avatar off a phone-width header. */}
            <div className="flex min-w-0 items-center gap-2">
              {/* A real <button>, not a <div onClick> or an <a href="/">:
                  it must be reachable by Tab and activatable by Enter and
                  Space, and a full-page navigation would discard the
                  client-side chat state this is meant to reset. */}
              <button
                type="button"
                onClick={handleHome}
                aria-label={t('app.homeLabel')}
                title={t('app.homeLabel')}
                className="flex min-w-0 items-center gap-2 rounded-md px-1 py-0.5 transition-colors hover:bg-[var(--muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-md bg-white">
                  <img src="/favicon.png" alt="" className="h-full w-full object-contain" />
                </span>
                {/* Truncated rather than hidden on a phone: the product
                    name is the only branding in the header, and the full
                    string stays available as the button's accessible
                    name (aria-label) and tooltip. */}
                <span className="truncate text-sm font-semibold">{t('app.title')}</span>
              </button>
              <span className="hidden rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)] sm:inline">
                {t('header.envIndicator')}
              </span>
            </div>

              <nav aria-label="Primary navigation" className="flex min-w-0 shrink-0 flex-1 items-center gap-1">
                {menuGroups.length > 0 && <ScreenMenu groups={menuGroups} />}
              </nav>

            <div className="ml-auto flex shrink-0 items-center gap-1.5">
              <UserMenu onOpenSettings={() => setIsSettingsOpen(true)} triggerRef={userMenuTriggerRef} />
            </div>
          </header>

          {/* min-h-0 is load-bearing here: without it, a flex child defaults
              to min-height:auto and refuses to shrink below its content
              size, which breaks the single-inner-scrollbar layout below --
              the page would grow the whole window instead of scrolling
              internally. Each routed page owns exactly one scroll region at
              full width (edge-to-edge, scrollbar flush against the browser
              edge) with its content centered inside via its own max-w
              wrapper. */}
          <main
            id={MAIN_CONTENT_ID}
            /* `tabIndex={-1}` so the skip link's fragment jump actually
               moves focus here: following a `#id` link scrolls to the
               target but only focuses it if it is focusable, and without
               that the next Tab would resume from the skip link -- back at
               the top of the nav, which is the very thing being skipped. */
            tabIndex={-1}
            className="relative min-h-0 min-w-0 flex-1 overflow-hidden outline-none"
          >
            {children ?? <Outlet />}
          </main>
        </div>
      </div>

      <SettingsDialog
        open={isSettingsOpen}
        onOpenChange={setIsSettingsOpen}
        triggerRef={userMenuTriggerRef}
      />
      <ShortcutHelpDialog open={isShortcutHelpOpen} onOpenChange={setIsShortcutHelpOpen} />
    </div>
  )
}
