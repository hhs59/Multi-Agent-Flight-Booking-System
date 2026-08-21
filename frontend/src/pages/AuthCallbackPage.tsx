import { useEffect, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { consumeReturnPath } from '../auth/returnPath'
import { ErrorState, LoadingScreen } from '../components/ui'

export function AuthCallbackPage() {
  const { status, completeSignIn, completeGoogleSignIn } = useAuth()
  const [error, setError] = useState<unknown>(null)
  const [destination, setDestination] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true

    // Check for Google OAuth access_token in URL hash or query params
    const hash = window.location.hash
    const search = window.location.search
    const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : search)
    const accessToken = params.get('access_token')

    if (accessToken) {
      void completeGoogleSignIn(accessToken)
        .then(() => setDestination(consumeReturnPath() ?? '/assistant'))
        .catch(setError)
      return
    }

    void completeSignIn()
      .then(() => setDestination(consumeReturnPath() ?? '/assistant'))
      .catch(setError)
  }, [completeSignIn, completeGoogleSignIn])

  if (status === 'authenticated' && destination) {
    return <Navigate to={destination} replace />
  }
  if (error) {
    return (
      <div className="center-page">
        <ErrorState error={error} onRetry={() => (window.location.href = '/login')} />
      </div>
    )
  }
  return <LoadingScreen label="Đang hoàn tất xác thực đăng nhập Google / SSO..." />
}

export function SilentCallbackPage() {
  const { completeSilentSignIn } = useAuth()
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    void completeSilentSignIn().catch(() => undefined)
  }, [completeSilentSignIn])
  return <LoadingScreen label="Đang làm mới phiên làm việc..." />
}
