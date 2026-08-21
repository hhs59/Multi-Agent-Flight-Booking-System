import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  Bot,
  Compass,
  MapPin,
  MessageCircle,
  Pencil,
  Plane,
  Plus,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  TrendingUp,
  UserRound,
  Zap,
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
import { Button, ConfirmDialog, EmptyState, ErrorState, Input, PromptDialog, Skeleton } from '../components/ui'
import { OfferBookingDialog } from '../components/OfferBookingDialog'
import { TravelPreferencesPanel } from '../components/TravelPreferencesPanel'
import { StructuredResult } from '../components/StructuredResult'
import { SecureMutationError } from '../components/SecureMutationError'
import type { Message, MessagePage, MessageTurn, Offer, Thread, ThreadPage } from '../types/api'
import { ApiError } from '../api/errors'
import { relativeDate } from '../lib/format'

type SendVariables = { threadId: string; content: string; clientMessageId: string }

function isBackendUnavailableError(err: unknown): boolean {
  if (err instanceof ApiError) {
    return err.status === 0 || err.status === 404 || err.status === 401 || err.status === 403
  }
  return false
}

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
  const [renameTarget, setRenameTarget] = useState<Thread | null>(null)
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
    onMutate: async (thread: Thread) => {
      // 1. Immediately close confirmation dialog for instant response
      setDeleteTarget(null)

      // 2. Cancel outgoing queries
      await client.cancelQueries({ queryKey: queryKeys.threads })

      // 3. Snapshot previous threads
      const previousThreads = client.getQueryData<ThreadPage>(queryKeys.threads)

      // 4. Optimistically update thread list cache
      if (previousThreads) {
        client.setQueryData<ThreadPage>(queryKeys.threads, {
          ...previousThreads,
          items: previousThreads.items.filter((t) => t.id !== thread.id),
        })
      }

      // 5. If deleting current thread, navigate immediately
      if (thread.id === threadId) {
        navigate('/assistant')
      }

      return { previousThreads }
    },
    onError: (_err, _thread, context) => {
      if (context?.previousThreads) {
        client.setQueryData(queryKeys.threads, context.previousThreads)
      }
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: queryKeys.threads })
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

  const createAndFocus = (initialPrompt?: string): void => {
    createMutation.mutate(undefined, {
      onSuccess: (newThread) => {
        void client.invalidateQueries({ queryKey: queryKeys.threads })
        navigate('/assistant/' + newThread.id)
        if (initialPrompt && initialPrompt.trim()) {
          const clientMessageId = newMessageId()
          setOutgoing({
            threadId: newThread.id,
            content: initialPrompt.trim(),
            clientMessageId,
            state: 'sending',
          })
          sendMutation.mutate({
            threadId: newThread.id,
            content: initialPrompt.trim(),
            clientMessageId,
          })
        }
      },
    })
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
      {/* Sidebar Thread List */}
      <aside className="thread-rail">
        <div className="thread-rail-header">
          <div className="thread-rail-title">
            <MessageCircle size={17} className="text-primary" />
            <h2>Conversations</h2>
          </div>
          <Button
            variant="secondary"
            size="sm"
            loading={createMutation.isPending}
            onClick={() => createAndFocus()}
            aria-label="New conversation"
            className="new-chat-btn"
          >
            <Plus size={15} /> New Trip
          </Button>
        </div>

        {threadsQuery.isLoading ? (
          <div className="thread-skeletons">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </div>
        ) : null}

        {threadsQuery.isError && !isBackendUnavailableError(threadsQuery.error) ? (
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
              onRename={() => setRenameTarget(thread)}
              onDelete={() => setDeleteTarget(thread)}
            />
          ))}
          {!threadsQuery.data?.items.length && !threadsQuery.isLoading ? (
            <div className="no-threads-hint">
              <Compass size={24} className="hint-icon" />
              <p>No recent trips yet. Start a search to begin!</p>
            </div>
          ) : null}
        </div>
      </aside>

      {/* Main Content Area */}
      <section className="assistant-panel">
        {!threadId ? (
          <BookedAiHero
            onSubmitPrompt={(prompt) => createAndFocus(prompt)}
            loading={createMutation.isPending}
          />
        ) : (
          <div className="conversation">
            <div className="conversation-header">
              <div className="header-trip-info">
                <div className="active-trip-mark">
                  <Plane size={16} />
                </div>
                <div>
                  <h1>{activeThread?.title || 'New Flight Search'}</h1>
                  <span className="trip-subtext">{activeThread?.summary || 'Multi-Agent AI Active'}</span>
                </div>
              </div>

              {activeThread ? (
                <div className="inline-actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={openPreferences}
                    aria-label="Travel preferences"
                    title="Travel preferences"
                    className="action-icon-btn"
                  >
                    <SlidersHorizontal size={16} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setRenameTarget(activeThread)}
                    aria-label="Đổi tên cuộc trò chuyện"
                    title="Đổi tên"
                    className="action-icon-btn"
                  >
                    <Pencil size={15} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setDeleteTarget(activeThread)}
                    aria-label="Xóa cuộc trò chuyện"
                    title="Xóa"
                    className="action-icon-btn action-delete-btn"
                  >
                    <Trash2 size={15} />
                  </Button>
                </div>
              ) : null}
            </div>

            {threadQuery.isError ? (
              <ErrorState error={threadQuery.error} onRetry={() => void threadQuery.refetch()} />
            ) : null}

            <div className="message-list">
              {messagesQuery.isLoading ? (
                <div className="message-loading-wrap">
                  <Skeleton className="message-skeleton message-skeleton-wide" />
                  <Skeleton className="message-skeleton message-skeleton-short" />
                </div>
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
                    <Sparkles size={16} />
                  </div>
                  <div className="message-bubble assistant-bubble typing-bubble">
                    <span className="dot" />
                    <span className="dot" />
                    <span className="dot" />
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
                  icon={<Sparkles size={24} />}
                  title="How can I help you travel?"
                  description="Ask anything in plain English or Vietnamese — routes, budget, dates, or inspiration."
                />
              ) : null}
            </div>

            {/* Glowing Bottom Composer */}
            <div className="composer-wrap">
              <div className="composer chat-bar-glow">
                <Input
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      send()
                    }
                  }}
                  placeholder="Ask about a route, dates, or fare (e.g. Find flights Hanoi to Da Nang next Friday)..."
                  aria-label="Message the assistant"
                  maxLength={50000}
                  className="composer-input"
                />
                <button
                  type="button"
                  className="composer-send-btn"
                  onClick={send}
                  disabled={!canSend}
                  aria-label="Send message"
                >
                  <Send size={16} />
                </button>
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
        title="Xác nhận xóa cuộc trò chuyện?"
        message={`Bạn có chắc chắn muốn xóa cuộc trò chuyện "${deleteTarget?.title || 'Chuyến đi không tên'}"? Toàn bộ lịch sử trao đổi và các gợi ý chuyến bay sẽ bị xóa vĩnh viễn khỏi hệ thống.`}
        confirmLabel="Xóa cuộc trò chuyện"
        cancelLabel="Hủy bỏ"
        danger
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget)
        }}
        loading={deleteMutation.isPending}
      />

      <PromptDialog
        open={Boolean(renameTarget)}
        title="Đổi tên cuộc trò chuyện"
        subtitle="Đặt tên gợi nhớ cho chuyến bay hoặc lịch trình du lịch của bạn:"
        defaultValue={renameTarget?.title || ''}
        placeholder="Nhập tên chuyến đi (vd: Du lịch Đà Nẵng cuối tuần)..."
        confirmLabel="Lưu thay đổi"
        cancelLabel="Hủy bỏ"
        onCancel={() => setRenameTarget(null)}
        onConfirm={(newTitle) => {
          if (renameTarget) {
            renameMutation.mutate(
              { id: renameTarget.id, title: newTitle },
              {
                onSuccess: () => setRenameTarget(null),
              },
            )
          }
        }}
        loading={renameMutation.isPending}
      />
    </div>
  )
}

