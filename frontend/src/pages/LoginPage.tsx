import { useState, type FormEvent } from 'react'
import {
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  Mail,
  ShieldCheck,
  User,
  UserPlus,
  X,
} from 'lucide-react'
import { Navigate, useLocation } from 'react-router-dom'
import { env } from '../config/env'
import { useAuth } from '../auth/AuthProvider'
import { returnPathFromLocationState } from '../auth/returnPath'
import { Button, Card, InfoBanner } from '../components/ui'
import { VietnamAirlines3DBackground } from '../components/VietnamAirlines3DBackground'

export function LoginPage() {
  const { status, signIn, signInPassword, signUp, signInGoogle, signInGoogleDirect, error } =
    useAuth()
  const location = useLocation()
  const returnPath = returnPathFromLocationState(location.state)

  const [activeTab, setActiveTab] = useState<'login' | 'register'>('login')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [rememberMe, setRememberMe] = useState(true)

  // Login form state
  const [loginEmail, setLoginEmail] = useState('')
  const [loginPassword, setLoginPassword] = useState('')

  // Register form state
  const [regName, setRegName] = useState('')
  const [regEmail, setRegEmail] = useState('')
  const [regPassword, setRegPassword] = useState('')
  const [regConfirmPassword, setRegConfirmPassword] = useState('')

  // Google OAuth modal state
  const [showGoogleModal, setShowGoogleModal] = useState(false)
  const [googleEmailInput, setGoogleEmailInput] = useState('nguyenkhanhson03@gmail.com')
  const [googleNameInput, setGoogleNameInput] = useState('Son Nguyen')
  const [customClientIdInput, setCustomClientIdInput] = useState('')

  // Local UI state
  const [localError, setLocalError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (status === 'authenticated') return <Navigate to={returnPath ?? '/assistant'} replace />

  const handleLoginSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setLocalError(null)
    setSuccessMsg(null)

    if (!loginEmail.trim()) {
      setLocalError('Vui lòng nhập Email hoặc Tên đăng nhập.')
      return
    }
    if (!loginPassword) {
      setLocalError('Vui lòng nhập mật khẩu.')
      return
    }

    setIsSubmitting(true)
    try {
      await signInPassword(loginEmail, loginPassword, returnPath)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleRegisterSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setLocalError(null)
    setSuccessMsg(null)

    if (!regName.trim()) {
      setLocalError('Vui lòng nhập Họ và tên của bạn.')
      return
    }
    if (!regEmail.trim() || !regEmail.includes('@')) {
      setLocalError('Vui lòng nhập Địa chỉ Email hợp lệ.')
      return
    }
    if (regPassword.length < 6) {
      setLocalError('Mật khẩu phải chứa ít nhất 6 ký tự.')
      return
    }
    if (regPassword !== regConfirmPassword) {
      setLocalError('Mật khẩu xác nhận không khớp.')
      return
    }

    setIsSubmitting(true)
    try {
      await signUp(regName, regEmail, regPassword, returnPath)
      setSuccessMsg('Đăng ký tài khoản thành công! Đang tự động đăng nhập...')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleGoogleClick = () => {
    setLocalError(null)
    if (env.googleClientId) {
      setIsSubmitting(true)
      void signInGoogle(undefined, returnPath).finally(() => setIsSubmitting(false))
    } else {
      setShowGoogleModal(true)
    }
  }

  const handleGoogleModalLogin = async (e: FormEvent) => {
    e.preventDefault()
    const email = googleEmailInput.trim().toLowerCase()
    const name = googleNameInput.trim() || email.split('@')[0]

    if (!email || !email.includes('@')) {
      setLocalError('Vui lòng nhập Email Google hợp lệ.')
      return
    }

    setIsSubmitting(true)
    setShowGoogleModal(false)
    try {
      await signInGoogleDirect({ email, displayName: name }, returnPath)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleInstantGoogleAccount = async (email: string, name: string) => {
    setLocalError(null)
    setIsSubmitting(true)
    setShowGoogleModal(false)
    try {
      await signInGoogleDirect({ email, displayName: name }, returnPath)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleCustomClientIdSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!customClientIdInput.trim()) return
    setIsSubmitting(true)
    setShowGoogleModal(false)
    void signInGoogle(customClientIdInput.trim(), returnPath).finally(() => setIsSubmitting(false))
  }

  return (
    <div className="auth-page">
      {/* Living Photorealistic Vietnam Airlines Sky & Parallax Engine */}
      <VietnamAirlines3DBackground />
      
      <div className="auth-layout">
        {/* Left Column: Original Waypoint Brand Story */}
        <div className="auth-story">
          <div className="brand brand-light">
            <div className="brand-mark">
              <img src="/images/bamboo_logo.jpg" alt="Logo" className="brand-logo-img" />
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

        {/* Right Column: Original Styled Auth Card */}
        <Card className="auth-card">
          <div className="auth-card-heading">
            <p className="eyebrow">Welcome back</p>
            <h2>Sign in to Waypoint</h2>
            <p>Use your account to continue planning your trip.</p>
          </div>

          {/* Tab Switcher */}
          <div className="auth-tabs">
            <button
              type="button"
              className={`auth-tab ${activeTab === 'login' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('login')
                setLocalError(null)
              }}
            >
              <KeyRound size={15} />
              <span>Đăng nhập</span>
            </button>
            <button
              type="button"
              className={`auth-tab ${activeTab === 'register' ? 'active' : ''}`}
              onClick={() => {
                setActiveTab('register')
                setLocalError(null)
              }}
            >
              <UserPlus size={15} />
              <span>Đăng ký</span>
            </button>
          </div>

          {/* Notifications */}
          {error ? <InfoBanner tone="danger">{error}</InfoBanner> : null}
          {localError ? <InfoBanner tone="danger">{localError}</InfoBanner> : null}
          {successMsg ? <InfoBanner tone="success">{successMsg}</InfoBanner> : null}

          {/* Social Auth Buttons */}
          <div className="social-auth-row">
            <button
              type="button"
              className="social-auth-btn google-auth-btn"
              onClick={handleGoogleClick}
              disabled={isSubmitting}
            >
              <svg className="google-icon" viewBox="0 0 24 24" width="18" height="18">
                <path
                  fill="#4285F4"
                  d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                />
                <path
                  fill="#34A853"
                  d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                />
                <path
                  fill="#FBBC05"
                  d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                />
                <path
                  fill="#EA4335"
                  d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                />
              </svg>
              <span>Google</span>
            </button>

            <button
              type="button"
              className="social-auth-btn sso-auth-btn"
              onClick={() => void signIn(returnPath)}
              disabled={isSubmitting}
              title="Đăng nhập qua Keycloak SSO"
            >
              <KeyRound size={16} />
              <span>Keycloak SSO</span>
            </button>
          </div>

          <div className="auth-divider">
            <span>hoặc</span>
          </div>

          {/* Form Login */}
          {activeTab === 'login' && (
            <form onSubmit={handleLoginSubmit} className="auth-form">
              <div className="form-group">
                <label htmlFor="login-email">Email / Tên đăng nhập</label>
                <div className="input-with-icon">
                  <Mail size={17} className="input-icon" />
                  <input
                    id="login-email"
                    type="text"
                    placeholder="ban@example.com"
                    value={loginEmail}
                    onChange={(e) => setLoginEmail(e.target.value)}
                    required
                  />
                </div>
              </div>

              <div className="form-group">
                <div className="label-with-action">
                  <label htmlFor="login-password">Mật khẩu</label>
                  <button
                    type="button"
                    className="forgot-link"
                    onClick={() =>
                      alert('💡 Mẹo: Bạn có thể sử dụng đăng nhập bằng Google phía trên để vào hệ thống nhanh chóng!')
                    }
                  >
                    Quên mật khẩu?
                  </button>
                </div>
                <div className="input-with-icon">
                  <Lock size={17} className="input-icon" />
                  <input
                    id="login-password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="••••••••"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    required
                  />
                  <button
                    type="button"
                    className="password-toggle-btn"
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              <div className="form-options">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                  />
                  <span>Ghi nhớ phiên</span>
                </label>
              </div>

              <Button
                type="submit"
                size="lg"
                className="full-width auth-submit-btn"
                disabled={isSubmitting}
              >
                <span>{isSubmitting ? 'Đang xử lý...' : 'Đăng nhập vào hệ thống'}</span>
                <ArrowRight size={17} />
              </Button>
            </form>
          )}

          {/* Form Register */}
          {activeTab === 'register' && (
            <form onSubmit={handleRegisterSubmit} className="auth-form">
              <div className="form-group">
                <label htmlFor="reg-name">Họ và tên</label>
                <div className="input-with-icon">
                  <User size={17} className="input-icon" />
                  <input
                    id="reg-name"
                    type="text"
                    placeholder="Nguyễn Văn A"
                    value={regName}
                    onChange={(e) => setRegName(e.target.value)}
                    required
                  />
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="reg-email">Email</label>
                <div className="input-with-icon">
                  <Mail size={17} className="input-icon" />
                  <input
                    id="reg-email"
                    type="email"
                    placeholder="ban@example.com"
                    value={regEmail}
                    onChange={(e) => setRegEmail(e.target.value)}
                    required
                  />
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="reg-password">Mật khẩu</label>
                <div className="input-with-icon">
                  <Lock size={17} className="input-icon" />
                  <input
                    id="reg-password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="Tối thiểu 6 ký tự"
                    value={regPassword}
                    onChange={(e) => setRegPassword(e.target.value)}
                    required
                  />
                  <button
                    type="button"
                    className="password-toggle-btn"
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="reg-confirm">Xác nhận mật khẩu</label>
                <div className="input-with-icon">
                  <Lock size={17} className="input-icon" />
                  <input
                    id="reg-confirm"
                    type={showConfirmPassword ? 'text' : 'password'}
                    placeholder="Nhập lại mật khẩu"
                    value={regConfirmPassword}
                    onChange={(e) => setRegConfirmPassword(e.target.value)}
                    required
                  />
                  <button
                    type="button"
                    className="password-toggle-btn"
                    onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                  >
                    {showConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              <Button
                type="submit"
                size="lg"
                className="full-width auth-submit-btn"
                disabled={isSubmitting}
              >
                <span>{isSubmitting ? 'Đang tạo...' : 'Tạo tài khoản ngay'}</span>
                <ArrowRight size={17} />
              </Button>
            </form>
          )}
        </Card>
      </div>

      {/* Google Account Selector Modal */}
      {showGoogleModal && (
        <div className="modal-backdrop">
          <div className="modal google-modal">
            <div className="modal-header google-modal-header">
              <div className="google-header-brand">
                <svg viewBox="0 0 24 24" width="22" height="22">
                  <path
                    fill="#4285F4"
                    d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                  />
                  <path
                    fill="#34A853"
                    d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                  />
                  <path
                    fill="#FBBC05"
                    d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                  />
                  <path
                    fill="#EA4335"
                    d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                  />
                </svg>
                <span>Xác thực tài khoản Google</span>
              </div>
              <button
                type="button"
                className="close-btn"
                onClick={() => setShowGoogleModal(false)}
              >
                <X size={18} />
              </button>
            </div>

            <div className="modal-body google-modal-body">
              <p className="google-subtitle">
                Đăng nhập tài khoản Google của bạn vào <strong>Waypoint Flight Concierge</strong>.
              </p>

              {/* Instant Google Account Selection */}
              <div className="google-accounts-list">
                <button
                  type="button"
                  className="google-account-card selected"
                  onClick={() => handleInstantGoogleAccount('nguyenkhanhson03@gmail.com', 'Nguyen Khanh Son')}
                >
                  <div className="google-avatar">S</div>
                  <div className="google-account-info">
                    <strong>Nguyen Khanh Son</strong>
                    <span>nguyenkhanhson03@gmail.com</span>
                  </div>
                  <CheckCircle2 size={18} className="check-icon text-success" />
                </button>
              </div>

              {/* Form Option 1: Instant Email Auth */}
              <form onSubmit={handleGoogleModalLogin} className="google-custom-form">
                <p className="google-form-title">Đăng nhập tài khoản Google:</p>

                <div className="form-group">
                  <label htmlFor="google-email">Email Google (@gmail.com)</label>
                  <div className="input-with-icon">
                    <Mail size={18} className="input-icon" />
                    <input
                      id="google-email"
                      type="email"
                      placeholder="nguyenkhanhson03@gmail.com"
                      value={googleEmailInput}
                      onChange={(e) => setGoogleEmailInput(e.target.value)}
                      required
                    />
                  </div>
                </div>

                <div className="form-group">
                  <label htmlFor="google-name">Tên hiển thị</label>
                  <div className="input-with-icon">
                    <User size={18} className="input-icon" />
                    <input
                      id="google-name"
                      type="text"
                      placeholder="Son Nguyen"
                      value={googleNameInput}
                      onChange={(e) => setGoogleNameInput(e.target.value)}
                    />
                  </div>
                </div>

                <Button
                  type="submit"
                  size="lg"
                  className="full-width auth-submit-btn"
                  style={{ marginTop: '0.75rem' }}
                >
                  <CheckCircle2 size={18} />
                  <span>Xác thực & Đăng nhập ngay</span>
                </Button>
              </form>

              {/* Form Option 2: Custom Client ID redirect */}
              <form
                onSubmit={handleCustomClientIdSubmit}
                className="google-custom-form"
                style={{
                  marginTop: '1.25rem',
                  paddingTop: '1rem',
                  borderTop: '1px solid rgba(255, 255, 255, 0.08)',
                }}
              >
                <p className="google-form-title" style={{ fontSize: '0.82rem', color: '#94a3b8' }}>
                  🔑 Hoặc mở trực tiếp trang <code>accounts.google.com</code> qua Google Cloud OAuth2:
                </p>

                <div className="form-group">
                  <label htmlFor="google-client-id" style={{ fontSize: '0.8rem' }}>
                    Google Client ID
                  </label>
                  <input
                    id="google-client-id"
                    type="text"
                    placeholder="YOUR_CLIENT_ID.apps.googleusercontent.com"
                    value={customClientIdInput}
                    onChange={(e) => setCustomClientIdInput(e.target.value)}
                  />
                </div>

                <button
                  type="submit"
                  className="button button-secondary full-width"
                  disabled={!customClientIdInput.trim()}
                  style={{ width: '100%' }}
                >
                  Mở trang đăng nhập Google OAuth
                </button>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
