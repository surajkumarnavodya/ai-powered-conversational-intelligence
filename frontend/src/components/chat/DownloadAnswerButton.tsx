import { FileDown, FileText, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ActionTooltip } from '@/components/ui/action-tooltip'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { answerFilenameStem, buildMarkdownDocument, downloadTextFile } from '@/lib/markdownFile'
import { downloadMarkdownAsPdf } from '@/lib/pdf'

/** Saves this answer to a file, as markdown or as PDF.
 *
 * This was a single button that produced only a PDF. Markdown was added
 * beside it rather than replacing it, because the two formats answer
 * different needs -- a PDF is for sending to someone, a `.md` is for
 * editing or committing -- and the PDF path is the one already covered by
 * `lib/pdf.tsx`'s careful dark-mode handling.
 *
 * The markdown path is synchronous and cannot fail in a way worth showing,
 * so only the PDF path drives the spinner: `jspdf` and `html2canvas-pro`
 * are dynamically imported and the rasterisation genuinely takes a moment.
 */
export function DownloadAnswerButton({ question, answer }: { question: string; answer: string }) {
  const { t } = useTranslation()
  const [isGenerating, setIsGenerating] = useState(false)

  const handleMarkdown = () => {
    downloadTextFile(
      `${answerFilenameStem(question)}.md`,
      buildMarkdownDocument(question, answer),
      // `charset=utf-8` matters: an answer routinely carries currency
      // symbols, accented names and the en dashes the answer prompt asks
      // for, and a consumer that assumes a byte encoding mangles them.
      'text/markdown;charset=utf-8',
    )
  }

  const handlePdf = async () => {
    setIsGenerating(true)
    try {
      await downloadMarkdownAsPdf(question, answer, `${answerFilenameStem(question)}.pdf`)
    } finally {
      setIsGenerating(false)
    }
  }

  return (
    <DropdownMenu>
      {/* Three triggers nested on one button, each `asChild`: the tooltip
          names it on hover/focus, the dropdown opens the format menu, and
          the Button is the single rendered element. The menu items keep
          their own text -- going icon-only applies to the toolbar, not to
          a menu where "Markdown" and "PDF" are the whole choice. */}
      <ActionTooltip label={t('common.downloadAnswer')}>
        <DropdownMenuTrigger asChild>
          <Button
            size="icon"
            variant="ghost"
            disabled={isGenerating}
            aria-label={t('common.downloadAnswer')}
          >
            {isGenerating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FileDown className="h-4 w-4" />
            )}
          </Button>
        </DropdownMenuTrigger>
      </ActionTooltip>
      <DropdownMenuContent>
        <DropdownMenuItem onSelect={handleMarkdown}>
          <FileText className="h-3.5 w-3.5" />
          {t('common.downloadMarkdown')}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void handlePdf()}>
          <FileDown className="h-3.5 w-3.5" />
          {t('common.downloadPdf')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
