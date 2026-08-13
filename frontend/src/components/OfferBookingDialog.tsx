import { useMutation, useQuery } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { AlertTriangle, CheckCircle2, LockKeyhole, RefreshCw, UsersRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  createBookingIntent,
  createIdempotencyKey,
  listTravelers,
  repriceOffer,
} from '../api/services'
import { ApiError, isCsrfError } from '../api/errors'
import { queryKeys } from '../api/queryKeys'
import type { Offer, Traveler } from '../types/api'
import { formatDateTime, formatMoney } from '../lib/format'
import {
  isTravelerReadyForDuffel,
  travelerDisplayName,
  travelerReadiness,
  travelerReadinessLabel,
} from '../lib/travelerReadiness'
import { ApiNotice, Button, ErrorState, InfoBanner, Modal, Skeleton } from './ui'

type Phase = 'repricing' | 'review' | 'changed' | 'travelers' | 'blocked'

type IntentAttempt = {
  fingerprint: string
  key: string
  travelerIds: string[]
}

export type OfferBookingDialogProps = {
  offer: Offer | null
  threadId?: string
  onClose: () => void
  onIntentCreated: (intentId: string) => void
}

export function OfferBookingDialog({
  offer,
  threadId,
  onClose,
  onIntentCreated,
}: OfferBookingDialogProps) {
  const [phase, setPhase] = useState<Phase>('repricing')
  const [safeOffer, setSafeOffer] = useState<Offer | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [selectedTravelerIds, setSelectedTravelerIds] = useState<string[]>([])
  const [intentAttempt, setIntentAttempt] = useState<IntentAttempt | null>(null)
  const { restoreSecureSession, isRestoringSession } = useAuth()

  const repriceMutation = useMutation({
    mutationFn: (offerId: string) => repriceOffer(offerId),
    onSuccess: (response) => {
      if (response.status === 'unavailable' || response.status === 'expired') {
        setPhase('blocked')
        setSafeOffer(null)
        return
      }
      const nextOffer = response.repriced_offer || offer
      if (!nextOffer) {
        setPhase('blocked')
        return
      }
      setSafeOffer(nextOffer)
      setPhase(response.status === 'changed' ? 'changed' : 'review')
    },
    onError: () => setPhase('blocked'),
  })

  const travelersQuery = useQuery({
    queryKey: queryKeys.travelers,
    queryFn: listTravelers,
    enabled: Boolean(offer && phase === 'travelers'),
  })

  const intentMutation = useMutation({
    mutationFn: ({ travelerIds, key }: { travelerIds: string[]; key: string }) => {
      if (!safeOffer) throw new Error('The reviewed offer is no longer available.')
      return createBookingIntent(safeOffer.offer_id, travelerIds, threadId, key)
    },
    onSuccess: (response) => onIntentCreated(response.id),
  })

  useEffect(() => {
    if (!offer) return
    setPhase('repricing')
    setSafeOffer(null)
    setAcknowledged(false)
    setSelectedTravelerIds([])
    setIntentAttempt(null)
    repriceMutation.reset()
    repriceMutation.mutate(offer.offer_id)
  }, [offer?.offer_id])

  if (!offer) return null

  const displayedOffer = safeOffer || offer
  const travelers = travelersQuery.data || []
  const readyTravelers = travelers.filter(isTravelerReadyForDuffel)
  const canContinue = phase === 'review' || (phase === 'changed' && acknowledged)
  const fingerprint = safeOffer
    ? [safeOffer.offer_id, threadId || '', [...selectedTravelerIds].sort().join('|')].join('::')
    : ''

  const submitIntent = (): void => {
    if (!selectedTravelerIds.length || !safeOffer) return
    const travelerIds = [...selectedTravelerIds].sort()
    const nextFingerprint = [safeOffer.offer_id, threadId || '', travelerIds.join('|')].join('::')
    const currentAttempt = intentAttempt?.fingerprint === nextFingerprint ? intentAttempt : null
    const key = currentAttempt?.key || createIdempotencyKey()
    setIntentAttempt({ fingerprint: nextFingerprint, key, travelerIds })
    intentMutation.mutate({ travelerIds, key })
  }

  const title =
    phase === 'repricing'
      ? 'Reviewing current fare'
      : phase === 'travelers'
        ? 'Choose travelers'
        : phase === 'blocked'
          ? 'Offer unavailable'
          : 'Review offer'

  return (
    <Modal
      open
      title={title}
      onClose={onClose}
      disableEscape={intentMutation.isPending}
      disableClose={intentMutation.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={intentMutation.isPending}>
            Cancel
          </Button>
          {phase === 'review' || phase === 'changed' ? (
            <Button disabled={!canContinue} onClick={() => setPhase('travelers')}>
              <UsersRound size={15} /> Continue to travelers
            </Button>
          ) : null}
          {phase === 'travelers' ? (
            <Button
              loading={intentMutation.isPending}
              disabled={!selectedTravelerIds.length || !readyTravelers.length}
              onClick={submitIntent}
            >
              <LockKeyhole size={15} /> Create booking intent
            </Button>
          ) : null}
        </>
      }
    >
      {phase === 'repricing' ? (
        <RepricingState
          offer={offer}
          error={repriceMutation.error}
          onRestore={() => void restoreSecureSession()}
          restoring={isRestoringSession}
        />
      ) : null}
      {phase === 'blocked' ? (
        <BlockedState
          error={repriceMutation.error}
          onClose={onClose}
          onRestore={() => void restoreSecureSession()}
          restoring={isRestoringSession}
          onRetry={() => {
            setPhase('repricing')
            repriceMutation.mutate(offer.offer_id)
          }}
        />
      ) : null}
      {phase === 'review' || phase === 'changed' ? (
        <ReviewedOffer
          offer={offer}
          currentOffer={displayedOffer}
          changed={phase === 'changed'}
          acknowledged={acknowledged}
          onAcknowledge={setAcknowledged}
        />
      ) : null}
      {phase === 'travelers' ? (
        <TravelerSelection
          travelers={travelers}
          selectedIds={selectedTravelerIds}
          onChange={setSelectedTravelerIds}
          loading={travelersQuery.isLoading}
          error={travelersQuery.error || intentMutation.error}
          onRestore={() => void restoreSecureSession()}
          restoring={isRestoringSession}
          retry={
            travelersQuery.isError
              ? () => void travelersQuery.refetch()
              : intentMutation.isError && intentAttempt?.fingerprint === fingerprint
                ? submitIntent
                : undefined
          }
        />
      ) : null}
    </Modal>
  )
}

