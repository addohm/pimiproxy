import { useState } from 'react'
import { api } from '../api'

export default function Check({ onUnauthorized }) {
  const [input, setInput]     = useState('')
  const [result, setResult]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  async function handleCheck(e) {
    e.preventDefault()
    if (!input.trim()) return
    setLoading(true)
    setResult(null)
    setError('')
    try {
      const r = await api.check(input.trim())
      setResult(r)
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <h1 className="page-title">Check URL</h1>

      <form onSubmit={handleCheck} style={{ marginBottom: 32 }}>
        <div className="field">
          <label>URL or Domain</label>
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="e.g. netflix.com or https://www.netflix.com/watch/123"
            autoFocus
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? 'Checking…' : 'Check'}
        </button>
      </form>

      {error && <p style={{ color: 'var(--warm)', fontSize: 13, marginBottom: 16 }}>{error}</p>}

      {result && (
        <>
          <div className="section-label" style={{ marginBottom: 12 }}>
            Result for <span style={{ color: 'var(--light)', fontFamily: 'monospace' }}>{result.domain}</span>
          </div>
          <div className="card-list">
            {result.matched ? (
              <>
                <div className="card-row">
                  <span className="card-meta">Matched rule</span>
                  <span style={{ fontFamily: 'monospace', fontSize: 13, color: 'var(--light)', marginLeft: 'auto' }}>
                    {result.rule}
                  </span>
                </div>
                <div className="card-row">
                  <span className="card-meta">Source</span>
                  <span className="card-name" style={{ textAlign: 'right' }}>
                    {result.source === 'user'   ? 'Your rules'
                   : result.source === 'mihomo' ? 'Mihomo (live)'
                   : 'Provider rules'}
                  </span>
                </div>
                <div className="card-row">
                  <span className="card-meta">Routes to</span>
                  <span className="card-name" style={{ textAlign: 'right' }}>{result.target}</span>
                </div>

                {/* Full proxy chain from Mihomo — shown when source === 'mihomo' */}
                {result.chains && result.chains.length > 1 && (
                  <div className="card-row">
                    <span className="card-meta">Via</span>
                    <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                      {result.chains.slice(1).map((c, i) => (
                        <span key={i} className="card-type" style={{ fontFamily: 'monospace', fontSize: 11 }}>{c}</span>
                      ))}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <div className="card-row">
                <span className="card-meta">No rule matched</span>
                <span style={{ fontSize: 12, color: 'var(--dim)', marginLeft: 'auto', maxWidth: '60%', textAlign: 'right' }}>
                  {result.note}
                </span>
              </div>
            )}

            {'connection_ok' in result && (
              <div className="card-row">
                <span className="card-meta">Connection test</span>
                <span style={{ marginLeft: 'auto', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                  {result.connection_ok ? (
                    <>
                      <span className="dot dot-green" />
                      <span style={{ color: 'var(--teal)' }}>{result.connection_status}</span>
                    </>
                  ) : (
                    <>
                      <span className="dot dot-yellow" />
                      <span style={{ color: 'var(--warm)' }}>{result.connection_error || 'Failed'}</span>
                    </>
                  )}
                </span>
              </div>
            )}
          </div>
        </>
      )}
    </>
  )
}
