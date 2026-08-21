import {
  ChevronDown,
  LogOut,
  Moon,
  Plane,
  ShieldCheck,
  Sparkles,
  Star,
  Sun,
  Ticket,
  User,
  Users,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { loadPersonalProfile, UserProfileModal } from './UserProfileModal'

interface UserAvatarMenuProps {
  theme: string
  onToggleTheme: () => void
}

export function UserAvatarMenu({ theme, onToggleTheme }: UserAvatarMenuProps) {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
  const [isOpen, setIsOpen] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [modalTab, setModalTab] = useState<'general' | 'preferences' | 'security'>('general')
  const menuRef = useRef<HTMLDivElement>(null)

  const profile = loadPersonalProfile(user)
  const displayName = profile.fullName || user?.displayName || 'Traveler'
  const email = profile.email || user?.email || 'traveler@waypoint.com'
  const initial = displayName.slice(0, 1).toUpperCase()

  // Click outside to close
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const openModalWithTab = (tab: 'general' | 'preferences' | 'security') => {
    setModalTab(tab)
    setModalOpen(true)
    setIsOpen(false)
  }

  const handleSignOut = () => {
    setIsOpen(false)
    void signOut().then(() => navigate('/login'))
  }

  return (
    <div ref={menuRef} className="user-avatar-menu-container">
      {/* Clickable Avatar Button */}
      <button
        type="button"
        className={`user-profile-badge-btn ${isOpen ? 'active' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        aria-label="User Account Menu"
        aria-expanded={isOpen}
      >
        <span className="header-avatar">
          {initial}
          <span className="avatar-online-dot" />
        </span>
        <div className="user-badge-text-group">
          <span className="user-name-text">{displayName}</span>
          <span className="user-tier-label">
            <Star size={10} className="tier-star" /> Bamboo Gold
          </span>
        </div>
        <ChevronDown size={14} className={`avatar-chevron ${isOpen ? 'rotate' : ''}`} />
      </button>

      {/* Floating Dropdown Menu */}
      {isOpen ? (
        <div className="avatar-dropdown-card">
          {/* Header Card */}
          <div className="avatar-dropdown-header">
            <div className="avatar-large-circle">
              {initial}
              <span className="online-badge-dot" />
            </div>
            <div className="avatar-user-details">
              <strong className="user-full-name">{displayName}</strong>
              <span className="user-email-text">{email}</span>
              <div className="user-status-tag">
                <Sparkles size={11} />
                <span>Hội viên VIP Bamboo Gold</span>
              </div>
            </div>
          </div>

          <div className="avatar-dropdown-divider" />

          {/* Menu Action Items */}
          <div className="avatar-menu-list">
            <button
              type="button"
              className="avatar-menu-item"
              onClick={() => openModalWithTab('general')}
            >
              <div className="menu-item-icon icon-emerald">
                <User size={16} />
              </div>
              <div className="menu-item-text">
                <strong>Thông tin cá nhân</strong>
                <span>Xem & chỉnh sửa họ tên, email, SĐT</span>
              </div>
            </button>

            <button
              type="button"
              className="avatar-menu-item"
              onClick={() => {
                setIsOpen(false)
                navigate('/travelers')
              }}
            >
              <div className="menu-item-icon icon-blue">
                <Users size={16} />
              </div>
              <div className="menu-item-text">
                <strong>Hồ sơ hành khách của tôi</strong>
                <span>Quản lý thông tin hộ chiếu & giấy tờ</span>
              </div>
            </button>

            <button
              type="button"
              className="avatar-menu-item"
              onClick={() => {
                setIsOpen(false)
                navigate('/bookings')
              }}
            >
              <div className="menu-item-icon icon-green">
                <Ticket size={16} />
              </div>
              <div className="menu-item-text">
                <strong>Chuyến bay & Vé đã đặt</strong>
                <span>Xem vé điện tử, hóa đơn & hỗ trợ</span>
              </div>
            </button>

            <button
              type="button"
              className="avatar-menu-item"
              onClick={() => openModalWithTab('preferences')}
            >
              <div className="menu-item-icon icon-amber">
                <Plane size={16} />
              </div>
              <div className="menu-item-text">
                <strong>Tùy chọn & Sở thích bay</strong>
                <span>Sân bay mặc định, hạng ghế, thẻ bay</span>
              </div>
            </button>

            <button
              type="button"
              className="avatar-menu-item"
              onClick={() => openModalWithTab('security')}
            >
              <div className="menu-item-icon icon-teal">
                <ShieldCheck size={16} />
              </div>
              <div className="menu-item-text">
                <strong>Bảo mật & Quyền riêng tư</strong>
                <span>Mã hóa AES-256 & trạng thái phiên</span>
              </div>
            </button>
          </div>

          <div className="avatar-dropdown-divider" />

          {/* Bottom Quick Controls */}
          <div className="avatar-dropdown-footer">
            <button
              type="button"
              className="footer-theme-toggle-btn"
              onClick={onToggleTheme}
            >
              {theme === 'light' ? (
                <>
                  <Moon size={15} />
                  <span>Chế độ Tối (Dark)</span>
                </>
              ) : (
                <>
                  <Sun size={15} />
                  <span>Chế độ Sáng (Light)</span>
                </>
              )}
            </button>

            <button
              type="button"
              className="footer-signout-btn"
              onClick={handleSignOut}
            >
              <LogOut size={15} />
              <span>Đăng xuất</span>
            </button>
          </div>
        </div>
      ) : null}

      {/* User Profile Details Modal */}
      <UserProfileModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        defaultTab={modalTab}
      />
    </div>
  )
}
