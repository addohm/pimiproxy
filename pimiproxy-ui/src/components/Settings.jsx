import { useState, useEffect } from 'react'
import { api } from '../api'

export default function Settings({ onUnauthorized }) {
  const [config, setConfig]       = useState(null)
  const [providerUrl, setProviderUrl] = useState('')
  const [saveStatus, setSaveStatus]   = useState('')   // '', 'saving', 'saved', 'error'
  const [refreshStatus, setRefreshStatus] = useState('') // '', 'refreshing', 'done', 'error'
  const [autoRefresh, setAutoRefresh]       = useState(true)
  const [intervalDays, setIntervalDays]     = useState(1)
  const [schedSaveStatus, setSchedSaveStatus] = useState('') // '', 'saving', 'saved', 'error'
  const [error, setError]             = useState('')

  async function load() {
    try {
      const c = await api.config()
      setConfig(c)
      setProviderUrl(c.provider_url || '')
      setAutoRefresh(c.auto_refresh_enabled ?? true)
      setIntervalDays(c.refresh_interval_days ?? 1)
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [])

  async function saveProvider(e) {
    e.preventDefault()
    setSaveStatus('saving')
    try {
      await api.setProvider(providerUrl.trim())
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus(''), 2000)
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setSaveStatus('error')
      setError(err.message)
    }
  }

  async function handleRefresh() {
    setRefreshStatus('refreshing')
    try {
      await api.refresh()
      setRefreshStatus('done')
      await load()
      setTimeout(() => setRefreshStatus(''), 3000)
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setRefreshStatus('error')
      setError(err.message)
    }
  }

  async function saveSchedule(e) {
    e.preventDefault()
    const days = parseInt(intervalDays, 10)
    if (!days || days < 1) { setError('Interval must be at least 1 day'); return }
    setSchedSaveStatus('saving')
    try {
      await api.setRefreshSettings({ auto_refresh_enabled: autoRefresh, refresh_interval_days: days })
      setSchedSaveStatus('saved')
      setTimeout(() => setSchedSaveStatus(''), 2000)
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setSchedSaveStatus('error')
      setError(err.message)
    }
  }

  const saveLabel = { '': 'Save', saving: 'Saving…', saved: 'Saved', error: 'Error' }[saveStatus]
  const refreshLabel = { '': 'Refresh Now', refreshing: 'Refreshing…', done: 'Done', error: 'Error' }[refreshStatus]
  const schedSaveLabel = { '': 'Save', saving: 'Saving…', saved: 'Saved', error: 'Error' }[schedSaveStatus]

  return (
    <>
      <h1 className="page-title">Settings</h1>

      {error && <p style={{ color: 'var(--warm)', fontSize: 13, marginBottom: 16 }}>{error}</p>}

      {/* Provider URL */}
      <div style={{ marginBottom: 40 }}>
        <div className="section-label" style={{ marginBottom: 16 }}>Provider</div>
        <form onSubmit={saveProvider}>
          <div className="field">
            <label>Subscription URL</label>
            <input
              type="url"
              value={providerUrl}
              onChange={e => setProviderUrl(e.target.value)}
              placeholder="https://…"
            />
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={saveStatus === 'saving'}
            >
              {saveLabel}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={handleRefresh}
              disabled={refreshStatus === 'refreshing'}
            >
              {refreshLabel}
            </button>
          </div>
          {refreshStatus === 'done' && (
            <p style={{ color: 'var(--teal)', fontSize: 12, marginTop: 10 }}>
              Provider refreshed — Mihomo restarted
            </p>
          )}
        </form>
      </div>

      {/* Auto-refresh schedule */}
      <div style={{ marginBottom: 40 }}>
        <div className="section-label" style={{ marginBottom: 16 }}>Auto Refresh</div>
        <form onSubmit={saveSchedule}>
          <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <label style={{ marginBottom: 0, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={e => setAutoRefresh(e.target.checked)}
                style={{ accentColor: 'var(--teal)', width: 16, height: 16 }}
              />
              Enable automatic refresh
            </label>
          </div>
          {autoRefresh && (
            <div className="field" style={{ marginTop: 14 }}>
              <label>Refresh interval (days)</label>
              <input
                type="number"
                min="1"
                max="365"
                value={intervalDays}
                onChange={e => setIntervalDays(e.target.value)}
                style={{ width: 100 }}
              />
            </div>
          )}
          <div style={{ marginTop: 14 }}>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={schedSaveStatus === 'saving'}
            >
              {schedSaveLabel}
            </button>
          </div>
          {!autoRefresh && (
            <p style={{ color: 'var(--dim)', fontSize: 12, marginTop: 10 }}>
              Auto refresh is off — provider data will only update when you click Refresh Now.
            </p>
          )}
          {autoRefresh && (
            <p style={{ color: 'var(--dim)', fontSize: 12, marginTop: 10 }}>
              Provider is refreshed automatically every {intervalDays} day{intervalDays == 1 ? '' : 's'}.
            </p>
          )}
        </form>
      </div>

      {/* Config info */}
      {config && (
        <div style={{ marginBottom: 40 }}>
          <div className="section-label" style={{ marginBottom: 16 }}>Config Summary</div>
          <div className="card-list">
            <div className="card-row">
              <span className="card-meta">Provider groups</span>
              <span className="card-name" style={{ textAlign: 'right' }}>{config.sub_group_names?.length ?? 0}</span>
            </div>
            <div className="card-row">
              <span className="card-meta">Provider rules</span>
              <span className="card-name" style={{ textAlign: 'right' }}>{config.sub_rule_count ?? 0}</span>
            </div>
            <div className="card-row">
              <span className="card-meta">Your groups</span>
              <span className="card-name" style={{ textAlign: 'right' }}>{config.user_groups?.length ?? 0}</span>
            </div>
            <div className="card-row">
              <span className="card-meta">Your rules</span>
              <span className="card-name" style={{ textAlign: 'right' }}>{config.user_rules?.length ?? 0}</span>
            </div>
            <div className="card-row">
              <span className="card-meta">Proxies loaded</span>
              <span className="card-name" style={{ textAlign: 'right' }}>{config.proxy_names?.length ?? 0}</span>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
