import { IconHome, IconGroups, IconRules, IconSettings, IconSearch, IconSun, IconMoon } from './Icons'

const NAV_TOP = [
  { id: 'overview', icon: IconHome,   label: 'Overview'   },
  { id: 'groups',   icon: IconGroups, label: 'Groups'     },
  { id: 'rules',    icon: IconRules,  label: 'Rules'      },
  { id: 'check',    icon: IconSearch, label: 'Check URL'  },
]

export default function Sidebar({ tab, onTab, theme, onTheme, onLogout, dashUrl }) {
  return (
    <nav className="sidebar">
      <div className="sidebar-logo">pimi</div>

      {NAV_TOP.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          className={`nav-item${tab === id ? ' active' : ''}`}
          onClick={() => onTab(id)}
          title={label}
        >
          <Icon size={18} />
        </button>
      ))}

      {/* Metacubexd dashboard link */}
      <a
        href={dashUrl || '#'}
        target="_blank"
        rel="noopener noreferrer"
        className="xd-link"
        title="Open Metacubexd dashboard"
      >
        <span className="xd-paren">(</span>
        <span className="xd-text">xd</span>
        <span className="xd-paren">)</span>
      </a>

      <button
        className={`nav-item${tab === 'settings' ? ' active' : ''}`}
        onClick={() => onTab('settings')}
        title="Settings"
      >
        <IconSettings size={18} />
      </button>

      <div className="nav-spacer" />

      <button className="nav-item" onClick={onTheme} title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
        {theme === 'dark' ? <IconSun size={16} /> : <IconMoon size={16} />}
      </button>

      <button
        className="nav-item"
        onClick={onLogout}
        title="Sign out"
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
