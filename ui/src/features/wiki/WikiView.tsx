import { useState } from 'react'
import { api } from '../../api'
import { useLocation } from '../../app/router'
import { useApiResource } from '../../hooks/useApiResource'
import { ErrorState, LoadingState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { Section } from '../../components/Section'
import { RequiresWorkspace } from '../workspaces/RequiresWorkspace'
import { AgentLearnedEntryDetailView } from './AgentLearnedEntryDetailView'
import { AgentLearnedPanel } from './AgentLearnedPanel'
import { SchemaWikiPanel } from './SchemaWikiPanel'
import { WikiEntityDetailView } from './WikiEntityDetailView'
import { WikiPageDetailView } from './WikiPageDetailView'
import { WikiSearchPanel } from './WikiSearchPanel'
import { WikiStatusPanel } from './WikiStatusPanel'

type WikiTab = 'schema' | 'agentic' | 'search'

const WIKI_TABS: { id: WikiTab; label: string }[] = [
  { id: 'schema', label: 'Schema Wiki' },
  { id: 'agentic', label: 'Agent Learned' },
  { id: 'search', label: 'Search' },
]

const WIKI_DESCRIPTION =
  'Workspace knowledge in two clearly separated forms: Schema Wiki content built from the bound Schema, and Agent Learned entries promoted from research.'

/** One segment of a Wiki detail route, or `null` for the overview. */
function detailId(pathname: string, base: string): string | null {
  const prefix = `${base}/`
  if (!pathname.startsWith(prefix)) {
    return null
  }
  const rest = pathname.slice(prefix.length).replace(/\/$/, '')
  if (rest === '' || rest.includes('/')) {
    return null
  }
  return decodeURIComponent(rest)
}

/**
 * Workspace Wiki.
 *
 * `/wiki` shows Wiki capability/status, Schema Wiki content, Agent Learned
 * entries, and Wiki search. `/wiki/pages/{id}`, `/wiki/entities/{id}`, and
 * `/wiki/entries/{id}` show the detail of one knowledge object.
 *
 * All knowledge comes from the frozen Wiki API for the open Workspace; the UI
 * keeps no second authoritative copy and never treats an unsupported Schema
 * Wiki as an application error (REQ-010 / REQ-013).
 */
export function WikiView() {
  const pathname = useLocation().split('?')[0]
  const pageId = detailId(pathname, '/wiki/pages')
  const entityId = detailId(pathname, '/wiki/entities')
  const entryId = detailId(pathname, '/wiki/entries')

  if (pageId !== null) {
    return (
      <Section
        title="Wiki page"
        description="One Schema Wiki page for the open workspace."
        actions={<LinkButton to="/wiki">Back to Wiki</LinkButton>}
      >
        <RequiresWorkspace>
          {(workspaceId) => <WikiPageDetailView workspaceId={workspaceId} pageId={pageId} />}
        </RequiresWorkspace>
      </Section>
    )
  }

  if (entityId !== null) {
    return (
      <Section
        title="Wiki topic"
        description="One Schema Wiki topic for the open workspace."
        actions={<LinkButton to="/wiki">Back to Wiki</LinkButton>}
      >
        <RequiresWorkspace>
          {(workspaceId) => <WikiEntityDetailView workspaceId={workspaceId} entityId={entityId} />}
        </RequiresWorkspace>
      </Section>
    )
  }

  if (entryId !== null) {
    return (
      <Section
        title="Agent Learned entry"
        description="One Agent Learned knowledge entry for the open workspace."
        actions={<LinkButton to="/wiki">Back to Wiki</LinkButton>}
      >
        <RequiresWorkspace>
          {(workspaceId) => (
            <AgentLearnedEntryDetailView workspaceId={workspaceId} entryId={entryId} />
          )}
        </RequiresWorkspace>
      </Section>
    )
  }

  return (
    <Section title="Wiki" description={WIKI_DESCRIPTION}>
      <RequiresWorkspace>
        {(workspaceId) => <WikiOverviewView workspaceId={workspaceId} />}
      </RequiresWorkspace>
    </Section>
  )
}

function WikiOverviewView({ workspaceId }: { workspaceId: string }) {
  const [tab, setTab] = useState<WikiTab>('schema')
  const overview = useApiResource(
    (signal) => api.wiki.overview(workspaceId, { signal }),
    [workspaceId],
  )
  const workspace = useApiResource(
    (signal) => api.workspaces.get(workspaceId, { signal }),
    [workspaceId],
  )

  if (overview.status === 'error') {
    return (
      <ErrorState
        error={overview.error}
        title="Workspace knowledge unavailable"
        onRetry={overview.reload}
      />
    )
  }
  if (workspace.status === 'error') {
    return (
      <ErrorState
        error={workspace.error}
        title="Workspace unavailable"
        description="The open workspace could not be read, so its Wiki capability is unknown."
        onRetry={workspace.reload}
      />
    )
  }

  const overviewData = overview.data
  const workspaceData = workspace.data
  if (!overviewData || !workspaceData) {
    return <LoadingState label="Loading workspace knowledge…" />
  }

  return (
    <div className="stack">
      <WikiStatusPanel overview={overviewData} schemaMode={workspaceData.schema_mode} />
      <WikiTabStrip
        active={tab}
        agenticEntryCount={overviewData.agentic_wiki_entry_count}
        onChange={setTab}
      />
      <div
        className="wiki-panel"
        role="tabpanel"
        id={`wiki-panel-${tab}`}
        aria-labelledby={`wiki-tab-${tab}`}
        data-testid={`wiki-panel-${tab}`}
      >
        {tab === 'schema' ? (
          <SchemaWikiPanel
            workspaceId={workspaceId}
            overview={overviewData}
            schemaMode={workspaceData.schema_mode}
            onWikiChanged={overview.reload}
          />
        ) : null}
        {tab === 'agentic' ? <AgentLearnedPanel workspaceId={workspaceId} /> : null}
        {tab === 'search' ? <WikiSearchPanel workspaceId={workspaceId} /> : null}
      </div>
    </div>
  )
}

interface WikiTabStripProps {
  active: WikiTab
  agenticEntryCount: number
  onChange: (tab: WikiTab) => void
}

/** Accessible tab strip separating the two knowledge sources and search. */
function WikiTabStrip({ active, agenticEntryCount, onChange }: WikiTabStripProps) {
  return (
    <div className="wiki-tabs" role="tablist" aria-label="Workspace knowledge">
      {WIKI_TABS.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          id={`wiki-tab-${item.id}`}
          aria-selected={active === item.id}
          aria-controls={`wiki-panel-${item.id}`}
          className={`wiki-tab${active === item.id ? ' wiki-tab--active' : ''}`}
          data-testid={`wiki-tab-${item.id}`}
          onClick={() => onChange(item.id)}
        >
          <span>{item.label}</span>
          {item.id === 'agentic' ? (
            <span className="wiki-tab__count" data-testid="wiki-tab-agentic-count">
              {agenticEntryCount}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  )
}
