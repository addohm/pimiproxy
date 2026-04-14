import { IconHome, IconGroups, IconRules, IconSettings, IconSun, IconMoon } from './Icons'

const NAV = [
  { id: 'overview', icon: IconHome,     label: 'Overview' },
  { id: 'groups',   icon: IconGroups,   label: 'Groups'   },
  { id: 'rules',    icon: IconRules,    label: 'Rules'    },
  { id: 'settings', icon: IconSettings, label: 'Settings' },
]

export default function Sidebar({ tab, onTab, theme, onTheme, onLogout }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-logo">pimi</div>

      {NAV.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          className={`nav-item${tab === id ? ' active' : ''}`}
          onClick={() => onTab(id)}
          title={label}
        >
          <Icon size={18} />
        </button>
      ))}

      <div className="nav-spacer" />

      <button className="nav-item" onClick={onTheme} title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
        {theme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
      </button>

      <button
        className="nav-item"
        onClick={onLogout}
        title="Sign out"
        style={{ fontSize: 12, fontWeight: 600, color: 'var(--dim)' }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
          <polyline points="16 17 21 12 16 7" />
          <line x1="21" y1="12" x2="9" y2="12" />
        </svg>
      </button>
    </nav>
  )
}
