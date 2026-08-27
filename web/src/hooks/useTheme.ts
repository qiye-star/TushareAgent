import { useCallback, useEffect, useState } from 'react'
import { getStoredTheme, storeTheme, type Theme } from '@/lib/storage'

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = getStoredTheme()
    if (stored) return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    storeTheme(theme)
  }, [theme])

  const toggle = useCallback(
    () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')),
    [],
  )

  return { theme, toggle, setTheme }
}
