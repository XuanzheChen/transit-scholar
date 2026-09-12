import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, toApiError } from '../api'
import type { ApiError, CapabilityResponse, HealthResponse } from '../api'

export type BackendConnection = 'checking' | 'connected' | 'unavailable'

export interface BackendStatusValue {
  connection: BackendConnection
  health: HealthResponse | null
  capabilities: CapabilityResponse | null
  connectivityError: ApiError | null
  capabilitiesError: ApiError | null
  lastCheckedAt: string | null
  refresh: () => void
}

const BackendStatusContext = createContext<BackendStatusValue | null>(null)

export interface BackendStatusProviderProps {
  children: ReactNode
  /** Health re-check interval while the app stays open. */
  pollIntervalMs?: number
}

/**
 * Tracks backend connectivity from `GET /api/v1/health`.
 *
 * The connection state is always derived from the API. When the backend cannot
 * be reached the provider reports `unavailable` instead of assuming success.
 */
export function BackendStatusProvider({ children, pollIntervalMs = 30000 }: BackendStatusProviderProps) {
  const [connection, setConnection] = useState<BackendConnection>('checking')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [capabilities, setCapabilities] = useState<CapabilityResponse | null>(null)
  const [connectivityError, setConnectivityError] = useState<ApiError | null>(null)
  const [capabilitiesError, setCapabilitiesError] = useState<ApiError | null>(null)
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null)

  const check = useCallback(async (signal?: AbortSignal) => {
    setConnection((previous) => (previous === 'connected' ? previous : 'checking'))
    try {
      const healthResponse = await api.system.health(signal)
      if (signal?.aborted) {
        return
      }
      setHealth(healthResponse)
      setConnectivityError(null)
      setConnection('connected')
      try {
        const capabilityResponse = await api.system.capabilities(signal)
        if (signal?.aborted) {
          return
        }
        setCapabilities(capabilityResponse)
        setCapabilitiesError(null)
      } catch (error) {
        if (signal?.aborted) {
          return
        }
        setCapabilities(null)
        setCapabilitiesError(toApiError(error))
      }
    } catch (error) {
      if (signal?.aborted) {
        return
      }
      setHealth(null)
      setCapabilities(null)
      setCapabilitiesError(null)
      setConnectivityError(toApiError(error))
      setConnection('unavailable')
    } finally {
      if (!signal?.aborted) {
        setLastCheckedAt(new Date().toISOString())
      }
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    void check(controller.signal)
    const handleFocus = () => {
      void check()
    }
    window.addEventListener('focus', handleFocus)
    const timer = window.setInterval(() => {
      void check()
    }, pollIntervalMs)
    return () => {
      controller.abort()
      window.removeEventListener('focus', handleFocus)
      window.clearInterval(timer)
    }
  }, [check, pollIntervalMs])

  const refresh = useCallback(() => {
    void check()
  }, [check])

  const value = useMemo<BackendStatusValue>(
    () => ({
      connection,
      health,
      capabilities,
      connectivityError,
      capabilitiesError,
      lastCheckedAt,
      refresh,
    }),
    [connection, health, capabilities, connectivityError, capabilitiesError, lastCheckedAt, refresh],
  )

  return <BackendStatusContext.Provider value={value}>{children}</BackendStatusContext.Provider>
}

export function useBackendStatus(): BackendStatusValue {
  const value = useContext(BackendStatusContext)
  if (value === null) {
    throw new Error('useBackendStatus must be used inside a BackendStatusProvider')
  }
  return value
}
