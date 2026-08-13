import { useState } from 'react'
import { CheckCircle2, CircleAlert, PlaneTakeoff } from 'lucide-react'
import type { Offer } from '../types/api'
import { isRecord, stringValue } from '../types/api'
import { isDestinationRecommendation } from './DestinationRecommendations'
import { JourneyContext } from './JourneyContext'
import { OfferList } from './FlightOfferCard'
import {
  isTripInspirationResult,
  TripInspirationRecommendations,
} from './TripInspirationRecommendations'
import { Button, EmptyState } from './ui'

export function StructuredResult({
  value,
  onBook,
  onSelectInspirationOption,
}: {
  value: unknown
  onBook?: (offer: Offer) => void
  onSelectInspirationOption?: (rank: number, city: string) => void
}) {
  const [showAllOffers, setShowAllOffers] = useState(false)
  if (!isRecord(value)) return null
  if (isTripInspirationResult(value)) {
    return (
      <TripInspirationRecommendations value={value} onSelectOption={onSelectInspirationOption} />
    )
  }
  const action = stringValue(value.action)
  const status = stringValue(value.status)
  const offers = Array.isArray(value.offers) ? value.offers.filter(isOffer) : []
  const recommendation = isDestinationRecommendation(value.destination_recommendations)
    ? value.destination_recommendations
    : null
  const visibleOffers = showAllOffers ? offers : offers.slice(0, 3)

  if (!offers.length && !recommendation) {
    if (status === 'no_results') {
      return (
        <EmptyState
          icon={<CircleAlert size={20} />}
          title="No matching options yet"
          description="Try a wider date window, another airport, or fewer constraints."
        />
      )
    }
    if (status === 'provider_unavailable' || status === 'disabled') {
      return (
        <div className="structured-notice">
          <CircleAlert size={17} />
          <span>
            {status === 'disabled'
              ? 'This feature is not available right now.'
              : 'The provider is temporarily unavailable. You can retry when it is back.'}
          </span>
        </div>
      )
    }
    if (status === 'completed' || status === 'results') {
      return (
        <div className="structured-notice structured-success">
          <CheckCircle2 size={17} />
          <span>The assistant completed this step.</span>
        </div>
      )
    }
    return null
  }

  return (
    <div className="structured-result-group">
      {offers.length ? (
        <section className="structured-result" aria-label="Flight results">
          <div className="structured-heading">
            <PlaneTakeoff size={16} />
            <strong>{action === 'trip_discovery' ? 'Flight matches' : 'Flights found'}</strong>
          </div>
          <OfferList offers={visibleOffers} onReview={onBook} />
          {offers.length > 3 ? (
            <div className="structured-offer-controls">
              <span>
                Showing {visibleOffers.length} of {offers.length}
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowAllOffers((current) => !current)}
              >
                {showAllOffers ? 'Show fewer' : `Show all ${offers.length}`}
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}
      <JourneyContext
        recommendation={recommendation}
        weather={value.weather}
        showWeatherStatus={offers.length > 0}
      />
    </div>
  )
}

const isOffer = (value: unknown): value is Offer =>
  isRecord(value) &&
  typeof value.offer_id === 'string' &&
  typeof value.origin === 'string' &&
  typeof value.destination === 'string'
