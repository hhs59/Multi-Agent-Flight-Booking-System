import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Bell,
  CalendarClock,
  CheckCircle2,
  Pause,
  Play,
  Plus,
  Trash2,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { createWatch, deleteWatch, listWatches, transitionWatch } from '../api/services'
import { queryKeys } from '../api/queryKeys'
import {
  ApiNotice,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  InfoBanner,
  Input,
  Modal,
  Select,
  StatusBadge,
} from '../components/ui'
import type { FlightWatchCriteriaInput, WatchRecord } from '../types/api'
import { formatDate, formatDateTime, formatMoney } from '../lib/format'
import { SecureMutationError } from '../components/SecureMutationError'

type WatchAction = 'activate' | 'pause' | 'resume'
type TransitionAction = WatchAction | 'cancel'

type WatchForm = {
  origin: string
  destination: string
  dateFrom: string
  dateTo: string
  cabin: FlightWatchCriteriaInput['cabin']
  maxStops: string
  maximumPrice: string
  currency: string
  requireRefundable: boolean
}

const initialForm: WatchForm = {
  origin: 'SGN',
  destination: 'HAN',
  dateFrom: '',
  dateTo: '',
  cabin: 'economy',
  maxStops: '',
  maximumPrice: '',
  currency: 'VND',
  requireRefundable: false,
}

const transitionForStatus = (
  status: string,
): { action: WatchAction; label: string; icon: ReactNode } | null => {
  if (status === 'draft') return { action: 'activate', label: 'Activate', icon: <Play size={14} /> }
  if (status === 'active' || status === 'matched' || status === 'awaiting_confirmation') {
    return { action: 'pause', label: 'Pause', icon: <Pause size={14} /> }
  }
  if (status === 'paused' || status === 'needs_user_action' || status === 'failed') {
    return { action: 'resume', label: 'Resume', icon: <Play size={14} /> }
  }
  return null
}

const canCancel = (status: string): boolean =>
  [
    'draft',
    'active',
    'paused',
    'matched',
    'awaiting_confirmation',
    'needs_user_action',
    'failed',
    'booked',
  ].includes(status)

const isTerminal = (status: string): boolean =>
  ['expired', 'cancelled', 'completed'].includes(status)

