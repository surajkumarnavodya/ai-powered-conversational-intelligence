/** Persistence and rehydration for the two font preferences.
 *
 * These exercise what a page refresh actually does: zustand's `persist`
 * reads `tsql-settings` out of localStorage and replays it over the
 * defaults. A unit test that only called the setters would never touch
 * that path, which is exactly where a stale or hand-edited value arrives.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_MONO_FONT_ID, DEFAULT_UI_FONT_ID } from '@/lib/fonts'
import { useSettingsStore } from './settingsStore'

const STORAGE_KEY = 'tsql-settings'

function sansToken(): string {
  return document.documentElement.style.getPropertyValue('--font-sans')
}

function monoToken(): string {
  return document.documentElement.style.getPropertyValue('--font-mono')
}

/** Writes a persisted payload the way zustand's `persist` stores it, then
 * forces the store to re-read it -- the closest thing to a page refresh
 * without reloading the module graph. */
function reloadWith(persisted: Record<string, unknown>): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ state: persisted, version: 0 }))
  useSettingsStore.persist.rehydrate()
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('style')
  useSettingsStore.getState().resetFonts()
})

afterEach(() => {
  localStorage.clear()
})

describe('font preference persistence', () => {
  it('writes both selections to localStorage', () => {
    useSettingsStore.getState().setFont('geist')
    useSettingsStore.getState().setMonoFont('geist-mono')

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    expect(stored.state.font).toBe('geist')
    expect(stored.state.monoFont).toBe('geist-mono')
  })

  it('restores both selections after a refresh, and repaints the tokens', () => {
    reloadWith({ font: 'plus-jakarta-sans', monoFont: 'geist-mono' })

    expect(useSettingsStore.getState().font).toBe('plus-jakarta-sans')
    expect(useSettingsStore.getState().monoFont).toBe('geist-mono')
    expect(sansToken()).toContain('Plus Jakarta Sans Variable')
    expect(monoToken()).toContain('Geist Mono Variable')
  })

  it('stores only font ids -- never a CSS stack, and nothing sensitive', () => {
    useSettingsStore.getState().setFont('inter')
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')

    // An id, not a stack: the stack is a constant in code, so a tampered
    // store cannot inject one.
    expect(stored.state.font).toBe('inter')
    expect(JSON.stringify(stored)).not.toContain('ui-sans-serif')
    // Appearance preferences only -- no token, email or user identifier.
    expect(JSON.stringify(stored)).not.toMatch(/token|password|email|@/i)
  })

  it('coerces a persisted value this build no longer offers', () => {
    // 'jetbrains' was a *UI* font id in the old combined list. It must not
    // leave the <select> bound to a value with no matching <option>.
    reloadWith({ font: 'jetbrains', monoFont: 'jetbrains-mono' })

    expect(useSettingsStore.getState().font).toBe(DEFAULT_UI_FONT_ID)
    expect(sansToken()).toContain('Inter Variable')
  })

  it.each([
    ['a hand-edited CSS payload', 'red; background: url(https://evil.example/x)'],
    ['an empty string', ''],
    ['a prototype key', '__proto__'],
  ])('falls back safely for %s', (_label, value) => {
    reloadWith({ font: value, monoFont: value })

    expect(useSettingsStore.getState().font).toBe(DEFAULT_UI_FONT_ID)
    expect(useSettingsStore.getState().monoFont).toBe(DEFAULT_MONO_FONT_ID)
    expect(sansToken()).not.toContain('evil.example')
    expect(monoToken()).not.toContain('evil.example')
  })

  it('handles a non-string persisted value without throwing', () => {
    expect(() => reloadWith({ font: 42, monoFont: null })).not.toThrow()
    expect(useSettingsStore.getState().font).toBe(DEFAULT_UI_FONT_ID)
    expect(useSettingsStore.getState().monoFont).toBe(DEFAULT_MONO_FONT_ID)
  })

  it('keeps a UI font persisted by a build that had no mono preference', () => {
    // An upgrade from before this feature: `font` is honored, `monoFont`
    // is absent and must take the default rather than becoming undefined.
    reloadWith({ font: 'merriweather' })

    expect(useSettingsStore.getState().font).toBe('merriweather')
    expect(useSettingsStore.getState().monoFont).toBe(DEFAULT_MONO_FONT_ID)
    expect(monoToken()).toContain('JetBrains Mono Variable')
  })

  it('survives unavailable storage', () => {
    // Private browsing / blocked site data: localStorage.setItem throws.
    const setItem = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new DOMException('QuotaExceededError')
      })

    expect(() => useSettingsStore.getState().setFont('geist')).not.toThrow()
    // The selection still applies for this session, it simply is not saved.
    expect(sansToken()).toContain('Geist Variable')
    setItem.mockRestore()
  })

  it('leaves unrelated preferences untouched when fonts are reset', () => {
    useSettingsStore.getState().setThemeMode('dark')
    useSettingsStore.getState().setAccent('emerald')
    useSettingsStore.getState().setLanguage('fr')
    useSettingsStore.getState().setFont('geist')

    useSettingsStore.getState().resetFonts()

    const state = useSettingsStore.getState()
    expect(state.font).toBe(DEFAULT_UI_FONT_ID)
    expect(state.themeMode).toBe('dark')
    expect(state.accent).toBe('emerald')
    expect(state.language).toBe('fr')
  })
})
