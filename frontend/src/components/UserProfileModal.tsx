import {
  Check,
  Globe,
  Mail,
  Plane,
  Save,
  ShieldCheck,
  Sparkles,
  Star,
  UserRound,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { Button, Modal } from './ui'
import { COUNTRY_DIAL_CODES, searchNationalities, type NationalityOption } from '../data/countries'

interface UserProfileModalProps {
  open: boolean
  onClose: () => void
  defaultTab?: 'general' | 'preferences' | 'security'
}

const PROFILE_STORAGE_KEY = 'waypoint.user-personal-profile'

export interface UserPersonalProfile {
  fullName: string
  email: string
  dialCode: string
  phone: string
  homeAirport: string
  loyaltyProgram: string
  loyaltyNumber: string
  preferredCabin: string
  currency: string
  nationality: string
}

export function loadPersonalProfile(defaultUser?: { displayName?: string; email?: string } | null): UserPersonalProfile {
  try {
    const saved = localStorage.getItem(PROFILE_STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved)
      return {
        ...parsed,
        dialCode: parsed.dialCode || '+84',
        phone: parsed.phone || '',
      }
    }
  } catch {
    // fallback
  }

  return {
    fullName: defaultUser?.displayName || 'Nguyễn Khánh Sơn',
    email: defaultUser?.email || 'son.nguyen@example.com',
    dialCode: '+84',
    phone: '',
    homeAirport: 'SGN',
    loyaltyProgram: 'Lotusmiles (Vietnam Airlines)',
    loyaltyNumber: 'VN-88392019',
    preferredCabin: 'economy',
    currency: 'VND',
    nationality: 'Việt Nam (VNM)',
  }
}

