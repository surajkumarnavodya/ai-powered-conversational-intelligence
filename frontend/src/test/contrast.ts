/** WCAG contrast arithmetic over the design tokens in `src/index.css`.
 *
 * **Why this exists.** `src/test/axe.ts` disables axe's `color-contrast`
 * rule, and correctly so: jsdom has no layout or paint, so axe cannot
 * resolve a computed colour and would report neither a real pass nor a
 * real failure. That left contrast as the one accessibility criterion this
 * repo could not check anywhere, documented as a gap only a real browser
 * could close.
 *
 * It turns out a useful part of it does not need a browser. The tokens are
 * declared as literal colours in one stylesheet, and the pairings that
 * matter -- body text on the page, secondary text on a card, status text
 * on its own tinted badge -- are known. Reading the real file and doing
 * the arithmetic catches the whole class of "someone retuned a swatch and
 * quietly dropped below AA", which is exactly how the 2026-10-18 audit
 * found fourteen failures.
 *
 * **What it still does not prove.** It checks *declared tokens*, not
 * rendered pixels. It cannot see a component that hardcodes a colour
 * instead of using a token, text over an image or gradient, a translucent
 * surface stacked more than two deep, or anything a browser composites at
 * paint time. Real-browser verification remains necessary; this must not
 * be cited as proof that the rendered UI meets AA.
 *
 * **One sentence of that list was wrong and cost a real finding.** It
 * said a translucent surface was out of reach, and the whole class of
 * `bg-[var(--token)]/15 text-[var(--token)]` status badges was left
 * unchecked on that basis. Phase 9 drove real Chrome over the live app
 * and found ten of those pairings below AA -- then reproduced every one
 * of Chrome's ratios here, to the second decimal, with nothing but the
 * arithmetic already in this file (`#15803d` on `#dcece2`: Chrome 4.09,
 * computed 4.09). `tintedSurface` below is that case, and the lesson
 * generalises: before recording a pairing as browser-only, check whether
 * the composite is actually computable.
 */

export interface Rgb {
  r: number
  g: number
  b: number
  /** 0-1. Eight-digit hex tokens (`--card` in dark mode) are translucent. */
  a: number
}

export function parseColor(value: string): Rgb {
  const hex = value.trim().replace('#', '')
  const full = hex.length === 3 || hex.length === 4
    ? hex.split('').map((c) => c + c).join('')
    : hex
  return {
    r: parseInt(full.slice(0, 2), 16),
    g: parseInt(full.slice(2, 4), 16),
    b: parseInt(full.slice(4, 6), 16),
    a: full.length === 8 ? parseInt(full.slice(6, 8), 16) / 255 : 1,
  }
}

/** Composites a possibly-translucent colour onto an opaque backdrop.
 * Without this, a token like dark mode's `--card` (#15132485, 52% alpha)
 * would be measured as if it were opaque and report the wrong ratio. */
export function compositeOver(fg: Rgb, bg: Rgb): Rgb {
  if (fg.a >= 1) return fg
  return {
    r: Math.round(fg.r * fg.a + bg.r * (1 - fg.a)),
    g: Math.round(fg.g * fg.a + bg.g * (1 - fg.a)),
    b: Math.round(fg.b * fg.a + bg.b * (1 - fg.a)),
    a: 1,
  }
}

/** Relative luminance, per WCAG 2.x. */
export function relativeLuminance({ r, g, b }: Rgb): number {
  const channel = (raw: number) => {
    const v = raw / 255
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

/** Contrast ratio between two token values.
 *
 * `base` is the opaque page colour that a translucent `background` sits
 * on; it defaults to `background` itself for the opaque case.
 */
export function contrastRatio(foreground: string, background: string, base?: string): number {
  const pageColor = parseColor(base ?? background)
  const bg = compositeOver(parseColor(background), pageColor)
  const fg = compositeOver(parseColor(foreground), bg)
  const [lighter, darker] = [relativeLuminance(fg), relativeLuminance(bg)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

/** A Tailwind alpha tint (`bg-[var(--token)]/15`) composited onto the
 * surface it sits on, which may itself be translucent over the opaque page.
 *
 * This is the pairing a status badge actually renders: the same token as
 * both text and a faint fill. Worth having as a named helper because the
 * two-step composite is exactly what was skipped -- `index.contrast.test.ts`
 * asserted the token on the *opaque* surface and reasoned that the tint
 * was too faint to matter. It is not: 15% of `--success` over a white card
 * lifts the backdrop enough to drop the pairing from 5.33 to 4.09, under
 * AA. Verified against Chrome's own computed values, Phase 9 `UI-008`.
 */
export function tintedSurface(
  token: string,
  surface: string,
  base: string,
  alphaPercent: number,
): Rgb {
  const page = parseColor(base)
  const opaqueSurface = compositeOver(parseColor(surface), page)
  const tint = { ...parseColor(token), a: alphaPercent / 100 }
  return compositeOver(tint, opaqueSurface)
}

/** Contrast ratio of a foreground token against an already-composited
 * backdrop, for the cases where the backdrop was built by `tintedSurface`
 * rather than read from a token. */
export function contrastAgainst(foreground: string, backdrop: Rgb): number {
  const fg = compositeOver(parseColor(foreground), backdrop)
  const [lighter, darker] = [relativeLuminance(fg), relativeLuminance(backdrop)].sort(
    (a, b) => b - a,
  )
  return (lighter + 0.05) / (darker + 0.05)
}

/** Reproduces `color-mix(in srgb, <a> <pct>%, <b>)` for `--accent-soft`,
 * which is derived in CSS and therefore has no literal value to read. */
export function colorMix(a: string, b: string, percent: number): string {
  const x = parseColor(a)
  const y = parseColor(b)
  const t = percent / 100
  const channel = (p: number, q: number) =>
    Math.round(p * t + q * (1 - t)).toString(16).padStart(2, '0')
  return `#${channel(x.r, y.r)}${channel(x.g, y.g)}${channel(x.b, y.b)}`
}

/** Extracts one theme's custom properties from the stylesheet source.
 *
 * Reads the real file rather than a copy of the values, so this cannot
 * silently agree with a stale duplicate of the palette.
 */
export function readThemeTokens(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(selector)
  if (start === -1) throw new Error(`Theme block not found: ${selector}`)
  const open = css.indexOf('{', start)
  const close = css.indexOf('\n}', open)
  const block = css.slice(open, close)
  const tokens: Record<string, string> = {}
  for (const match of block.matchAll(/^\s*--([a-z0-9-]+):\s*([^;]+);/gim)) {
    tokens[match[1]] = match[2].trim()
  }
  return tokens
}
