import { Select } from '@/components/ui/select'
import { MONO_FONT_OPTIONS, UI_FONT_OPTIONS } from '@/lib/fonts'
import { useSettingsStore } from '@/store/settingsStore'

/** The application (sans) font -- drives `--font-sans`.
 *
 * The accessible name comes from the visible `<label htmlFor>` in
 * `FontSettings`, not an `aria-label` here -- an `aria-label` would
 * silently override the label a sighted user is reading, so the two could
 * drift apart. `id` is a prop rather than a constant so a second instance
 * would not duplicate it. */
export function FontPicker({ id = 'app-font' }: { id?: string }) {
  const font = useSettingsStore((state) => state.font)
  const setFont = useSettingsStore((state) => state.setFont)

  return (
    <Select
      id={id}
      value={font}
      onChange={(event) => setFont(event.target.value)}
      className="w-full"
    >
      {UI_FONT_OPTIONS.map((option) => (
        <option key={option.id} value={option.id}>
          {option.label}
        </option>
      ))}
    </Select>
  )
}

/** The monospace font -- drives `--font-mono` (SQL, code, JSON, logs).
 * Named by `FontSettings`' visible label, as above. */
export function MonoFontPicker({ id = 'code-font' }: { id?: string }) {
  const monoFont = useSettingsStore((state) => state.monoFont)
  const setMonoFont = useSettingsStore((state) => state.setMonoFont)

  return (
    <Select
      id={id}
      value={monoFont}
      onChange={(event) => setMonoFont(event.target.value)}
      className="w-full"
    >
      {MONO_FONT_OPTIONS.map((option) => (
        <option key={option.id} value={option.id}>
          {option.label}
        </option>
      ))}
    </Select>
  )
}
