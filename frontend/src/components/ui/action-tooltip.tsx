import type { ReactNode } from 'react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

/** Wraps a single icon-only control in the design system's hover/focus
 * tooltip.
 *
 * It exists because the answer-action row went icon-only: once the visible
 * text is gone, every one of those controls needs the same
 * provider + trigger + content scaffolding, and six copies of it would be
 * six chances to drift on `delayDuration`, `side`, or to forget the
 * keyboard half entirely.
 *
 * **The tooltip is a hint, not the accessible name.** The child keeps its
 * own `aria-label` -- Radix wires this content as `aria-describedby`, which
 * describes a control that must already have a name. That split also lets
 * the two differ where it helps: `SaveArtifactButton`'s name names the
 * question it pins, while its tooltip explains whether the pin is durable
 * or session-only.
 *
 * **No `title` alongside it.** A native `title` plus this would stack two
 * hints for the same control, and `title` is the one that does not
 * reliably appear on keyboard focus -- which is the whole reason this
 * wrapper exists rather than a `title` attribute.
 */
export function ActionTooltip({
  label,
  side = 'top',
  children,
}: {
  label: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  children: ReactNode
}) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent side={side}>{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
