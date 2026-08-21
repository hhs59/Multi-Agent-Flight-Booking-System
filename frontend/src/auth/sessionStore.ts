import type { AuthUser } from '../types/auth'

export type AuthSession = {
  csrfToken: string
  expiresAt: string
  user?: AuthUser
}

const sessionKey = 'flight-web.csrf-session'
const userKey = 'flight-web.auth-user'

const canUseStorage = (): boolean => typeof window !== 'undefined'

const clearStoredSession = (): void => {
  if (!canUseStorage()) return
  try {
    window.sessionStorage.removeItem(sessionKey)
    window.sessionStorage.removeItem(userKey)
    window.localStorage.removeItem(sessionKey)
    window.localStorage.removeItem(userKey)
  } catch {
    // ignore storage errors
  }
}

export const sessionStore = {
  read(): AuthSession | null {
    if (!canUseStorage()) return null
    try {
      let raw = window.sessionStorage.getItem(sessionKey)
      if (!raw) raw = window.localStorage.getItem(sessionKey)
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
  write(session: AuthSession, remember: boolean = true): void {
    if (!canUseStorage()) return
    const str = JSON.stringify(session)
    if (remember) {
      window.localStorage.setItem(sessionKey, str)
    } else {
      window.sessionStorage.setItem(sessionKey, str)
    }
  },
  readUser(): AuthUser | null {
    if (!canUseStorage()) return null
    try {
      let raw = window.sessionStorage.getItem(userKey)
      if (!raw) raw = window.localStorage.getItem(userKey)
      if (!raw) return null
      return JSON.parse(raw) as AuthUser
    } catch {
      return null
    }
  },
  writeUser(user: AuthUser, remember: boolean = true): void {
    if (!canUseStorage()) return
    const str = JSON.stringify(user)
    if (remember) {
      window.localStorage.setItem(userKey, str)
    } else {
      window.sessionStorage.setItem(userKey, str)
    }
  },
  clear: clearStoredSession,
}

function isSession(value: unknown): value is AuthSession {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.csrfToken === 'string' && typeof candidate.expiresAt === 'string'
}

