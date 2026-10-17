import { ChevronDown, ChevronUp } from 'lucide-react'
import { useLayoutEffect, useRef, useState, type ClipboardEvent, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

const clampStyle = (lines: number): CSSProperties => ({
  display: '-webkit-box',
  WebkitLineClamp: lines,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
})

/** The copied text as an *inline* HTML fragment.
 *
 * A browser's own copy puts two flavors on the clipboard, and only the
 * plain-text one is clean here: the HTML one is the selected subtree,
 * which for this component is one or two block-level `<p>` elements (the
 * visible paragraph plus, when the selection covers the whole box, the
 * invisible measuring copy below). A paste target that prefers the HTML
 * flavor turns a block into a paragraph -- which is where the stray blank
 * line after a pasted question came from. A `<span>` is inline, so it
 * pastes as exactly the characters that were selected.
 */
function asInlineHtml(text: string): string {
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return `<span>${escaped.replace(/\r?\n/g, '<br>')}</span>`
}

/** Clamps long text to `maxLines`, with a bottom fade and a "Show more"
 * toggle when it actually overflows -- the ChatGPT/Perplexity treatment
 * for a long pasted question, so one long turn doesn't push everything
 * else down the page by default.
 *
 * Overflow is measured on a separate, always-clamped, invisible copy of
 * the text (absolutely positioned out of the layout flow) rather than on
 * the visible paragraph itself -- the visible one stops being clamped
 * once expanded, at which point measuring *it* for overflow would always
 * read "not overflowing" and make the toggle disappear right when it's
 * showing the expanded state. Re-measures on window resize so the
 * decision stays correct across breakpoints/zoom levels.
 */
export function ExpandableText({
  text,
  maxLines = 5,
  className,
  textClassName,
  fadeToColor,
  align = 'left',
}: {
  text: string
  maxLines?: number
  className?: string
  textClassName?: string
  /** CSS color (e.g. `var(--accent)` or `var(--card)`) the bottom fade
   * blends into -- should match whatever background this sits on. */
  fadeToColor: string
  align?: 'left' | 'center'
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const measureRef = useRef<HTMLParagraphElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  /** Copies exactly the selected characters, in both clipboard flavors.
   *
   * Left to the browser, copying a question out of its bubble pastes with
   * a trailing blank line (see asInlineHtml). Only a selection that lies
   * entirely inside this component is rewritten -- a selection that runs
   * on into the rest of the turn is left to the browser, since replacing
   * it with just this component's text would silently drop the rest of
   * what the user had highlighted. */
  const handleCopy = (event: ClipboardEvent<HTMLDivElement>) => {
    const root = rootRef.current
    const selection = window.getSelection()
    if (!root || !selection || selection.rangeCount === 0) return
    if (!root.contains(selection.getRangeAt(0).commonAncestorContainer)) return

    // `toString()` already excludes the invisible measuring paragraph --
    // it is `visibility: hidden`, which selection skips.
    const selected = selection.toString().trim()
    if (!selected) return

    event.clipboardData.setData('text/plain', selected)
    event.clipboardData.setData('text/html', asInlineHtml(selected))
    event.preventDefault()
  }

  useLayoutEffect(() => {
    const el = measureRef.current
    if (!el) return
    const check = () => setOverflowing(el.scrollHeight > el.clientHeight + 1)
    check()
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [text, maxLines])

  return (
    <div ref={rootRef} className={className} onCopy={handleCopy}>
      <div className="relative">
        <p
          ref={measureRef}
          aria-hidden="true"
          /* `select-none` keeps this duplicate out of a selection that
             spans several turns, where handleCopy stands aside and the
             browser serializes the subtree itself. */
          className={cn(
            'pointer-events-none invisible absolute inset-x-0 top-0 -z-10 select-none whitespace-pre-wrap',
            textClassName,
          )}
          style={clampStyle(maxLines)}
        >
          {text}
        </p>
        <p
          className={cn('whitespace-pre-wrap', textClassName)}
          style={!expanded ? clampStyle(maxLines) : undefined}
        >
          {text}
        </p>
        {!expanded && overflowing && (
          <div
            className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t to-transparent"
            style={{ backgroundImage: `linear-gradient(to top, ${fadeToColor}, transparent)` }}
          />
        )}
      </div>
      {overflowing && (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className={cn(
            'mt-1 flex items-center gap-1 text-xs font-medium opacity-90 hover:underline hover:opacity-100',
            align === 'center' && 'mx-auto',
          )}
        >
          {expanded ? t('common.showLess') : t('common.showMore')}
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
      )}
    </div>
  )
}
