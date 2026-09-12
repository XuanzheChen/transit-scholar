import { useState } from 'react'
import { api } from '../../api'
import type { ConversationSummary } from '../../api'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { ErrorState } from '../../components/AsyncState'
import { Button } from '../../components/Button'
import { Field, TextInput } from '../../components/Form'
import { Modal } from '../../components/Modal'

export interface NewConversationDialogProps {
  workspaceId: string
  onClose: () => void
  onCreated: (conversation: ConversationSummary) => void
}

/**
 * Create a Conversation through the existing Conversation API.
 *
 * The Conversation is persisted server-side; the frontend keeps only the
 * returned identifier and never reconstructs conversation context itself
 * (REQ-004).
 */
export function NewConversationDialog({ workspaceId, onClose, onCreated }: NewConversationDialogProps) {
  const [title, setTitle] = useState('')
  const create = useAsyncAction((conversationTitle: string) =>
    api.conversations.create(workspaceId, conversationTitle ? { title: conversationTitle } : {}),
  )

  async function handleCreate(): Promise<void> {
    const trimmed = title.trim()
    const result = await create.run(trimmed)
    if (result) {
      onCreated(result)
    }
  }

  return (
    <Modal
      title="New conversation"
      description="Conversations keep their turns on the server so context carries across prompts."
      onClose={onClose}
      testId="new-conversation-dialog"
      footer={
        <>
          <Button onClick={onClose} disabled={create.running}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={create.running}
            disabled={create.running}
            onClick={() => {
              void handleCreate()
            }}
            data-testid="create-conversation-submit"
          >
            Create conversation
          </Button>
        </>
      }
    >
      <Field
        label="Conversation title"
        htmlFor="new-conversation-title"
        description="Optional. A title helps you find this conversation later."
      >
        <TextInput
          id="new-conversation-title"
          data-testid="conversation-title-input"
          value={title}
          maxLength={512}
          disabled={create.running}
          onChange={(event) => setTitle(event.target.value)}
        />
      </Field>

      {create.status === 'error' ? (
        <ErrorState error={create.error} title="The conversation could not be created" />
      ) : null}
    </Modal>
  )
}