export function WatchesPage() {
  const client = useQueryClient()
  const { restoreSecureSession, isRestoringSession } = useAuth()
  const query = useQuery({
    queryKey: queryKeys.watches,
    queryFn: listWatches,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<WatchForm>(initialForm)
  const [deleteTarget, setDeleteTarget] = useState<WatchRecord | null>(null)
  const [cancelTarget, setCancelTarget] = useState<WatchRecord | null>(null)
  const [transitionErrors, setTransitionErrors] = useState<Record<string, unknown>>({})

  const refresh = (): void => {
    void client.invalidateQueries({ queryKey: queryKeys.watches })
    setOpen(false)
  }

  const createMutation = useMutation({
    mutationFn: () =>
      createWatch({
        action_mode: 'notify',
        cabin: form.cabin,
        departure_date_from: form.dateFrom,
        departure_date_to: form.dateTo || form.dateFrom,
        destination: form.destination.trim().toUpperCase(),
        excluded_carriers: [],
        max_stops: form.maxStops === '' ? null : Number(form.maxStops),
        maximum_total: form.maximumPrice.trim()
          ? { amount: form.maximumPrice.trim(), currency: form.currency }
          : null,
        minimum_checked_pieces: null,
        notification_behavior: 'first_match',
        notification_channels: ['in_app'],
        origin: form.origin.trim().toUpperCase(),
        passengers: { adults: 1, children: 0, infants: 0 },
        preferred_carriers: [],
        purchase_deadline: null,
        require_refundable: form.requireRefundable,
        selected_provider: null,
        timezone: 'Asia/Ho_Chi_Minh',
        traveler_profile_ids: [],
      }),
    onSuccess: refresh,
  })

  const deleteMutation = useMutation({
    mutationFn: (watch: WatchRecord) => deleteWatch(watch.id),
    onSuccess: () => {
      setDeleteTarget(null)
      void client.invalidateQueries({ queryKey: queryKeys.watches })
    },
  })

  const transitionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: TransitionAction }) =>
      transitionWatch(id, action),
    onSuccess: (_watch, variables) => {
      setTransitionErrors((current) => {
        const next = { ...current }
        delete next[variables.id]
        return next
      })
      void client.invalidateQueries({ queryKey: queryKeys.watches })
    },
    onError: (error, variables) => {
      setTransitionErrors((current) => ({ ...current, [variables.id]: error }))
    },
  })

  const requestTransition = (watch: WatchRecord, action: TransitionAction): void => {
    setTransitionErrors((current) => {
      const next = { ...current }
      delete next[watch.id]
      return next
    })
    transitionMutation.mutate({ id: watch.id, action })
  }

  const transitionIsPending = (watchId: string): boolean =>
    transitionMutation.isPending && transitionMutation.variables?.id === watchId

  return (
    <div className="page">
      <div className="page-header">
        <h1>Price watches</h1>
        <Button onClick={() => setOpen(true)}>
          <Plus size={17} /> New watch
        </Button>
      </div>
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : null}
      {deleteMutation.isError ? (
        <SecureMutationError
          error={deleteMutation.error}
          onRetry={() => {
            if (deleteTarget) deleteMutation.mutate(deleteTarget)
          }}
          retryLabel="Retry watch deletion"
        />
      ) : null}
      {query.data?.length === 0 ? (
        <EmptyState
          icon={<Bell size={23} />}
          title="No price watches yet"
          description="Create a watch for a route and date window; you can pause or cancel it any time."
          action={<Button onClick={() => setOpen(true)}>Create a watch</Button>}
        />
      ) : null}
      {query.isLoading ? (
        <div className="watch-grid">
          <div className="card-skeleton" />
          <div className="card-skeleton" />
        </div>
      ) : null}
      {query.data?.length ? (
        <div className="watch-grid">
          {query.data.map((watch) => (
            <WatchCard
              key={watch.id}
              watch={watch}
              error={transitionErrors[watch.id]}
              onRestore={() => void restoreSecureSession()}
              restoring={isRestoringSession}
              onDelete={() => setDeleteTarget(watch)}
              onCancel={() => setCancelTarget(watch)}
              onTransition={(action) => requestTransition(watch, action)}
              loading={transitionIsPending(watch.id)}
            />
          ))}
        </div>
      ) : null}
      <Modal
        open={open}
        title="Create a price watch"
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              loading={createMutation.isPending}
              disabled={!form.dateFrom}
              onClick={() => createMutation.mutate()}
            >
              Create watch
            </Button>
          </>
        }
      >
        <div className="form-stack">
          <InfoBanner tone="info">
            This watch sends notifications only. It will not authorize payment or booking.
          </InfoBanner>
          <div className="form-grid-two">
            <Field label="From" required>
              <Input
                maxLength={3}
                value={form.origin}
                onChange={(event) => setForm({ ...form, origin: event.target.value })}
              />
            </Field>
            <Field label="To" required>
              <Input
                maxLength={3}
                value={form.destination}
                onChange={(event) => setForm({ ...form, destination: event.target.value })}
              />
            </Field>
            <Field label="Departure from" required>
              <Input
                type="date"
                value={form.dateFrom}
                onChange={(event) => setForm({ ...form, dateFrom: event.target.value })}
              />
            </Field>
            <Field label="Departure to">
              <Input
                type="date"
                min={form.dateFrom}
                value={form.dateTo}
                onChange={(event) => setForm({ ...form, dateTo: event.target.value })}
              />
            </Field>
            <Field label="Cabin">
              <Select
                value={form.cabin}
                onChange={(event) =>
                  setForm({ ...form, cabin: event.target.value as WatchForm['cabin'] })
                }
              >
                <option value="economy">Economy</option>
                <option value="premium_economy">Premium economy</option>
                <option value="business">Business</option>
                <option value="first">First</option>
              </Select>
            </Field>
            <Field label="Max stops">
              <Select
                value={form.maxStops}
                onChange={(event) => setForm({ ...form, maxStops: event.target.value })}
              >
                <option value="">Any</option>
                <option value="0">Direct only</option>
                <option value="1">Up to 1 stop</option>
              </Select>
            </Field>
            <Field
              label="Maximum price"
              hint={
                form.maximumPrice
                  ? 'Only fares at or below this amount can match.'
                  : 'Leave blank for any price.'
              }
            >
              <Input
                type="text"
                inputMode="decimal"
                value={form.maximumPrice}
                onChange={(event) => setForm({ ...form, maximumPrice: event.target.value })}
                placeholder="Any price"
              />
            </Field>
            <Field label="Currency">
              <Input
                maxLength={3}
                value={form.currency}
                onChange={(event) =>
                  setForm({ ...form, currency: event.target.value.toUpperCase() })
                }
              />
            </Field>
          </div>
          <label className="check-field">
            <input
              type="checkbox"
              checked={form.requireRefundable}
              onChange={(event) => setForm({ ...form, requireRefundable: event.target.checked })}
            />
            <span>Only notify me about refundable fares</span>
          </label>
          {createMutation.isError ? (
            <SecureMutationError
              error={createMutation.error}
              onRetry={() => createMutation.mutate()}
              retryLabel="Retry watch creation"
            />
          ) : null}
        </div>
      </Modal>
      <ConfirmDialog
        open={Boolean(cancelTarget)}
        title="Cancel this watch?"
        message="The watch will stop running permanently. This cannot be undone."
        confirmLabel="Cancel watch"
        danger
        onCancel={() => setCancelTarget(null)}
        onConfirm={() => {
          if (cancelTarget) {
            requestTransition(cancelTarget, 'cancel')
            setCancelTarget(null)
          }
        }}
        loading={Boolean(cancelTarget && transitionIsPending(cancelTarget.id))}
      />
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete this watch?"
        message="The notification rule and its saved criteria will be removed."
        confirmLabel="Delete watch"
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

