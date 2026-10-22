import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import {
  applyMonoFont,
  applyUiFont,
  DEFAULT_MONO_FONT_ID,
  DEFAULT_UI_FONT_ID,
  resolveMonoFont,
  resolveUiFont,
} from '@/lib/fonts'
import { applyAccent, applyThemeMode, type ThemeMode } from '@/lib/theme'

interface SettingsState {
  themeMode: ThemeMode
  accent: string
  /** UI font id from `UI_FONT_OPTIONS` (`--font-sans`). Still named `font`
   * rather than `uiFont` so preferences persisted by earlier builds under
   * this key keep working untouched. */
  font: string
  /** Monospace font id from `MONO_FONT_OPTIONS` (`--font-mono`) -- SQL,
   * code, JSON and logs. Independent of `font`: a user can keep a system
   * UI face and still read SQL in JetBrains Mono. */
  monoFont: string
  language: string
  /** Settings-section id -> collapsed. Missing id defaults to expanded. */
  collapsedSections: Record<string, boolean>
  /** User's own opt-in, independent of the server's `voice_enabled` infra
   * flag (`GET /health`) -- both must be true for the mic button/settings
   * row to actually appear. Defaults to true (voice mode costs nothing and
   * calls no external API, unlike media generation's approval gate) --
   * the user can still turn it off per-device via the settings toggle.
   * Persisted like theme/accent/font (a device preference), not
   * session-only like `chatStore`'s `enableInsight`. */
  voiceModeEnabled: boolean
  /** Desktop (`lg:`+) left-rail collapse preference -- the single source of
   * truth for "is the persistent sidebar a full list or an icon rail."
   * Deliberately NOT the same state as MobileNav's own `open` (whether the
   * off-canvas drawer overlay is showing) -- that's ephemeral per-visit
   * interaction state, not a preference, and always starts closed on a
   * fresh page load regardless of this value. Persisted like
   * theme/accent/font (`sidebarCollapsed=true`/`false` in localStorage) --
   * a non-sensitive UI preference only, never chat content. */
  sidebarCollapsed: boolean
  /** The user's own Ollama model choice, from the "AI Model" picker in
   * Settings (HistorySettingsSection.tsx) -- a model id from the most
   * recent GET /models response, or null to use the server-configured
   * default. Persisted like theme/accent/font (a device preference). Unlike
   * that flag, this is validated server-side on every /ask (see
   * agent.model_registry.validate_model_selection) -- if a model an earlier
   * session picked is no longer in the server's allowed set (a config
   * change since then), HistorySettingsSection.tsx resets this back to
   * null the next time GET /models loads, rather than leaving every /ask
   * failing with a 400 until the user notices and re-picks manually. */
  selectedModel: string | null
  /** Source name(s) this caller has narrowed questions to (from a
   * GET /sources response's sources[].name), or null for "no
   * restriction" -- the default, and what makes /ask behave exactly as it
   * did before the picker existed. Persisted like selectedModel (a device
   * preference).
   *
   * Purely an affordance. The server intersects whatever this sends with
   * what is configured AND what the caller's role permits, so a stale
   * value here can only narrow a route, never widen one -- and an
   * unhonorable selection comes back as
   * AskResponse.source_selection_notice rather than failing the request,
   * so this never needs defensive resetting the way selectedModel does. */
  selectedSources: string[] | null
  setThemeMode: (mode: ThemeMode) => void
  setAccent: (accentId: string) => void
  setFont: (fontId: string) => void
  setMonoFont: (fontId: string) => void
  /** Restores both font preferences to the shipped defaults. */
  resetFonts: () => void
  setLanguage: (language: string) => void
  setVoiceModeEnabled: (enabled: boolean) => void
  setSelectedModel: (modelId: string | null) => void
  setSelectedSources: (sources: string[] | null) => void
  toggleSection: (sectionId: string) => void
  isSectionCollapsed: (sectionId: string) => boolean
  toggleSidebarCollapsed: () => void
}

/** `localStorage` that degrades to "not saved" instead of throwing.
 *
 * Every accessor here can fail for reasons that have nothing to do with
 * this app: Safari private browsing and a blocked-site-data policy make
 * `setItem` throw `QuotaExceededError`, and reading can throw outright
 * where storage is disabled. Unwrapped, that exception propagates out of
 * `set()` through the `persist` middleware and out of whichever setter the
 * user just invoked -- so clicking a font, theme or accent would break the
 * control rather than quietly failing to persist.
 *
 * These are non-essential device preferences, so the right failure mode is
 * to apply the choice for this session and lose it on refresh. */
