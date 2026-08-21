import {
  Activity,
  Bookmark,
  BriefcaseBusiness,
  Moon,
  Search,
  Sparkles,
  Sun,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { cn } from '../components/ui'
import { UserAvatarMenu } from '../components/UserAvatarMenu'

const navigation = [
  { to: '/assistant', label: 'AI Concierge', icon: Sparkles, badge: 'AI' },
  { to: '/search', label: 'Flights', icon: Search },
  { to: '/bookings', label: 'Bookings', icon: BriefcaseBusiness },
  { to: '/travelers', label: 'Travelers', icon: UsersRound },
  { to: '/watches', label: 'Price Watches', icon: Bookmark },
  { to: '/operations', label: 'Ops Desk', icon: Activity },
]

export function AppShell() {
  const location = useLocation()
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const saved = window.localStorage.getItem('waypoint-theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    window.localStorage.setItem('waypoint-theme', theme)
  }, [theme])

  return (
    <div className="app-frame">
      <header className="app-header">
        <div className="header-container">
          <NavLink className="brand" to="/assistant" aria-label="Waypoint home">
            <div className="brand-mark">
              <img src="/images/bamboo_logo.jpg" alt="Logo" className="brand-logo-img" />
            </div>
            <div className="brand-text">
              <strong>Waypoint <span className="brand-ai-badge">AI</span></strong>
              <span>Travel Concierge</span>
            </div>
          </NavLink>

          <nav className="desktop-nav" aria-label="Primary navigation">
            {navigation.map((item) => (
              <NavItem key={item.to} {...item} />
            ))}
          </nav>

          <div className="header-actions">
            <button
              type="button"
              className="theme-toggle"
              onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
              aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
              title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
            >
              {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
            </button>

            <UserAvatarMenu
              theme={theme}
              onToggleTheme={() => setTheme(theme === 'light' ? 'dark' : 'light')}
            />
          </div>
        </div>
      </header>

      <main
        className={cn(
          'main-content',
          location.pathname.startsWith('/assistant') && 'main-content-assistant',
        )}
      >
        <Outlet />
      </main>

      <nav className="mobile-nav" aria-label="Mobile navigation">
        {navigation.map((item) => (
          <NavItem key={item.to} {...item} />
        ))}
      </nav>
    </div>
  )
}

function NavItem({
  to,
  label,
  icon: Icon,
  badge,
}: {
  to: string
  label: string
  icon: LucideIcon
  badge?: string
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => cn('nav-item', isActive && 'nav-item-active')}
    >
      <Icon size={16} strokeWidth={2} />
      <span>{label}</span>
      {badge ? <span className="nav-item-badge">{badge}</span> : null}
    </NavLink>
  )
}
