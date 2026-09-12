import { useId, useState } from 'react'
import { api } from '../../api'
import type { PaperImportResponse } from '../../api'
import { useBackendStatus } from '../../app/BackendStatusContext'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Button } from '../../components/Button'
import { ErrorState } from '../../components/AsyncState'
import { Field, TextInput } from '../../components/Form'
import { Modal } from '../../components/Modal'
import { Note } from '../../components/Section'
import { formatFileSize } from '../../lib/format'
import { describeImportOutcome } from './labels'

export interface ImportPaperDialogProps {
  onClose: () => void
  onImported: (result: PaperImportResponse) => void
}

/**
 * PDF import flow.
 *
 * Uploads through the existing `POST /api/v1/papers/import` multipart endpoint.
 * The advertised upload limit comes from `GET /api/v1/capabilities`; the client
 * only refuses to send a file that already exceeds the reported limit, and the
 * backend remains the authority that enforces it.
 */
export function ImportPaperDialog({ onClose, onImported }: ImportPaperDialogProps) {
  const fileId = useId()
  const { capabilities, connection } = useBackendStatus()
  const [file, setFile] = useState<File | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)

  const upload = useAsyncAction((selected: File) => api.papers.importPdf(selected))

  const maxBytes = capabilities?.pdf_upload_max_bytes ?? null
  const tooLarge = file !== null && maxBytes !== null && file.size > maxBytes
  const importOutcome = upload.status === 'done' && upload.result ? describeImportOutcome(upload.result) : null
  const canSubmit = file !== null && !tooLarge && localError === null && !upload.running

  async function handleSubmit(): Promise<void> {
    if (!file || !canSubmit) {
      return
    }
    const result = await upload.run(file)
    if (result && !describeImportOutcome(result)) {
      onImported(result)
    }
  }

  return (
    <Modal
      title="Import a PDF"
      description="The PDF is stored in the local Library and processed by the TransitScholar backend."
      onClose={onClose}
      testId="import-paper-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={upload.running}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={upload.running}
            disabled={!canSubmit}
            onClick={() => {
              void handleSubmit()
            }}
            data-testid="import-paper-submit"
          >
            Import PDF
          </Button>
        </>
      }
    >
      <div className="stack stack--tight">
        <Field
          label="PDF file"
          htmlFor={fileId}
          required
          description={
            maxBytes !== null
              ? `Maximum upload size reported by the backend: ${formatFileSize(maxBytes)}.`
              : 'Upload size limit is not reported by the backend.'
          }
          error={tooLarge ? `This file is larger than the ${formatFileSize(maxBytes)} upload limit.` : localError}
        >
          <TextInput
            id={fileId}
            data-testid="import-paper-file-input"
            type="file"
            accept="application/pdf,.pdf"
            disabled={upload.running}
            onChange={(event) => {
              const selected = event.target.files?.[0] ?? null
              setFile(selected)
              setLocalError(
                selected && !/\.pdf$/i.test(selected.name) && selected.type !== 'application/pdf'
                  ? 'Only PDF files can be imported.'
                  : null,
              )
            }}
          />
        </Field>

        {file ? (
          <p className="card__meta" data-testid="import-paper-file-summary">
            {file.name} · {formatFileSize(file.size)}
          </p>
        ) : null}

        {connection === 'unavailable' ? (
          <Note tone="warning">
            The backend is currently unreachable, so the PDF cannot be imported right now.
          </Note>
        ) : null}

        {upload.status === 'error' ? (
          <ErrorState error={upload.error} title="The PDF could not be imported" />
        ) : null}

        {importOutcome ? (
          <div className="state-block state-block--unavailable" role="status" data-testid="import-paper-outcome">
            <p className="state-block__title">Import needs attention</p>
            <p className="state-block__description">{importOutcome}</p>
            {upload.result?.paper_id ? (
              <div className="state-block__action">
                <Button
                  onClick={() => {
                    if (upload.result) {
                      onImported(upload.result)
                    }
                  }}
                >
                  Open the existing paper
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        <Note>
          After import, the paper appears in the Library. Its processing status and research readiness are
          reported by the backend and can be inspected from the paper detail view.
        </Note>
      </div>
    </Modal>
  )
}