const safeLocalStorage: Storage = {
  getItem(key) {
    try {
      return localStorage.getItem(key)
    } catch {
      return null
    }
  },
  setItem(key, value) {
    try {
      localStorage.setItem(key, value)
    } catch {
      // Preference not persisted; it still applies for this session.
    }
  },
  removeItem(key) {
    try {
      localStorage.removeItem(key)
    } catch {
      // Nothing to do -- the entry is already unreachable.
    }
  },
  clear() {
    try {
      localStorage.clear()
    } catch {
      /* see above */
    }
  },
  key(index) {
    try {
      return localStorage.key(index)
    } catch {
      return null
    }
  },
  get length() {
    try {
      return localStorage.length
    } catch {
      return 0
    }
  },
}

/** Applies the persisted (or default) appearance to the DOM immediately --
 * called once at module load, before React even renders, so there's no
 * flash of the wrong theme/accent/font on refresh. */
function applyAppearance(
  state: Pick<SettingsState, 'themeMode' | 'accent' | 'font' | 'monoFont'>,
): void {
  applyThemeMode(state.themeMode)
  applyAccent(state.accent)
  applyUiFont(state.font)
  applyMonoFont(state.monoFont)
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      themeMode: 'light',
      accent: 'indigo',
      font: DEFAULT_UI_FONT_ID,
      monoFont: DEFAULT_MONO_FONT_ID,
      language: 'en',
      collapsedSections: {},
      voiceModeEnabled: true,
      sidebarCollapsed: false,
      selectedModel: null,
      selectedSources: null,
      setVoiceModeEnabled: (enabled) => set({ voiceModeEnabled: enabled }),
      setSelectedModel: (modelId) => set({ selectedModel: modelId }),
      // An empty array is normalized to null: "nothing selected" and "no
      // restriction" are the same thing to the user, and AskRequest.sources
      // rejects [] outright (it would be ambiguous server-side between
      // "unrestricted" and "no sources at all").
      setSelectedSources: (sources) =>
        set({ selectedSources: sources && sources.length > 0 ? sources : null }),
      toggleSidebarCollapsed: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setThemeMode: (mode) => {
        applyThemeMode(mode)
        set({ themeMode: mode })
      },
      setAccent: (accentId) => {
        applyAccent(accentId)
        set({ accent: accentId })
      },
      // Both setters normalize through the allowlist before storing, so an
      // unknown id can never be persisted in the first place -- the picker
      // and the DOM always agree on which option is selected.
      setFont: (fontId) => {
        const id = resolveUiFont(fontId).id
        applyUiFont(id)
        set({ font: id })
      },
      setMonoFont: (fontId) => {
        const id = resolveMonoFont(fontId).id
        applyMonoFont(id)
        set({ monoFont: id })
      },
      resetFonts: () => {
        applyUiFont(DEFAULT_UI_FONT_ID)
        applyMonoFont(DEFAULT_MONO_FONT_ID)
        set({ font: DEFAULT_UI_FONT_ID, monoFont: DEFAULT_MONO_FONT_ID })
      },
      setLanguage: (language) => set({ language }),
      toggleSection: (sectionId) =>
        set((state) => ({
          collapsedSections: {
            ...state.collapsedSections,
            [sectionId]: !state.collapsedSections[sectionId],
          },
        })),
      isSectionCollapsed: (sectionId) => Boolean(get().collapsedSections[sectionId]),
    }),
    {
      name: 'tsql-settings',
      storage: createJSONStorage(() => safeLocalStorage),
      onRehydrateStorage: () => (state) => {
        if (!state) return
        // localStorage is user-writable and survives across deploys, so a
        // persisted id may be hand-edited, corrupt, or simply a font an
        // older build offered and this one does not (the combined list's
        // UI-font 'jetbrains' is exactly that case). Coerce to the
        // allowlist *here*, not just at paint time, so the <select> shows a
        // real option rather than rendering blank against a stale value.
        state.font = resolveUiFont(state.font).id
        state.monoFont = resolveMonoFont(state.monoFont).id
        applyAppearance(state)
      },
    },
  ),
)

// Applies the default appearance immediately for a first-ever visit
// (before persisted state, if any, has finished rehydrating).
applyAppearance(useSettingsStore.getState())
