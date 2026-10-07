import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import { cn } from '@/lib/utils'

// @radix-ui/react-tooltip is already an installed dependency (package.json)
// but had no reusable wrapper in this design system yet -- this is that
// wrapper, not a new dependency. Styling mirrors ui/popover.tsx exactly
// (same radius/border/surface/shadow tokens), so a tooltip reads as the
// same design language as every other floating surface in the app.
//
// Radix's Tooltip already covers mouse hover *and* keyboard focus (a
// focused trigger shows the tooltip via the same Content, dismissed on
// Escape/blur) and wires the accessible description (`aria-describedby`)
// itself -- exactly what a hand-rolled `title` attribute does not reliably
// give on keyboard focus in most browsers.

export const TooltipProvider = TooltipPrimitive.Provider
export const Tooltip = TooltipPrimitive.Root
export const TooltipTrigger = TooltipPrimitive.Trigger

export function TooltipContent({ className, sideOffset = 6, ...props }: TooltipPrimitive.TooltipContentProps) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        sideOffset={sideOffset}
        className={cn(
          'z-50 max-w-xs rounded-lg border border-[var(--border)] bg-[var(--popover-surface)] px-2.5 py-1.5 text-xs text-[var(--card-foreground)] shadow-lg',
          className,
        )}
        {...props}
      />
    </TooltipPrimitive.Portal>
  )
}
