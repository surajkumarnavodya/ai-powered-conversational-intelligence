/** WCAG AA contrast guard over `src/index.css`'s design tokens, for both
 * themes and every selectable accent.
 *
 * See `src/test/contrast.ts` for why this is computed rather than measured
 * in a browser, and for the precise limits of what it proves.
 */
import { describe, expect, it } from 'vitest'
import { ACCENT_OPTIONS } from '@/lib/theme'
import {
  colorMix,
  contrastAgainst,
  contrastRatio,
  readThemeTokens,
  tintedSurface,
} from '@/test/contrast'
import baseline from './index.contrast.baseline.json'
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
    '--%s is legible as text on every opaque surface it is used on',
    (token) => {
      // The opaque case: `text-[var(--token)]` directly on the page, a
      // card or a muted panel.
      //
      // This assertion used to carry a comment saying the `/10`-`/15`
      // tint these tokens are *usually* set on "is too faint to shift the
      // result, so the opaque surfaces below are the real test". That was
      // false, and false in the direction that hides a failure: the tint
      // moves `--success` in light mode from 5.33 on the card to **4.09**
      // on its own tint. Measured in Chrome against the live app
      // 2026-10-29 and reproduced here to the second decimal (Phase 9,
      // `UI-008`). The tinted pairing now has its own guard below.
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

/** The two pairings this file never checked, measured rather than assumed.
 *
 * ## Why they are a baseline rather than a plain assertion
 *
 * Both fail today, in the shipped palette, and neither can be fixed from
 * here: the fix is a new colour value, which is a visible design change and
 * an owner's decision (Phase 9 `UI-008`, flagged, not fixed). The choice
 * was therefore between asserting nothing -- which is how they went
 * unnoticed -- and recording exactly what fails so the number cannot move
 * silently.
 *
 * So this is a **no-growth** guard, the same shape as
 * `i18n/localeParity.test.ts`: the computed set of sub-AA pairings must
 * equal `index.contrast.baseline.json` exactly. A newly failing pairing
 * fails the test (a swatch was retuned downward), and a pairing that stops
 * failing *also* fails it (the baseline went stale and must shrink). That
 * keeps the file a live measure of the debt instead of a permanent
 * allowlist.
 *
 * ## Why these are computable at all
 *
 * `src/test/contrast.ts` composites, so a Tailwind `/15` tint over a
 * possibly-translucent card over the opaque page is arithmetic, not paint.
 * Every ratio below was checked against Chrome's own computed values on
 * the live app (2026-10-29) and agrees to the second decimal. Contrast was
 * never browser-only for this class; it was merely unchecked.
 */
describe('contrast — pairings measured in a browser and pinned here', () => {
  /** `bg-[var(--token)]/15 text-[var(--token)]` -- the status badge shape
   * used by Decision Center evidence chips, onboarding step states,
   * recommendation evidence labels and semantic-review flags. */
  const STATUS_TINT_ALPHA = 15

  function failingTintPairings(): string[] {
    const failures: string[] = []
    for (const theme of ['light', 'dark'] as const) {
      const t = themes[theme]
      for (const token of ['success', 'warning', 'danger'] as const) {
        for (const surface of TEXT_SURFACES) {
          const backdrop = tintedSurface(t[token], t[surface], t.background, STATUS_TINT_ALPHA)
          const measured = contrastAgainst(t[token], backdrop)
          if (measured < TEXT_AA) {
            failures.push(`${theme}/${token}-on-its-own-tint-over-${surface}`)
          }
        }
      }
    }
    return failures.sort()
  }

  /** `--accent` set as 12px text: the composer's model-picker label, the
   * "Go to a screen you can use" link, inline source links. Distinct from
   * the two accent checks above, which cover the accent as a *fill* and as
   * a *graphic* at 3:1 -- neither holds it to text contrast. */
  function failingAccentTextPairings(): string[] {
    const failures: string[] = []
    for (const theme of ['light', 'dark'] as const) {
      const t = themes[theme]
      for (const accent of ACCENT_OPTIONS) {
        for (const surface of TEXT_SURFACES) {
          if (contrastRatio(accent.value, t[surface], t.background) < TEXT_AA) {
            failures.push(`${theme}/${accent.id}-as-text-on-${surface}`)
          }
        }
      }
    }
    return failures.sort()
  }

  it('the status-tint failures are exactly the ones recorded', () => {
    expect(failingTintPairings()).toEqual([...baseline.statusTintBelowAA].sort())
  })

  it('the accent-as-text failures are exactly the ones recorded', () => {
    expect(failingAccentTextPairings()).toEqual([...baseline.accentAsTextBelowAA].sort())
  })

  it('reproduces the exact ratios Chrome measured, so the guard is real', () => {
    // Four values lifted from the Chrome run (see
    // docs/uat-readiness/phase-9-ui.md). If the arithmetic here drifted
    // from what a browser paints, every assertion above would be measuring
    // the wrong thing -- so the agreement is itself pinned.
    const light = themes.light
    const onCard = tintedSurface(light.success, light.card, light.background, 15)
    expect(contrastAgainst(light.success, onCard)).toBeCloseTo(4.09, 2)
    const onPage = tintedSurface(light.success, light.background, light.background, 15)
    expect(contrastAgainst(light.success, onPage)).toBeCloseTo(3.93, 2)
    const warnOnPage = tintedSurface(light.warning, light.background, light.background, 15)
    expect(contrastAgainst(light.warning, warnOnPage)).toBeCloseTo(3.92, 2)
    // Chrome reported 3.01 for the accent on the dark card; the 0.01 is
    // Chrome rounding its own composite, not a disagreement.
    expect(contrastRatio('#4f46e5', themes.dark.card, themes.dark.background)).toBeCloseTo(3.02, 2)
  })

  it('the baseline is not empty, so these tests cannot pass vacuously', () => {
    // If a future palette change fixes everything, the two assertions above
    // fail and point here. This one says why that is the intended outcome.
    expect(baseline.statusTintBelowAA.length + baseline.accentAsTextBelowAA.length).toBeGreaterThan(
      0,
    )
  })
})
