import { useCallback, useEffect, useRef, useState } from 'react'

/** Lifecycle of a single user-triggered write operation. */
export type ActionStatus = 'idle' | 'running' | 'error' | 'done'

export interface AsyncAction<Args extends unknown[], Result> {
  status: ActionStatus
  running: boolean
  /** The original failure from the API boundary; never a locally invented error. */
  error: unknown | null
  result: Result | null
  run: (...args: Args) => Promise<Result | null>
  reset: () => void
}

/**
 * Run an API mutation while exposing explicit idle/running/error/done state.
 *
 * The hook never interprets the failure: callers render `error` through the
 * shared `ErrorState`, which reads the API error envelope. A failed action
 * leaves the previous result untouched only when it has none; a successful one
 * replaces it.
 */
export function useAsyncAction<Args extends unknown[], Result>(
  action: (...args: Args) => Promise<Result>,
): AsyncAction<Args, Result> {
  const [status, setStatus] = useState<ActionStatus>('idle')
  const [error, setError] = useState<unknown | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const actionRef = useRef(action)
  const activeRef = useRef(true)

  useEffect(() => {
    actionRef.current = action
  }, [action])

  useEffect(() => {
    activeRef.current = true
    return () => {
      activeRef.current = false
    }
  }, [])

  const run = useCallback(async (...args: Args): Promise<Result | null> => {
    setStatus('running')
    setError(null)
    try {
      const value = await actionRef.current(...args)
      if (!activeRef.current) {
        return null
      }
      setResult(value)
      setStatus('done')
      return value
    } catch (cause) {
      if (!activeRef.current) {
        return null
      }
      setError(cause)
      setStatus('error')
      return null
    }
  }, [])

  const reset = useCallback(() => {
    setStatus('idle')
    setError(null)
    setResult(null)
  }, [])

  return { status, running: status === 'running', error, result, run, reset }
}
