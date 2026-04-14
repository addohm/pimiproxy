import { useState, useEffect } from 'react'
import { api } from '../api'

function StatusDot({ state }) {
  const running = state === 'running'
  return <span className={`dot ${running ? 'dot-green' : 'dot-yellow'}`} />
}

export default function Overview({ onUnauthorized }) {
  const [status, setStatus] = useState(null)
  const [config, setConfig] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    async function load() {
      try {
        const [s, c] = await Promise.all([api.status(), api.config()])
        setStatus(s)
        setConfig(c)
      } catch (err) {
        if (err.status === 401) { onUnauthorized(); return }
        setError(err.message)
      }
    }
    load()
    const timer = setInterval(load, 10000)
    return () => clearInterval(timer)
  }, [])

  const mihomoState   = status?.state ?? '—'
  const proxyCount    = config?.proxy_names?.length ?? '—'
  const providerUrl   = config?.provider_url || 'Not set'
  const subRules      = config?.sub_rule_count ?? '—'
  const dashPort      = status?.dashboard_port ?? '8080'
  const dashName      = status?.dashboard ?? 'metacubexd'
  const dashUrl       = `http://${window.location.hostname}:${dashPort}`

  return (
    <>
      <h1 className="page-title">Overview</h1>

      {error && (
        <p style={{ color: 'var(--warm)', fontSize: 13, marginBottom: 24 }}>{error}</p>
      )}

      <div className="overview-grid">
        <div className="overview-card">
          <div className="overview-card-label">Mihomo</div>
          <div className="overview-card-value">
            <StatusDot state={mihomoState} />
            {mihomoState}
          </div>
        </div>

        <div className="overview-card">
          <div className="overview-card-label">Proxies loaded</div>
          <div className="overview-card-value">{proxyCount}</div>
        </div>

        <div className="overview-card">
          <div className="overview-card-label">Provider rules</div>
          <div className="overview-card-value">{subRules}</div>
        </div>
      </div>

      <div className="overview-card" style={{ marginBottom: 16 }}>
        <div className="overview-card-label">Provider URL</div>
        <div style={{ fontSize: 13, color: 'var(--dim)', marginTop: 8, wordBreak: 'break-all' }}>
          {providerUrl}
        </div>
      </div>

      {config?.sub_group_names?.length > 0 && (
        <div className="overview-card">
          <div className="overview-card-label">Provider groups</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
            {config.sub_group_names.map(name => (
              <span key={name} className="card-type">{name}</span>
            ))}
          </div>
        </div>
      )}

      <div className="overview-card" style={{ marginTop: 8 }}>
        <div className="overview-card-label">Dashboard</div>
        <div style={{ marginTop: 8 }}>
          <a
            href={dashUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontSize: 13, color: 'var(--teal)', textDecoration: 'none',
            }}
          >
            {dashName} — {dashUrl}
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </a>
        </div>
      </div>
    </>
  )
}
