import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from '@phosphor-icons/react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { IconButton } from './IconButton'

/**
 * 居中弹窗。
 * - size='md'（默认）：原有紧凑确认框，现有调用点行为不变。
 * - size='wide'：宽高固定的内容容器，正文区独立滚动；现嵌套在 SideSheet（引用来源抽屉）内部弹出
 *   （点击来源卡 → 弹「数据」），层级显式提到 z-45/z-55，稳定叠在抽屉（z-40/z-50）之上，不依赖挂载顺序。
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  children,
  description,
  size = 'md',
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  size?: 'md' | 'wide'
}) {
  const wide = size === 'wide'

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            'fixed inset-0 bg-zinc-900/40 backdrop-blur-sm',
            wide ? 'z-[45]' : 'z-40',
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2',
            'rounded-2xl border border-zinc-200 bg-white shadow-card',
            'dark:border-zinc-700 dark:bg-zinc-900',
            wide ? 'z-[55]' : 'z-50',
            wide
              ? 'flex h-[85vh] w-[min(72rem,92vw)] max-w-none flex-col'
              : 'w-[92%] max-w-md p-5',
          )}
        >
          {wide ? (
            <>
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
            </>
          ) : (
            <>
              <DialogPrimitive.Title className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                {title}
              </DialogPrimitive.Title>
              {description && (
                <DialogPrimitive.Description className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
                  {description}
                </DialogPrimitive.Description>
              )}
              <div className="mt-4">{children}</div>
            </>
          )}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
