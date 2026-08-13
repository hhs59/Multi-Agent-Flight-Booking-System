import type { components } from '../api/generated/schema'
import { MapPin } from 'lucide-react'
import { isRecord } from '../types/api'

export type DestinationRecommendation = components['schemas']['DestinationRecommendationResult']

type PlaceCandidate = components['schemas']['PlaceCandidate']
type RecommendedPlace = components['schemas']['RecommendedPlace']

const statuses = new Set<DestinationRecommendation['status']>([
  'completed',
  'no_results',
  'unsupported_destination',
  'provider_unavailable',
  'timed_out',
  'disabled',
])

function isPlaceCandidate(value: unknown): value is PlaceCandidate {
  if (!isRecord(value)) return false
  return (
    typeof value.place_id === 'string' &&
    typeof value.name === 'string' &&
    typeof value.source_name === 'string' &&
    typeof value.environment === 'string' &&
    typeof value.is_live === 'boolean'
  )
}

function isRecommendedPlace(value: unknown): value is RecommendedPlace {
  if (!isRecord(value)) return false
  return (
    typeof value.rank === 'number' &&
    typeof value.reason === 'string' &&
    isPlaceCandidate(value.candidate)
  )
}

export function isDestinationRecommendation(value: unknown): value is DestinationRecommendation {
  if (!isRecord(value)) return false
  const places = value.places
  return (
    typeof value.advisory_notice === 'string' &&
    typeof value.city === 'string' &&
    typeof value.country === 'string' &&
    typeof value.destination_airport === 'string' &&
    typeof value.retryable === 'boolean' &&
    typeof value.status === 'string' &&
    statuses.has(value.status as DestinationRecommendation['status']) &&
    typeof value.trace_id === 'string' &&
    (places === undefined || (Array.isArray(places) && places.every(isRecommendedPlace)))
  )
}

export function DestinationRecommendations({
  recommendation,
}: {
  recommendation: DestinationRecommendation | null | undefined
}) {
  if (!recommendation || recommendation.status !== 'completed' || !recommendation.places?.length)
    return null

  return (
    <section className="destination-recommendations" aria-label="Destination recommendations">
      <h3>
        <MapPin size={17} /> Places to explore in {recommendation.city}
      </h3>
      <ul className="place-list">
        {recommendation.places.map((place) => (
          <li key={place.candidate.place_id}>
            <strong>{place.candidate.name}</strong>
            {place.reason ? <span>{place.reason}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
