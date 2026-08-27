import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function DropdownMenu({
  trigger,
  children,
  align = 'end',
  className,
}: {
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'center' | 'end'
  className?: string
}) {
  return (
    <DropdownMenuPrimitive.Root>
      <DropdownMenuPrimitive.Trigger asChild>
        {trigger}
      </DropdownMenuPrimitive.Trigger>
      <DropdownMenuPrimitive.Portal>
        <DropdownMenuPrimitive.Content
          align={align}
          sideOffset={6}
          className={cn(
            'z-50 min-w-[8.5rem] rounded-xl border border-zinc-200 bg-white p-1 shadow-card dark:border-zinc-700 dark:bg-zinc-900',
            className,
          )}
        >
          {children}
        </DropdownMenuPrimitive.Content>
      </DropdownMenuPrimitive.Portal>
    </DropdownMenuPrimitive.Root>
  )
}

export function MenuItem({
  children,
  onSelect,
  destructive,
  disabled,
}: {
  children: ReactNode
  onSelect?: () => void
  destructive?: boolean
  disabled?: boolean
}) {
  return (
    <DropdownMenuPrimitive.Item
      onSelect={onSelect}
      disabled={disabled}
      className={cn(
        'cursor-pointer rounded-lg px-3 py-2 text-sm text-zinc-700 outline-none transition-colors data-[highlighted]:bg-zinc-100 data-[disabled]:opacity-50 dark:text-zinc-200 dark:data-[highlighted]:bg-zinc-800',
        destructive &&
          'text-rose-600 data-[highlighted]:bg-rose-50 dark:text-rose-400 dark:data-[highlighted]:bg-rose-500/10',
      )}
    >
      {children}
    </DropdownMenuPrimitive.Item>
  )
}
