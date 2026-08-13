import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, FileCheck2, LockKeyhole, UserRound } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  confirmBooking,
  createIdempotencyKey,
  getBookingIntent,
  listTravelers,
  prepareBooking,
} from '../api/services'
import { queryKeys } from '../api/queryKeys'
import {
  Button,
  Card,
  ErrorState,
  Field,
  InfoBanner,
  Input,
  LinkButton,
  StatusBadge,
} from '../components/ui'
import {
  isRecord,
  type BookingQuoteSummary,
  type BookingWorkflowResponse,
  type Traveler,
} from '../types/api'
import { formatDateTime, formatMoney } from '../lib/format'
import { SecureMutationError } from '../components/SecureMutationError'

export function BookingIntentPage() {
  const { intentId } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [international, setInternational] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [paymentReference, setPaymentReference] = useState('')
  const [preparedQuote, setPreparedQuote] = useState<BookingWorkflowResponse | null>(null)
  const [operationMessage, setOperationMessage] = useState<string | null>(null)
  const [confirmAttempt, setConfirmAttempt] = useState<{
    key: string
    body: {
      quote_version: number
      acknowledged_fare_terms: boolean
      consent_scope: string
      payment_method_reference: string | null
    }
  } | null>(null)

  const intentQuery = useQuery({
    queryKey: queryKeys.bookingIntent(intentId || ''),
    queryFn: () => getBookingIntent(intentId || ''),
    enabled: Boolean(intentId),
  })
  const travelersQuery = useQuery({ queryKey: queryKeys.travelers, queryFn: listTravelers })

  const prepareMutation = useMutation({
    mutationFn: () => {
      const intent = intentQuery.data
      if (!intent) throw new Error('Booking intent is not loaded.')
      return prepareBooking(intent.id, intent.traveler_profile_ids, international)
    },
    onSuccess: (value) => {
      setPreparedQuote(value)
      setPaymentReference('')
      setOperationMessage('The latest quote is ready to review.')
      void client.invalidateQueries({ queryKey: queryKeys.bookingIntent(intentId || '') })
      void client.invalidateQueries({ queryKey: queryKeys.bookings })
    },
  })

  const confirmMutation = useMutation({
    mutationFn: ({
      body,
      key,
    }: {
      body: {
        quote_version: number
        acknowledged_fare_terms: boolean
        consent_scope: string
        payment_method_reference: string | null
      }
      key: string
    }) => {
      const intent = intentQuery.data
      if (!intent) throw new Error('Booking intent is not loaded.')
      return confirmBooking(intent.id, body, key)
    },
    onSuccess: (value) => {
      setOperationMessage('The booking request was submitted safely.')
      void client.invalidateQueries({ queryKey: queryKeys.bookingIntent(intentId || '') })
      void client.invalidateQueries({ queryKey: queryKeys.bookings })
      if (isRecord(value) && typeof value.booking_id === 'string')
        navigate('/bookings/' + value.booking_id)
    },
  })

  const retryConfirm = (): void => {
    if (confirmMutation.variables) {
      confirmMutation.mutate(confirmMutation.variables)
    } else if (confirmAttempt) {
      confirmMutation.mutate(confirmAttempt)
    }
  }

  const intent = intentQuery.data
  const travelers = travelersQuery.data || []
  const selectedTravelers = intent
    ? travelers.filter((traveler) => intent.traveler_profile_ids.includes(traveler.id))
    : []
  const canPrepare = Boolean(intent && ['draft', 'quote_ready', 'expired'].includes(intent.status))
  const canConfirm = Boolean(
    intent && intent.quote_version > 0 && intent.status === 'awaiting_confirmation' && acknowledged,
  )
  const effectiveQuote = quoteFromWorkflow(preparedQuote) ?? intent?.current_quote ?? null
  const paymentReferenceRequired = effectiveQuote?.payment_reference_required ?? true
  const isProviderBalance =
    effectiveQuote?.settlement_mode === 'balance' && effectiveQuote.environment === 'sandbox'

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link className="back-link" to="/search">
            <ArrowLeft size={15} /> Back to search
          </Link>
          <h1>Review booking</h1>
        </div>
        {intent ? <StatusBadge status={intent.status} /> : null}
      </div>
      {intentQuery.isLoading ? (
        <Card>
          <div className="form-skeleton" />
        </Card>
      ) : null}
      {intentQuery.isError ? (
        <ErrorState error={intentQuery.error} onRetry={() => void intentQuery.refetch()} />
      ) : null}
      {operationMessage ? (
        <InfoBanner tone="success">
          <CheckCircle2 size={18} /> {operationMessage}
        </InfoBanner>
      ) : null}
      {prepareMutation.isError ? (
        <SecureMutationError
          error={prepareMutation.error}
          onRetry={() => prepareMutation.mutate()}
          retryLabel="Retry fare preparation"
        />
      ) : null}
      {confirmMutation.isError ? (
        <SecureMutationError
          error={confirmMutation.error}
          onRetry={retryConfirm}
          retryLabel="Retry booking confirmation"
        />
      ) : null}
      {intent ? (
        <div className="booking-layout">
          <div className="booking-main">
            <Card className="workflow-card">
              <div className="workflow-heading">
                <div className="workflow-step">1</div>
                <h2>Travelers</h2>
              </div>
              <div className="booking-traveler-list">
                {selectedTravelers.length ? (
                  selectedTravelers.map((traveler) => (
                    <TravelerRow key={traveler.id} traveler={traveler} />
                  ))
                ) : (
                  <p className="muted">No traveler profiles were returned for this intent.</p>
                )}
              </div>
              <LinkButton to="/travelers">Manage traveler profiles</LinkButton>
            </Card>
            <Card className="workflow-card">
              <div className="workflow-heading">
                <div className="workflow-step">2</div>
                <h2>Check price</h2>
              </div>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={international}
                  onChange={(event) => setInternational(event.target.checked)}
                />
                <span>This is an international itinerary</span>
              </label>
              <Button
                loading={prepareMutation.isPending}
                disabled={!canPrepare}
                onClick={() => prepareMutation.mutate()}
              >
                <FileCheck2 size={16} /> Prepare latest quote
              </Button>
            </Card>
            <Card className="workflow-card">
              <div className="workflow-heading">
                <div className="workflow-step">3</div>
                <h2>Confirm</h2>
              </div>
              {intent.status === 'awaiting_confirmation' ? (
                <>
                  <div className="quote-detail">
                    <span>Quote version</span>
                    <strong>{effectiveQuote?.quote_version || intent.quote_version}</strong>
                    {effectiveQuote?.total && effectiveQuote.currency ? (
                      <>
                        <span>Final amount</span>
                        <strong>
                          {formatMoney(effectiveQuote.total, effectiveQuote.currency)}
                        </strong>
                      </>
                    ) : null}
                    {effectiveQuote?.expires_at ? (
                      <>
                        <span>Quote expires</span>
                        <strong>{formatDateTime(effectiveQuote.expires_at)}</strong>
                      </>
                    ) : null}
                    {effectiveQuote?.provider ? (
                      <>
                        <span>Provider</span>
                        <strong>
                          {effectiveQuote.provider}{' '}
                          {effectiveQuote.environment === 'sandbox' ? '(Test)' : ''}
                        </strong>
                      </>
                    ) : null}
                  </div>
                  {intent.currency_disclosure ? (
                    <InfoBanner tone="warning">{intent.currency_disclosure}</InfoBanner>
                  ) : null}
                  {isProviderBalance ? (
                    <div className="test-booking-note">Test booking · No payment will be charged.</div>
                  ) : paymentReferenceRequired ? (
                    <Field
                      label="Payment reference"
                      hint="Use a server-approved reference; never enter raw card data."
                    >
                      <Input
                        value={paymentReference}
                        onChange={(event) => setPaymentReference(event.target.value)}
                        autoComplete="off"
                        placeholder="Payment method reference"
                      />
                    </Field>
                  ) : null}
                  <label className="check-field">
                    <input
                      type="checkbox"
                      checked={acknowledged}
                      onChange={(event) => setAcknowledged(event.target.checked)}
                    />
                    <span>I have reviewed and acknowledge the current fare terms.</span>
                  </label>
                  <Button
                    loading={confirmMutation.isPending}
                    disabled={!canConfirm}
                    onClick={() => {
                      if (!intent) return
                      const body = {
                        quote_version: effectiveQuote?.quote_version ?? intent.quote_version,
                        acknowledged_fare_terms: acknowledged,
                        consent_scope: 'single_booking',
                        payment_method_reference: paymentReferenceRequired
                          ? paymentReference.trim() || null
                          : null,
                      }
                      const samePayload =
                        confirmAttempt &&
                        JSON.stringify(confirmAttempt.body) === JSON.stringify(body)
                      const key = samePayload ? confirmAttempt.key : createIdempotencyKey()
                      setConfirmAttempt({ key, body })
                      confirmMutation.mutate({ body, key })
                    }}
                  >
                    <LockKeyhole size={16} /> Confirm booking
                  </Button>
                </>
              ) : (
                <p className="muted">Check the latest price to continue.</p>
              )}
            </Card>
          </div>
          <aside className="booking-summary">
            <Card>
              <h3>Booking summary</h3>
              <div className="summary-list">
                <span>Status</span>
                <StatusBadge status={intent.status} />
                <span>Travelers</span>
                <strong>{intent.traveler_profile_ids.length}</strong>
              </div>

            </Card>
          </aside>
        </div>
      ) : null}
    </div>
  )
}

