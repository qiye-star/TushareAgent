import * as DialogPrimitive from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function Dialog({
  open,
  onOpenChange,
  title,
  children,
  description,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-zinc-900/40 backdrop-blur-sm" />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[92%] max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-2xl border border-zinc-200 bg-white p-5 shadow-card',
            'dark:border-zinc-700 dark:bg-zinc-900',
          )}
        >
          <DialogPrimitive.Title className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
            {title}
          </DialogPrimitive.Title>
          {description && (
            <DialogPrimitive.Description className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              {description}
            </DialogPrimitive.Description>
          )}
          <div className="mt-4">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