function WatchCard({
  watch,
  error,
  onRestore,
  restoring,
  onDelete,
  onCancel,
  onTransition,
  loading,
}: {
  watch: WatchRecord
  error?: unknown
  onRestore: () => void
  restoring: boolean
  onDelete: () => void
  onCancel: () => void
  onTransition: (action: TransitionAction) => void
  loading: boolean
}) {
  const criteria = watch.criteria
  const transition = transitionForStatus(watch.status)
  const cancelAllowed = canCancel(watch.status)
  const terminal = isTerminal(watch.status)

  return (
    <Card className="watch-card">
      <div className="watch-card-top">
        <div className="watch-icon">
          <Bell size={18} />
        </div>
        <StatusBadge status={watch.status} />
      </div>
      <div className="watch-route">
        <strong>
          {criteria.origin || '—'} → {criteria.destination || '—'}
        </strong>
        <span>
          <CalendarClock size={14} />{' '}
          {criteria.departure_date_from ? formatDate(criteria.departure_date_from) : 'Date window'}
          {criteria.departure_date_to && criteria.departure_date_to !== criteria.departure_date_from
            ? ' – ' + formatDate(criteria.departure_date_to)
            : ''}
        </span>
      </div>
      <div className="watch-facts">
        <span>{criteria.cabin || 'economy'}</span>
        <span>
          {criteria.max_stops === null || criteria.max_stops === undefined
            ? 'Any stops'
            : criteria.max_stops === 0
              ? 'Direct only'
              : 'Up to ' + criteria.max_stops + ' stop(s)'}
        </span>
        <span>{criteria.require_refundable ? 'Refundable only' : 'All fare types'}</span>
        <span>
          {criteria.maximum_total
            ? `Up to ${criteria.maximum_total.amount} ${criteria.maximum_total.currency}`
            : 'Any price'}
        </span>
      </div>
      <div className="watch-monitoring">
        {watch.run_count === 0 || watch.run_count === null ? (
          <span>Waiting for first check</span>
        ) : watch.last_checked_at ? (
          <span>Last checked {formatDateTime(watch.last_checked_at)}</span>
        ) : (
          <span>Check status unavailable</span>
        )}
        {watch.status === 'active' && watch.next_run_at ? (
          <span>Next check {formatDateTime(watch.next_run_at)}</span>
        ) : null}
      </div>
      {watch.latest_match ? (
        <div className="watch-match-summary">
          <div className="watch-match-heading">
            <CheckCircle2 size={16} /> Latest match
          </div>
          <strong>
            {formatMoney(watch.latest_match.price, watch.latest_match.currency)} ·{' '}
            {watch.latest_match.origin} → {watch.latest_match.destination}
          </strong>
          <span>Departure {formatDateTime(watch.latest_match.departure_at)}</span>
        </div>
      ) : null}
      {watch.latest_notifications.length ? (
        <div className="watch-notifications">
          <span>Notifications</span>
          {watch.latest_notifications.map((notification) => (
            <span key={notification.channel}>
              {notification.channel.replace('_', ' ')}: {notification.status}
              {notification.error_code ? ` (${notification.error_code})` : ''}
            </span>
          ))}
        </div>
      ) : null}
      {watch.last_error_code ? (
        <InfoBanner tone="warning">
          <AlertTriangle size={16} /> Latest worker issue: {watch.last_error_code}
        </InfoBanner>
      ) : null}
      {error ? (
        <div className="watch-transition-error">
          <ApiNotice error={error} onRestore={onRestore} restoring={restoring} />
          <ErrorState error={error} compact />
        </div>
      ) : null}
      <div className="watch-footer">
        <div className="inline-actions">
          {transition ? (
            <Button
              variant="ghost"
              size="sm"
              loading={loading}
              disabled={loading}
              onClick={() => onTransition(transition.action)}
            >
              {transition.icon} {transition.label}
            </Button>
          ) : null}
          {cancelAllowed ? (
            <Button variant="ghost" size="sm" disabled={loading} onClick={onCancel}>
              <Trash2 size={14} /> Cancel
            </Button>
          ) : terminal ? (
            <Button variant="ghost" size="sm" disabled={loading} onClick={onDelete}>
              <Trash2 size={14} /> Delete
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  )
}
