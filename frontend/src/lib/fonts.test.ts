import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  applyMonoFont,
  applyUiFont,
  DEFAULT_MONO_FONT_ID,
  DEFAULT_UI_FONT_ID,
  MONO_FONT_OPTIONS,
  resolveMonoFont,
  resolveUiFont,
  UI_FONT_OPTIONS,
} from './fonts'

function sansToken(): string {
  return document.documentElement.style.getPropertyValue('--font-sans')
}

function monoToken(): string {
  return document.documentElement.style.getPropertyValue('--font-mono')
}

beforeEach(() => {
  document.documentElement.removeAttribute('style')
})

describe('font option lists', () => {
  it('offers the documented UI faces, with Inter as the default', () => {
    expect(UI_FONT_OPTIONS.map((option) => option.id)).toEqual([
      'inter',
      'geist',
      'ibm-plex-sans',
      'plus-jakarta-sans',
      'system',
      'roboto',
      'merriweather',
    ])
    expect(DEFAULT_UI_FONT_ID).toBe('inter')
    expect(resolveUiFont(undefined).id).toBe('inter')
  })

  it('offers the documented monospace faces, with JetBrains Mono as the default', () => {
    expect(MONO_FONT_OPTIONS.map((option) => option.id)).toEqual([
      'jetbrains-mono',
      'geist-mono',
      'system-mono',
    ])
    expect(DEFAULT_MONO_FONT_ID).toBe('jetbrains-mono')
    expect(resolveMonoFont(undefined).id).toBe('jetbrains-mono')
  })

  it('keeps the two lists disjoint, so a mono face can never become the UI face', () => {
    // The bug this feature replaced: one combined list meant selecting
    // "JetBrains Mono" set --font-sans and left SQL untouched.
    const uiIds = new Set(UI_FONT_OPTIONS.map((option) => option.id))
    for (const mono of MONO_FONT_OPTIONS) {
      expect(uiIds.has(mono.id)).toBe(false)
    }
  })

  it('every option ends in a system fallback, so a failed download still renders', () => {
    for (const option of [...UI_FONT_OPTIONS, ...MONO_FONT_OPTIONS]) {
      expect(option.stack).toMatch(/(sans-serif|serif|monospace)\s*$/)
    }
  })

  it('every label and id is unique', () => {
    const all = [...UI_FONT_OPTIONS, ...MONO_FONT_OPTIONS]
    expect(new Set(all.map((o) => o.id)).size).toBe(all.length)
    expect(new Set(all.map((o) => o.label)).size).toBe(all.length)
  })
})

describe('allowlist validation', () => {
  // The security property: a persisted value selects a constant stack, it
  // is never interpolated into CSS. Each of these is what a hand-edited
  // localStorage entry could realistically contain.
  const hostile = [
    'red; background: url(https://evil.example/x)',
    '</style><script>alert(1)</script>',
    'var(--anything)',
    'jetbrains', // a real id from the old combined list -- no longer a UI font
    '',
    '__proto__',
    'constructor',
  ]

  it.each(hostile)('falls back to the default UI font for %j', (value) => {
    expect(resolveUiFont(value).id).toBe(DEFAULT_UI_FONT_ID)
  })

  it.each(hostile)('falls back to the default mono font for %j', (value) => {
    expect(resolveMonoFont(value).id).toBe(DEFAULT_MONO_FONT_ID)
  })

  it('never writes an unrecognized value into the CSS token', () => {
    applyUiFont('red; background: url(https://evil.example/x)')
    expect(sansToken()).toBe(resolveUiFont(DEFAULT_UI_FONT_ID).stack)
    expect(sansToken()).not.toContain('evil.example')
  })
})

describe('applying a font', () => {
  it('points --font-sans at the selected stack', () => {
    applyUiFont('geist')
    expect(sansToken()).toContain('Geist Variable')
  })

  it('points --font-mono at the selected stack', () => {
    applyMonoFont('geist-mono')
    expect(monoToken()).toContain('Geist Mono Variable')
  })

  it('changes the two tokens independently', () => {
    applyUiFont('ibm-plex-sans')
    applyMonoFont('jetbrains-mono')
    expect(sansToken()).toContain('IBM Plex Sans Variable')
    expect(monoToken()).toContain('JetBrains Mono Variable')

    // Re-selecting only the UI face must leave the mono token alone.
    applyUiFont('plus-jakarta-sans')
    expect(sansToken()).toContain('Plus Jakarta Sans Variable')
    expect(monoToken()).toContain('JetBrains Mono Variable')
  })

  it('downloads nothing for the system options', () => {
    expect(UI_FONT_OPTIONS.find((o) => o.id === 'system')?.load).toBeUndefined()
    expect(MONO_FONT_OPTIONS.find((o) => o.id === 'system-mono')?.load).toBeUndefined()
  })

  it('survives a font asset that fails to load', async () => {
    // Offline, blocked by a CSP, or pruned from the build: the token must
    // still be set and nothing may throw or reject unhandled.
    const option = UI_FONT_OPTIONS.find((o) => o.id === 'inter')
    const spy = vi
      .spyOn(option as { load: () => Promise<unknown> }, 'load')
      .mockRejectedValue(new Error('network failure'))

    expect(() => applyUiFont('inter')).not.toThrow()
    expect(sansToken()).toContain('Inter Variable')
    // Let the rejected promise settle so an unhandled rejection would surface.
    await Promise.resolve()
    await Promise.resolve()
    expect(spy).toHaveBeenCalled()
  })
})
