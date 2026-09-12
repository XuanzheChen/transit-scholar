import { useBackendStatus } from '../app/BackendStatusContext'
import { Badge } from './Badge'
import { Note } from './Section'
import { Spinner } from './Spinner'

interface CapabilityRow {
  label: string
  available: boolean
  detail?: string
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return 'not reported'
  }
  return `${Math.round(bytes / (1024 * 1024))} MiB`
}

/**
 * Backend capability disclosure.
 *
 * Every value shown here comes from `GET /api/v1/capabilities`; unavailable
 * capabilities are presented explicitly instead of being hidden or guessed.
 */
export function CapabilityPanel() {
  const { connection, capabilities, capabilitiesError } = useBackendStatus()

  if (connection === 'checking') {
    return (
      <div className="capability-panel">
        <Spinner size="sm" />
        <span>Checking backend capabilities…</span>
      </div>
    )
  }

  if (connection === 'unavailable') {
    return (
      <div className="capability-panel">
        <Note>Capabilities are unknown while the backend is unavailable.</Note>
      </div>
    )
  }

  if (capabilitiesError) {
    return (
      <div className="capability-panel">
        <Note tone="warning">
          Capability information could not be loaded ({capabilitiesError.code}).
        </Note>
      </div>
    )
  }

  if (!capabilities) {
    return (
      <div className="capability-panel">
        <Note>The backend did not report capability information.</Note>
      </div>
    )
  }

  const rows: CapabilityRow[] = [
    { label: 'Pause and resume agent runs', available: capabilities.pause_resume },
    { label: 'Create Schema versions', available: capabilities.user_schema_creation },
    { label: 'Schema Wiki (base knowledge)', available: capabilities.base_wiki },
    { label: 'Agent Learned Wiki entries', available: capabilities.agentic_wiki },
    { label: 'Semantic Wiki search', available: capabilities.semantic_wiki_search },
    {
      label: 'PDF import limit',
      available: true,
      detail: formatBytes(capabilities.pdf_upload_max_bytes),
    },
  ]

  return (
    <div className="capability-panel">
      <dl className="capability-panel__list">
        {rows.map((row) => (
          <div className="capability-panel__row" key={row.label}>
            <dt>{row.label}</dt>
            <dd>
              {row.available ? (
                <Badge tone="success">{row.detail ?? 'Available'}</Badge>
              ) : (
                <Badge tone="neutral">Unavailable</Badge>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
