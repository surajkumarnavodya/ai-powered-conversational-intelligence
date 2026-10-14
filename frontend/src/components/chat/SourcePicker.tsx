import { Database, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useSources } from '@/hooks/queries'
import { sanitizeLabel } from '@/lib/analyticsCharts'
import type { SourceOut } from '@/lib/types'
import { useSettingsStore } from '@/store/settingsStore'

/**
 * Restricts the next question to specific data source(s).
 *
 * The list comes from `GET /sources`, which reports every registered
 * source along with `configured` and `authorized` separately. Both are
 * shown: a source the caller cannot use appears disabled **with a
 * reason**, rather than being silently omitted, so "why can't I pick
 * SharePoint" has a visible answer. That is the same decision
 * `GET /navigation` makes for screens.
 *
 * This is an affordance, not a boundary. The server intersects whatever
 * this sends with what is configured and what the caller's role permits
 * (`agent/orchestrator/nodes.py::_apply_source_selection`), so a stale or
 * tampered selection can only narrow a route, never widen one. Nothing
 * here is relied on for access control.
 *
 * Selecting nothing means "no restriction" -- the store normalizes an
 * empty selection to null and `AskRequest.sources` is then omitted
 * entirely, so a user who never opens this sends exactly the request the
 * app sent before the picker existed.
 */
export function SourcePicker({ disabled = false }: { disabled?: boolean }) {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useSources()
  const selected = useSettingsStore((state) => state.selectedSources)
  const setSelected = useSettingsStore((state) => state.setSelectedSources)

  // A source is pickable only when configuration and permission both
  // allow it. `per_request` sources (attachments) are excluded: their
  // availability depends on whether a file is attached to that individual
  // question, so offering them as a standing choice would be misleading --
  // the server keeps an attached file in the route regardless.
  const pickable = (data?.sources ?? []).filter(
    (source) => !source.per_request && source.configured && source.authorized,
  )
  const blocked = (data?.sources ?? []).filter(
    (source) => !source.per_request && !(source.configured && source.authorized),
  )

  const activeCount = selected?.length ?? 0

  const toggle = (name: string) => {
    const next = selected?.includes(name)
      ? selected.filter((item) => item !== name)
      : [...(selected ?? []), name]
    setSelected(next)
  }

  const reasonFor = (source: SourceOut) =>
    !source.configured ? t('sources.notConfigured') : t('sources.notPermitted')

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="secondary"
          size="icon"
          disabled={disabled}
          aria-label={t('sources.pickerLabel')}
          title={
            activeCount > 0
              ? t('sources.activeCount', { count: activeCount })
              : t('sources.pickerLabel')
          }
          className="relative rounded-xl"
        >
          <Database className="h-4 w-4" />
          {activeCount > 0 && (
            <span
              // Decorative: the count is already in the button's own
              // accessible name via `aria-label`/`title` above, so this
              // badge is hidden from assistive tech rather than read twice.
              aria-hidden="true"
              className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--accent)] px-1 text-[10px] font-semibold text-[var(--accent-foreground)]"
            >
              {activeCount}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72">
        <p className="text-sm font-semibold">{t('sources.pickerTitle')}</p>
        <p className="mt-1 text-xs text-[var(--muted-foreground)]">
          {t('sources.pickerHelp')}
        </p>

        {isLoading && (
          <p className="mt-3 flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t('common.loading')}
          </p>
        )}

        {isError && (
          <p className="mt-3 text-sm text-[var(--warning)]">{t('sources.loadFailed')}</p>
        )}

        {!isLoading && !isError && pickable.length === 0 && (
          <p className="mt-3 text-sm text-[var(--muted-foreground)]">
            {t('sources.noneAvailable')}
          </p>
        )}

        {pickable.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1">
            {pickable.map((source) => {
              const isOn = selected?.includes(source.name) ?? false
              return (
                <li key={source.name}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-[var(--muted)]">
                    <input
                      type="checkbox"
                      checked={isOn}
                      onChange={() => toggle(source.name)}
                      className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                    />
                    <span className="min-w-0">
                      {/* Server-derived string -- sanitized before display,
                          per the frontend convention for any value that
                          originates outside this app. */}
                      <span className="font-medium">{sanitizeLabel(source.name)}</span>
                      <span className="block truncate text-xs text-[var(--muted-foreground)]">
                        {sanitizeLabel(source.kind)}
                      </span>
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
        )}

        {activeCount > 0 && (
          <Button
            variant="ghost"
            className="mt-2 h-auto w-full justify-start px-1.5 py-1 text-xs"
            onClick={() => setSelected(null)}
          >
            {t('sources.clearSelection')}
          </Button>
        )}

        {blocked.length > 0 && (
          <div className="mt-3 border-t border-[var(--border)] pt-2">
            <p className="text-xs font-medium text-[var(--muted-foreground)]">
              {t('sources.unavailableHeading')}
            </p>
            <ul className="mt-1 flex flex-col gap-0.5">
              {blocked.map((source) => (
                <li
                  key={source.name}
                  className="flex items-baseline justify-between gap-2 px-1.5 text-xs text-[var(--muted-foreground)]"
                >
                  <span className="truncate">{sanitizeLabel(source.name)}</span>
                  <span className="shrink-0">{reasonFor(source)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
