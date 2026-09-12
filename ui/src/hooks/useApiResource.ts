import { useCallback, useEffect, useRef, useState } from 'react'

/** Resource state derived purely from API responses. */
export type ResourceStatus = 'loading' | 'ready' | 'error'

export interface ApiResource<T> {
  status: ResourceStatus
  data: T | null
  error: unknown | null
  /** Re-run the loader, keeping previously loaded data visible while loading. */
  reload: () => void
}

/**
 * Load a value from the API and expose explicit loading/ready/error state.
 *
 * The hook never invents fallback data: when a request fails the view receives
 * `status === 'error'` and the original error for user-visible handling.
 */
export function useApiResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): ApiResource<T> {
  const [status, setStatus] = useState<ResourceStatus>('loading')
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown | null>(null)
  const [attempt, setAttempt] = useState(0)
  const loadRef = useRef(load)
  loadRef.current = load

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setStatus('loading')
    setError(null)
    loadRef
      .current(controller.signal)
      .then((value) => {
        if (!active) {
          return
        }
        setData(value)
        setStatus('ready')
      })
      .catch((cause: unknown) => {
        if (!active || controller.signal.aborted) {
          return
        }
        setError(cause)
        setStatus('error')
      })
    return () => {
      active = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt])

  const reload = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  return { status, data, error, reload }
}
