import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import type { RunState, TimelineEvent } from '../../api'
import { isTerminalRunStatus } from './labels'

/**
 * Polling interval for an active AgentRun.
 *
 * The initial UI implementation polls instead of using SSE/WebSocket; the
 * recommended window is 500-1000 ms.
 */
export const RUN_POLL_INTERVAL_MS = 800

export type RunMonitorStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface AgentRunMonitor {
  status: RunMonitorStatus
  /** Authoritative API Run state; never invented locally. */
  run: RunState | null
  /** Timeline events accumulated with the API sequence cursor. */
  events: TimelineEvent[]
  error: unknown | null
  /** True once the API reported a terminal Run status. */
  settled: boolean
  /** Apply an API `RunState` response directly (pause/resume responses). */
  applyState: (state: RunState) => void
  /** Retry the status/Timeline read after a failure. */
  refresh: () => void
}

export interface AgentRunMonitorOptions {
  /** Whether the monitor should read the run at all. */
  enabled: boolean
  /** Whether to keep polling while the run is active. */
  poll: boolean
  /** Called once when the API first reports a terminal Run status. */
  onSettled?: (runId: string) => void
}

/**
 * Monitor one AgentRun through the frozen status and Timeline APIs.
 *
 * * The run state comes from `GET /api/v1/runs/{id}` only.
 * * Timeline events are requested incrementally with `after_sequence`, using
 *   the `next_sequence` cursor returned by the API, so the complete event
 *   history is not re-fetched on every poll.
 * * Polling stops as soon as the API reports a terminal status.
 */
export function useAgentRunMonitor(
  runId: string | null,
  { enabled, poll, onSettled }: AgentRunMonitorOptions,
): AgentRunMonitor {
  const [status, setStatus] = useState<RunMonitorStatus>('idle')
  const [run, setRun] = useState<RunState | null>(null)
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [error, setError] = useState<unknown | null>(null)
  const [settled, setSettled] = useState(false)
  const [attempt, setAttempt] = useState(0)

  const cursorRef = useRef(0)
  const trackedRunRef = useRef<string | null>(null)
  const settledRef = useRef(false)
  const notifiedRef = useRef(false)
  const onSettledRef = useRef(onSettled)
  onSettledRef.current = onSettled

  useEffect(() => {
    if (!runId || !enabled) {
      return
    }

    if (trackedRunRef.current !== runId) {
      trackedRunRef.current = runId
      cursorRef.current = 0
      settledRef.current = false
      notifiedRef.current = false
      setEvents([])
      setRun(null)
      setSettled(false)
      setError(null)
      setStatus('loading')
    }

    let active = true
    const controller = new AbortController()
    const currentRunId = runId

    async function tick(): Promise<void> {
      try {
        const state = await api.runs.read(currentRunId, { signal: controller.signal })
        if (!active) {
          return
        }
        setRun(state)
        setError(null)
        setStatus('ready')

        const timeline = await api.runs.timeline(currentRunId, cursorRef.current, {
          signal: controller.signal,
        })
        if (!active) {
          return
        }
        if (timeline.events.length > 0) {
          const lastSequence = timeline.events[timeline.events.length - 1].sequence
          cursorRef.current = Math.max(cursorRef.current, timeline.next_sequence, lastSequence)
          setEvents((previous) => {
            const known = new Set(previous.map((event) => event.sequence))
            const fresh = timeline.events.filter((event) => !known.has(event.sequence))
            return fresh.length === 0 ? previous : [...previous, ...fresh]
          })
        }

        if (isTerminalRunStatus(state.status)) {
          settledRef.current = true
          setSettled(true)
          if (!notifiedRef.current) {
            notifiedRef.current = true
            onSettledRef.current?.(currentRunId)
          }
        }
      } catch (cause) {
        if (!active || controller.signal.aborted) {
          return
        }
        setError(cause)
        setStatus((previous) => (previous === 'ready' ? 'ready' : 'error'))
      }
    }

    void tick()
    const timer = poll
      ? window.setInterval(() => {
          if (!settledRef.current) {
            void tick()
          }
        }, RUN_POLL_INTERVAL_MS)
      : undefined

    return () => {
      active = false
      controller.abort()
      if (timer !== undefined) {
        window.clearInterval(timer)
      }
    }
  }, [runId, enabled, poll, attempt])

  const applyState = useCallback((state: RunState) => {
    setRun(state)
    setStatus('ready')
    setError(null)
  }, [])

  const refresh = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  return { status, run, events, error, settled, applyState, refresh }
}
