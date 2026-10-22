import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ActionTooltip } from '@/components/ui/action-tooltip'
import { Button } from '@/components/ui/button'
import { copyMarkdownToClipboard } from '@/lib/clipboard'

/** Icon-only, with the label on hover and on keyboard focus.
 *
 * The copied-confirmation still has to be *visible*, not just audible --
 * losing the text label must not cost the "it worked" feedback. So the
 * check icon swaps in for 1.5s as before, and the label the tooltip and
 * the accessible name both report changes with it, which means a screen
 * reader hears the confirmation too rather than only sighted users seeing
 * a glyph change.
 */
export function CopyAnswerButton({ answer }: { answer: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await copyMarkdownToClipboard(answer)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const label = copied ? t('common.copied') : t('common.copyAnswer')

  return (
    <ActionTooltip label={label}>
      <Button size="icon" variant="ghost" aria-label={label} onClick={() => void handleCopy()}>
        {copied ? <Check className="h-4 w-4 text-[var(--success)]" /> : <Copy className="h-4 w-4" />}
      </Button>
    </ActionTooltip>
  )
}
