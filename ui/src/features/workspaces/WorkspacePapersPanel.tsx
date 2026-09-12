import { useMemo, useState } from 'react'
import { api } from '../../api'
import type { PaperSummary } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Field, Select } from '../../components/Form'
import { Link } from '../../app/router'
import { LinkButton } from '../../components/LinkButton'
import { Note } from '../../components/Section'
import { pluralize } from '../../lib/format'
import { paperStatusTone } from '../library/labels'

/**
 * Workspace paper membership.
 *
 * Adding and removing a Paper here changes Workspace membership only. It never
 * deletes the Paper from the global Library, and the wording is kept distinct
 * from the Library deletion vocabulary (CON-009 / C-009).
 */
export function WorkspacePapersPanel({ workspaceId }: { workspaceId: string }) {
  const members = useApiResource((signal) => api.workspaces.listPapers(workspaceId, { signal }), [workspaceId])
  const library = useApiResource((signal) => api.papers.list({ signal, limit: 500 }), [])
  const [selectedPaperId, setSelectedPaperId] = useState('')

  const add = useAsyncAction((paperId: string) => api.workspaces.addPaper(workspaceId, paperId))
  const remove = useAsyncAction((paperId: string) => api.workspaces.removePaper(workspaceId, paperId))

  const memberIds = useMemo(
    () => new Set((members.data?.items ?? []).map((item) => item.paper_id)),
    [members.data],
  )

  const libraryById = useMemo(() => {
    const map = new Map<string, PaperSummary>()
    for (const paper of library.data?.items ?? []) {
      map.set(paper.paper_id, paper)
    }
    return map
  }, [library.data])

  const candidates = useMemo(
    () => (library.data?.items ?? []).filter((paper) => !memberIds.has(paper.paper_id)),
    [library.data, memberIds],
  )

  function describeMember(paperId: string): string {
    const paper = libraryById.get(paperId)
    if (!paper) {
      return paperId
    }
    return paper.title ?? 'Untitled paper'
  }

  async function handleAdd(): Promise<void> {
    if (!selectedPaperId) {
      return
    }
    const result = await add.run(selectedPaperId)
    if (result) {
      setSelectedPaperId('')
      members.reload()
    }
  }

  async function handleRemove(paperId: string): Promise<void> {
    const result = await remove.run(paperId)
    if (result) {
      members.reload()
    }
  }

  return (
    <div className="subsection">
      <div className="subsection__header">
        <h3 className="subsection__title">Papers in this workspace</h3>
      </div>

      {members.status === 'loading' ? <LoadingState label="Loading workspace papers…" /> : null}

      {members.status === 'error' ? (
        <ErrorState error={members.error} onRetry={members.reload} title="Workspace papers unavailable" />
      ) : null}

      {members.status === 'ready' && (members.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          title="No papers in this workspace"
          description="Add a paper from the global Library to make it available for research in this workspace."
          action={<LinkButton to="/library">Open Library</LinkButton>}
        />
      ) : null}

      {members.status === 'ready' && (members.data?.items.length ?? 0) > 0 ? (
        <ul className="card-list" data-testid="workspace-paper-list">
          {members.data?.items.map((member) => {
            const paper = libraryById.get(member.paper_id)
            return (
              <li className="card" key={member.paper_id}>
                <div className="card__main">
                  <div className="card__title-row">
                    <h4 className="card__title">{describeMember(member.paper_id)}</h4>
                    {paper ? <Badge tone={paperStatusTone(paper.status)}>{paper.status}</Badge> : null}
                  </div>
                  <p className="card__meta">
                    <Link to={`/library/${encodeURIComponent(member.paper_id)}`}>Open paper</Link>
                  </p>
                </div>
                <div className="card__actions">
                  <Button
                    size="sm"
                    variant="danger"
                    busy={remove.running}
                    onClick={() => {
                      void handleRemove(member.paper_id)
                    }}
                    data-testid={`remove-paper-${member.paper_id}`}
                  >
                    Remove from workspace
                  </Button>
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}

      {remove.status === 'error' ? (
        <ErrorState error={remove.error} title="Paper could not be removed from this workspace" />
      ) : null}

      <div className="membership-add">
        <Field
          label="Add a paper from the Library"
          htmlFor={`${workspaceId}-add-paper`}
          description="The paper stays in the global Library; it is only added to this workspace."
        >
          <Select
            id={`${workspaceId}-add-paper`}
            data-testid="add-workspace-paper-select"
            value={selectedPaperId}
            disabled={add.running || candidates.length === 0}
            onChange={(event) => setSelectedPaperId(event.target.value)}
          >
            <option value="">
              {candidates.length === 0
                ? 'No Library papers available to add…'
                : 'Select a Library paper…'}
            </option>
            {candidates.map((paper) => (
              <option key={paper.paper_id} value={paper.paper_id}>
                {paper.title ?? 'Untitled paper'}
              </option>
            ))}
          </Select>
        </Field>
        <Button
          variant="primary"
          size="md"
          busy={add.running}
          disabled={!selectedPaperId || add.running}
          onClick={() => {
            void handleAdd()
          }}
          data-testid="add-workspace-paper-submit"
        >
          Add to workspace
        </Button>
      </div>

      {add.status === 'error' ? (
        <ErrorState error={add.error} title="Paper could not be added to this workspace" />
      ) : null}
      {add.status === 'done' && add.result?.already_member ? (
        <Note>The paper was already a member of this workspace.</Note>
      ) : null}

      <Note>
        {pluralize(members.data?.items.length ?? 0, 'paper')} in this workspace. Removing a paper from a
        workspace only ends this workspace&apos;s membership; the paper remains in the global Library.
      </Note>
    </div>
  )
}
