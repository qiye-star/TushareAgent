import * as SwitchPrimitive from '@radix-ui/react-switch'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Props = {
  checked?: boolean
  onCheckedChange?: (v: boolean) => void
  label?: ReactNode
  disabled?: boolean
  className?: string
}

export function Switch({
  checked,
  onCheckedChange,
  label,
  disabled,
  className,
}: Props) {
  return (
    <label
      className={cn(
        'flex items-center gap-2.5 text-sm text-zinc-700 dark:text-zinc-300',
        disabled && 'opacity-60',
        className,
      )}
    >
      <SwitchPrimitive.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        className={cn(
          'relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full bg-zinc-300 transition-colors data-[state=checked]:bg-primary-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40',
          'disabled:cursor-not-allowed dark:bg-zinc-700',
        )}
      >
        <SwitchPrimitive.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white shadow-sm transition-transform data-[state=checked]:translate-x-[18px]" />
      </SwitchPrimitive.Root>
      {label && <span>{label}</span>}
    </label>
  )
}
