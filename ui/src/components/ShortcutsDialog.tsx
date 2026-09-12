import { Modal } from './Modal'
import { NAVIGATION_CHORDS } from '../app/useKeyboardShortcuts'
import { PRODUCT_NAV_ITEMS } from '../app/navigation'

interface ShortcutRow {
  keys: string[]
  /** Rendered between keys, e.g. `+` for a combination or `then` for a chord. */
  joiner: string
  description: string
}

const NAVIGATION_ROWS: ShortcutRow[] = PRODUCT_NAV_ITEMS.map((item) => ({
  keys: ['g', item.shortcut],
  joiner: 'then',
  description: `Go to ${item.label}`,
}))

const SHORTCUT_ROWS: ShortcutRow[] = [
  ...NAVIGATION_ROWS,
  { keys: ['/'], joiner: '', description: "Focus the current view's main input" },
  { keys: ['Ctrl', 'Enter'], joiner: '+', description: 'Send the research prompt' },
  { keys: ['←', '→'], joiner: 'or', description: 'Move between citations in the citation dialog' },
  { keys: ['?'], joiner: '', description: 'Show this shortcut reference' },
  { keys: ['Esc'], joiner: '', description: 'Close the open dialog' },
]

/**
 * Shortcut reference.
 *
 * Discoverability matters more than the shortcuts themselves: the reference is
 * reachable from the shell and lists only interactions implemented by this UI.
 */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      title="Keyboard shortcuts"
      description="Shortcuts stay inactive while you are typing in a field."
      onClose={onClose}
      testId="shortcuts-dialog"
      footer={
        <button type="button" className="btn btn--ghost btn--md" onClick={onClose}>
          Close
        </button>
      }
    >
      <ul className="shortcut-list" data-testid="shortcut-list">
        {SHORTCUT_ROWS.map((row) => (
          <li className="shortcut-row" key={`${row.keys.join('-')}-${row.description}`}>
            <span className="shortcut-keys">
              {row.keys.map((key, index) => (
                <span className="shortcut-keys__part" key={`${key}-${index}`}>
                  {index > 0 && row.joiner ? (
                    <span className="shortcut-plus">{row.joiner === 'then' ? 'then' : row.joiner}</span>
                  ) : null}
                  <kbd className="kbd">{key}</kbd>
                </span>
              ))}
            </span>
            <span className="shortcut-description">{row.description}</span>
          </li>
        ))}
      </ul>
      <p className="note">
        Shortcuts only move between product sections and focus inputs. They never change Workspace,
        Schema, Paper, or AgentRun state.
      </p>
      <p className="card__meta">
        {Object.keys(NAVIGATION_CHORDS).length} section shortcuts · local single-user application
      </p>
    </Modal>
  )
}
