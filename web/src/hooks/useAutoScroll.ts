import { useEffect, useRef } from 'react'

/**
 * Keep the scroll container pinned to the bottom while new content streams,
 * but only if the user is already near the bottom (so they can scroll up to
 * read without being yanked down).
 */
export function useAutoScroll<T extends HTMLElement>(dep: unknown) {
  const ref = useRef<T>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [dep])

  return ref
}
