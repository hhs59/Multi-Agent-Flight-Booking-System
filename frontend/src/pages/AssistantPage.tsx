import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  MessageCircle,
  MoreHorizontal,
  Plus,
  Send,
  SlidersHorizontal,
  Trash2,
  UserRound,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  createThread,
  deleteThread,
  getThread,
  listMessages,
  listThreads,
  renameThread,
  sendMessage,
} from '../api/services'
import { queryKeys } from '../api/queryKeys'
import { Button, ConfirmDialog, EmptyState, ErrorState, Input, Skeleton } from '../components/ui'
import { OfferBookingDialog } from '../components/OfferBookingDialog'
import { TravelPreferencesPanel } from '../components/TravelPreferencesPanel'
import { StructuredResult } from '../components/StructuredResult'
import { SecureMutationError } from '../components/SecureMutationError'
import type { Message, MessagePage, MessageTurn, Offer, Thread } from '../types/api'
import { relativeDate } from '../lib/format'

type SendVariables = { threadId: string; content: string; clientMessageId: string }

type OutgoingMessage = {
  threadId: string
  content: string
  clientMessageId: string
  state: 'sending' | 'failed'
}

export function AssistantPage() {
  const { threadId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const client = useQueryClient()
  const [draft, setDraft] = useState('')
  const [outgoing, setOutgoing] = useState<OutgoingMessage | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Thread | null>(null)
  const [selectedOffer, setSelectedOffer] = useState<Offer | null>(null)

  const threadsQuery = useQuery({ queryKey: queryKeys.threads, queryFn: () => listThreads() })
  const threadQuery = useQuery({
    queryKey: queryKeys.thread(threadId || ''),
    queryFn: () => getThread(threadId || ''),
    enabled: Boolean(threadId),
  })
  const messagesQuery = useQuery({
    queryKey: queryKeys.messages(threadId || ''),
    queryFn: () => listMessages(threadId || ''),
    enabled: Boolean(threadId),
  })
  const preferencesOpen = searchParams.get('panel') === 'preferences'

  const openPreferences = (): void => {
    setSearchParams(
      (current) => {
        current.set('panel', 'preferences')
        return current
      },
      { replace: true },
    )
  }

  const closePreferences = (): void => {
    setSearchParams(
      (current) => {
        current.delete('panel')
        return current
      },
      { replace: true },
    )
  }

  const createMutation = useMutation({
    mutationFn: () => createThread(),
    onSuccess: (thread) => {
      void client.invalidateQueries({ queryKey: queryKeys.threads })
      navigate('/assistant/' + thread.id)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (thread: Thread) => deleteThread(thread.id),
    onSuccess: (_, thread) => {
      setDeleteTarget(null)
      void client.invalidateQueries({ queryKey: queryKeys.threads })
      if (thread.id === threadId) navigate('/assistant')
    },
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => renameThread(id, title),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.threads }),
  })

  const sendMutation = useMutation({
    mutationFn: ({ threadId: activeThreadId, content, clientMessageId }: SendVariables) =>
      sendMessage(activeThreadId, content, clientMessageId),
    onSuccess: (response, variables) => {
      const messagesKey = queryKeys.messages(variables.threadId)
      const merged = mergeMessageTurn(client.getQueryData<MessagePage>(messagesKey), response.turn)
      client.setQueryData(messagesKey, merged)
      setOutgoing((current) => {
        if (current?.clientMessageId !== variables.clientMessageId) return current
        const persistedUser = merged.items.some(
          (message) =>
            message.role === 'user' &&
            (message.id === response.turn.message.id ||
              message.client_message_id === variables.clientMessageId),
        )
        return persistedUser ? null : current
      })
      void client.invalidateQueries({ queryKey: queryKeys.thread(variables.threadId) })
      void client.invalidateQueries({ queryKey: messagesKey })
      void client.invalidateQueries({ queryKey: queryKeys.threads })
    },
    onError: (_, variables) => {
      setOutgoing((current) =>
        current?.clientMessageId === variables.clientMessageId
          ? { ...current, state: 'failed' }
          : { ...variables, state: 'failed' },
      )
    },
  })

  const activeThread =
    threadQuery.data?.thread || threadsQuery.data?.items.find((thread) => thread.id === threadId)
  const messages = messagesQuery.data?.items || []
  const visibleOutgoing = outgoing?.threadId === threadId ? outgoing : null
  const mutationBelongsToThread = sendMutation.variables?.threadId === threadId
  const canSend = Boolean(threadId && draft.trim() && !sendMutation.isPending)

  const createAndFocus = (): void => {
    createMutation.mutate()
  }

  const sendContent = (rawContent: string, clearDraft = false): void => {
    const content = rawContent.trim()
    if (!threadId || !content || sendMutation.isPending) return
    const clientMessageId = newMessageId()
    const activeThreadId = threadId
    setOutgoing({
      threadId: activeThreadId,
      content,
      clientMessageId,
      state: 'sending',
    })
    if (clearDraft) setDraft('')
    sendMutation.mutate({ threadId: activeThreadId, content, clientMessageId })
  }

  const send = (): void => sendContent(draft, true)

  const retry = (): void => {
    const variables = sendMutation.variables
    if (!variables) return
    setOutgoing((current) =>
      current?.clientMessageId === variables.clientMessageId
        ? { ...current, state: 'sending' }
        : { ...variables, state: 'sending' },
    )
    sendMutation.mutate(variables)
  }

  return (
    <div className={threadId ? 'assistant-layout assistant-layout-active' : 'assistant-layout'}>
      <aside className="thread-rail">
        <div className="thread-rail-header">
          <h2>Chats</h2>
          <Button
            variant="secondary"
            size="sm"
            loading={createMutation.isPending}
            onClick={createAndFocus}
            aria-label="New conversation"
          >
            <Plus size={16} /> New
          </Button>
        </div>
        {threadsQuery.isLoading ? (
          <div className="thread-skeletons">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </div>
        ) : null}
        {threadsQuery.isError ? (
          <ErrorState
            error={threadsQuery.error}
            onRetry={() => void threadsQuery.refetch()}
            compact
          />
        ) : null}
        <div className="thread-list">
          {threadsQuery.data?.items.map((thread) => (
            <ThreadListItem
              key={thread.id}
              thread={thread}
              active={thread.id === threadId}
              onSelect={() => navigate('/assistant/' + thread.id)}
              onDelete={() => setDeleteTarget(thread)}
            />
          ))}
        </div>
      </aside>

      <section className="assistant-panel">
        {!threadId ? (
          <AssistantWelcome onStart={createAndFocus} loading={createMutation.isPending} />
        ) : (
          <div className="conversation">
            <div className="conversation-header">
              <h1>{activeThread?.title || 'New trip'}</h1>
              {activeThread ? (
                <div className="inline-actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={openPreferences}
                    aria-label="Travel preferences"
                    title="Travel preferences"
                  >
                    <SlidersHorizontal size={17} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => rename(activeThread, renameMutation)}
                    aria-label="Rename conversation"
                    title="Rename"
                  >
                    <MoreHorizontal size={17} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setDeleteTarget(activeThread)}
                    aria-label="Delete conversation"
                    title="Delete"
                  >
                    <Trash2 size={16} />
                  </Button>
                </div>
              ) : null}
            </div>
            {threadQuery.isError ? (
              <ErrorState error={threadQuery.error} onRetry={() => void threadQuery.refetch()} />
            ) : null}
            <div className="message-list">
              {messagesQuery.isLoading ? (
                <>
                  <Skeleton className="message-skeleton message-skeleton-wide" />
                  <Skeleton className="message-skeleton message-skeleton-short" />
                </>
              ) : null}
              {messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  onBook={setSelectedOffer}
                  onSelectInspirationOption={(rank, city) =>
                    sendContent(`Show flight options for ${city} (option ${rank})`)
                  }
                />
              ))}
              {visibleOutgoing ? <OutgoingBubble message={visibleOutgoing} /> : null}

              {mutationBelongsToThread && sendMutation.isPending ? (
                <div className="message-row assistant-row">
                  <div className="message-avatar">
                    <Bot size={15} />
                  </div>
                  <div className="message-bubble assistant-bubble typing-bubble">
                    <span />
                    <span />
                    <span />
                  </div>
                </div>
              ) : null}
              {mutationBelongsToThread && sendMutation.isError ? (
                <div className="send-error">
                  <SecureMutationError
                    error={sendMutation.error}
                    onRetry={retry}
                    retryLabel="Retry same message"
                  />
                </div>
              ) : null}
              {!messages.length && !visibleOutgoing && !messagesQuery.isLoading ? (
                <EmptyState
                  icon={<Bot size={22} />}
                  title="Start planning"
                  description="Tell me where, when, or how much you want to spend."
                />
              ) : null}
            </div>
            <div className="composer-wrap">
              <div className="composer">
                <Input
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      send()
                    }
                  }}
                  placeholder="Ask about a route, dates, or fare..."
                  aria-label="Message the assistant"
                  maxLength={50000}
                />
                <Button
                  size="sm"
                  onClick={send}
                  disabled={!canSend}
                  loading={sendMutation.isPending}
                  aria-label="Send message"
                >
                  <Send size={16} />
                </Button>
              </div>
            </div>
          </div>
        )}
      </section>

      <OfferBookingDialog
        offer={selectedOffer}
        threadId={threadId}
        onClose={() => setSelectedOffer(null)}
        onIntentCreated={(intentId) => {
          setSelectedOffer(null)
          navigate('/booking-intents/' + intentId)
        }}
      />

      <TravelPreferencesPanel open={preferencesOpen} onClose={closePreferences} />

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete this conversation?"
        message="This removes the thread and its chat history. Booking records are kept separately."
        confirmLabel="Delete conversation"
        danger
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget)
        }}
        loading={deleteMutation.isPending}
      />
    </div>
  )
}

