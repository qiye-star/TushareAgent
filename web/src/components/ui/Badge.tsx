import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Tone = 'neutral' | 'primary' | 'success' | 'error'

const tones: Record<Tone, string> = {
  neutral:
    'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300',
  primary:
    'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300',
  success:
    'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  error:
    'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300',
}

export function Badge({
  children,
  tone = 'neutral',
  className,
}: {
  children: ReactNode
  tone?: Tone
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium leading-4',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
