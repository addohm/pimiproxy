import { useState, useEffect, useRef } from 'react'
import { api } from '../api'
import { IconPlus, IconX, IconGrip } from './Icons'

const RULE_TYPES = [
  'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD',
  'IP-CIDR', 'IP-CIDR6', 'GEOIP', 'GEOSITE',
  'PROCESS-NAME', 'PROCESS-PATH',
  'RULE-SET', 'MATCH',
]

function AddRuleModal({ groups, onAdd, onClose }) {
  const [ruleType, setRuleType] = useState('DOMAIN-SUFFIX')
  const [value, setValue]       = useState('')
  const [target, setTarget]     = useState(groups[0] || 'DIRECT')
  const [err, setErr]           = useState('')

  const needsValue = ruleType !== 'MATCH'

  async function submit(e) {
    e.preventDefault()
    if (needsValue && !value.trim()) { setErr('Value is required'); return }
    try {
      await onAdd({ rule_type: ruleType, value: needsValue ? value.trim() : '', target })
      onClose()
    } catch (ex) {
      setErr(ex.message)
    }
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-title">Add Rule</div>
        <form onSubmit={submit}>
          <div className="field">
            <label>Rule Type</label>
            <select value={ruleType} onChange={e => setRuleType(e.target.value)}>
              {RULE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          {needsValue && (
            <div className="field">
              <label>Value</label>
              <input value={value} onChange={e => setValue(e.target.value)} placeholder="e.g. example.com" autoFocus />
            </div>
          )}
          <div className="field">
            <label>Target</label>
            <select value={target} onChange={e => setTarget(e.target.value)}>
              <option value="DIRECT">DIRECT</option>
              <option value="REJECT">REJECT</option>
              {groups.map(g => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
          {err && <div style={{ color: 'var(--warm)', fontSize: 12, marginBottom: 12 }}>{err}</div>}
          <div className="modal-footer">
            <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary">Add Rule</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// Minimal drag-to-reorder list — no external deps
function DraggableRuleList({ rules, onDelete, onReorder }) {
  const dragIdx = useRef(null)
  const [order, setOrder] = useState(() => rules.map((_, i) => i))

  // Reset when rules change
  useEffect(() => { setOrder(rules.map((_, i) => i)) }, [rules.length])

  function onDragStart(i) { dragIdx.current = i }

  function onDragOver(e, i) {
    e.preventDefault()
    if (dragIdx.current === null || dragIdx.current === i) return
    const next = [...order]
    const [moved] = next.splice(dragIdx.current, 1)
    next.splice(i, 0, moved)
    dragIdx.current = i
    setOrder(next)
  }

  async function onDrop() {
    if (dragIdx.current === null) return
    dragIdx.current = null
    await onReorder(order)
  }

  if (rules.length === 0) {
    return <div style={{ color: 'var(--dim)', fontSize: 13, padding: '8px 0' }}>No custom rules yet</div>
  }

  return (
    <div className="card-list">
      {order.map((origIdx, displayIdx) => {
        const r = rules[origIdx]
        const text = r.value ? `${r.rule_type},${r.value},${r.target}` : `${r.rule_type},${r.target}`
        return (
          <div
            key={origIdx}
            className="card-row"
            draggable
            onDragStart={() => onDragStart(displayIdx)}
            onDragOver={e => onDragOver(e, displayIdx)}
            onDrop={onDrop}
            style={{ cursor: 'default' }}
          >
            <span className="drag-handle"><IconGrip size={15} /></span>
            <span className="card-type">{r.rule_type}</span>
            {r.value && <span className="card-name" style={{ fontSize: 13 }}>{r.value}</span>}
            <span className="card-meta" style={{ marginLeft: 'auto' }}>→ {r.target}</span>
            <button className="btn btn-danger" onClick={() => onDelete(origIdx)} title="Delete">
              <IconX size={13} />
            </button>
          </div>
        )
      })}
    </div>
  )
}

export default function Rules({ onUnauthorized }) {
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

  async function handleAdd(rule) {
    await api.addRule(rule)
    await load()
  }

  async function handleDelete(index) {
    try {
      await api.deleteRule(index)
      await load()
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setError(err.message)
    }
  }

  async function handleReorder(order) {
    try {
      await api.reorderRules(order)
      await load()
    } catch (err) {
      if (err.status === 401) { onUnauthorized(); return }
      setError(err.message)
    }
  }

  const [subSearch, setSubSearch] = useState('')

  const userRules  = config?.user_rules ?? []
  const allSubRules = config?.sub_rules ?? []
  const subRules   = subSearch.trim()
    ? allSubRules.filter(r => r.toLowerCase().includes(subSearch.toLowerCase()))
    : allSubRules
  const allGroups = [
    ...(config?.sub_group_names ?? []),
    ...(config?.user_groups ?? []).map(g => g.name),
  ]

  return (
    <>
      <h1 className="page-title">Rules</h1>

      {error && <p style={{ color: 'var(--warm)', fontSize: 13, marginBottom: 16 }}>{error}</p>}

      {/* User rules — drag to reorder */}
      <div className="section-header">
        <span className="section-label">Your Rules</span>
        <button className="btn btn-ghost" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 12px' }} onClick={() => setShowAdd(true)}>
          <IconPlus size={13} /> Add Rule
        </button>
      </div>
      <div style={{ fontSize: 11, color: 'var(--meta-text)', marginBottom: 10 }}>
        Applied first — drag to reorder
      </div>

      <DraggableRuleList
        rules={userRules}
        onDelete={handleDelete}
        onReorder={handleReorder}
      />

      {/* Provider rules — searchable */}
      <div className="section-header" style={{ marginTop: 32 }}>
        <span className="section-label">Provider</span>
        <span className="section-badge">
          {subSearch.trim()
            ? `${subRules.length} / ${config?.sub_rule_count ?? 0}`
            : `${config?.sub_rule_count ?? 0}`}
        </span>
      </div>
      <input
        type="search"
        placeholder="Search provider rules…"
        value={subSearch}
        onChange={e => setSubSearch(e.target.value)}
        style={{
          width: '100%', boxSizing: 'border-box',
          marginBottom: 10, padding: '7px 12px',
          background: 'var(--bg)', border: '1px solid var(--border-strong)',
          borderRadius: 8, color: 'var(--light)', fontSize: 13,
          outline: 'none',
        }}
      />
      <div className="card-list">
        {allSubRules.length === 0
          ? <div style={{ color: 'var(--dim)', fontSize: 13, padding: '8px 0' }}>No provider rules</div>
          : subRules.length === 0
            ? <div style={{ color: 'var(--dim)', fontSize: 13, padding: '8px 0' }}>No rules match</div>
            : subRules.map((r, i) => (
              <div key={i} className="card-row readonly">
                <span className="card-name" style={{ fontSize: 13, fontFamily: 'monospace' }}>{r}</span>
              </div>
            ))
        }
      </div>

      {showAdd && (
        <AddRuleModal
          groups={allGroups}
          onAdd={handleAdd}
          onClose={() => setShowAdd(false)}
        />
      )}
    </>
  )
}
