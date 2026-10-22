import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu'
import { cn } from '@/lib/utils'

export const DropdownMenu = DropdownMenuPrimitive.Root
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger

export function DropdownMenuContent({ className, ...props }: DropdownMenuPrimitive.DropdownMenuContentProps) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        sideOffset={4}
        align="end"
        className={cn(
          'z-50 min-w-36 rounded-lg border border-[var(--border)] bg-[var(--popover-surface)] text-[var(--card-foreground)] p-1 shadow-lg outline-none',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  )
}

export function DropdownMenuItem({ className, ...props }: DropdownMenuPrimitive.DropdownMenuItemProps) {
  return (
    <DropdownMenuPrimitive.Item
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none transition-colors',
        // Radix sets `data-highlighted` for both pointer hover and keyboard
        // navigation, and this used to be a background swap alone -- which
        // measured 1.10:1 in light and 1.06:1 in dark against the popover
        // surface. As a hover affordance that is fine; as the *only* signal
        // of where keyboard focus is, it is close to invisible. The inset
        // ring is the part a keyboard user can actually see: --focus-ring
        // is held to 3:1 on this surface (WCAG 1.4.11), where --muted is
        // not and cannot be without becoming a heavy block of color.
        'data-[highlighted]:bg-[var(--muted)] data-[highlighted]:text-[var(--foreground)]',
        'data-[highlighted]:ring-1 data-[highlighted]:ring-inset data-[highlighted]:ring-[var(--focus-ring)]',
        className,
      )}
      {...props}
    />
  )
}

export function DropdownMenuSeparator({ className, ...props }: DropdownMenuPrimitive.DropdownMenuSeparatorProps) {
  return (
    <DropdownMenuPrimitive.Separator
      className={cn('my-1 h-px bg-[var(--border)]', className)}
      {...props}
    />
  )
}

export function DropdownMenuLabel({ className, ...props }: DropdownMenuPrimitive.DropdownMenuLabelProps) {
  return (
    <DropdownMenuPrimitive.Label
      className={cn('px-2 py-1.5 text-xs font-medium text-[var(--muted-foreground)]', className)}
      {...props}
    />
  )
}
