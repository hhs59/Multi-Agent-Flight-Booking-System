import type { Traveler } from '../types/api'

export type TravelerReadiness = 'incomplete' | 'duffel' | 'international'

const DUFFEL_TITLES = new Set(['mr', 'mrs', 'ms', 'miss', 'dr'])
const DUFFEL_GENDER_MARKERS = new Set(['m', 'male', 'f', 'female'])

export function travelerDisplayName(traveler: Traveler): string {
  const givenName = traveler.given_name?.trim()
  const familyName = traveler.family_name?.trim()
  if (givenName && familyName) return `${givenName} ${familyName}`
  return traveler.legal_name?.trim() || 'Name details required'
}

function isoDate(value: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null
  const parsed = new Date(`${value}T00:00:00.000Z`)
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) return null
  return value
}

function todayIso(value: Date | string): string | null {
  if (typeof value === 'string') return isoDate(value)
  if (Number.isNaN(value.getTime())) return null
  return value.toISOString().slice(0, 10)
}

export function travelerReadiness(
  traveler: Traveler,
  today: Date | string = new Date(),
): TravelerReadiness {
  const genderMarker = traveler.gender_marker?.trim().toLowerCase()
  const readyForDuffel = Boolean(
    traveler.given_name?.trim() &&
    traveler.family_name?.trim() &&
    traveler.title &&
    DUFFEL_TITLES.has(traveler.title.toLowerCase()) &&
    genderMarker &&
    DUFFEL_GENDER_MARKERS.has(genderMarker) &&
    traveler.birth_year &&
    traveler.masked_email &&
    traveler.masked_phone,
  )
  if (!readyForDuffel) return 'incomplete'

  const expiryDate = traveler.passport_expiry_date ? isoDate(traveler.passport_expiry_date) : null
  const currentDate = todayIso(today)
  const readyForInternational = Boolean(
    traveler.nationality &&
    traveler.passport_ending &&
    traveler.passport_issuing_country &&
    expiryDate &&
    currentDate &&
    expiryDate > currentDate,
  )
  return readyForInternational ? 'international' : 'duffel'
}

export function travelerReadinessLabel(readiness: TravelerReadiness): string {
  if (readiness === 'international') return 'Ready for international booking'
  if (readiness === 'duffel') return 'Ready for Duffel booking'
  return 'Saved — details incomplete'
}

export function isTravelerReadyForDuffel(traveler: Traveler): boolean {
  return travelerReadiness(traveler) !== 'incomplete'
}
