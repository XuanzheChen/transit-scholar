import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { resolveApiBaseUrl } from '../api'
import { Button } from '../components/Button'
import { CapabilityPanel } from '../components/CapabilityPanel'
import { ShortcutsDialog } from '../components/ShortcutsDialog'
import { formatTimestamp } from '../lib/format'
import { ActiveWorkspaceBar } from '../features/workspaces/ActiveWorkspaceBar'
import { useBackendStatus } from './BackendStatusContext'
import { PRODUCT_NAV_ITEMS, navItemForPath } from './navigation'
import { Link, navigate, useLocation } from './router'
import { focusPrimaryInput, useKeyboardShortcuts } from './useKeyboardShortcuts'

const CONNECTION_LABELS = {
  checking: 'Checking backend',
  connected: 'Backend connected',
  unavailable: 'Backend unavailable',
} as const

/**
 * Persistent product navigation shell.
 *
 * Sections are product concepts only. Backend connectivity and capability
 * availability are shown explicitly and derived from the API.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const activeItem = navItemForPath(location)
  const { connection, lastCheckedAt, refresh } = useBackendStatus()
  const [capabilitiesOpen, setCapabilitiesOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const apiBase = resolveApiBaseUrl() || 'this origin'

  useKeyboardShortcuts({
    onNavigate: navigate,
    onOpenHelp: () => setShortcutsOpen(true),
    onFocusPrimaryInput: focusPrimaryInput,
  })

  // The browser tab follows the active product section, not internal routes.
  useEffect(() => {
    document.title = activeItem ? `${activeItem.label} · TransitScholar` : 'TransitScholar'
  }, [activeItem])

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <header className="topbar">
        <div className="topbar__brand">
          <span className="topbar__mark" aria-hidden="true">
            TS
          </span>
          <div>
            <p className="topbar__name">TransitScholar</p>
            <p className="topbar__tagline">Local literature research workspace</p>
          </div>
        </div>

        <div className="topbar__right">
          {activeItem ? <span className="topbar__section">{activeItem.label}</span> : null}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setShortcutsOpen(true)}
            title="Keyboard shortcuts (?)"
            data-testid="shortcuts-help-button"
          >
            Shortcuts
          </Button>
          <div className="popover-anchor">
            <button
              type="button"
              className={`connection connection--${connection}`}
              onClick={() => setCapabilitiesOpen((open) => !open)}
              aria-expanded={capabilitiesOpen}
              aria-haspopup="dialog"
            >
              <span className={`status-dot status-dot--${connection}`} aria-hidden="true" />
              <span>{CONNECTION_LABELS[connection]}</span>
              <span className="connection__chevron" aria-hidden="true">
                {capabilitiesOpen ? '▴' : '▾'}
              </span>
            </button>

            {capabilitiesOpen ? (
              <div className="popover" role="dialog" aria-label="Backend capabilities">
                <div className="popover__header">
                  <div>
                    <p className="popover__title">Backend capabilities</p>
                    <p className="popover__meta">
                      API base: {apiBase} · checked {formatTimestamp(lastCheckedAt)}
                    </p>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => setCapabilitiesOpen(false)}>
                    Close
                  </Button>
                </div>
                <CapabilityPanel />
                <div className="popover__footer">
                  <Button size="sm" onClick={refresh}>
                    Re-check backend
                  </Button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <div className="app__body">
        <aside className="sidebar">
          <nav className="sidebar__nav" aria-label="Product sections">
            {PRODUCT_NAV_ITEMS.map((item) => {
              const isActive = activeItem?.id === item.id
              return (
                <Link
                  key={item.id}
                  to={item.path}
                  className={`nav-item${isActive ? ' nav-item--active' : ''}`}
                  aria-current={isActive ? 'page' : undefined}
                  title={`${item.label} · press g then ${item.shortcut}`}
                >
                  <span className="nav-item__label">{item.label}</span>
                  <span className="nav-item__summary">{item.summary}</span>
                </Link>
              )
            })}
          </nav>

          <ActiveWorkspaceBar />

          <p className="sidebar__footnote">
            Local single-user application · one server · API under <code>/api/v1</code>
          </p>
        </aside>

        <main className="app__main" id="main-content" tabIndex={-1}>
          {connection === 'unavailable' ? (
            <div className="banner banner--error" role="alert">
              <div>
                <p className="banner__title">Backend unavailable</p>
                <p className="banner__text">
                  The UI could not reach the TransitScholar API at <code>{apiBase}</code>. Start the local
                  server, then retry. Last checked {formatTimestamp(lastCheckedAt)}.
                </p>
              </div>
              <Button onClick={refresh}>Retry</Button>
            </div>
          ) : null}

          {connection === 'checking' && lastCheckedAt === null ? (
            <div className="banner banner--info" role="status">
              <p className="banner__text">Connecting to the local TransitScholar backend…</p>
            </div>
          ) : null}

          {children}
        </main>
      </div>

      {shortcutsOpen ? <ShortcutsDialog onClose={() => setShortcutsOpen(false)} /> : null}
    </div>
  )
}
