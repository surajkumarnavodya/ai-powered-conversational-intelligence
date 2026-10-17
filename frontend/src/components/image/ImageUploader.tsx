import { Plus } from 'lucide-react'
import { useRef, type ChangeEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

/** Every extension attachments/validation.py's EXTENSION_TO_MEDIA_TYPE
 * allowlist accepts -- kept in sync with that server-side allowlist since
 * this is a UX convenience filter only (the real validation is server-side,
 * same "not a security boundary" posture frontend/src/lib/imageValidation.ts
 * already documents for images specifically). */
const ACCEPTED_EXTENSIONS =
  '.png,.jpg,.jpeg,.webp,.gif,.pdf,.txt,.md,.markdown,.json,.csv,.docx,.xlsx,.pptx'

/** The file-picker half of chat attachments -- a plain icon button + a
 * hidden `<input type="file">`. Drag-and-drop and clipboard paste are
 * wired directly onto ChatInput's own container instead of duplicated
 * here (they need to work anywhere in the composer, not just on this one
 * button), calling the same `onFilesSelected` callback this component
 * uses -- see ChatInput.tsx's onDrop/onPaste handlers. */
export function ImageUploader({
  onFilesSelected,
  disabled,
}: {
  onFilesSelected: (files: FileList) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement>(null)

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files && event.target.files.length > 0) {
      onFilesSelected(event.target.files)
    }
    // Reset so selecting the exact same file again still fires onChange.
    event.target.value = ''
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_EXTENSIONS}
        multiple
        onChange={handleChange}
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
      />
      {/* Borderless, like the other composer controls -- the composer's
          own rounded container is the visible frame, so a border per
          button only boxed them up inside it. The accessible name stays
          "attach", which is what the control does whatever its icon.
          The hover hint is the design system's Tooltip rather than a
          native `title`: a bare `+` needs a label that also appears on
          keyboard focus, which `title` does not reliably give. There is
          deliberately no `title` alongside it -- the two would stack up
          as two hints for the same control. */}
      <TooltipProvider delayDuration={300}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => inputRef.current?.click()}
              disabled={disabled}
              aria-label={t('attachments.attach')}
              className="rounded-xl"
            >
              <Plus className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">{t('attachments.attach')}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </>
  )
}