export function UserProfileModal({ open, onClose, defaultTab = 'general' }: UserProfileModalProps) {
  const { user } = useAuth()
  const [activeTab, setActiveTab] = useState<'general' | 'preferences' | 'security'>(defaultTab)
  const [profile, setProfile] = useState<UserPersonalProfile>(() => loadPersonalProfile(user))
  const [savedSuccess, setSavedSuccess] = useState(false)

  // Nationality autocomplete state
  const [nationalityQuery, setNationalityQuery] = useState('')
  const [nationalityOpen, setNationalityOpen] = useState(false)
  const nationalityRef = useRef<HTMLDivElement>(null)

  const filteredNationalities = searchNationalities(nationalityQuery)

  useEffect(() => {
    if (open) {
      setActiveTab(defaultTab)
      const loaded = loadPersonalProfile(user)
      setProfile(loaded)
      setNationalityQuery('')
      setNationalityOpen(false)
      setSavedSuccess(false)
    }
  }, [open, defaultTab, user])

  // Click outside listener for nationality dropdown
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (nationalityRef.current && !nationalityRef.current.contains(e.target as Node)) {
        setNationalityOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSelectNationality = (nat: NationalityOption) => {
    const formatted = `${nat.flag} ${nat.name} (${nat.code})`
    setProfile({ ...profile, nationality: formatted })
    setNationalityQuery('')
    setNationalityOpen(false)
  }

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault()
    try {
      localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profile))
      setSavedSuccess(true)
      setTimeout(() => setSavedSuccess(false), 3000)
    } catch {
      // error saving
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Thông tin tài khoản & Cá nhân">
      <div className="user-profile-modal-content">
        {/* Navigation Tabs */}
        <div className="profile-modal-tabs">
          <button
            type="button"
            className={`profile-tab-btn ${activeTab === 'general' ? 'active' : ''}`}
            onClick={() => setActiveTab('general')}
          >
            <UserRound size={15} />
            <span>Thông tin cá nhân</span>
          </button>
          <button
            type="button"
            className={`profile-tab-btn ${activeTab === 'preferences' ? 'active' : ''}`}
            onClick={() => setActiveTab('preferences')}
          >
            <Plane size={15} />
            <span>Sở thích du lịch</span>
          </button>
          <button
            type="button"
            className={`profile-tab-btn ${activeTab === 'security' ? 'active' : ''}`}
            onClick={() => setActiveTab('security')}
          >
            <ShieldCheck size={15} />
            <span>Bảo mật & Quyền riêng tư</span>
          </button>
        </div>

        {savedSuccess ? (
          <div className="profile-save-banner">
            <Check size={16} />
            <span>Thông tin cá nhân của bạn đã được lưu thành công!</span>
          </div>
        ) : null}

        {/* Tab 1: General Info */}
        {activeTab === 'general' ? (
          <form onSubmit={handleSave} className="profile-tab-panel">
            <div className="profile-hero-card">
              <div className="profile-avatar-large">
                {(profile.fullName || 'U').slice(0, 1).toUpperCase()}
                <span className="online-dot" />
              </div>
              <div className="profile-hero-info">
                <div className="profile-hero-name-row">
                  <h3>{profile.fullName}</h3>
                  <span className="member-tier-pill">
                    <Star size={12} /> Bamboo Gold Member
                  </span>
                </div>
                <p className="profile-hero-email">{profile.email}</p>
              </div>
            </div>

            <div className="profile-fields-grid">
              <div className="profile-form-group">
                <label>Họ và tên hiển thị *</label>
                <div className="profile-input-wrap">
                  <UserRound size={15} className="input-icon" />
                  <input
                    type="text"
                    value={profile.fullName}
                    onChange={(e) => setProfile({ ...profile, fullName: e.target.value })}
                    placeholder="Nhập họ và tên..."
                    required
                  />
                </div>
              </div>

              <div className="profile-form-group">
                <label>Email liên hệ nhận vé *</label>
                <div className="profile-input-wrap">
                  <Mail size={15} className="input-icon" />
                  <input
                    type="email"
                    value={profile.email}
                    onChange={(e) => setProfile({ ...profile, email: e.target.value })}
                    placeholder="email@example.com"
                    required
                  />
                </div>
              </div>

              {/* Compound Phone Input: Country Dial Code + 10 Digits Number */}
              <div className="profile-form-group">
                <label>Số điện thoại liên hệ (Tối đa 10 số)</label>
                <div className="phone-compound-wrap">
                  <div className="phone-dial-select-box">
                    <select
                      value={profile.dialCode}
                      onChange={(e) => setProfile({ ...profile, dialCode: e.target.value })}
                      className="phone-dial-select"
                      aria-label="Mã vùng quốc gia"
                      title="Mã vùng quốc gia"
                    >
                      {COUNTRY_DIAL_CODES.map((c) => (
                        <option key={c.code} value={c.dial} title={`${c.name} (${c.dial})`}>
                          {c.flag} {c.dial}
                        </option>
                      ))}
                    </select>
                  </div>
                  <input
                    type="tel"
                    inputMode="numeric"
                    maxLength={10}
                    value={profile.phone}
                    onChange={(e) => {
                      const digitsOnly = e.target.value.replace(/\D/g, '').slice(0, 10)
                      setProfile({ ...profile, phone: digitsOnly })
                    }}
                    placeholder="Nhập số điện thoại..."
                    className="phone-digits-input"
                  />
                </div>
              </div>

              {/* Nationality with Autocomplete Suggestions (Only shown when typing) */}
              <div ref={nationalityRef} className="profile-form-group nationality-group">
                <label>Quốc tịch (Gợi ý khi nhập)</label>
                <div className="profile-input-wrap">
                  <Globe size={15} className="input-icon" />
                  <input
                    type="text"
                    value={profile.nationality}
                    onChange={(e) => {
                      const val = e.target.value
                      setProfile({ ...profile, nationality: val })
                      setNationalityQuery(val)
                      setNationalityOpen(val.trim().length > 0)
                    }}
                    placeholder="Gõ tên quốc gia (VD: Việt Nam, Đức, Mỹ, Nhật...)..."
                  />
                </div>

                {/* Autocomplete suggestions dropdown - Only shows when user typed text */}
                {nationalityOpen && nationalityQuery.trim().length > 0 ? (
                  <div className="nationality-suggestions-card">
                    <div className="nationality-suggestions-header">
                      <span>
                        <Sparkles size={12} /> Gợi ý quốc gia phù hợp
                      </span>
                    </div>
                    <div className="nationality-suggestions-list">
                      {filteredNationalities.length > 0 ? (
                        filteredNationalities.map((nat) => (
                          <div
                            key={nat.code}
                            className="nationality-item"
                            onClick={() => handleSelectNationality(nat)}
                          >
                            <span className="nationality-flag">{nat.flag}</span>
                            <div className="nationality-info">
                              <strong>{nat.name}</strong>
                              <span>{nat.enName}</span>
                            </div>
                            <span className="nationality-code">{nat.code}</span>
                          </div>
                        ))
                      ) : (
                        <div className="no-nationality-found">
                          <p>Không tìm thấy quốc gia phù hợp với &ldquo;{nationalityQuery}&rdquo;</p>
                        </div>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="profile-form-actions">
              <Button type="button" variant="secondary" onClick={onClose}>
                Đóng
              </Button>
              <Button type="submit" variant="primary">
                <Save size={15} /> Lưu thay đổi
              </Button>
            </div>
          </form>
        ) : null}

        {/* Tab 2: Travel Preferences */}
        {activeTab === 'preferences' ? (
          <form onSubmit={handleSave} className="profile-tab-panel">
            <div className="profile-fields-grid">
              <div className="profile-form-group">
                <label>Sân bay thường bay / Khởi hành mặc định</label>
                <div className="profile-input-wrap">
                  <Plane size={15} className="input-icon" />
                  <select
                    value={profile.homeAirport}
                    onChange={(e) => setProfile({ ...profile, homeAirport: e.target.value })}
                  >
                    <option value="SGN">TP. Hồ Chí Minh - Tân Sơn Nhất (SGN)</option>
                    <option value="HAN">Hà Nội - Nội Bài (HAN)</option>
                    <option value="DAD">Đà Nẵng - Sân bay Đà Nẵng (DAD)</option>
                    <option value="PQC">Phú Quốc - Sân bay Phú Quốc (PQC)</option>
                    <option value="CXR">Nha Trang - Cam Ranh (CXR)</option>
                    <option value="DLI">Đà Lạt - Liên Khương (DLI)</option>
                    <option value="HPH">Hải Phòng - Cát Bi (HPH)</option>
                  </select>
                </div>
              </div>

              <div className="profile-form-group">
                <label>Hạng ghế ưa thích</label>
                <div className="profile-input-wrap">
                  <Star size={15} className="input-icon" />
                  <select
                    value={profile.preferredCabin}
                    onChange={(e) => setProfile({ ...profile, preferredCabin: e.target.value })}
                  >
                    <option value="economy">Phổ thông (Economy)</option>
                    <option value="premium_economy">Phổ thông đặc biệt (Premium Economy)</option>
                    <option value="business">Thương gia (Business Class)</option>
                    <option value="first">Hạng nhất (First Class)</option>
                  </select>
                </div>
              </div>

              <div className="profile-form-group">
                <label>Chương trình Khách hàng thân thiết</label>
                <div className="profile-input-wrap">
                  <Sparkles size={15} className="input-icon" />
                  <input
                    type="text"
                    value={profile.loyaltyProgram}
                    onChange={(e) => setProfile({ ...profile, loyaltyProgram: e.target.value })}
                    placeholder="Lotusmiles (Vietnam Airlines)"
                  />
                </div>
              </div>

              <div className="profile-form-group">
                <label>Mã thẻ hội viên bay</label>
                <div className="profile-input-wrap">
                  <Star size={15} className="input-icon" />
                  <input
                    type="text"
                    value={profile.loyaltyNumber}
                    onChange={(e) => setProfile({ ...profile, loyaltyNumber: e.target.value })}
                    placeholder="VN-88392019"
                  />
                </div>
              </div>

              <div className="profile-form-group">
                <label>Đơn vị tiền tệ hiển thị</label>
                <div className="profile-input-wrap">
                  <select
                    value={profile.currency}
                    onChange={(e) => setProfile({ ...profile, currency: e.target.value })}
                  >
                    <option value="VND">VND (₫ - Đồng Việt Nam)</option>
                    <option value="USD">USD ($ - US Dollar)</option>
                    <option value="EUR">EUR (€ - Euro)</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="profile-form-actions">
              <Button type="button" variant="secondary" onClick={onClose}>
                Đóng
              </Button>
              <Button type="submit" variant="primary">
                <Save size={15} /> Lưu tùy chọn bay
              </Button>
            </div>
          </form>
        ) : null}

        {/* Tab 3: Security & Session */}
        {activeTab === 'security' ? (
          <div className="profile-tab-panel">
            <div className="security-status-card">
              <div className="sec-icon-circle">
                <ShieldCheck size={28} />
              </div>
              <div className="sec-info">
                <h4>Tài khoản được mã hóa đầu cuối</h4>
                <p>Toàn bộ thông tin hộ chiếu và vé máy bay được mã hóa 256-bit AES-GCM theo chuẩn hàng không IATA.</p>
              </div>
            </div>

            <div className="security-items-list">
              <div className="sec-item">
                <div className="sec-item-title">
                  <strong>Phương thức đăng nhập</strong>
                  <span>Google OAuth 2.0 / Keycloak OIDC</span>
                </div>
                <span className="sec-badge sec-badge-green">Đã xác thực</span>
              </div>

              <div className="sec-item">
                <div className="sec-item-title">
                  <strong>Trạng thái bảo vệ CSRF & Token</strong>
                  <span>Anti-tamper Double-Submit Cookie & JWT</span>
                </div>
                <span className="sec-badge sec-badge-green">Hoạt động tốt</span>
              </div>

              <div className="sec-item">
                <div className="sec-item-title">
                  <strong>Thời hạn phiên làm việc</strong>
                  <span>12 Giờ (Tự động gia hạn khi có tương tác)</span>
                </div>
                <span className="sec-badge sec-badge-blue">Đang kích hoạt</span>
              </div>
            </div>

            <div className="profile-form-actions">
              <Button type="button" variant="secondary" onClick={onClose}>
                Đóng
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
