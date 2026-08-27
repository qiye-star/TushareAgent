import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  trigger: ReactNode
  children: ReactNode
  className?: string
  contentClassName?: string
}

/** Lightweight animated collapsible using the grid-rows 0fr→1fr technique. */
export function Collapsible({
  open,
  onOpenChange,
  trigger,
  children,
  className,
  contentClassName,
}: Props) {
  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        className="w-full text-left"
      >
        {trigger}
      </button>
      <div
        className={cn(
          'grid transition-[grid-template-rows] duration-200 ease-out',
          open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]',
        )}
      >
        <div className={cn('min-h-0 overflow-hidden', contentClassName)}>
          {children}
        </div>
      </div>
    </div>
  )
}
