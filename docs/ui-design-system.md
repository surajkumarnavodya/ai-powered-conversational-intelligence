# Frontend Design System

Extends the existing Tailwind v4 + CSS-custom-property token system
(`frontend/src/index.css`) — no new styling framework was introduced, per
the "continue using the repository's existing approach" constraint. This
document covers what changed in this pass; see
`docs/frontend-ui-audit.md` §7 for the full before/after token inventory.

## Theming

Unchanged mechanism, still the source of truth: `data-theme` attribute on
`<html>` (`src/lib/theme.ts`), with Tailwind's `dark:` variant remapped to
match it (`@custom-variant dark (&:where([data-theme='dark'] *))`) so both
mechanisms point at one switch. Accent color and the two fonts are
independently user-configurable CSS variables on top of light/dark.

## Typography

Two independent preferences, both set in **Settings → Appearance** and both
applied immediately:

| Control | Token | Governs | Options | Default |
|---|---|---|---|---|
| Application font | `--font-sans` | body, headings, forms, buttons, nav, dialogs, tables, chat, settings, dashboards | Inter, Geist Sans, IBM Plex Sans, Plus Jakarta Sans, System default, Roboto, Merriweather (serif) | **Inter** |
| Code & SQL font | `--font-mono` | SQL editor, generated SQL, code blocks, JSON, logs, ids, numeric table cells | JetBrains Mono, Geist Mono, System monospace | **JetBrains Mono** |

`src/lib/fonts.ts` is the single owner of both lists — the ids, the
`font-family` stacks and the lazy loaders. `src/lib/theme.ts` keeps theme
mode and accent only; it no longer defines fonts. Because `index.css`'s
`@theme inline` block maps Tailwind's `font-sans`/`font-mono` utilities onto
these two custom properties, setting them on `<html>` re-fonts every
surface at once — no component names a typeface itself.

**Why these were split.** There used to be one combined list, so choosing
"JetBrains Mono" set the *UI* face to a monospace font and left SQL
unchanged — the opposite of what the label implied. A monospace option is
now unreachable from the application-font control, pinned by a test.

**Self-hosted, lazily.** Each face ships as a variable WOFF2 from
`@fontsource-variable/*` (all OFL-1.1; notice in
`frontend/public/font-licenses.txt`) rather than from a font CDN, so the
dashboard makes no third-party request — the same local-first posture as
the LLM, database and vector store. `fonts.ts` loads a face through a
dynamic `import()`, so a visitor downloads only the face they selected
(~87 KB for the two defaults, latin subset) rather than all eight. Nothing
is fetched at startup: first paint uses the plain system stacks in
`index.css`, every option ends in a system fallback, and every bundled face
is `font-display: swap`, so a font that is blocked, offline or missing
degrades to the system face instead of blank text.

**Persistence.** `settingsStore`'s `font` and `monoFont`, in the
`tsql-settings` localStorage key alongside theme/accent/language — a
per-device, non-sensitive preference. There is no server-side preference
API and this feature did not add one, so preferences are per-browser, not
per-account. Stored values are **ids, never CSS**: a persisted id selects a
constant stack from the allowlist, and anything unrecognized (hand-edited,
corrupt, or an option an older build offered) falls back to the default on
both read and write. **Reset fonts to defaults** restores both and leaves
theme, accent and language untouched.

## Tokens added this pass

All additive — nothing existing was renamed or removed, so no consumer
broke.

| Token | Purpose |
|---|---|
| `--focus-ring` | Distinct semantic name for the keyboard-focus ring color (currently mirrors `--accent`, but themeable independently later). Adopted in `Button`, `Dialog`'s close button, `ConversationSearch`, `ConversationListItem`'s rename input. |
| `--user-message-bg` / `--user-message-foreground` | Named surface for the user's side of a conversation turn. |
| `--assistant-message-bg` / `--assistant-message-foreground` | Named surface for the assistant's side. |
| `--code-block-bg` / `--code-block-foreground` | Named surface for code/SQL content, distinct from generic `--muted`/`--card`. |
| `--sql-result-bg` | Named surface for the SQL result area. |
| `--z-dropdown` / `--z-drawer` / `--z-modal` / `--z-toast` | Named stacking tiers (30/40/50/60) so a new overlay picks the right layer without guessing against existing ad hoc z-index numbers. |
| `--duration-fast` / `--duration-base` / `--duration-slow` | Named transition speeds (100/150/250ms) instead of a bare value repeated per component. |

