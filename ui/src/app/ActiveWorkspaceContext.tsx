import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

/** Browser-local key for the currently open Workspace selection. */
export const ACTIVE_WORKSPACE_STORAGE_KEY = 'transit-scholar.ui.active-workspace-id'

export interface ActiveWorkspaceValue {
  /**
   * The Workspace the user has opened in the UI.
   *
   * This is a navigation selection only. All Workspace data (name, status,
   * Schema binding, papers) is always read from the API for this identifier;
   * nothing about backend state is cached or duplicated here.
   */
  activeWorkspaceId: string | null
  selectWorkspace: (workspaceId: string | null) => void
}

const ActiveWorkspaceContext = createContext<ActiveWorkspaceValue | null>(null)

function readStoredWorkspaceId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY)
  } catch {
    return null
  }
}

function writeStoredWorkspaceId(workspaceId: string | null): void {
  try {
    if (workspaceId === null) {
      window.localStorage.removeItem(ACTIVE_WORKSPACE_STORAGE_KEY)
    } else {
      window.localStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, workspaceId)
    }
  } catch {
    // A blocked storage area must not break navigation.
  }
}

export function ActiveWorkspaceProvider({ children }: { children: ReactNode }) {
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(readStoredWorkspaceId)

  const selectWorkspace = useCallback((workspaceId: string | null) => {
    setActiveWorkspaceId(workspaceId)
    writeStoredWorkspaceId(workspaceId)
  }, [])

  const value = useMemo<ActiveWorkspaceValue>(
    () => ({ activeWorkspaceId, selectWorkspace }),
    [activeWorkspaceId, selectWorkspace],
  )

  return <ActiveWorkspaceContext.Provider value={value}>{children}</ActiveWorkspaceContext.Provider>
}

export function useActiveWorkspace(): ActiveWorkspaceValue {
  const value = useContext(ActiveWorkspaceContext)
  if (value === null) {
    throw new Error('useActiveWorkspace must be used inside an ActiveWorkspaceProvider')
  }
  return value
}
