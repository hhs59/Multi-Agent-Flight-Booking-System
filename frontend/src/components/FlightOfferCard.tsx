import { ArrowRight, Clock3, Luggage } from 'lucide-react'
import type { Offer } from '../types/api'
import { formatDateTime, formatMoney, durationLabel } from '../lib/format'
import { Button, Card } from './ui'

function FlightOfferCard({
  offer,
  onReview,
  reviewing,
}: {
  offer: Offer
  onReview?: () => void
  reviewing?: boolean
}) {
  const baggage = offer.baggage.checked_pieces
    ? offer.baggage.checked_pieces +
      ' checked bag' +
      (offer.baggage.checked_pieces === 1 ? '' : 's')
    : offer.baggage.cabin_pieces
      ? 'Cabin bag included'
      : 'Baggage varies'
  return (
    <Card className="offer-card" as="article">
      <div className="offer-topline">
        <div className="offer-provider">
          <span className="carrier-mark">{offer.carrier.slice(0, 2)}</span>
          <span>{offer.carrier}</span>
        </div>
      </div>
      <div className="offer-route">
        <div className="route-point">
          <strong>{formatTime(offer.departure_at)}</strong>
          <span>{offer.origin}</span>
        </div>
        <div className="route-line">
          <span className="route-duration">{durationLabel(offer.duration_minutes)}</span>
          <span className="route-arrow">
            <span /> <ArrowRight size={15} />
          </span>
          <span className="route-stops">
            {offer.stops === 0 ? 'Direct' : offer.stops + ' stop' + (offer.stops === 1 ? '' : 's')}
          </span>
        </div>
        <div className="route-point route-point-end">
          <strong>{formatTime(offer.arrival_at)}</strong>
          <span>{offer.destination}</span>
        </div>
      </div>
      <div className="offer-meta">
        <span>
          <Clock3 size={14} />
          {formatDateTime(offer.departure_at)}
        </span>
        <span>
          <Luggage size={14} />
          {baggage}
        </span>
      </div>
      {offer.ranking_reasons?.length ? (
        <div className="offer-reasons">
          {offer.ranking_reasons.slice(0, 2).map((reason) => (
            <span key={reason}>{rankingLabel(reason)}</span>
          ))}
        </div>
      ) : null}
      <div className="offer-footer">
        <div>
          <span className="price-label">Total fare</span>
          <strong className="offer-price">{formatMoney(offer.total, offer.currency)}</strong>
        </div>
        <div className="offer-actions">
          {onReview ? (
            <Button size="sm" loading={reviewing} onClick={onReview}>
              Review offer
            </Button>
          ) : null}
        </div>
      </div>
      <details className="offer-details">
        <summary>Fare details</summary>
        <div className="details-grid">
          <span>Fare rules</span>
          <strong>{offer.fare_conditions.description || 'See provider terms'}</strong>
          <span>Offer expires</span>
          <strong>{formatDateTime(offer.expires_at)}</strong>
        </div>
      </details>
    </Card>
  )
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf())
    ? '—'
    : new Intl.DateTimeFormat('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(date)
}

export function OfferList({
  offers,
  onReview,
  reviewingId,
}: {
  offers: Offer[]
  onReview?: (offer: Offer) => void
  reviewingId?: string | null
}) {
  if (!offers.length) return null
  return (
    <div className="offer-list">
      {offers.map((offer) => (
        <FlightOfferCard
          key={offer.offer_id}
          offer={offer}
          onReview={onReview ? () => onReview(offer) : undefined}
          reviewing={reviewingId === offer.offer_id}
        />
      ))}
    </div>
  )
}

function rankingLabel(value: string): string {
  const labels: Record<string, string> = {
    lowest_total: 'Best price',
    shorter_duration: 'Short journey',
    nonstop: 'Nonstop',
    baggage_included: 'Bag included',
    preferred_time: 'Good departure time',
  }
  return labels[value] || value.replaceAll('_', ' ')
}
