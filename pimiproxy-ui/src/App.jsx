import { useState, useEffect, useCallback } from 'react'
import { api } from './api'
import Sidebar from './components/Sidebar'
import Overview from './components/Overview'
import Groups from './components/Groups'
import Rules from './components/Rules'
import Settings from './components/Settings'
import Check from './components/Check'

function LoginPage({ onLogin }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      await api.login(password)
      onLogin()
    } catch (err) {
      setError('Invalid password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-title">pimiproxy</div>
        <div className="login-sub">Enter your API secret to continue</div>
        <form onSubmit={handleSubmit}>
          <div className="field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoFocus
              placeholder="API secret"
            />
          </div>
          <button className="btn btn-primary" style={{ width: '100%' }} disabled={loading}>
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
          {error && <div className="login-error">{error}</div>}
        </form>
      </div>
    </div>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(null) // null = checking, false = logged out, true = logged in
  const [tab, setTab] = useState('overview')
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark')
  const [dashUrl, setDashUrl] = useState('')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : '')
    localStorage.setItem('theme', theme)
  }, [theme])

  // Check if already authenticated on mount
  useEffect(() => {
    api.status()
      .then(s => {
        setAuthed(true)
        const port = s.dashboard_port ?? '8080'
        setDashUrl(`http://${window.location.hostname}:${port}`)
      })
      .catch(() => setAuthed(false))
  }, [])

  // After login (authed flips true but dashUrl not yet set), fetch dashboard URL
  useEffect(() => {
    if (!authed || dashUrl) return
    api.status()
      .then(s => {
        const port = s.dashboard_port ?? '8080'
        setDashUrl(`http://${window.location.hostname}:${port}`)
      })
      .catch(() => {})
  }, [authed])

  const handleLogout = useCallback(async () => {
    try { await api.logout() } catch (_) {}
    setAuthed(false)
  }, [])

  const handleUnauthorized = useCallback(() => {
    setAuthed(false)
  }, [])

  if (authed === null) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--dim)', fontSize: 14 }}>Loading…</span>
      </div>
    )
  }

  if (!authed) {
    return <LoginPage onLogin={() => setAuthed(true)} />
  }

  const pages = { overview: Overview, groups: Groups, rules: Rules, check: Check, settings: Settings }
  const Page = pages[tab] || Overview

  return (
    <div className="app-shell">
      <Sidebar
        tab={tab}
        onTab={setTab}
        theme={theme}
        onTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
        onLogout={handleLogout}
        dashUrl={dashUrl}
      />
      <main className="main-content">
        <Page onUnauthorized={handleUnauthorized} />
      </main>
    </div>
  )
}