function RepricingState({
  offer,
  error,
  onRestore,
  restoring,
}: {
  offer: Offer
  error: unknown
  onRestore: () => void
  restoring: boolean
}) {
  return (
    <div className="dialog-state">
      <div className="dialog-state-icon">
        <RefreshCw className="spin" size={23} />
      </div>
      <h3>Checking availability with the provider</h3>
      <p>
        We are checking {offer.origin} to {offer.destination} at the current fare. Traveler
        selection will unlock after this step.
      </p>
      {error ? (
        <>
          <ApiNotice error={error} onRestore={onRestore} restoring={restoring} />
          <ErrorState error={error} compact />
        </>
      ) : null}
    </div>
  )
}

function BlockedState({
  error,
  onClose,
  onRestore,
  restoring,
  onRetry,
}: {
  error: unknown
  onClose: () => void
  onRestore: () => void
  restoring: boolean
  onRetry: () => void
}) {
  const unavailable = error instanceof ApiError && error.status === 503
  return (
    <div className="dialog-state">
      <div className="dialog-state-icon dialog-state-danger">
        <AlertTriangle size={23} />
      </div>
      <h3>{unavailable ? 'Provider unavailable' : 'This offer cannot be booked'}</h3>
      <p>
        {unavailable
          ? 'The provider did not respond in time. No booking intent was created.'
          : 'This offer is expired or no longer available. Start a new search to choose another fare.'}
      </p>
      {error ? (
        <>
          <ApiNotice error={error} onRestore={onRestore} restoring={restoring} />
          <ErrorState error={error} compact />
        </>
      ) : null}
      {isCsrfError(error) ? (
        <Button variant="secondary" onClick={onRetry}>
          Retry fare check
        </Button>
      ) : null}
      <Button variant="secondary" onClick={onClose}>
        Back to search
      </Button>
    </div>
  )
}

