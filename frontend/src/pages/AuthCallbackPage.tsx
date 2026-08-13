import { useEffect, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { consumeReturnPath } from '../auth/returnPath'
import { ErrorState, LoadingScreen } from '../components/ui'

export function AuthCallbackPage() {
  const { status, completeSignIn } = useAuth()
  const [error, setError] = useState<unknown>(null)
  const [destination, setDestination] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    void completeSignIn()
      .then(() => setDestination(consumeReturnPath() ?? '/assistant'))
      .catch(setError)
  }, [completeSignIn])

  if (status === 'authenticated' && destination) {
    return <Navigate to={destination} replace />
  }
  if (error) {
    return (
      <div className="center-page">
        <ErrorState error={error} onRetry={() => window.location.reload()} />
      </div>
    )
  }
  return <LoadingScreen label="Completing secure sign-in..." />
}

export function SilentCallbackPage() {
  const { completeSilentSignIn } = useAuth()
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    void completeSilentSignIn().catch(() => undefined)
  }, [completeSilentSignIn])
  return <LoadingScreen label="Refreshing secure session..." />
}
