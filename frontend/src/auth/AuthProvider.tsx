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

type AuthContextValue = {
  status: AuthStatus
  user: AuthUser | null
  error: string | null
  signIn: (returnPath?: string | null) => Promise<void>
  signOut: () => Promise<void>
  completeSignIn: () => Promise<void>
  completeSilentSignIn: () => Promise<void>
  restoreSecureSession: () => Promise<boolean>
  isRestoringSession: boolean
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

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
  }
}

async function authRequest(
  path: string,
  options: { method?: string; csrf?: boolean } = {},
): Promise<unknown> {
  const headers = new Headers({ Accept: 'application/json' })
  if (options.csrf) {
    const csrfToken = sessionStore.read()?.csrfToken
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(env.apiBaseUrl + path, {
    method: options.method ?? 'GET',
    credentials: 'include',
    headers,
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
      message: 'The authentication service could not complete this request.',
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
      sessionStore.clear()
      if (keepAuthenticatedPageMounted) {
        setStatus('authenticated')
        setError('Your secure session could not be restored. Try again or sign in again.')
      } else {
        clearAuth('Your secure session needs to be renewed. Please sign in again.')
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
      if (!sessionStore.read()) {
        const restored = await restoreSecureSession()
        if (!restored) return
      } else {
        setUser(nextUser)
        setStatus('authenticated')
        setError(null)
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        clearAuth()
      } else {
        clearAuth(
          cause instanceof Error ? cause.message : 'Your secure session could not be restored.',
        )
      }
    }
  }, [clearAuth, restoreSecureSession])

  useEffect(() => {
    setUnauthorizedHandler(clearAuth)
    const callbackRoute =
      typeof window !== 'undefined' &&
      (window.location.pathname === '/auth/callback' ||
        window.location.pathname === '/auth/silent-callback')
    if (!callbackRoute) void refresh()
    return () => setUnauthorizedHandler(undefined)
  }, [clearAuth, refresh])

  const signIn = useCallback(async (returnPath?: string | null): Promise<void> => {
    rememberReturnPath(returnPath)
    setError(null)
    try {
      await oidcManager().signinRedirect()
    } catch {
      setError('Sign-in could not start. Check the identity provider configuration.')
    }
  }, [])

  const completeSignIn = useCallback(async (): Promise<void> => {
    setError(null)
    try {
      const oidcUser = await oidcManager().signinRedirectCallback()
      const nextUser = await exchangeOidcUserForBackendSession(oidcUser)
      setUser(nextUser)
      setStatus('authenticated')
    } catch {
      clearAuth('Sign-in could not be completed. Please try again.')
      throw new Error('Sign-in failed.')
    }
  }, [clearAuth])

  const completeSilentSignIn = useCallback(async (): Promise<void> => {
    try {
      await oidcManager().signinSilentCallback()
    } catch {
      clearAuth('Your secure session could not be restored. Please sign in again.')
      throw new Error('Silent sign-in callback failed.')
    }
  }, [clearAuth])

  const signOut = useCallback(async (): Promise<void> => {
    let logoutError: string | undefined
    try {
      await authRequest('/auth/logout', { method: 'POST', csrf: true })
    } catch {
      logoutError = 'The server could not confirm logout. Your local session was cleared.'
    } finally {
      try {
        await oidcManager().removeUser()
      } finally {
        clearAuth(logoutError)
      }
    }
  }, [clearAuth])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      error,
      signIn,
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
