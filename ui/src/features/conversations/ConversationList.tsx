import type { ConversationSummary } from '../../api'
import { Button } from '../../components/Button'
import { EmptyState } from '../../components/AsyncState'
import { formatTimestamp, pluralize } from '../../lib/format'

export interface ConversationListProps {
  conversations: ConversationSummary[]
  selectedConversationId: string | null
  onSelect: (conversationId: string) => void
  onNewConversation: () => void
}

/** Conversation navigation beside the active Conversation (REQ-004). */
export function ConversationList({
  conversations,
  selectedConversationId,
  onSelect,
  onNewConversation,
}: ConversationListProps) {
  return (
    <div className="research-nav" data-testid="conversation-list-panel">
      <div className="subsection__header">
        <h3 className="subsection__title">Conversations</h3>
        <Button
          size="sm"
          variant="primary"
          onClick={onNewConversation}
          data-testid="new-conversation-button"
        >
          New conversation
        </Button>
      </div>

      {conversations.length === 0 ? (
        <EmptyState
          title="No conversations yet"
          description="Create a conversation to ask the first research question in this workspace."
        />
      ) : (
        <ul className="card-list" data-testid="conversation-list">
          {conversations.map((conversation) => {
            const selected = conversation.conversation_id === selectedConversationId
            return (
              <li key={conversation.conversation_id}>
                <button
                  type="button"
                  className={`conversation-item${selected ? ' conversation-item--active' : ''}`}
                  aria-current={selected ? 'true' : undefined}
                  onClick={() => onSelect(conversation.conversation_id)}
                  data-testid={`conversation-item-${conversation.conversation_id}`}
                >
                  <span className="conversation-item__title">
                    {conversation.title ?? 'Untitled conversation'}
                  </span>
                  <span className="conversation-item__meta">
                    created {formatTimestamp(conversation.created_at)}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <p className="research-nav__count">{pluralize(conversations.length, 'conversation')}</p>
    </div>
  )
}
