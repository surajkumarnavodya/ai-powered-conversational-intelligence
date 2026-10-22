import { RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { DEFAULT_MONO_FONT_ID, DEFAULT_UI_FONT_ID } from '@/lib/fonts'
import { useSettingsStore } from '@/store/settingsStore'
import { FontPicker, MonoFontPicker } from './FontPicker'

/** A live sample of both selected faces at once.
 *
 * Deliberately not a mock: it inherits `--font-sans` from the document and
 * names `var(--font-mono)` for the technical rows, which are the very same
 * tokens the real chat, SQL editor and tables read. Selections apply
 * immediately (matching the theme and accent controls next to it), so this
 * is less a "preview before saving" than a compact place to see the effect
 * on representative content without leaving Settings -- prose, headings,
 * SQL, JSON and a numeric table column each render differently enough that
 * a face can look fine in one and wrong in another.
 */
function FontPreview() {
  const { t } = useTranslation()

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--border)] bg-[var(--card)] p-3"
      // Not `aria-hidden`: a screen-reader user may be choosing a font for a
      // sighted colleague, or judging it by the labels. It is a labelled
      // group rather than hidden decoration.
      role="group"
      aria-label={t('sidebar.fontPreview')}
    >
      <p className="text-sm font-semibold">{t('sidebar.previewHeading')}</p>
      <p className="text-xs text-[var(--muted-foreground)]">{t('sidebar.previewUser')}</p>
      <p className="text-sm">{t('sidebar.previewAssistant')}</p>

      <pre
        className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-[var(--muted)] p-2 text-xs"
        style={{ fontFamily: 'var(--font-mono)' }}
      >
        {'SELECT region, COUNT(*) AS orders\nFROM sales\nWHERE year = 2026\nGROUP BY region;'}
      </pre>
      <pre
        className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-[var(--muted)] p-2 text-xs"
        style={{ fontFamily: 'var(--font-mono)' }}
      >
        {'{ "region": "North", "orders": 1482 }'}
      </pre>

      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--border)] text-left">
            <th className="py-1 font-medium">{t('sidebar.previewColRegion')}</th>
            <th className="py-1 text-right font-medium">{t('sidebar.previewColOrders')}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="py-1">North</td>
            {/* tabular-nums so the digits line up column-wise, the same
                treatment real result tables get. */}
            <td
              className="py-1 text-right tabular-nums"
              style={{ fontFamily: 'var(--font-mono)' }}
            >
              1,482.50
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

/** The Appearance section's typography block: the UI face, the monospace
 * face, a live sample of both, and a reset. */
export function FontSettings() {
  const { t } = useTranslation()
  const font = useSettingsStore((state) => state.font)
  const monoFont = useSettingsStore((state) => state.monoFont)
  const resetFonts = useSettingsStore((state) => state.resetFonts)
  const isDefault = font === DEFAULT_UI_FONT_ID && monoFont === DEFAULT_MONO_FONT_ID

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        <label className="text-sm" htmlFor="app-font">
          {t('sidebar.appFont')}
        </label>
        <FontPicker />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-sm" htmlFor="code-font">
          {t('sidebar.codeFont')}
        </label>
        <MonoFontPicker />
      </div>

      <FontPreview />

      <Button
        variant="ghost"
        size="sm"
        className="self-start"
        onClick={resetFonts}
        // Disabled rather than hidden: a control that vanishes once used
        // gives no clue it was ever there, and "already at defaults" is
        // itself useful feedback.
        disabled={isDefault}
      >
        <RotateCcw className="h-3.5 w-3.5" />
        {t('sidebar.resetFonts')}
      </Button>
    </div>
  )
}