/* Booked.ai Signature Hero Section */
function BookedAiHero({
  onSubmitPrompt,
  loading,
}: {
  onSubmitPrompt: (prompt: string) => void
  loading: boolean
}) {
  const [heroInput, setHeroInput] = useState('')

  const handleHeroSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (heroInput.trim() && !loading) {
      onSubmitPrompt(heroInput.trim())
    }
  }

  const samplePrompts = [
    { label: '✈️ Hà Nội đi Singapore thứ Ba tới', query: 'Tìm vé máy bay từ Hà Nội đi Singapore thứ Ba tới dưới 5 triệu' },
    { label: '🏖️ Cuối tuần đi biển Đà Nẵng', query: 'Tìm vé máy bay từ Hồ Chí Minh đi Đà Nẵng thứ Sáu tuần này' },
    { label: '🗼 Chuyến bay Tokyo tháng sau', query: 'Chuyến bay giá rẻ nhất từ Hà Nội đi Tokyo tháng sau' },
    { label: '🌴 Du lịch nghỉ dưỡng Phú Quốc', query: 'Tìm chuyến bay từ Sài Gòn đi Phú Quốc dịp cuối tuần' },
  ]

  const featuredDestinations = [
    {
      city: 'The Hague & Amsterdam',
      country: 'Netherlands',
      tag: 'Culture & Art',
      price: 'From 12,500,000 ₫',
      desc: 'Immerse in historic canals, royal architecture, and world-class museum exhibitions.',
      query: 'Tìm chuyến bay từ Hà Nội đi Amsterdam tháng tới',
      image: 'https://images.unsplash.com/photo-1512470876302-972faa2aa9a4?w=600&q=80',
    },
    {
      city: 'Da Nang & Hoi An',
      country: 'Vietnam',
      tag: 'Beaches & Food',
      price: 'From 1,250,000 ₫',
      desc: 'Pristine beaches, Golden Bridge in Ba Na Hills, and the lantern-lit ancient town.',
      query: 'Tìm vé máy bay từ Hà Nội đi Đà Nẵng cuối tuần này',
      image: 'https://images.unsplash.com/photo-1559592413-7cec4d0cae2b?w=600&q=80',
    },
    {
      city: 'Singapore',
      country: 'Singapore',
      tag: 'City & Nature',
      price: 'From 2,850,000 ₫',
      desc: 'Gardens by the Bay, Marina Bay Sands skyline, and vibrant multicultural street dining.',
      query: 'Tìm chuyến bay từ TP.HCM đi Singapore tuần sau',
      image: 'https://images.unsplash.com/photo-1525625293386-3f8f99389edd?w=600&q=80',
    },
    {
      city: 'Tokyo',
      country: 'Japan',
      tag: 'Mega City',
      price: 'From 6,400,000 ₫',
      desc: 'Futuristic neon metropolis blended with historic shrines, culinary perfection, and shopping.',
      query: 'Tìm vé máy bay giá rẻ từ Hà Nội đi Tokyo',
      image: 'https://images.unsplash.com/photo-1503899036084-c55cdd92da26?w=600&q=80',
    },
  ]

  return (
    <div className="booked-hero-section">
      {/* Background Ambient Glows */}
      <div className="hero-ambient-glow glow-blue" />
      <div className="hero-ambient-glow glow-indigo" />

      <div className="hero-content">
        {/* Top Feature Pill */}
        <div className="hero-badge">
          <Sparkles size={14} className="hero-badge-icon" />
          <span>IATA-Accredited Multi-Agent AI Travel Concierge</span>
        </div>

        {/* Dynamic Title */}
        <h1 className="hero-title">
          <span className="title-lead">Your AI Travel Agent —</span>
          <br />
          <span className="title-highlight">
            Finds the <span className="underline-word">Cheapest Flights!</span>
          </span>
        </h1>

        <p className="hero-subtitle">
          Search across 300+ airlines with real-time pricing and zero hidden booking fees. Just chat naturally.
        </p>

        {/* Glowing Hero Search Bar (chat-bar-glow) */}
        <div className="hero-search-container chat-bar-glow">
          <form onSubmit={handleHeroSubmit} className="hero-search-form">
            <textarea
              value={heroInput}
              onChange={(e) => setHeroInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleHeroSubmit(e)
                }
              }}
              placeholder="Where do you want to fly? (e.g. Find cheap flights from Hanoi to Singapore next Tuesday under 5M VND...)"
              className="hero-search-textarea"
              rows={2}
            />
            <div className="hero-search-actions">
              <button
                type="submit"
                disabled={!heroInput.trim() || loading}
                className="hero-submit-btn"
                aria-label="Search flights with AI"
              >
                <ArrowRight size={18} />
              </button>
            </div>
          </form>
        </div>

        {/* Suggestion Chips */}
        <div className="hero-chips-container">
          <span className="chips-label">Try asking:</span>
          <div className="chips-list">
            {samplePrompts.map((p, idx) => (
              <button
                key={idx}
                type="button"
                className="hero-prompt-chip"
                onClick={() => onSubmitPrompt(p.query)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {/* Trust Badges */}
        <div className="hero-trust-bar">
          <div className="trust-item">
            <Zap size={16} className="text-primary" />
            <span>300+ Global Airlines Aggregated</span>
          </div>
          <div className="trust-item">
            <ShieldCheck size={16} className="text-success" />
            <span>IATA Compliant & Encrypted PII</span>
          </div>
          <div className="trust-item">
            <TrendingUp size={16} className="text-accent" />
            <span>Live Price Trend Watching</span>
          </div>
        </div>
      </div>

      {/* Latest in AI Travel / Trending Section */}
      <section className="latest-travel-section">
        <div className="section-header">
          <div>
            <h2 className="section-title">Latest in AI Travel</h2>
            <p className="section-subtitle">Trending destination ideas & cheap flight routes curated for you</p>
          </div>
        </div>

        <div className="destinations-grid">
          {featuredDestinations.map((dest, i) => (
            <article key={i} className="destination-card">
              <div className="destination-image-wrap">
                <img src={dest.image} alt={dest.city} className="destination-img" loading="lazy" />
                <span className="destination-tag">{dest.tag}</span>
                <span className="destination-price">{dest.price}</span>
              </div>
              <div className="destination-body">
                <div className="destination-location">
                  <MapPin size={13} className="loc-icon" />
                  <span>{dest.country}</span>
                </div>
                <h3 className="destination-name">{dest.city}</h3>
                <p className="destination-desc">{dest.desc}</p>
                <button
                  type="button"
                  className="destination-cta"
                  onClick={() => onSubmitPrompt(dest.query)}
                >
                  Search flights <ArrowRight size={14} />
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}

function ThreadListItem({
  thread,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  thread: Thread
  active: boolean
  onSelect: () => void
  onRename: () => void
  onDelete: () => void
}) {
  return (
    <div className={'thread-item ' + (active ? 'thread-item-active' : '')}>
      <button type="button" className="thread-item-main" onClick={onSelect}>
        <span className="thread-item-icon">
          <MessageCircle size={15} />
        </span>
        <span className="thread-text-wrap">
          <strong className="thread-title">{thread.title || 'Untitled trip'}</strong>
          <small className="thread-summary">{thread.summary || 'No summary yet'}</small>
        </span>
      </button>
      <div className="thread-item-actions">
        <button
          type="button"
          className="thread-action-btn thread-rename"
          onClick={(e) => {
            e.stopPropagation()
            onRename()
          }}
          aria-label="Đổi tên cuộc trò chuyện"
          title="Đổi tên"
        >
          <Pencil size={13} />
        </button>
        <button
          type="button"
          className="thread-action-btn thread-delete"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          aria-label="Xóa cuộc trò chuyện"
          title="Xóa"
        >
          <Trash2 size={13} />
        </button>
      </div>
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
          {assistant ? <Bot size={16} /> : <UserRound size={16} />}
        </div>
        <div className={'message-bubble ' + (assistant ? 'assistant-bubble' : 'user-bubble')}>
          <p className="message-text">{message.content}</p>
          <small className="message-timestamp">{relativeDate(message.created_at)}</small>
        </div>
      </div>
      {assistant && message.result ? (
        <div className="message-result-wrap">
          <StructuredResult
            value={message.result}
            onBook={onBook}
            onSelectInspirationOption={onSelectInspirationOption}
          />
        </div>
      ) : null}
    </div>
  )
}

function OutgoingBubble({ message }: { message: OutgoingMessage }) {
  const failed = message.state === 'failed'
  return (
    <div className="message-row user-row outgoing-row">
      <div className="message-avatar">
        <UserRound size={16} />
      </div>
      <div
        className={
          'message-bubble user-bubble outgoing-bubble' + (failed ? ' outgoing-failed' : '')
        }
      >
        <p className="message-text">{message.content}</p>
        <small className="message-timestamp">{failed ? 'Not sent' : 'Sending...'}</small>
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

function newMessageId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : 'message-' + Date.now()
}
