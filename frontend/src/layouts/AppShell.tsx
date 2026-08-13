import {
  Bookmark,
  BriefcaseBusiness,
  LogOut,
  MessageCircle,
  Moon,
  Plane,
  Search,
  Sun,
  UsersRound,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { cn } from '../components/ui'

const navigation = [
  { to: '/assistant', label: 'Assistant', icon: MessageCircle },
  { to: '/search', label: 'Flights', icon: Search },
  { to: '/bookings', label: 'Bookings', icon: BriefcaseBusiness },
  { to: '/travelers', label: 'Travelers', icon: UsersRound },
  { to: '/watches', label: 'Watches', icon: Bookmark },
]

export function AppShell() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()
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
        <NavLink className="brand" to="/assistant" aria-label="Waypoint home">
          <span className="brand-mark"><Plane size={18} /></span>
          <strong>Waypoint</strong>
        </NavLink>
        <nav className="desktop-nav" aria-label="Primary navigation">
          {navigation.map((item) => <NavItem key={item.to} {...item} />)}
        </nav>
        <div className="header-actions">
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}
            aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
            title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
          >
            {theme === 'light' ? <Moon size={17} /> : <Sun size={17} />}
          </button>
          <span className="header-avatar" title={user?.displayName || user?.email || 'Traveler'}>
            {(user?.displayName || 'U').slice(0, 1).toUpperCase()}
          </span>
          <button
            className="header-signout"
            type="button"
            onClick={() => void signOut().then(() => navigate('/login'))}
            aria-label="Sign out"
            title="Sign out"
          >
            <LogOut size={17} />
          </button>
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
        {navigation.map((item) => <NavItem key={item.to} {...item} />)}
      </nav>
    </div>
  )
}

function NavItem({
  to,
  label,
  icon: Icon,
}: {
  to: string
  label: string
  icon: typeof MessageCircle
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => cn('nav-item', isActive && 'nav-item-active')}
    >
      <Icon size={17} strokeWidth={1.9} />
      <span>{label}</span>
    </NavLink>
  )
}
