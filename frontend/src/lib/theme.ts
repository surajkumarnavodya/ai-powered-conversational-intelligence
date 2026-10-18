export type ThemeMode = 'light' | 'dark' | 'system'

export interface AccentOption {
  id: string
  label: string
  value: string
  /** Text color for content painted ON TOP of the solid accent color (e.g.
   * a primary button's label) -- unlike --accent-soft (see applyAccent's
   * comment), this genuinely doesn't depend on light/dark theme, since the
   * accent hue's own lightness doesn't change between themes. */
  foreground: string
}

export const ACCENT_OPTIONS: AccentOption[] = [
  { id: 'indigo', label: 'Indigo', value: '#4f46e5', foreground: '#ffffff' },
  { id: 'blue', label: 'Blue', value: '#2563eb', foreground: '#ffffff' },
  // Darkened from #059669 (2026-10-18 contrast audit): that value put its
  // own white `foreground` at 3.77:1, so every primary button, badge and
  // selected state rendered under WCAG AA whenever this accent was picked.
  // #047857 is the nearest shade of the same hue that clears 4.5:1 (5.48).
  { id: 'emerald', label: 'Emerald', value: '#047857', foreground: '#ffffff' },
  { id: 'rose', label: 'Rose', value: '#e11d48', foreground: '#ffffff' },
  { id: 'amber', label: 'Amber', value: '#d97706', foreground: '#1f1300' },
  { id: 'violet', label: 'Violet', value: '#7c3aed', foreground: '#ffffff' },
]

// Font selection lives in `./fonts.ts`, not here -- it owns both the UI and
// the monospace preference, their allowlists and their lazy self-hosted
// loading.

/** Resolves 'system' against the OS-level light/dark preference. */
export function resolveThemeMode(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'system') {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return mode
}

export function applyThemeMode(mode: ThemeMode): void {
  document.documentElement.dataset.theme = resolveThemeMode(mode)
}

export function applyAccent(accentId: string): void {
  const accent = ACCENT_OPTIONS.find((option) => option.id === accentId) ?? ACCENT_OPTIONS[0]
  const root = document.documentElement.style
  root.setProperty('--accent', accent.value)
  root.setProperty('--accent-foreground', accent.foreground)
  // --accent-soft is deliberately NOT set here -- it's derived in index.css
  // via color-mix(--accent, --background) so it automatically matches the
  // *current* theme's background (a light tint in light mode, a dark tint
  // in dark mode). Setting a hardcoded light-mode hex here used to clobber
  // the dark theme's own --accent-soft rule (inline styles beat attribute
  // selectors), which is why selected/active items became invisible --
  // light text on a light "soft" background -- after switching to dark.
}

/** Re-applies 'system' mode when the OS preference changes mid-session. */
export function watchSystemTheme(onChange: () => void): () => void {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', onChange)
  return () => media.removeEventListener('change', onChange)
}
