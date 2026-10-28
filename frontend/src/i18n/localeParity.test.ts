/**
 * Translation-key drift between locales must not grow.
 *
 * CLAUDE.md 6.2 requires i18n keys "for user-visible text across all five
 * locales", and nothing enforced it. The gap was not hypothetical: a
 * change to `chat.placeholder` landed in `en` only, leaving `es`, `fr`,
 * `hi` and `mr` on the previous wording, so an English user saw one
 * constant composer placeholder while a Spanish user still saw a
 * two-state one. Nothing failed, because key sets were never compared.
 *
 * ## Why this is a no-growth guard rather than a parity assertion
 *
 * Measured 2026-10-28: `en` carries **757** keys and each other locale
 * **480** -- so 280 keys per locale, 1,120 in total, exist only in
 * English. That backlog long predates this file, and the honest options
 * were to fail the suite on it or to guard against it getting worse.
 * Machine-translating 1,120 strings into four languages to make a test
 * pass would be the worst of the three.
 *
 * So `localeParity.baseline.json` records the drift as it stands, and
 * these tests fail when a locale drifts *further*: add a key to `en`
 * without translating it and the new key is not in the baseline, so the
 * build tells you. Translate one and the baseline shrinks, which is also
 * a failure -- regenerate it in the same commit, which keeps the file an
 * accurate measure of the remaining debt rather than a rubber stamp.
 *
 * `en` is the reference because it is the fallback language in
 * `src/i18n/index.ts`: a key missing from `en` is unresolvable
 * everywhere, while a key missing from another locale quietly falls back
 * to English text inside an otherwise translated screen.
 */

import { describe, expect, it } from 'vitest'
import baseline from './localeParity.baseline.json'
import en from './locales/en/translation.json'
import es from './locales/es/translation.json'
import fr from './locales/fr/translation.json'
import hi from './locales/hi/translation.json'
import mr from './locales/mr/translation.json'
import { SUPPORTED_LANGUAGES } from './index'

const BUNDLES: Record<string, unknown> = { en, es, fr, hi, mr }
const TRANSLATED = ['es', 'fr', 'hi', 'mr'] as const

type LocaleBaseline = { untranslated: string[]; notInReference: string[] }
const BASELINE = baseline as Record<string, LocaleBaseline>

/** Every leaf path in a nested bundle, as dotted keys. */
function leafKeys(value: unknown, prefix = ''): string[] {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return [prefix]
  }
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    leafKeys(child, prefix ? `${prefix}.${key}` : key),
  )
}

const KEYS: Record<string, Set<string>> = Object.fromEntries(
  Object.entries(BUNDLES).map(([locale, bundle]) => [locale, new Set(leafKeys(bundle))]),
)

describe('i18n locale parity', () => {
  it('covers every language the picker offers', () => {
    // Non-vacuity guard: a sixth locale added but not imported here would
    // leave every comparison below silently ignoring it.
    const offered = SUPPORTED_LANGUAGES.map((language) =>
      typeof language === 'string' ? language : language.code,
    ).sort()
    expect(Object.keys(BUNDLES).sort()).toEqual(offered)
  })

  it('reads a non-trivial number of keys from the reference bundle', () => {
    // Second non-vacuity guard: a broken `leafKeys` returning [] would
    // make every set comparison below trivially empty and passing.
    expect(KEYS.en.size).toBeGreaterThan(700)
  })

  it.each(TRANSLATED)('%s gains no newly-untranslated key', (locale) => {
    const known = new Set(BASELINE[locale].untranslated)
    const added = [...KEYS.en].filter((key) => !KEYS[locale].has(key) && !known.has(key)).sort()
    expect(added).toEqual([])
  })

  it.each(TRANSLATED)('%s gains no new key the reference bundle lacks', (locale) => {
    const known = new Set(BASELINE[locale].notInReference)
    const added = [...KEYS[locale]].filter((key) => !KEYS.en.has(key) && !known.has(key)).sort()
    expect(added).toEqual([])
  })

  it.each(TRANSLATED)('%s has a baseline that is still accurate', (locale) => {
    // Catches the other direction: a key that *has* since been
    // translated, or removed from `en`, must leave the baseline. Without
    // this the file would rot into a permanent allowlist and stop
    // measuring anything.
    const stale = BASELINE[locale].untranslated
      .filter((key) => !KEYS.en.has(key) || KEYS[locale].has(key))
      .sort()
    expect(stale).toEqual([])
  })

  it('keeps the composer placeholder present in every locale', () => {
    // The specific key whose en-only edit motivated this file. Its
    // sibling `chat.placeholderEmpty` was removed when the component
    // stopped branching on conversation state, so it must be gone
    // everywhere too -- a leftover in one locale is dead weight that
    // reads as a live alternative.
    for (const locale of Object.keys(BUNDLES)) {
      expect(KEYS[locale].has('chat.placeholder')).toBe(true)
      expect(KEYS[locale].has('chat.placeholderEmpty')).toBe(false)
    }
  })

  it('leaves no empty string behind as a translation', () => {
    const blank: string[] = []
    for (const [locale, bundle] of Object.entries(BUNDLES)) {
      for (const key of leafKeys(bundle)) {
        const value = key
          .split('.')
          .reduce<unknown>(
            (node, part) => (node as Record<string, unknown> | undefined)?.[part],
            bundle,
          )
        if (typeof value === 'string' && value.trim() === '') blank.push(`${locale}:${key}`)
      }
    }
    expect(blank).toEqual([])
  })
})