function TravelerRow({ traveler }: { traveler: Traveler }) {
  return (
    <div className="booking-traveler-row">
      <span className="mini-avatar">
        <UserRound size={15} />
      </span>
      <div>
        <strong>{traveler.label}</strong>
        <span>
          {traveler.legal_name || 'Legal name pending'} ·{' '}
          {traveler.completeness.replaceAll('_', ' ')}
        </span>
      </div>
    </div>
  )
}

function quoteFromWorkflow(value: BookingWorkflowResponse | null): BookingQuoteSummary | null {
  if (!value) return null
  if (
    typeof value.quote_version !== 'number' ||
    typeof value.total !== 'string' ||
    typeof value.currency !== 'string' ||
    typeof value.expires_at !== 'string' ||
    typeof value.provider !== 'string' ||
    typeof value.environment !== 'string' ||
    (value.settlement_mode !== 'balance' && value.settlement_mode !== 'external') ||
    typeof value.payment_required !== 'boolean' ||
    typeof value.payment_reference_required !== 'boolean'
  ) {
    return null
  }
  return {
    quote_version: value.quote_version,
    total: value.total,
    currency: value.currency,
    expires_at: value.expires_at,
    provider: value.provider,
    environment: value.environment,
    settlement_mode: value.settlement_mode,
    payment_required: value.payment_required,
    payment_reference_required: value.payment_reference_required,
  }
}
