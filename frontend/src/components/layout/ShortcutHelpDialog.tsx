import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SHORTCUTS, formatShortcut } from '@/lib/shortcuts'

/** The discoverable list of global keyboard shortcuts, opened with `?`.
 *
 * Rendered from `SHORTCUTS` rather than from a hand-written list, so a
 * binding cannot work while going undocumented or be documented while
 * doing nothing -- the same single-owner rule the rest of this codebase
 * applies to a fact stated in two places (`CLAUDE.md` 4.8).
 *
 * A shortcut nobody can find is close to a shortcut that does not exist,
 * which is the half of feature 015 the app was missing: the component-
 * level handlers (Enter in the composer, arrows in the question
 * navigator) all worked and none of them was written down anywhere a user
 * would look.
 */
export function ShortcutHelpDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('shortcuts.title')}</DialogTitle>
          <DialogDescription>{t('shortcuts.hint')}</DialogDescription>
        </DialogHeader>
        <ul className="flex flex-col gap-1.5">
          {SHORTCUTS.map((shortcut) => (
            <li key={shortcut.action} className="flex items-center justify-between gap-4 text-sm">
              <span>{t(shortcut.labelKey)}</span>
              {/* <kbd> rather than a styled span: it is the element that
                  means "keyboard input", so a screen reader announces it
                  as such. */}
              <kbd className="shrink-0 rounded border border-[var(--border)] bg-[var(--muted)] px-1.5 py-0.5 font-mono text-xs text-[var(--muted-foreground)]">
                {formatShortcut(shortcut)}
              </kbd>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  )
}
