const THEME_KEY = 'agent-workbench:theme'
const SESSION_TITLES_KEY = 'agent-workbench:session-titles'
const TOOL_STATE_KEY = 'agent-workbench:tool-switches'

export type Theme = 'light' | 'dark'

export function getStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    /* ignore quota/private-mode errors */
  }
}

// --- client-side session titles (rename is a local concern; backend has no field) ---
export type SessionTitles = Record<string, string>

export function getSessionTitles(): SessionTitles {
  try {
    return JSON.parse(localStorage.getItem(SESSION_TITLES_KEY) || '{}')
  } catch {
    return {}
  }
}

export function setSessionTitle(sessionId: string, title: string): SessionTitles {
  const map = getSessionTitles()
  if (title) map[sessionId] = title
  else delete map[sessionId]
  try {
    localStorage.setItem(SESSION_TITLES_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
  return map
}

// --- available-tools switches (UI state only; not wired to the backend) ---
export type ToolSwitches = Record<string, boolean>

export function getToolSwitches(): ToolSwitches {
  try {
    return JSON.parse(localStorage.getItem(TOOL_STATE_KEY) || '{}')
  } catch {
    return {}
  }
}

export function setToolSwitch(id: string, on: boolean): ToolSwitches {
  const map = getToolSwitches()
  map[id] = on
  try {
    localStorage.setItem(TOOL_STATE_KEY, JSON.stringify(map))
  } catch {
    /* ignore */
  }
  return map
}
