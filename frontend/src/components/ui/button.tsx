import { cva, type VariantProps } from 'class-variance-authority'
import { type ButtonHTMLAttributes, forwardRef } from 'react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] disabled:pointer-events-none disabled:opacity-50 cursor-pointer',
  {
    variants: {
      variant: {
        primary: 'bg-[var(--accent)] text-[var(--accent-foreground)] hover:opacity-90 shadow-sm',
        secondary:
          'bg-transparent border border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--muted)]',
        ghost: 'bg-transparent text-[var(--foreground)] hover:bg-[var(--muted)]',
        // --danger-strong, not --danger: this is the only solid status
        // fill in the app, and in dark mode --danger lightens so error
        // *text* stays legible on a near-black page, which would drop this
        // white label to 3.76:1. See index.css's token comments.
        danger: 'bg-[var(--danger-strong)] text-white hover:opacity-90',
      },
      size: {
        sm: 'h-8 px-2.5 text-xs',
        md: 'h-9 px-4',
        // Larger hit area on touch-sized viewports, back to the compact
        // 32px box from `sm` up where a mouse is doing the aiming. One
        // owner for every icon button in the app, rather than each
        // toolbar deciding its own thumb-friendliness.
        icon: 'h-9 w-9 sm:h-8 sm:w-8',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
)
Button.displayName = 'Button'