function ReviewedOffer({
  offer,
  currentOffer,
  changed,
  acknowledged,
  onAcknowledge,
}: {
  offer: Offer
  currentOffer: Offer
  changed: boolean
  acknowledged: boolean
  onAcknowledge: (value: boolean) => void
}) {
  return (
    <div className="dialog-review">
      <div className="selected-flight-mini">
        <strong>
          {currentOffer.origin} → {currentOffer.destination}
        </strong>
        <span>
          {formatDateTime(currentOffer.departure_at)} · {currentOffer.provider}
        </span>
      </div>
      {changed ? (
        <InfoBanner tone="warning">
          <AlertTriangle size={17} /> The fare changed during reprice. Review and acknowledge the
          current total before choosing travelers.
        </InfoBanner>
      ) : (
        <InfoBanner tone="success">
          <CheckCircle2 size={17} /> This offer is currently available at the displayed fare.
        </InfoBanner>
      )}
      <div className="price-comparison">
        <div>
          <span>{changed ? 'Previous total' : 'Current total'}</span>
          <strong>{formatMoney(offer.total, offer.currency)}</strong>
        </div>
        <div>
          <span>{changed ? 'Current total' : 'Expires'}</span>
          <strong>
            {changed
              ? formatMoney(currentOffer.total, currentOffer.currency)
              : formatDateTime(currentOffer.expires_at)}
          </strong>
        </div>
      </div>
      {changed ? (
        <label className="check-field">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => onAcknowledge(event.target.checked)}
          />
          <span>I acknowledge the changed fare and want to continue.</span>
        </label>
      ) : null}
    </div>
  )
}

function TravelerSelection({
  travelers,
  selectedIds,
  onChange,
  loading,
  error,
  onRestore,
  restoring,
  retry,
}: {
  travelers: Traveler[]
  selectedIds: string[]
  onChange: (ids: string[]) => void
  loading: boolean
  error: unknown
  onRestore: () => void
  restoring: boolean
  retry?: () => void
}) {
  if (loading) return <Skeleton className="picker-skeleton" />
  return (
    <div className="picker-body">
      <p>
        Select at least one traveler ready for Duffel booking. This step creates an intent only; it
        does not confirm a booking.
      </p>
      {error ? (
        <>
          <ApiNotice error={error} onRestore={onRestore} restoring={restoring} />
          <ErrorState error={error} compact onRetry={retry} />
        </>
      ) : null}
      {!travelers.length ? (
        <InfoBanner tone="warning">
          Add a traveler profile with the required Duffel booking fields before creating a booking
          intent.
        </InfoBanner>
      ) : null}
      <div className="traveler-picker-list">
        {travelers.map((traveler) => {
          const readiness = travelerReadiness(traveler)
          const disabled = readiness === 'incomplete'
          const selected = selectedIds.includes(traveler.id)
          return (
            <label
              className={'traveler-picker-item ' + (disabled ? 'traveler-disabled' : '')}
              key={traveler.id}
            >
              <input
                type="checkbox"
                disabled={disabled}
                checked={selected}
                onChange={() =>
                  onChange(
                    selected
                      ? selectedIds.filter((id) => id !== traveler.id)
                      : [...selectedIds, traveler.id],
                  )
                }
              />
              <span>
                <strong>{traveler.label}</strong>
                <small>
                  {travelerDisplayName(traveler)} · {travelerReadinessLabel(readiness)}
                </small>
              </span>
            </label>
          )
        })}
      </div>
    </div>
  )
}
