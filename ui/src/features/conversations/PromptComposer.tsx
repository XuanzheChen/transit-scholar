import { useEffect, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { api } from '../../api'
import type { RunState } from '../../api'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { ErrorState } from '../../components/AsyncState'
import { Button } from '../../components/Button'
import { Field } from '../../components/Form'
import { Note } from '../../components/Section'
import { resolvePrimaryControl } from '../runs/labels'

export interface PromptComposerProps {
  conversationId: string
  /** API Run state of the run currently being monitored, if any. */
  run: RunState | null
  /** `pause_resume` capability reported by `GET /api/v1/capabilities`. */
  pauseResumeAvailable: boolean
  onSubmitted: (agentRunId: string) => void
  /** Apply an API Run state response (pause/resume) to the shared monitor. */
  onRunState: (state: RunState) => void
}

/**
 * Prompt input and the single primary Send / Pause / Pausing / Resume control.
 *
 * The control's behaviour follows the API Run state and the reported
 * pause/resume capability. Pause is a cooperative request; the UI never
 * presents it as an immediate forced termination (REQ-006 / AC-007).
 */
export function PromptComposer({
  conversationId,
  run,
  pauseResumeAvailable,
  onSubmitted,
  onRunState,
}: PromptComposerProps) {
  const [message, setMessage] = useState('')

  const submit = useAsyncAction((value: string) =>
    api.conversations.submitTurn(conversationId, { message: value }),
  )
  const runId = run?.agent_run_id ?? null
  const pause = useAsyncAction((targetRunId: string) => api.runs.pause(targetRunId))
  const resume = useAsyncAction((targetRunId: string) => api.runs.resume(targetRunId))

  useEffect(() => {
    setMessage('')
    submit.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  const control = resolvePrimaryControl({
    run,
    pauseResumeAvailable,
    promptEmpty: message.trim() === '',
    submitting: submit.running,
  })

  async function handleSend(): Promise<void> {
    const value = message.trim()
    if (!value) {
      return
    }
    const result = await submit.run(value)
    if (result) {
      setMessage('')
      onSubmitted(result.agent_run_id)
    }
  }

  async function handlePause(): Promise<void> {
    if (!runId) {
      return
    }
    const state = await pause.run(runId)
    if (state) {
      onRunState(state)
    }
  }

  async function handleResume(): Promise<void> {
    if (!runId) {
      return
    }
    const state = await resume.run(runId)
    if (state) {
      onRunState(state)
    }
  }

  function handlePrimaryClick(): void {
    if (control.kind === 'send') {
      void handleSend()
    } else if (control.kind === 'pause') {
      void handlePause()
    } else if (control.kind === 'resume') {
      void handleResume()
    }
  }

  /** Ctrl/Cmd + Enter sends from the keyboard without leaving the prompt. */
  function handlePromptKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== 'Enter' || (!event.ctrlKey && !event.metaKey)) {
      return
    }
    if (control.kind !== 'send' || control.disabled) {
      return
    }
    event.preventDefault()
    void handleSend()
  }

  const busy = control.busy || pause.running || resume.running
  const promptLocked = control.kind !== 'send'

  return (
    <div className="composer" data-testid="prompt-composer">
      <Field
        label="Ask a research question"
        htmlFor="research-prompt"
        description="The agent answers from the papers in this workspace. Conversation context stays on the server."
      >
        <textarea
          id="research-prompt"
          className="textarea"
          data-testid="prompt-input"
          data-primary-input="research-prompt"
          rows={3}
          value={message}
          disabled={promptLocked}
          placeholder={promptLocked ? 'A run is active…' : 'What should the agent investigate?'}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={handlePromptKeyDown}
        />
      </Field>

      <div className="composer__actions">
        <Button
          variant="primary"
          size="md"
          busy={busy}
          disabled={control.disabled}
          onClick={handlePrimaryClick}
          data-testid="research-primary-control"
          data-control={control.kind}
        >
          {control.label}
        </Button>
        {control.hint ? <p className="composer__hint">{control.hint}</p> : null}
        {!promptLocked ? (
          <span className="composer__shortcut">
            <kbd className="kbd">Ctrl</kbd>/<kbd className="kbd">⌘</kbd> +{' '}
            <kbd className="kbd">Enter</kbd> to send
          </span>
        ) : null}
      </div>

      {submit.status === 'error' ? (
        <ErrorState error={submit.error} title="The prompt could not be submitted" />
      ) : null}
      {pause.status === 'error' ? (
        <ErrorState error={pause.error} title="Pause could not be requested" />
      ) : null}
      {resume.status === 'error' ? (
        <ErrorState error={resume.error} title="The run could not be resumed" />
      ) : null}

      {control.kind === 'pausing' ? (
        <Note>
          Pausing is cooperative: the current step finishes before the run pauses. Resume continues
          the same run rather than starting a new one.
        </Note>
      ) : null}
    </div>
  )
}
