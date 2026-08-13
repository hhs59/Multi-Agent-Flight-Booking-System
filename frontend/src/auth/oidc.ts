import {
  InMemoryWebStorage,
  UserManager,
  WebStorageStateStore,
  type User,
  type UserManagerSettings,
} from 'oidc-client-ts'
import { ApiError } from '../api/errors'
import { env } from '../config/env'
import { sessionStore } from './sessionStore'
import type { AuthUser } from '../types/auth'

const transientStorage =
  typeof window === 'undefined' ? new InMemoryWebStorage() : window.sessionStorage

const settings: UserManagerSettings = {
  authority: env.oidcAuthority,
  client_id: env.oidcClientId,
  redirect_uri: env.oidcRedirectUri,
  silent_redirect_uri: env.oidcSilentRedirectUri,
  post_logout_redirect_uri: env.oidcPostLogoutRedirectUri,
  response_type: 'code',
  scope: 'openid profile email',
  extraQueryParams: {
    audience: env.oidcAudience,
  },
  stateStore: new WebStorageStateStore({ store: transientStorage }),
  userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
  monitorSession: false,
  automaticSilentRenew: false,
}

let manager: UserManager | undefined

export const oidcManager = (): UserManager => {
  manager ??= new UserManager(settings)
  return manager
}

function parseSession(
  value: unknown,
): { user: AuthUser; csrfToken: string; expiresAt: string } | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if (
    typeof record.user_id !== 'string' ||
    typeof record.email !== 'string' ||
    typeof record.csrf_token !== 'string' ||
    typeof record.expires_at !== 'string'
  ) {
    return null
  }
  return {
    user: {
      userId: record.user_id,
      email: record.email,
      displayName: typeof record.display_name === 'string' ? record.display_name : record.email,
      locale: record.locale === 'en' ? 'en' : 'vi',
      timezone: typeof record.timezone === 'string' ? record.timezone : 'Asia/Ho_Chi_Minh',
    },
    csrfToken: record.csrf_token,
    expiresAt: record.expires_at,
  }
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

export async function exchangeOidcUserForBackendSession(oidcUser: User): Promise<AuthUser> {
  try {
    const response = await fetch(env.apiBaseUrl + '/auth/session', {
      method: 'POST',
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer ' + oidcUser.access_token,
      },
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw new ApiError({
        status: response.status,
        code: 'auth_session_exchange_failed',
        message: 'The secure session could not be established.',
        retryable: response.status >= 500,
      })
    }
    const session = parseSession(body)
    if (!session || Date.parse(session.expiresAt) <= Date.now()) {
      throw new Error('The backend returned an invalid or expired session.')
    }
    sessionStore.write({ csrfToken: session.csrfToken, expiresAt: session.expiresAt })
    return session.user
  } finally {
    await oidcManager().removeUser()
  }
}
