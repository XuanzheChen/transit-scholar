import { useState } from 'react'
import { Badge } from '../../components/Badge'
import { Disclosure } from '../../components/Disclosure'
import { Note } from '../../components/Section'
import type { AgentRunMonitor } from './useAgentRunMonitor'
import { runPhaseLabel, runStatusLabel, runStatusTone } from './labels'
import { TimelineEvents } from './TimelineEvents'

export interface ActiveRunPanelProps {
  runId: string
  monitor: AgentRunMonitor
}

/**
 * The AgentRun currently being monitored.
 *
 * The run status shown here is the API-projected Run state. The Timeline is
 * expanded while the run is active and collapses automatically once the API
 * reports a terminal status, so the final answer becomes the primary result
 * (REQ-005 / AC-005 / AC-006).
 */
export function ActiveRunPanel({ runId, monitor }: ActiveRunPanelProps) {
  const [expandedWhileActive, setExpandedWhileActive] = useState(true)
  // Derived, not synchronized: a settled run is always shown collapsed.
  const expanded = monitor.settled ? false : expandedWhileActive

  const { run } = monitor
  const displayStatus = run?.display_status ?? run?.status ?? null

  return (
    <section className="run-panel" data-testid="active-run" aria-label="Active agent run">
      <header className="run-panel__header">
        <div className="run-panel__heading">
          <h3 className="subsection__title">Agent run</h3>
          <p className="run-panel__meta">Run {runId}</p>
        </div>
        <div className="run-panel__status" role="status" aria-live="polite">
          <Badge tone={run ? runStatusTone(run.status) : 'neutral'} testId="run-status">
            {run ? runStatusLabel(run.status) : 'Loading'}
          </Badge>
          {run ? <span className="run-panel__phase">Phase: {runPhaseLabel(run.phase)}</span> : null}
          {run && displayStatus && displayStatus !== run.status ? (
            <span className="run-panel__phase">Reported state: {displayStatus}</span>
          ) : null}
        </div>
      </header>

      {monitor.status === 'error' && monitor.events.length > 0 ? (
        <Note tone="warning">Run status could not be refreshed; showing the last known state.</Note>
      ) : null}

      <Disclosure
        summary={`Research process · ${monitor.events.length} step${monitor.events.length === 1 ? '' : 's'}`}
        open={expanded}
        onOpenChange={setExpandedWhileActive}
        testId="run-timeline"
      >
        <TimelineEvents
          events={monitor.events}
          status={monitor.status}
          error={monitor.error}
          onRetry={monitor.refresh}
        />
      </Disclosure>
    </section>
  )
}
