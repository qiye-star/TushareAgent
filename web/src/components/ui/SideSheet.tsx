import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { IconButton } from './IconButton'

/**
 * 右侧抽屉（Side sheet）：基于 Radix Dialog 使右锚定滑入。
 * 参照 Dialog.tsx 的配色/尺寸约定，保持一致性。
 */
export function SideSheet({
  open,
  onOpenChange,
  title,
  children,
  description,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  className?: string
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-zinc-900/40 backdrop-blur-sm" />
        <DialogPrimitive.Content
          className={cn(
            'fixed inset-y-0 right-0 z-50 flex w-[min(24rem,100%)] max-w-md flex-col',
            'border-l border-zinc-200 bg-white shadow-card',
            'translate-x-full transition-transform duration-300 ease-out data-[state=open]:translate-x-0',
            'dark:border-zinc-800 dark:bg-zinc-950',
            className,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-zinc-100 px-5 py-4 dark:border-zinc-800">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-1 truncate text-sm text-zinc-500 dark:text-zinc-400">
                  {description}
                </DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close asChild>
              <IconButton label="关闭" className="-mr-1.5 -mt-1">
                <X size={16} />
              </IconButton>
            </DialogPrimitive.Close>
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
