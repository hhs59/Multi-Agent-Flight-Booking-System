export type AuthUser = {
  userId: string
  email: string
  displayName: string
  locale: 'vi' | 'en'
  timezone: string
  avatarUrl?: string
  authProvider?: 'local' | 'oidc' | 'google' | 'demo'
}

export type DemoAccount = {
  id: string
  email: string
  displayName: string
  role: string
  avatar: string
}

