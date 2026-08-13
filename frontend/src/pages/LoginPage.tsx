import { Plane, ShieldCheck } from 'lucide-react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { returnPathFromLocationState } from '../auth/returnPath'
import { Button, Card, InfoBanner } from '../components/ui'

export function LoginPage() {
  const { status, signIn, error } = useAuth()
  const location = useLocation()
  const returnPath = returnPathFromLocationState(location.state)

  if (status === 'authenticated') return <Navigate to={returnPath ?? '/assistant'} replace />

  return (
    <div className="auth-page">
      <div className="auth-ambient auth-ambient-one" />
      <div className="auth-ambient auth-ambient-two" />
      <div className="auth-layout">
        <div className="auth-story">
          <div className="brand brand-light">
            <div className="brand-mark">
              <Plane size={19} />
            </div>
            <div>
              <strong>Waypoint</strong>
              <span>flight concierge</span>
            </div>
          </div>
          <div className="auth-story-copy">
            <p className="eyebrow light-eyebrow">Your next trip, made clear</p>
            <h1>Search, compare, and book with confidence.</h1>
            <p>Bring your route, dates, and preferences together in one calm place.</p>
          </div>
          <div className="auth-trust">
            <ShieldCheck size={18} />
            <span>Secure session. Your traveler data stays protected.</span>
          </div>
        </div>
        <Card className="auth-card">
          <div className="auth-card-heading">
            <p className="eyebrow">Welcome back</p>
            <h2>Sign in to Waypoint</h2>
            <p>Use your account to continue planning your trip.</p>
          </div>
          {error ? <InfoBanner tone="danger">{error}</InfoBanner> : null}
          <Button size="lg" className="full-width" onClick={() => void signIn(returnPath)}>
            Continue with secure sign-in
          </Button>
          <p className="auth-legal">
            By continuing, you agree to use Waypoint for your own travel planning and bookings.
          </p>
          {returnPath ? (
            <p className="auth-return">You will return to your requested page after sign-in.</p>
          ) : null}
        </Card>
      </div>
    </div>
  )
}
