/** The single owner of this app's selectable typography.
 *
 * Two independent preferences, deliberately separate lists: the UI font
 * (`--font-sans`, everything the app renders as prose/chrome) and the
 * monospace font (`--font-mono`, SQL/code/JSON/logs/ids). They used to be
 * one combined list in `theme.ts`, which meant picking "JetBrains Mono"
 * set the *UI* font to a monospace face and left SQL unchanged -- exactly
 * backwards from what the label implied.
 *
 * Every face is self-hosted via `@fontsource-variable/*` (all OFL-1.1, see
 * `public/font-licenses.txt`) rather than fetched from a web font CDN, so a
 * deployment makes no third-party request to render its own UI -- matching
 * this project's local-first posture (the LLM, the database and the vector
 * store are all local too). Each is a *variable* font: one woff2 covers the
 * whole weight axis, so no per-weight files are bundled.
 *
 * Loading is lazy and never blocks startup: `load()` is a dynamic import
 * that Vite code-splits, so a visitor downloads only the face they actually
 * selected, and only the unicode subsets their content needs (the Fontsource
 * CSS carries `unicode-range` per subset). Every stack ends in a system
 * fallback and every bundled face is `font-display: swap`, so text paints
 * immediately in the fallback and swaps in when (or if) the woff2 arrives --
 * a blocked, offline or failed font request degrades to the system face
 * instead of invisible text or a crash.
 */

export interface FontOption {
  /** Stable id -- this is what gets persisted, so renaming one is a
   * breaking change for anyone who already selected it. */
  id: string
  label: string
  /** The full CSS `font-family` stack, system fallback included. This is a
   * fixed constant, never built from user input -- see `resolveUiFont`. */
  stack: string
  /** Loads the self-hosted woff2. Absent for the system options, which have
   * nothing to download. */
  load?: () => Promise<unknown>
}

/** Shared tail for the sans stacks -- what renders before (or instead of)
 * the web font. */
const SYSTEM_SANS = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif"
const SYSTEM_MONO = "ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace"

/** `wght.css` (not the package default `index.css`) is imported on purpose:
 * it is the upright weight axis only, dropping the italic and optical-size
 * files the app never asks for. */
export const UI_FONT_OPTIONS: readonly FontOption[] = [
  {
    id: 'inter',
    label: 'Inter',
    stack: `'Inter Variable', 'Inter', ${SYSTEM_SANS}`,
    load: () => import('@fontsource-variable/inter/wght.css'),
  },
  {
    id: 'geist',
    label: 'Geist Sans',
    stack: `'Geist Variable', ${SYSTEM_SANS}`,
    load: () => import('@fontsource-variable/geist/wght.css'),
  },
  {
    id: 'ibm-plex-sans',
    label: 'IBM Plex Sans',
    stack: `'IBM Plex Sans Variable', ${SYSTEM_SANS}`,
    load: () => import('@fontsource-variable/ibm-plex-sans/wght.css'),
  },
  {
    id: 'plus-jakarta-sans',
    label: 'Plus Jakarta Sans',
    stack: `'Plus Jakarta Sans Variable', ${SYSTEM_SANS}`,
    load: () => import('@fontsource-variable/plus-jakarta-sans/wght.css'),
  },
  { id: 'system', label: 'System default', stack: SYSTEM_SANS },
  // Kept because earlier builds offered them and a user may already have one
  // persisted -- dropping them would silently change that user's UI on the
  // next load. Not recommended for new selections, hence last.
  {
    id: 'roboto',
    label: 'Roboto',
    stack: `'Roboto Variable', 'Roboto', ${SYSTEM_SANS}`,
    load: () => import('@fontsource-variable/roboto/wght.css'),
  },
  {
    id: 'merriweather',
    label: 'Merriweather (serif)',
    stack: "'Merriweather Variable', 'Merriweather', ui-serif, Georgia, serif",
    load: () => import('@fontsource-variable/merriweather/wght.css'),
  },
]

export const MONO_FONT_OPTIONS: readonly FontOption[] = [
  {
    id: 'jetbrains-mono',
    label: 'JetBrains Mono',
    stack: `'JetBrains Mono Variable', 'JetBrains Mono', ${SYSTEM_MONO}`,
    load: () => import('@fontsource-variable/jetbrains-mono/wght.css'),
  },
  {
    id: 'geist-mono',
    label: 'Geist Mono',
    stack: `'Geist Mono Variable', ${SYSTEM_MONO}`,
    load: () => import('@fontsource-variable/geist-mono/wght.css'),
  },
  { id: 'system-mono', label: 'System monospace', stack: SYSTEM_MONO },
]

export const DEFAULT_UI_FONT_ID = 'inter'
export const DEFAULT_MONO_FONT_ID = 'jetbrains-mono'

/** Maps a persisted id to its option, falling back to the default for
 * anything unrecognized.
 *
 * This is the allowlist, and the reason a persisted preference can never
 * become a CSS injection vector: the stored value selects a *constant*
 * stack defined above, and is never itself interpolated into CSS. A
 * hand-edited localStorage entry, a value from a build that offered a font
 * this one does not, or outright garbage all resolve to the default. */
export function resolveUiFont(fontId: string | undefined): FontOption {
  return (
    UI_FONT_OPTIONS.find((option) => option.id === fontId) ??
    UI_FONT_OPTIONS.find((option) => option.id === DEFAULT_UI_FONT_ID) ??
    UI_FONT_OPTIONS[0]
  )
}

export function resolveMonoFont(fontId: string | undefined): FontOption {
  return (
    MONO_FONT_OPTIONS.find((option) => option.id === fontId) ??
    MONO_FONT_OPTIONS.find((option) => option.id === DEFAULT_MONO_FONT_ID) ??
    MONO_FONT_OPTIONS[0]
  )
}

/** Starts the woff2 download, if the option has one.
 *
 * Deliberately fire-and-forget: nothing awaits it and a rejection is
 * swallowed, because a font that fails to arrive (offline, blocked, asset
 * pruned from the build) must leave the app running on the fallback stack
 * rather than surfacing an error. */
function loadFont(option: FontOption): void {
  if (!option.load) return
  void option.load().catch(() => {
    // Intentionally ignored -- see the doc comment. The CSS stack's system
    // fallback is already rendering.
  })
}

/** Points `--font-sans` at the chosen stack. Tailwind's `font-sans`
 * utilities and `body` both read that token (see `index.css`'s
 * `@theme inline` block), so one assignment re-fonts the whole app. */
export function applyUiFont(fontId: string | undefined): void {
  const option = resolveUiFont(fontId)
  document.documentElement.style.setProperty('--font-sans', option.stack)
  loadFont(option)
}

/** Points `--font-mono` at the chosen stack -- the SQL editor, generated
 * SQL, code blocks, JSON, logs and every `font-mono` utility. */
export function applyMonoFont(fontId: string | undefined): void {
  const option = resolveMonoFont(fontId)
  document.documentElement.style.setProperty('--font-mono', option.stack)
  loadFont(option)
}
