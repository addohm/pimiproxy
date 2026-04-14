import { useState, useEffect } from 'react'
import { api } from '../api'
import { IconPlus, IconX } from './Icons'

const GROUP_TYPES = [
  { value: 'select',       label: 'select — pick a server manually'          },
  { value: 'url-test',     label: 'url-test — auto fastest server'            },
  { value: 'fallback',     label: 'fallback — use first available'            },
  { value: 'load-balance', label: 'load-balance — spread across servers'      },
  { value: 'relay',        label: 'relay — chain proxies'                     },
]

function AddGroupModal({ proxies, onAdd, onClose }) {
  const [name, setName]       = useState('')
  const [type, setType]       = useState('select')
  const [selected, setSelected] = useState([])
  const [err, setErr]         = useState('')

  function toggle(p) {
    setSelected(s => s.includes(p) ? s.filter(x => x !== p) : [...s, p])
  }

  async function submit(e) {
    e.preventDefault()
    if (!name.trim()) { setErr('Name is required'); return }
    if (selected.length === 0) { setErr('Select at least one proxy'); return }
    try {
      await onAdd({ name: name.trim(), type, proxies: selected })
      onClose()
    } catch (ex) {
      setErr(ex.message)
    }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-title">Add Proxy Group</div>
        <form onSubmit={submit}>
          <div className="field">
            <label>Name</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="My Group" autoFocus />
          </div>
          <div className="field">
            <label>Type</label>
            <select value={type} onChange={e => setType(e.target.value)}>
              {GROUP_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Proxies ({selected.length} selected)</label>
            <div style={{
              maxHeight: 180, overflowY: 'auto', border: '1px solid var(--border-strong)',
              borderRadius: 8, padding: '4px 0', background: 'var(--bg)'
            }}>
              {proxies.length === 0
                ? <div style={{ padding: '8px 12px', color: 'var(--dim)', fontSize: 13 }}>No proxies available</div>
                : proxies.map(p => (
                  <label key={p} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '6px 12px', cursor: 'pointer', fontSize: 13,
                    color: selected.includes(p) ? 'var(--light)' : 'var(--dim)'
                  }}>
                    <input
                      type="checkbox"
                      checked={selected.includes(p)}
                      onChange={() => toggle(p)}
                      style={{ accentColor: 'var(--teal)' }}
                    />
                    {p}
                  </label>
                ))
              }
            </div>
          </div>
          {err && <div style={{ color: 'var(--warm)', fontSize: 12, marginBottom: 12 }}>{err}</div>}
          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary">Add Group</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function Groups({ onUnauthorized }) {
  const [config, setConfig]   = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [error, setError]     = useState('')

  async function load() {
    try {
      const c = await api.config()
      setConfig(c)
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setError(err.message)
    }
  }

  useEffect(() => { load() }, [])

  async function handleAdd(group) {
    await api.addGroup(group)
    await load()
  }

  async function handleDelete(name) {
    try {
      await api.deleteGroup(name)
      await load()
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setError(err.message)
    }
  }

  const subGroups  = config?.sub_group_names ?? []
  const userGroups = config?.user_groups ?? []
  const proxies    = config?.proxy_names ?? []

  return (
    <>
      <h1 className="page-title">Proxy Groups</h1>

      {error && <p style={{ color: 'var(--warm)', fontSize: 13, marginBottom: 16 }}>{error}</p>}

      {/* Provider groups — read-only */}
      <div className="section-header">
        <span className="section-label">Provider</span>
        <span className="section-badge">{subGroups.length}</span>
      </div>
      <div className="card-list">
        {subGroups.length === 0
          ? <div style={{ color: 'var(--dim)', fontSize: 13, padding: '8px 0' }}>No provider groups</div>
          : subGroups.map(name => (
            <div key={name} className="card-row readonly">
              <span className="card-name">{name}</span>
            </div>
          ))
        }
      </div>

      {/* User groups — CRUD */}
      <div className="section-header">
        <span className="section-label">Your Groups</span>
        <button className="btn btn-ghost" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px' }} onClick={() => setShowAdd(true)}>
          <IconPlus size={13} /> Add Group
        </button>
      </div>
      <div className="card-list">
        {userGroups.length === 0
          ? <div style={{ color: 'var(--dim)', fontSize: 13, padding: '8px 0' }}>No custom groups yet</div>
          : userGroups.map(g => (
            <div key={g.name} className="card-row">
              <span className="card-name">{g.name}</span>
              <span className="card-type">{g.type}</span>
              <span className="card-meta">{(g.proxies || []).length} proxies</span>
              <button className="btn btn-danger" onClick={() => handleDelete(g.name)} title="Delete">
                <IconX size={13} />
              </button>
            </div>
          ))
        }
      </div>

      {showAdd && (
        <AddGroupModal
          proxies={proxies}
          onAdd={handleAdd}
          onClose={() => setShowAdd(false)}
        />
      )}
    </>
  )
}
