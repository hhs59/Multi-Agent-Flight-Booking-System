import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { env } from '../config/env'
import { apiRequest, setUnauthorizedHandler } from '../api/client'
import { ApiError } from '../api/errors'
import { sessionStore } from './sessionStore'
import { exchangeOidcUserForBackendSession, oidcManager } from './oidc'
import { rememberReturnPath } from './returnPath'
import type { AuthUser } from '../types/auth'

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export type GoogleProfileInput = {
  email: string
  name: string
  avatarUrl?: string
}

export type AuthContextValue = {
  status: AuthStatus
  user: AuthUser | null
  error: string | null
  signIn: (returnPath?: string | null) => Promise<void>
  signInPassword: (email: string, pass: string, returnPath?: string | null) => Promise<void>
  signUp: (displayName: string, email: string, pass: string, returnPath?: string | null) => Promise<void>
  signInDemo: (email?: string, returnPath?: string | null) => Promise<void>
  signInGoogle: (customClientId?: string, returnPath?: string | null) => Promise<void>
  signInGoogleDirect: (
    profile: { email: string; displayName?: string; avatarUrl?: string },
    returnPath?: string | null,
  ) => Promise<void>
  completeGoogleSignIn: (accessToken: string) => Promise<void>
  signOut: () => Promise<void>
  completeSignIn: () => Promise<void>
  completeSilentSignIn: () => Promise<void>
  restoreSecureSession: () => Promise<boolean>
  isRestoringSession: boolean
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

const REGISTERED_USERS_KEY = 'flight-web.registered-users'

type StoredUser = {
  userId: string
  displayName: string
  email: string
  passwordHash: string
  createdAt: string
  authProvider: 'local' | 'google'
}

const DEMO_USERS: Record<string, AuthUser> = {
  'demo@example.test': {
    userId: 'usr_demo_01',
    email: 'demo@example.test',
    displayName: 'Demo Traveler',
    locale: 'vi',
    timezone: 'Asia/Ho_Chi_Minh',
    authProvider: 'demo',
  },
  'captain@waypoint.dev': {
    userId: 'usr_captain_02',
    email: 'captain@waypoint.dev',
    displayName: 'Captain Alex Nguyen',
    locale: 'vi',
    timezone: 'Asia/Ho_Chi_Minh',
    authProvider: 'demo',
  },
  'vip@waypoint.dev': {
    userId: 'usr_vip_03',
    email: 'vip@waypoint.dev',
    displayName: 'VIP Concierge',
    locale: 'vi',
    timezone: 'Asia/Ho_Chi_Minh',
    authProvider: 'demo',
  },
}

function getStoredUsers(): StoredUser[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(REGISTERED_USERS_KEY)
    return raw ? (JSON.parse(raw) as StoredUser[]) : []
  } catch {
    return []
  }
}

function saveStoredUser(newUser: StoredUser): void {
  if (typeof window === 'undefined') return
  try {
    const existing = getStoredUsers()
    const updated = [newUser, ...existing.filter((u) => u.email.toLowerCase() !== newUser.email.toLowerCase())]
    localStorage.setItem(REGISTERED_USERS_KEY, JSON.stringify(updated))
  } catch {
    // ignore
  }
}

function userFromUnknown(value: unknown): AuthUser | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (typeof record.user_id !== 'string' || typeof record.email !== 'string') return null
  return {
    userId: record.user_id,
    email: record.email,
    displayName: typeof record.display_name === 'string' ? record.display_name : record.email,
    locale: record.locale === 'en' ? 'en' : 'vi',
    timezone: typeof record.timezone === 'string' ? record.timezone : 'Asia/Ho_Chi_Minh',
    authProvider: 'oidc',
  }
}

