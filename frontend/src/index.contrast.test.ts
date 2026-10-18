/** WCAG AA contrast guard over `src/index.css`'s design tokens, for both
 * themes and every selectable accent.
 *
 * See `src/test/contrast.ts` for why this is computed rather than measured
 * in a browser, and for the precise limits of what it proves.
 */
import { describe, expect, it } from 'vitest'
import { ACCENT_OPTIONS } from '@/lib/theme'
import { colorMix, contrastRatio, readThemeTokens } from '@/test/contrast'
// The real stylesheet, as text, via Vite's `?raw` loader. Reading the
// shipped file is the whole point -- a copy of the palette kept here could
// agree with itself while the tokens that actually render drifted away.
// Deliberately not `node:fs`: `tsconfig.app.json` scopes `src` to
// `vite/client` types only, and widening it to Node types so one test can
// read a file would hand every app module `process` and `fs` as well.
import css from './index.css?raw'
const light = readThemeTokens(css, ':root {')
const dark = readThemeTokens(css, ":root[data-theme='dark'] {")

/** Dark mode overrides only some tokens; the rest cascade from `:root`. */
const themes = {
  light,
  dark: { ...light, ...dark },
}

/** WCAG 1.4.3: body text. */
const TEXT_AA = 4.5
/** WCAG 1.4.11: boundaries and states of user-interface components. */
const UI_AA = 3

type ThemeName = keyof typeof themes

function ratio(theme: ThemeName, fgToken: string, bgToken: string): number {
  const t = themes[theme]
  return contrastRatio(t[fgToken], t[bgToken], t.background)
}

/** Every opaque surface that prose is actually set on. */
const TEXT_SURFACES = ['background', 'card', 'muted'] as const

describe.each(['light', 'dark'] as const)('contrast — %s theme', (theme) => {
  const t = themes[theme]

  it.each([...TEXT_SURFACES])('primary text on --%s meets AA', (surface) => {
    expect(ratio(theme, 'foreground', surface)).toBeGreaterThanOrEqual(TEXT_AA)
  })

  it.each([...TEXT_SURFACES])('secondary text on --%s meets AA', (surface) => {
    // --muted-foreground carries real information here (timestamps, row
    // counts, source labels), so it is held to body-text contrast rather
    // than treated as decorative.
    expect(ratio(theme, 'muted-foreground', surface)).toBeGreaterThanOrEqual(TEXT_AA)
  })

  it.each(['success', 'warning', 'danger'])(
    '--%s is legible as text on every surface it is used on',
    (token) => {
      // Overwhelmingly used as `text-[var(--token)]`, including on top of
      // its own `/10`-`/15` tint. The tint is too faint to shift the
      // result, so the opaque surfaces below are the real test.
      for (const surface of TEXT_SURFACES) {
        expect(ratio(theme, token, surface)).toBeGreaterThanOrEqual(TEXT_AA)
      }
    },
  )

  it('selected rows keep both text levels legible on --accent-soft', () => {
    // --accent-soft is color-mix(accent 12%, background), so it has no
    // literal value in the stylesheet to read.
    const soft = colorMix(t.accent, t.background, 12)
    expect(contrastRatio(t.foreground, soft)).toBeGreaterThanOrEqual(TEXT_AA)
    expect(contrastRatio(t['muted-foreground'], soft)).toBeGreaterThanOrEqual(TEXT_AA)
  })

  it('a solid danger button keeps its white label legible', () => {
    // The single solid status fill in the app (button.tsx's danger
    // variant). It reads --danger-strong precisely because --danger
    // lightens in dark mode for text legibility.
    expect(contrastRatio('#ffffff', t['danger-strong'])).toBeGreaterThanOrEqual(TEXT_AA)
  })

  it('a form control has a boundary strong enough to identify it', () => {
    // WCAG 1.4.11. --input is near-indistinguishable from the page, so
    // the control's outline is what makes it a control.
    for (const surface of TEXT_SURFACES) {
      expect(ratio(theme, 'border-control', surface)).toBeGreaterThanOrEqual(UI_AA)
    }
  })

  it('the focus ring is visible on every surface it lands on', () => {
    // --focus-ring, not --accent. The accent is user-chosen and serves a
    // conflicting role (a fill dark enough for a white label), which is
    // why the default indigo measured 2.84-3.13:1 here before the two
    // were decoupled. Keyboard visibility must not depend on a decorative
    // preference, so this is checked against the dedicated token.
    for (const surface of ['background', 'card', 'muted', 'input'] as const) {
      expect(ratio(theme, 'focus-ring', surface)).toBeGreaterThanOrEqual(UI_AA)
    }
  })

  it('the focus ring does not silently fall back to the accent', () => {
    // A regression guard with teeth: re-pointing --focus-ring at
    // var(--accent) would reintroduce exactly the failure above, and every
    // other assertion here would still pass for the *default* accent.
    expect(themes[theme]['focus-ring']).toMatch(/^#[0-9a-f]{6}$/i)
  })

  it('selected text stays readable', () => {
    expect(contrastRatio(t['selection-foreground'], t['selection-bg'])).toBeGreaterThanOrEqual(
      TEXT_AA,
    )
  })
})

describe('contrast — every selectable accent', () => {
  it.each(ACCENT_OPTIONS.map((o) => [o.id, o.value, o.foreground] as const))(
    '%s keeps its own label legible on the fill',
    (_id, value, foreground) => {
      // The accent is painted solid behind this exact foreground on every
      // primary button and selected state, so a swatch that fails here
      // fails app-wide for anyone who picks it.
      expect(contrastRatio(foreground, value)).toBeGreaterThanOrEqual(TEXT_AA)
    },
  )

  it.each(ACCENT_OPTIONS.map((o) => [o.id, o.value] as const))(
    '%s stays distinguishable from the page it sits on',
    (_id, value) => {
      // 3:1 as a *graphic* (a selected state, a filled control against the
      // page), which is still required of any accent. Focus visibility is
      // no longer this token's job -- see --focus-ring above.
      for (const theme of ['light', 'dark'] as const) {
        expect(contrastRatio(value, themes[theme].background)).toBeGreaterThanOrEqual(UI_AA)
      }
    },
  )
})
