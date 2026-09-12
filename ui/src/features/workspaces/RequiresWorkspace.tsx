import type { ReactNode } from 'react'
import { useActiveWorkspace } from '../../app/ActiveWorkspaceContext'
import { EmptyState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'

/**
 * Gate for Workspace-scoped product areas.
 *
 * Without an open Workspace the section shows a normal empty state instead of
 * inventing a Workspace or fetching unrelated data.
 */
export function RequiresWorkspace({ children }: { children: (workspaceId: string) => ReactNode }) {
  const { activeWorkspaceId } = useActiveWorkspace()

  if (activeWorkspaceId === null) {
    return (
      <EmptyState
        title="No workspace is open"
        description="Open a workspace to continue. Workspaces keep papers, conversations, and knowledge together."
        action={<LinkButton to="/workspaces" variant="primary">Go to Workspaces</LinkButton>}
      />
    )
  }

  return <>{children(activeWorkspaceId)}</>
}
