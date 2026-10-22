import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { DEFAULT_MONO_FONT_ID, DEFAULT_UI_FONT_ID } from '@/lib/fonts'
import { useSettingsStore } from '@/store/settingsStore'
import { FontSettings } from './FontSettings'

function sansToken(): string {
  return document.documentElement.style.getPropertyValue('--font-sans')
}

function monoToken(): string {
  return document.documentElement.style.getPropertyValue('--font-mono')
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('style')
  useSettingsStore.getState().resetFonts()
})

describe('FontSettings', () => {
  it('renders both pickers with every supported option', async () => {
    render(<FontSettings />)

    const appFont = screen.getByLabelText('Application font')
    const codeFont = screen.getByLabelText('Code & SQL font')

    expect(within(appFont).getByRole('option', { name: 'Inter' })).toBeInTheDocument()
    expect(within(appFont).getByRole('option', { name: 'Geist Sans' })).toBeInTheDocument()
    expect(within(appFont).getByRole('option', { name: 'IBM Plex Sans' })).toBeInTheDocument()
    expect(within(appFont).getByRole('option', { name: 'Plus Jakarta Sans' })).toBeInTheDocument()
    expect(within(appFont).getByRole('option', { name: 'System default' })).toBeInTheDocument()

    expect(within(codeFont).getByRole('option', { name: 'JetBrains Mono' })).toBeInTheDocument()
    expect(within(codeFont).getByRole('option', { name: 'Geist Mono' })).toBeInTheDocument()
    expect(within(codeFont).getByRole('option', { name: 'System monospace' })).toBeInTheDocument()

    // A monospace face must not be offered as the application font.
    expect(within(appFont).queryByRole('option', { name: 'JetBrains Mono' })).toBeNull()
  })

  it('applies the configured defaults on a clean install', () => {
    render(<FontSettings />)
    expect(screen.getByLabelText('Application font')).toHaveValue(DEFAULT_UI_FONT_ID)
    expect(screen.getByLabelText('Code & SQL font')).toHaveValue(DEFAULT_MONO_FONT_ID)
    expect(sansToken()).toContain('Inter Variable')
    expect(monoToken()).toContain('JetBrains Mono Variable')
  })

  it('selecting a UI font restyles the live interface, not just the label', async () => {
    const user = userEvent.setup()
    render(<FontSettings />)

    await user.selectOptions(screen.getByLabelText('Application font'), 'ibm-plex-sans')

    expect(sansToken()).toContain('IBM Plex Sans Variable')
    expect(useSettingsStore.getState().font).toBe('ibm-plex-sans')
    // The preview is governed by the same token the rest of the app reads.
    expect(monoToken()).toContain('JetBrains Mono Variable')
  })

  it('selecting a monospace font restyles SQL and code content', async () => {
    const user = userEvent.setup()
    render(<FontSettings />)

    await user.selectOptions(screen.getByLabelText('Code & SQL font'), 'geist-mono')

    expect(monoToken()).toContain('Geist Mono Variable')
    expect(useSettingsStore.getState().monoFont).toBe('geist-mono')
    // The SQL/JSON samples name var(--font-mono), so they follow it.
    const sql = screen.getByText(/SELECT region/)
    expect(sql).toHaveStyle({ fontFamily: 'var(--font-mono)' })
  })

  it('the preview shows representative prose, SQL, JSON and table content', () => {
    render(<FontSettings />)
    const preview = screen.getByRole('group', { name: 'Font preview' })

    expect(within(preview).getByText(/Quarterly revenue by region/)).toBeInTheDocument()
    expect(within(preview).getByText(/You asked/)).toBeInTheDocument()
    expect(within(preview).getByText(/North grew fastest/)).toBeInTheDocument()
    expect(within(preview).getByText(/SELECT region/)).toBeInTheDocument()
    expect(within(preview).getByText(/"orders": 1482/)).toBeInTheDocument()
    expect(within(preview).getByRole('columnheader', { name: 'Region' })).toBeInTheDocument()
    expect(within(preview).getByText('1,482.50')).toBeInTheDocument()
  })

  it('reset restores both defaults and is disabled once there', async () => {
    const user = userEvent.setup()
    render(<FontSettings />)

    const reset = screen.getByRole('button', { name: /Reset fonts to defaults/ })
    expect(reset).toBeDisabled()

    await user.selectOptions(screen.getByLabelText('Application font'), 'merriweather')
    await user.selectOptions(screen.getByLabelText('Code & SQL font'), 'system-mono')
    expect(reset).toBeEnabled()

    await user.click(reset)

    expect(useSettingsStore.getState().font).toBe(DEFAULT_UI_FONT_ID)
    expect(useSettingsStore.getState().monoFont).toBe(DEFAULT_MONO_FONT_ID)
    expect(sansToken()).toContain('Inter Variable')
    expect(monoToken()).toContain('JetBrains Mono Variable')
    expect(reset).toBeDisabled()
  })

  it('is keyboard operable and labelled for assistive technology', async () => {
    const user = userEvent.setup()
    render(<FontSettings />)

    const appFont = screen.getByLabelText('Application font')
    const codeFont = screen.getByLabelText('Code & SQL font')

    // Both controls are reachable by Tab, in reading order, and the reset
    // button follows them -- no keyboard trap, no focus-order surprise.
    await user.tab()
    expect(appFont).toHaveFocus()
    await user.tab()
    expect(codeFont).toHaveFocus()

    // The accessible name comes from the visible <label>, so what a screen
    // reader announces is what a sighted user reads.
    expect(appFont).toHaveAccessibleName('Application font')
    expect(codeFont).toHaveAccessibleName('Code & SQL font')
  })

  it('switching theme preserves the font selection', async () => {
    const user = userEvent.setup()
    render(<FontSettings />)
    await user.selectOptions(screen.getByLabelText('Application font'), 'geist')

    useSettingsStore.getState().setThemeMode('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(useSettingsStore.getState().font).toBe('geist')
    expect(sansToken()).toContain('Geist Variable')

    useSettingsStore.getState().setThemeMode('light')
    expect(useSettingsStore.getState().font).toBe('geist')
    expect(sansToken()).toContain('Geist Variable')
  })
})
