import { Check, ChevronDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useAvailableModels } from '@/hooks/queries'
import { useSettingsStore } from '@/store/settingsStore'

/** The composer's own compact model picker -- the small "which model is
 * answering" control that sits under the chat box, the way a general
 * assistant UI puts it there rather than only in a settings panel.
 *
 * **Not a second owner of the selection.** It reads and writes exactly the
 * same `useSettingsStore.selectedModel` that
 * `components/settings/ModelSelector.tsx` does, over exactly the same
 * `GET /models` data (`useAvailableModels` ->
 * `agent.model_registry.build_model_options`), so the two controls are two
 * views of one value and cannot disagree: change it here and the Settings
 * picker already shows the change, and vice versa. The settings panel
 * keeps the fuller presentation (install badges, parameter size, refresh,
 * the pull hint); this is deliberately just the choice itself, because a
 * composer toolbar is not where that detail belongs.
 *
 * `null` in the store means "use the server default", which is why
 * selecting the default option clears it rather than pinning the default
 * model's id -- that is the pre-existing contract `ModelSelector`
 * established and `chatStore` relies on when it decides whether to send
 * `AskRequest.model` at all.
 *
 * A convenience, never a boundary: whatever this sends is re-validated
 * server-side against `Settings.ollama_allowed_models` on every `/ask`
 * (`agent.model_registry.validate_model_selection`). A model that is
 * configured but not installed is rendered disabled here for the same
 * reason it is in Settings -- it is a legitimate choice the operator has
 * not finished pulling, so it is shown and refused rather than hidden.
 *
 * Renders nothing at all when the server has model selection switched off
 * (`Settings.ollama_model_selection_enabled=false`), when no models are
 * configured, or while the query is loading/failed -- the composer is
 * fully usable without it, since the graph just uses the server default.
 */
export function ComposerModelPicker({ disabled = false }: { disabled?: boolean }) {
  const { t } = useTranslation()
  const { data, isError } = useAvailableModels()
  const selectedModel = useSettingsStore((state) => state.selectedModel)
  const setSelectedModel = useSettingsStore((state) => state.setSelectedModel)

  // Same silent-absence rule as `ModelSelector`: this is an optional
  // convenience, so nothing here is ever an error the composer surfaces.
  if (isError || !data || !data.selection_enabled || data.models.length === 0) {
    return null
  }

  const effectiveValue = selectedModel ?? data.default_model
  const selectedOption = data.models.find((model) => model.id === effectiveValue)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={t('sidebar.aiModel')}
          title={t('sidebar.aiModel')}
          className="flex max-w-[12rem] items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] disabled:pointer-events-none disabled:opacity-50"
        >
          <span className="truncate">{selectedOption?.display_name ?? effectiveValue}</span>
          <ChevronDown className="h-3 w-3 shrink-0" aria-hidden="true" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        {data.models.map((model) => (
          <DropdownMenuItem
            key={model.id}
            disabled={!model.installed}
            onSelect={() =>
              setSelectedModel(model.id === data.default_model ? null : model.id)
            }
          >
            {/* Fixed-width tick column so the labels stay aligned whether
                or not a row is the selected one. */}
            <Check
              className={`h-3.5 w-3.5 shrink-0 ${model.id === effectiveValue ? '' : 'invisible'}`}
              aria-hidden="true"
            />
            <span className="truncate">{model.display_name}</span>
            {model.is_default && (
              <span className="text-[var(--muted-foreground)]">({t('sidebar.modelDefault')})</span>
            )}
            {!model.installed && (
              <span className="text-[var(--muted-foreground)]">
                — {t('sidebar.modelNotInstalled')}
              </span>
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
