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

      {(() => {
        const subGroups    = config?.sub_group_names ?? []
        const systemGroups = config?.system_group_names ?? []
        const userGroups   = (config?.user_groups ?? []).map(g => g.name)
        if (!subGroups.length && !systemGroups.length && !userGroups.length) return null

        const Section = ({ label, names }) => names.length === 0 ? null : (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--meta-text)', marginBottom: 6 }}>
              {label}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {names.map(name => <span key={name} className="card-type">{name}</span>)}
            </div>
          </div>
        )

        return (
          <div className="overview-card">
            <div className="overview-card-label">Proxy groups</div>
            <div style={{ marginTop: 12 }}>
              <Section label="Provider" names={subGroups} />
              <Section label="System"   names={systemGroups} />
              <Section label="Yours"    names={userGroups} />
            </div>
          </div>
        )
      })()}

    </>
  )
}
