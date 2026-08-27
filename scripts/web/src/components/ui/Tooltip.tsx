import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={200}>{children}</TooltipPrimitive.Provider>
  )
}

type TooltipProps = {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'right' | 'bottom' | 'left'
  className?: string
}

export function Tooltip({ content, children, side = 'top', className }: TooltipProps) {
  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className={cn(
            'z-50 max-w-[15rem] rounded-lg bg-zinc-900 px-2.5 py-1.5 text-xs leading-snug text-white shadow-md',
            'dark:bg-zinc-100 dark:text-zinc-900',
            className,
          )}
        >
          {content}
          <TooltipPrimitive.Arrow className="fill-zinc-900 dark:fill-zinc-100" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  )
}
