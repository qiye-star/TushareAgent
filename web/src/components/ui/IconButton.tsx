import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string
  active?: boolean
  children: ReactNode
}

export const IconButton = forwardRef<HTMLButtonElement, Props>(
  ({ label, active, className, children, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 active:scale-95',
        'dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100',
        active &&
          'bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  ),
)
IconButton.displayName = 'IconButton'
