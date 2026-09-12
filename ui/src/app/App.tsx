import type { ReactElement } from 'react'
import { EmptyState } from '../components/AsyncState'
import { LinkButton } from '../components/LinkButton'
import { Section } from '../components/Section'
import { ResearchView } from '../features/conversations/ResearchView'
import { LibraryView } from '../features/library/LibraryView'
import { SchemasView } from '../features/schemas/SchemasView'
import { WikiView } from '../features/wiki/WikiView'
import { WorkspacesView } from '../features/workspaces/WorkspacesView'
import { ActiveWorkspaceProvider } from './ActiveWorkspaceContext'
import { AppShell } from './AppShell'
import { BackendStatusProvider } from './BackendStatusContext'
import { useLocation } from './router'

interface RouteDefinition {
  path: string
  element: ReactElement
}

const ROUTE_DEFINITIONS: RouteDefinition[] = [
  { path: '/workspaces', element: <WorkspacesView /> },
  { path: '/research', element: <ResearchView /> },
  { path: '/library', element: <LibraryView /> },
  { path: '/wiki', element: <WikiView /> },
  { path: '/schemas', element: <SchemasView /> },
]

function RouteView() {
  const location = useLocation()
  const [pathname = '/'] = location.split('?')
  const normalized = pathname === '/' ? '/workspaces' : pathname
  const route = ROUTE_DEFINITIONS.find(
    (definition) =>
      normalized === definition.path || normalized.startsWith(`${definition.path}/`),
  )

  if (!route) {
    return (
      <Section title="Page not found">
        <EmptyState
          title="This page does not exist"
          description={`No product section matches ${pathname}.`}
          action={<LinkButton to="/workspaces" variant="primary">Go to Workspaces</LinkButton>}
        />
      </Section>
    )
  }

  return route.element
}

export default function App() {
  return (
    <BackendStatusProvider>
      <ActiveWorkspaceProvider>
        <AppShell>
          <RouteView />
        </AppShell>
      </ActiveWorkspaceProvider>
    </BackendStatusProvider>
  )
}
