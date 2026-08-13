export type AuthSession = {
  csrfToken: string
  expiresAt: string
}

const sessionKey = 'flight-web.csrf-session'

const canUseStorage = (): boolean => typeof window !== 'undefined' && 'sessionStorage' in window

const clearStoredSession = (): void => {
  if (canUseStorage()) window.sessionStorage.removeItem(sessionKey)
}

export const sessionStore = {
  read(): AuthSession | null {
    if (!canUseStorage()) return null
    try {
      const raw = window.sessionStorage.getItem(sessionKey)
      if (!raw) return null
      const parsed: unknown = JSON.parse(raw)
      if (!isSession(parsed)) return null
      if (Date.parse(parsed.expiresAt) <= Date.now()) {
        clearStoredSession()
        return null
      }
      return parsed
    } catch {
      return null
    }
  },
  write(session: AuthSession): void {
    if (!canUseStorage()) return
    window.sessionStorage.setItem(sessionKey, JSON.stringify(session))
  },
  clear: clearStoredSession,
}

function isSession(value: unknown): value is AuthSession {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.csrfToken === 'string' && typeof candidate.expiresAt === 'string'
}
