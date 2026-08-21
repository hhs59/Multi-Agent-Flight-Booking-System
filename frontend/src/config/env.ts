const readEnv = (key: string, fallback: string): string => {
  const value = import.meta.env[key] as string | undefined
  return value?.trim() || fallback
}

const origin = typeof window === 'undefined' ? 'http://localhost:5173' : window.location.origin

export const env = {
  apiBaseUrl: readEnv('VITE_API_BASE_URL', 'http://localhost:8000'),
  oidcAuthority: readEnv('VITE_OIDC_AUTHORITY', 'http://localhost:8080/realms/flight-dev'),
  oidcClientId: readEnv('VITE_OIDC_CLIENT_ID', 'flight-web'),
  oidcAudience: readEnv('VITE_OIDC_AUDIENCE', 'flight-api'),
  oidcRedirectUri: readEnv('VITE_OIDC_REDIRECT_URI', origin + '/auth/callback'),
  oidcSilentRedirectUri: readEnv('VITE_OIDC_SILENT_REDIRECT_URI', origin + '/auth/silent-callback'),
  oidcPostLogoutRedirectUri: readEnv('VITE_OIDC_POST_LOGOUT_REDIRECT_URI', origin + '/login'),
  defaultLocale: readEnv('VITE_DEFAULT_LOCALE', 'vi') === 'en' ? 'en' : 'vi',
  googleClientId: readEnv('VITE_GOOGLE_CLIENT_ID', ''),
  diagnosticsEnabled: readEnv('VITE_ENABLE_DEV_DIAGNOSTICS', 'false') === 'true',
} as const