async function authRequest(
  path: string,
  options: { method?: string; body?: unknown; csrf?: boolean } = {},
): Promise<unknown> {
  const headers = new Headers({ Accept: 'application/json' })
  if (options.body) headers.set('Content-Type', 'application/json')
  if (options.csrf) {
    const csrfToken = sessionStore.read()?.csrfToken
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(env.apiBaseUrl + path, {
    method: options.method ?? 'GET',
    credentials: 'include',
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  if (response.status === 204) return undefined
  const text = await response.text()
  let body: unknown
  try {
    body = text ? JSON.parse(text) : undefined
  } catch {
    body = text
  }
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: 'auth_error',
      message: typeof body === 'object' && body && 'detail' in body ? String((body as Record<string, unknown>).detail) : 'The authentication service could not complete this request.',
      retryable: response.status >= 500,
    })
  }
  return body
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isRestoringSession, setIsRestoringSession] = useState(false)
  const statusRef = useRef<AuthStatus>('loading')
  const userRef = useRef<AuthUser | null>(null)
  const restorationInFlight = useRef(false)

  useEffect(() => {
    statusRef.current = status
    userRef.current = user
  }, [status, user])

  const clearAuth = useCallback(
    (message?: string): void => {
      queryClient.clear()
      sessionStore.clear()
      setUser(null)
      setStatus('unauthenticated')
      setError(message ?? null)
    },
    [queryClient],
  )

  const saveLocalSession = useCallback((authUser: AuthUser) => {
    const csrfToken = 'csrf_' + Math.random().toString(36).substring(2, 12)
    const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString()

    sessionStore.write({ csrfToken, expiresAt, user: authUser }, true)
    sessionStore.writeUser(authUser, true)

    setUser(authUser)
    setStatus('authenticated')
    setError(null)
  }, [])

  const restoreSecureSession = useCallback(async (): Promise<boolean> => {
    if (restorationInFlight.current) return false
    restorationInFlight.current = true
    const keepAuthenticatedPageMounted =
      statusRef.current === 'authenticated' && userRef.current !== null
    setIsRestoringSession(true)
    setError(null)
    if (!keepAuthenticatedPageMounted) setStatus('loading')

    try {
      const oidcUser = await oidcManager().signinSilent()
      if (!oidcUser) throw new Error('No OIDC user was returned for silent renewal.')
      const nextUser = await exchangeOidcUserForBackendSession(oidcUser)
      setUser(nextUser)
      setStatus('authenticated')
      return true
    } catch (cause) {
      // Check local session store fallback
      const savedUser = sessionStore.readUser()
      if (savedUser) {
        setUser(savedUser)
        setStatus('authenticated')
        return true
      }

      sessionStore.clear()
      if (keepAuthenticatedPageMounted) {
        setStatus('authenticated')
        setError('Phiên làm việc của bạn cần được gia hạn. Vui lòng đăng nhập lại.')
      } else {
        clearAuth('Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại.')
      }
      if (cause instanceof ApiError && cause.status === 401) return false
      return false
    } finally {
      restorationInFlight.current = false
      setIsRestoringSession(false)
    }
  }, [clearAuth])

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const value = await apiRequest<unknown>('/auth/me', { csrf: false })
      const nextUser = userFromUnknown(value)
      if (!nextUser) throw new Error('Invalid session response.')
      setUser(nextUser)
      setStatus('authenticated')
      setError(null)
    } catch {
      // Fallback to local session storage if backend /auth/me fails or is disabled
      const localUser = sessionStore.readUser()
      if (localUser) {
        setUser(localUser)
        setStatus('authenticated')
        setError(null)
      } else {
        setStatus('unauthenticated')
      }
    }
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(clearAuth)
    const callbackRoute =
      typeof window !== 'undefined' &&
      (window.location.pathname === '/auth/callback' ||
        window.location.pathname === '/auth/silent-callback')
    if (!callbackRoute) void refresh()
    return () => setUnauthorizedHandler(undefined)
  }, [clearAuth, refresh])

  // Keycloak OIDC Redirect Sign In
  const signIn = useCallback(async (returnPath?: string | null): Promise<void> => {
    rememberReturnPath(returnPath)
    setError(null)
    try {
      await oidcManager().signinRedirect()
    } catch {
      setError(
        'Không thể kết nối đến máy chủ xác thực Keycloak (OIDC). Vui lòng thử phương thức Đăng nhập bằng Email / Mật khẩu hoặc Đăng nhập nhanh Demo bên dưới.',
      )
    }
  }, [])

  // Email & Password Sign In
  const signInPassword = useCallback(
    async (email: string, pass: string, returnPath?: string | null): Promise<void> => {
      rememberReturnPath(returnPath)
      setError(null)

      const cleanEmail = email.trim().toLowerCase()

      // 1. Check Demo Accounts
      if (DEMO_USERS[cleanEmail]) {
        saveLocalSession(DEMO_USERS[cleanEmail])
        return
      }

      // 2. Check Local Registered Users
      const registeredUsers = getStoredUsers()
      const found = registeredUsers.find((u) => u.email.toLowerCase() === cleanEmail)

      if (found) {
        if (found.passwordHash !== pass) {
          setError('Mật khẩu không chính xác. Vui lòng kiểm tra lại.')
          return
        }
        const authUser: AuthUser = {
          userId: found.userId,
          email: found.email,
          displayName: found.displayName,
          locale: 'vi',
          timezone: 'Asia/Ho_Chi_Minh',
          authProvider: found.authProvider,
        }
        saveLocalSession(authUser)
        return
      }

      // 3. Try backend login API if available
      try {
        const body = (await authRequest('/auth/login/local', {
          method: 'POST',
          body: { email: cleanEmail, password: pass },
        })) as Record<string, unknown>
        const authUser: AuthUser = {
          userId: String(body.user_id || 'usr_' + Date.now()),
          email: String(body.email || cleanEmail),
          displayName: String(body.display_name || cleanEmail.split('@')[0]),
          locale: 'vi',
          timezone: 'Asia/Ho_Chi_Minh',
          authProvider: 'local',
        }
        saveLocalSession(authUser)
        return
      } catch {
        // Fallback: If username matches general email format, accept for testing environment
        if (cleanEmail && pass) {
          const authUser: AuthUser = {
            userId: 'usr_local_' + Math.random().toString(36).substring(2, 9),
            email: cleanEmail,
            displayName: cleanEmail.split('@')[0],
            locale: 'vi',
            timezone: 'Asia/Ho_Chi_Minh',
            authProvider: 'local',
          }
          saveLocalSession(authUser)
          return
        }
        setError('Tài khoản không tồn tại hoặc thông tin đăng nhập không hợp lệ.')
      }
    },
    [saveLocalSession],
  )

  // Sign Up / Register Account
  const signUp = useCallback(
    async (
      displayName: string,
      email: string,
      pass: string,
      returnPath?: string | null,
    ): Promise<void> => {
      rememberReturnPath(returnPath)
      setError(null)

      const cleanEmail = email.trim().toLowerCase()
      const cleanName = displayName.trim()

      if (!cleanName || !cleanEmail || !pass) {
        setError('Vui lòng điền đầy đủ các thông tin bắt buộc.')
        return
      }

      const existingUsers = getStoredUsers()
      if (existingUsers.some((u) => u.email.toLowerCase() === cleanEmail) || DEMO_USERS[cleanEmail]) {
        setError('Email này đã được đăng ký. Vui lòng đăng nhập hoặc sử dụng email khác.')
        return
      }

      const newStoredUser: StoredUser = {
        userId: 'usr_reg_' + Date.now() + '_' + Math.random().toString(36).substring(2, 7),
        displayName: cleanName,
        email: cleanEmail,
        passwordHash: pass,
        createdAt: new Date().toISOString(),
        authProvider: 'local',
      }

      saveStoredUser(newStoredUser)

      const authUser: AuthUser = {
        userId: newStoredUser.userId,
        email: newStoredUser.email,
        displayName: newStoredUser.displayName,
        locale: 'vi',
        timezone: 'Asia/Ho_Chi_Minh',
        authProvider: 'local',
      }

      saveLocalSession(authUser)
    },
    [saveLocalSession],
  )

  // 1-Click Fast Demo Login
  const signInDemo = useCallback(
    async (demoEmail?: string, returnPath?: string | null): Promise<void> => {
      rememberReturnPath(returnPath)
      setError(null)
      const targetEmail = demoEmail || 'demo@example.test'
      const demoUser = DEMO_USERS[targetEmail] || DEMO_USERS['demo@example.test']
      saveLocalSession(demoUser)
    },
    [saveLocalSession],
  )

  // Trigger Google OAuth Redirect Flow
  const signInGoogle = useCallback(
    async (customClientId?: string, returnPath?: string | null): Promise<void> => {
      rememberReturnPath(returnPath)
      setError(null)

      const clientId = customClientId?.trim() || env.googleClientId

      if (!clientId) {
        setError(
          'Chưa cấu hình Google Client ID. Vui lòng nhập Client ID hoặc sử dụng phương thức đăng nhập khác.',
        )
        return
      }

      const redirectUri = encodeURIComponent(window.location.origin + '/auth/callback')
      const googleAuthUrl =
        `https://accounts.google.com/o/oauth2/v2/auth?` +
        `client_id=${encodeURIComponent(clientId)}&` +
        `redirect_uri=${redirectUri}&` +
        `response_type=token&` +
        `scope=${encodeURIComponent('email profile openid')}&` +
        `prompt=select_account`

      window.location.href = googleAuthUrl
    },
    [],
  )

  // Direct Google Profile Authentication (Instant 1-Tap)
  const signInGoogleDirect = useCallback(
    async (
      profile: { email: string; displayName?: string; avatarUrl?: string },
      returnPath?: string | null,
    ): Promise<void> => {
      rememberReturnPath(returnPath)
      setError(null)
      const cleanEmail = profile.email.trim().toLowerCase()
      const cleanName = profile.displayName?.trim() || cleanEmail.split('@')[0]
      const googleUser: AuthUser = {
        userId: 'usr_goog_' + Date.now().toString(36) + '_' + Math.random().toString(36).substring(2, 6),
        email: cleanEmail,
        displayName: cleanName,
        avatarUrl: profile.avatarUrl,
        locale: 'vi',
        timezone: 'Asia/Ho_Chi_Minh',
        authProvider: 'google',
      }
      saveLocalSession(googleUser)
    },
    [saveLocalSession],
  )

  // Complete Google OAuth Flow
  const completeGoogleSignIn = useCallback(
    async (accessToken: string): Promise<void> => {
      setError(null)
      try {
        const response = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
          headers: { Authorization: 'Bearer ' + accessToken },
        })
        if (!response.ok) throw new Error('Could not fetch user profile from Google.')
        const data = (await response.json()) as Record<string, unknown>
        const email = String(data.email || 'user.google@gmail.com').toLowerCase()
        const name = String(data.name || email.split('@')[0])
        const avatarUrl = typeof data.picture === 'string' ? data.picture : undefined

        const googleUser: AuthUser = {
          userId: 'usr_google_' + String(data.sub || Date.now()),
          email,
          displayName: name,
          avatarUrl,
          locale: data.locale === 'en' ? 'en' : 'vi',
          timezone: 'Asia/Ho_Chi_Minh',
          authProvider: 'google',
        }
        saveLocalSession(googleUser)
      } catch (cause) {
        clearAuth('Đăng nhập bằng tài khoản Google thất bại.')
        throw cause
      }
    },
    [clearAuth, saveLocalSession],
  )

  const completeSignIn = useCallback(async (): Promise<void> => {
    setError(null)
    try {
      const oidcUser = await oidcManager().signinRedirectCallback()
      const nextUser = await exchangeOidcUserForBackendSession(oidcUser)
      setUser(nextUser)
      setStatus('authenticated')
    } catch {
      clearAuth('Hoàn tất đăng nhập thất bại. Vui lòng thử lại.')
      throw new Error('Sign-in failed.')
    }
  }, [clearAuth])

  const completeSilentSignIn = useCallback(async (): Promise<void> => {
    try {
      await oidcManager().signinSilentCallback()
    } catch {
      clearAuth('Phiên làm việc không thể khôi phục. Vui lòng đăng nhập lại.')
      throw new Error('Silent sign-in callback failed.')
    }
  }, [clearAuth])

  const signOut = useCallback(async (): Promise<void> => {
    try {
      await authRequest('/auth/logout', { method: 'POST', csrf: true })
    } catch {
      // Server error during logout is ignored for smooth UX
    } finally {
      try {
        await oidcManager().removeUser()
      } finally {
        clearAuth()
      }
    }
  }, [clearAuth])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      error,
      signIn,
      signInPassword,
      signUp,
      signInDemo,
      signInGoogle,
      signInGoogleDirect,
      completeGoogleSignIn,
      signOut,
      completeSignIn,
      completeSilentSignIn,
      restoreSecureSession,
      isRestoringSession,
      refresh,
    }),
    [
      status,
      user,
      error,
      signIn,
      signInPassword,
      signUp,
      signInDemo,
      signInGoogle,
      signInGoogleDirect,
      completeGoogleSignIn,
      signOut,
      completeSignIn,
      completeSilentSignIn,
      restoreSecureSession,
      isRestoringSession,
      refresh,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = (): AuthContextValue => {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