function ThreadListItem({
  thread,
  active,
  onSelect,
  onDelete,
}: {
  thread: Thread
  active: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  return (
    <div className={'thread-item ' + (active ? 'thread-item-active' : '')}>
      <button type="button" className="thread-item-main" onClick={onSelect}>
        <span className="thread-item-icon">
          <MessageCircle size={15} />
        </span>
        <span>
          <strong>{thread.title || 'Untitled trip'}</strong>
          <small>{thread.summary || 'No summary yet'}</small>
        </span>
      </button>
      <button
        type="button"
        className="thread-delete"
        onClick={onDelete}
        aria-label="Delete conversation"
      >
        <Trash2 size={14} />
      </button>
    </div>
  )
}

function MessageBubble({
  message,
  onBook,
  onSelectInspirationOption,
}: {
  message: Message
  onBook: (offer: Offer) => void
  onSelectInspirationOption: (rank: number, city: string) => void
}) {
  const assistant = message.role === 'assistant'
  return (
    <div className="message-entry">
      <div className={'message-row ' + (assistant ? 'assistant-row' : 'user-row')}>
        <div className="message-avatar">
          {assistant ? <Bot size={15} /> : <UserRound size={15} />}
        </div>
        <div className={'message-bubble ' + (assistant ? 'assistant-bubble' : 'user-bubble')}>
          <p>{message.content}</p>
          <small>{relativeDate(message.created_at)}</small>
        </div>
      </div>
      {assistant && message.result ? (
        <StructuredResult
          value={message.result}
          onBook={onBook}
          onSelectInspirationOption={onSelectInspirationOption}
        />
      ) : null}
    </div>
  )
}

function OutgoingBubble({ message }: { message: OutgoingMessage }) {
  const failed = message.state === 'failed'
  return (
    <div className="message-row user-row outgoing-row">
      <div className="message-avatar">
        <UserRound size={15} />
      </div>
      <div
        className={
          'message-bubble user-bubble outgoing-bubble' + (failed ? ' outgoing-failed' : '')
        }
      >
        <p>{message.content}</p>
        <small>{failed ? 'Not sent' : 'Sending...'}</small>
      </div>
    </div>
  )
}

function mergeMessageTurn(page: MessagePage | undefined, turn: MessageTurn): MessagePage {
  const incoming = [turn.message, turn.assistant_message].filter(
    (message): message is Message => message !== null,
  )
  const incomingIds = new Set(incoming.map((message) => message.id))
  const incomingClientIds = new Set(
    incoming
      .map((message) => message.client_message_id)
      .filter((clientMessageId): clientMessageId is string => Boolean(clientMessageId)),
  )
  const retained = (page?.items ?? []).filter(
    (message) =>
      !incomingIds.has(message.id) &&
      (message.client_message_id === null || !incomingClientIds.has(message.client_message_id)),
  )
  const seen = new Set<string>()
  const items = [...retained, ...incoming].filter((message) => {
    const key =
      message.id ||
      [message.thread_id, message.client_message_id ?? '', message.role, message.sequence].join(':')
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  items.sort((left, right) => {
    const sequenceDifference = left.sequence - right.sequence
    if (sequenceDifference !== 0) return sequenceDifference
    return Date.parse(left.created_at) - Date.parse(right.created_at)
  })
  return { items, next_cursor: page?.next_cursor ?? null }
}

function AssistantWelcome({ onStart, loading }: { onStart: () => void; loading: boolean }) {
  return (
    <div className="assistant-welcome">
      <Button size="lg" loading={loading} onClick={onStart}>
        <Plus size={18} /> Start planning
      </Button>
    </div>
  )
}

function rename(thread: Thread, mutation: ReturnType<typeof useMutation>): void {
  const title = window.prompt('Name this conversation', thread.title || '')
  if (title?.trim()) mutation.mutate({ id: thread.id, title: title.trim() })
}

function newMessageId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : 'message-' + Date.now()
}
