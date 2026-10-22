import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { describe, expect, it } from 'vitest'
import { useSettingsStore } from '@/store/settingsStore'
import { SettingsDialog } from './SettingsDialog'

/** Regression coverage for the focus-restoration bug found during the
 * 2026-09-19 functional UI audit (docs/functional-ui-audit.md): closing
 * Settings left `document.activeElement` on `<body>` instead of returning
 * to the account-menu button, because Settings is opened from a
 * `DropdownMenuItem` that unmounts (closing the whole menu) before the
 * Dialog's `open` prop flips true -- Radix's own automatic "restore focus
 * to whatever was active when I mounted" had nothing useful to restore to.
 * `SettingsDialog` now takes an explicit `triggerRef` and refocuses it via
 * `onCloseAutoFocus`; this harness renders a real button standing in for
 * `UserMenu`'s avatar button to verify that explicit path, independent of
 * `UserMenu`'s own dropdown-open/close mechanics (already covered by
 * `UserMenu.test.tsx`). */
function Harness() {
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button ref={triggerRef} onClick={() => setOpen(true)}>
        Account menu
      </button>
      <SettingsDialog open={open} onOpenChange={setOpen} triggerRef={triggerRef} />
    </>
  )
}

function renderHarness() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  )
}

describe('SettingsDialog', () => {
  it('opens with a labeled dialog and a dimmed backdrop overlay', async () => {
    renderHarness()
    await userEvent.click(screen.getByRole('button', { name: 'Account menu' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText('Settings')).toBeInTheDocument()
    // A real backdrop element (Radix's Overlay) must exist and render
    // above the app -- see docs/functional-ui-audit.md's Settings-overlay
    // finding for why this is specifically worth locking in as a test,
    // not just eyeballing it.
    expect(document.querySelector('[data-radix-popper-content-wrapper], [data-state="open"]')).toBeTruthy()
  })

  it('closes on Escape and restores focus to the trigger button, not document.body', async () => {
    renderHarness()
    const trigger = screen.getByRole('button', { name: 'Account menu' })
    await userEvent.click(trigger)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.activeElement).toBe(trigger)
  })

  it('closes via the close button and also restores focus to the trigger', async () => {
    renderHarness()
    const trigger = screen.getByRole('button', { name: 'Account menu' })
    await userEvent.click(trigger)

    await userEvent.click(screen.getByRole('button', { name: /close dialog/i }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.activeElement).toBe(trigger)
  })

  it('moves focus into the dialog when it opens', async () => {
    renderHarness()
    await userEvent.click(screen.getByRole('button', { name: 'Account menu' }))

    const dialog = screen.getByRole('dialog')
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  // Wiring check for the typography controls: `FontSettings.test.tsx`
  // covers their behavior in isolation, but only this proves they are
  // actually reachable in the Appearance section of the real dialog,
  // alongside the theme/accent/language controls they sit with.
  it('offers both font controls in the Appearance section, applied live', async () => {
    renderHarness()
    await userEvent.click(screen.getByRole('button', { name: 'Account menu' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Appearance')).toBeInTheDocument()

    const appFont = within(dialog).getByLabelText('Application font')
    const codeFont = within(dialog).getByLabelText('Code & SQL font')

    await userEvent.selectOptions(appFont, 'geist')
    expect(document.documentElement.style.getPropertyValue('--font-sans')).toContain(
      'Geist Variable',
    )

    await userEvent.selectOptions(codeFont, 'geist-mono')
    expect(document.documentElement.style.getPropertyValue('--font-mono')).toContain(
      'Geist Mono Variable',
    )

    // The pre-existing appearance controls are untouched by the change.
    expect(within(dialog).getByText('Theme')).toBeInTheDocument()
    expect(within(dialog).getByText('Accent color')).toBeInTheDocument()
    expect(within(dialog).getByText('Language')).toBeInTheDocument()

    useSettingsStore.getState().resetFonts()
  })
})