Added by the later responsive pass (same additive rule):

| Token | Purpose |
|---|---|
| `--chat-column` | The conversation's reading measure (48rem), exposed as the `max-w-chat` utility via Tailwind v4's `--container-chat`. The turns, the composer and the shared read-only view all use it, so they cannot drift apart. See [`docs/ui-responsive-layout.md`](ui-responsive-layout.md), which is authoritative for the measure and the breakpoints. |
| `--selection-bg` / `--selection-foreground` | The `::selection` pair. Both halves are set deliberately: a background alone left light-on-accent text (a question bubble) unreadable the moment it was selected. |

**Deliberately not added** (see the audit's correction): a separate
spacing scale and a separate type scale. Tailwind v4's own built-in
spacing/`text-*` utilities already provide a consistent scale — adding a
parallel CSS-variable version would be pure duplication, not a real gap.
Reduced-motion support also already existed
(`index.css`'s `@media (prefers-reduced-motion: reduce)` block, global) —
an earlier draft of the audit incorrectly flagged it as missing; corrected
before this pass began.

## New shared components (`components/ui/`)

- **`dialog.tsx`** — a thin wrapper around `@radix-ui/react-dialog`
  (already an existing dependency, previously unused as a standalone
  component). Real focus trapping, Escape-to-close, `aria-modal`, and
  scroll-lock, all from Radix — not hand-rolled.
- **`drawer.tsx`** — the same Radix Dialog primitive, positioned as a
  sliding side panel instead of a centered modal. Used by `MobileNav`.
- **`toast.tsx`** — `ToastProvider` + `useToast()`. Replaces the pattern
  of each component rolling its own inline colored error text.
  Accessible by construction: an error toast is `role="alert"`
  (interrupts a screen reader), a success/info toast is `role="status"`
  (announced without interrupting) — both individually dismissible, never
  relying on the auto-dismiss timer alone.
- **`copy-button.tsx`** — shared icon-button copy affordance (used by
  `SqlEditor`), separate from the existing markdown-aware
  `CopyAnswerButton` (which does a richer HTML+plain-text dual clipboard
  write appropriate for prose, not code).

**Note on adoption scope**: `Toast` is wired into the app root
(`App.tsx`) and available everywhere via `useToast()`, but existing
inline-error components (e.g. `TurnCard.tsx`'s per-turn status text) were
**not** retrofitted to use it in this pass — that would be a much larger,
separately-riskier refactor touching many already-working components.
New code paths added in this pass do use it where relevant.

## Layout change: Sidebar/Settings split

The single, combined right-side history+settings drawer
(`HistoryDrawer.tsx`, now removed) is replaced with:

- **`layout/Sidebar.tsx`** — a persistent left rail on `lg:`+ viewports,
  collapsible to an icon rail (see "Sidebar collapse" below) — New Chat,
  search, and conversation list only. Owns exactly one concern (history
  navigation); it has no footer of its own.
- **`layout/MobileNav.tsx`** — a hamburger trigger + slide-in drawer below
  `lg:`, rendering the *same* `Sidebar` component (not a second
  implementation, and always in its fully-expanded form regardless of the
  desktop collapse preference) via a `variant`-free `onNavigate` callback
  that closes the drawer after a selection.
- **`layout/UserMenu.tsx`** — the single, consolidated account-level menu
  (avatar/initials trigger → display name, email, Settings, Theme,
  Sign out), rendered once, in the header. Replaces what was previously
  two independent copies each of Settings and Sign out (one in the header,
  one in the Sidebar's own footer, each with its own state) — see
  `docs/ui-production-audit.md` for exactly what that duplication looked
  like and `docs/navigation-and-actions.md` for the resulting one-owner
  rule.
- **`layout/SidebarToggle.tsx`** — the sidebar collapse/expand control,
  rendered exactly once (header), not duplicated into the sidebar itself.
- **`layout/SettingsDialog.tsx`** — the same, unmodified
  `HistorySettingsSection` content, in one dialog instance owned by
  `AppShell.tsx`, opened only via `UserMenu`'s "Settings" item.

History logic (search debounce, rename/delete, hydrate-from-server) was
extracted out of the old drawer into `hooks/useChatSearch.ts` and three
presentational components (`ConversationSearch`, `ConversationList`,
`ConversationListItem`, `ConversationSearchResults`) — each independently
unit-tested (see `docs/frontend-ui-audit.md`'s proposed architecture,
now implemented).

## Sidebar collapse

`settingsStore.sidebarCollapsed` (persisted, `tsql-settings` localStorage
key alongside theme/accent/font — a non-sensitive UI preference, never
chat content) is the single source of truth for whether the desktop
(`>=lg`) rail is expanded (`w-72`) or collapsed to an icon rail (`w-14`).
`AppShell.tsx`'s `<aside>` transitions its `width` on change
(`transition-[width] duration-[var(--duration-base)]`, so it inherits the
same reduced-motion override every other transition in this app already
respects — see `index.css`'s global `prefers-reduced-motion` block).
Collapsed, `Sidebar` renders a narrow icon-only rail (New Chat only —
a list of bare conversation titles can't render meaningfully at icon
width, so the list and search are hidden rather than squeezed into
illegibility); expanding restores the full content exactly as it was,
since nothing about the underlying `chatStore` history state is affected
by the collapse preference. `MobileNav`'s drawer never collapses — see
`docs/navigation-and-actions.md`'s "single source of truth for sidebar
state" for why that's a deliberately separate, unpersisted concern from
this preference.

## Accessibility fixes in this pass

- Explicit `aria-label` on the chat composer `<textarea>` (previously
  relied on placeholder text alone).
- A visually-hidden `aria-live="polite"` region (`ChatLiveRegion.tsx`)
  announces exactly two moments — a question starting to process, and its
  answer arriving — never the live "Thinking Ns…" tick, which would
  otherwise re-announce every second.
- Search result count announced via `role="status"` +
  `aria-label` (`ConversationSearchResults.tsx`).
- `NavTab` (top nav) carries `aria-label`/`title` even when its visible
  text label collapses to icon-only below `sm:`.

**Added 2026-10-18:**

- **A skip link** (`AppShell.tsx`) — WCAG 2.4.1 Bypass Blocks, **Level
  A**, and the most severe finding of that audit: every route repeated the
  sidebar and primary nav with no way past them. `<main>` carries
  `tabIndex={-1}` so the jump actually moves focus rather than only
  scrolling, and the click handler keeps `#main-content` out of the
  router's URL. It is parked off-screen by transform rather than
  `sr-only` + `focus-visible:not-sr-only`, because `not-sr-only` resets
  `padding` to `0` and Tailwind emits variants after plain utilities, so
  the focused link would render as a cramped pill.
- **Accessible names** for three controls that had only a placeholder or a
  `title` (`TenantAdmin`, `PlatformAdmin`, `RemoveTextDialog`).
- **A visible keyboard indicator on menu items.** `data-[highlighted]`
  was a background swap measuring 1.10:1 (light) / 1.06:1 (dark) against
  the popover surface — fine as a hover tint, close to invisible as the
  only focus signal. An inset `--focus-ring` ring was added.
- **Dialogs gained a 1rem gutter** below 512px (`w-[calc(100%-2rem)]`);
  they previously sat flush against both screen edges on a phone.

**`role="list"` is kept deliberately** on the five `<ul>`s that carry it,
and oxlint's `no-redundant-roles` is suppressed inline with a reason.
Tailwind preflight sets `ol,ul,menu{list-style:none}`, and WebKit drops
list semantics from an unmarkered list — removing the role would stop
VoiceOver announcing "list, N items" on Safari and iOS. The canonical note
is in `ConversationContextPanel.tsx`.

## Contrast and focus tokens (2026-10-18 audit)

A computed WCAG audit of every token pair in both themes found **14
failures**. They are fixed, and the arithmetic now runs in CI as
`src/index.contrast.test.ts` (42 assertions) using `src/test/contrast.ts`,
which reads `index.css` itself so the guard cannot drift from the shipped
palette.

| Token | Was | Now | Why |
|---|---|---|---|
| `--muted-foreground` (light) | `#64748b` | `#5a6878` | 4.34:1 on `--muted`, 3.80:1 on `--accent-soft` |
| `--success` (light) | `#16a34a` | `#15803d` | 3.15:1 as text |
| `--warning` (light) | `#d97706` | `#b45309` | 3.04:1 as text |
| `--danger` (light) | `#dc2626` | `#b91c1c` | 4.41:1 on `--muted` |
| `--success` / `--warning` (dark) | *inherited* | `#16a34a` / `#d97706` | now set per theme — see below |
| `--danger` (dark) | *inherited* | `#ef4444` | 4.07:1 on the dark page |
| `--danger-strong` | — | `#b91c1c` | **new**: the one solid status fill |
| `--border-control` | — | `#7a8795` / `#6b6894` | **new**: WCAG 1.4.11 control boundary |
| `--focus-ring` | `var(--accent)` | `#4338ca` / `#818cf8` | decoupled — see below |
| `ACCENT_OPTIONS.emerald` | `#059669` | `#047857` | its own white label was 3.77:1 |

Three of these are design decisions, not just reshades:

- **Status colors are per-theme now.** They are read as text ~111 times
  against 15 backgrounds, 14 of which are faint `/10`–`/30` tints behind
  that same text — so they are text colors, not fills. Darkening them for
  the light page initially *broke* dark mode (3.92:1), which is why both
  themes are measured whenever either moves.
- **`--danger-strong` exists because one token cannot do both jobs.** In
  dark mode `--danger` must lighten to stay legible as text, which would
  drop `button.tsx`'s white danger label to 3.76:1. The fill keeps its own
  token; the tints and text follow `--danger`.
- **`--focus-ring` no longer mirrors `--accent`.** The accent is
  user-chosen and pulls the opposite way (a fill wants to be dark enough
  for a white label; a ring wants to contrast with the surface). Measured
  per accent, the **default indigo failed in dark mode on all three
  surfaces** (3.13 / 2.90 / 2.84) and Amber failed in light (2.91), and no
  single shade fixed both roles. The token's original comment anticipated
  exactly this. All 29 `focus-visible:ring-[var(--accent)]` usages were
  migrated, so focus visibility no longer depends on a decorative
  preference.

**`--border` was deliberately left at 1.18:1.** It frames surfaces and
divides sections, which 1.4.11 exempts; only boundaries *required to
identify a control* need 3:1. Those moved to `--border-control` (11 call
sites, selected by the unambiguous `bg-[var(--input)]` signature). Painting
every card and separator at 3:1 would turn the UI into a wireframe.

## Known gaps, not addressed in this pass

- Existing components' inline error text was not migrated to the new
  `Toast` component (see "Note on adoption scope" above).
- No exhaustive accessibility audit was performed across every existing
  component — only the pieces touched or newly built in this pass were
  checked.

**Resolved by a later pass** (this note kept for history rather than
silently deleted): this section used to also flag that the header's
Settings gear and Sign out button were duplicated a second time in the
Sidebar's own footer, and that no sidebar-collapse feature existed. Both
are fixed — see `docs/ui-production-audit.md` and
`docs/navigation-and-actions.md`.
