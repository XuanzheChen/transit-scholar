import { useState } from 'react'
import { Disclosure } from '../../components/Disclosure'
import { useAgentRunMonitor } from './useAgentRunMonitor'
import { TimelineEvents } from './TimelineEvents'

export interface CompletedRunTimelineProps {
  runId: string
}

/**
 * Research Timeline for a Conversation turn whose AgentRun already finished.
 *
 * REQ-005 requires the Timeline to remain available after the final answer, but
 * collapsed by default. The events are fetched once, only when the reader opens
 * the disclosure, so completed turns cost nothing until inspected.
 */
export function CompletedRunTimeline({ runId }: CompletedRunTimelineProps) {
  const [expanded, setExpanded] = useState(false)
  const monitor = useAgentRunMonitor(runId, { enabled: expanded, poll: false })

  const count = monitor.events.length
  const summary = count > 0 ? `Research process · ${count} step${count === 1 ? '' : 's'}` : 'Research process'

  return (
    <Disclosure
      summary={summary}
      open={expanded}
      onOpenChange={setExpanded}
      testId={`turn-timeline-${runId}`}
    >
      <TimelineEvents
        events={monitor.events}
        status={monitor.status}
        error={monitor.error}
        onRetry={monitor.refresh}
      />
    </Disclosure>
  )
}
