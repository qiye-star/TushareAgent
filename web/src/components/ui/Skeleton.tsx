import { cn } from '@/lib/cn'

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-md bg-zinc-200/80 dark:bg-zinc-800',
        className,
      )}
    />
  )
}
