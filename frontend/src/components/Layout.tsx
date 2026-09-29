import { NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/auth'
import { Working } from './ui'

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <NavLink to="/" className="brand">
          Job Copilot
        </NavLink>
        {user && (
          <nav aria-label="Main">
            <NavLink to="/" end>
              History
            </NavLink>
            <NavLink to="/new">New job</NavLink>
            <NavLink to="/profile">Resume</NavLink>
            {user.is_admin && <NavLink to="/admin">AI status</NavLink>}
            <button
              type="button"
              className="link-button"
              onClick={async () => {
                await logout()
                navigate('/login')
              }}
            >
              Log out
            </button>
          </nav>
        )}
      </header>
      <main id="main" className="container">
        <Outlet />
      </main>
    </>
  )
}

/** Pages that need a logged-in user. Remembers where they were going. */
export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Working label="Loading" />
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <Outlet />
}

/**
 * Login/signup pages: logged-in users go straight to the app. This guard is the single place
 * that decides where to go after logging in (pages only call setUser), which avoids racing
 * a page's own navigate() against this redirect.
 */
export function GuestOnly() {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <Working label="Loading" />
  if (user) {
    const from = (location.state as { from?: string } | null)?.from
    // Just verified a new account: first stop is uploading a resume.
    const target = location.pathname === '/verify' ? '/profile' : (from ?? '/')
    return <Navigate to={target} replace />
  }
  return <Outlet />
}
