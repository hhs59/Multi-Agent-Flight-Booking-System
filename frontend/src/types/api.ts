import type { components } from '../api/generated/schema'

type Schemas = components['schemas']
export type Offer = Schemas['ApiSafeOfferResponse']
export type Cabin = Schemas['CabinClass']
export type SearchResponse = Schemas['FlightSearchResponse']
export type DiscoveryResponse = Schemas['FlightDiscoveryResponse']
export type RepriceResponse = Schemas['RepriceResponse']
export type Traveler = Schemas['TravelerResponse']
export type TravelerCreate = Schemas['TravelerCreateRequest']
export type TravelerPatch = Schemas['TravelerProfilePatch']
export type PreferencesView = Schemas['TravelPreferencesView']
export type PreferencesPatch = Schemas['TravelPreferencesPatch']
export type PreferencesState =
  | PreferencesView
  | Schemas['TravelPreferencesNotConfiguredResponse']
  | Schemas['TravelPreferencesFeatureDisabledResponse']
export type BookingIntentCreateResponse = Schemas['BookingIntentCreateResponse']
type BookingIntentStatus = Schemas['BookingIntentStatus']
export type BookingQuoteSummary = Schemas['BookingQuoteSummaryResponse']
export type BookingWorkflowResponse = Schemas['BookingWorkflowResponse']
type FlightWatchCriteria = Schemas['FlightWatchCriteria-Output']
export type FlightWatchCriteriaInput = Schemas['FlightWatchCriteria-Input']

type WatchMatchSummary = {
  match_id: string
  offer_id: string | null
  status: string
  price: string
  currency: string
  origin: string
  destination: string
  departure_at: string
  provider: string | null
  environment: string | null
  expires_at: string | null
  matched_at: string
}

type WatchNotificationSummary = {
  channel: 'in_app' | 'email' | 'sms'
  status: string
  sent_at: string | null
  error_code: string | null
}

export type Thread = {
  id: string
  user_id: string
  title: string | null
  locale: 'vi' | 'en'
  archived: boolean
  summary: string | null
  summary_version: number
  summarized_through_sequence: number
  created_at: string
  updated_at: string
}

export type Message = {
  id: string
  user_id: string
  thread_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  sequence: number
  client_message_id: string | null
  result: unknown
  created_at: string
}

export type ThreadPage = {
  items: Thread[]
  next_cursor: string | null
}

export type MessagePage = {
  items: Message[]
  next_cursor: string | null
}

export type MessageTurn = {
  created: boolean
  message: Message
  assistant_message: Message | null
  checkpoint_version: number | null
  result: unknown
  errors: unknown[]
  trace_id: string
}

export type ThreadEnvelope = {
  thread: Thread
  checkpoint: unknown
}

export type BookingIntent = {
  id: string
  source_offer_id: string
  status: BookingIntentStatus
  quote_version: number
  traveler_profile_ids: string[]
  current_quote_id: string | null
  current_quote: BookingQuoteSummary | null
  currency_disclosure?: string | null
}

export type Booking = {
  id: string
  booking_intent_id: string
  status: string
  provider?: string | null
  provider_environment?: string | null
  provider_live_mode?: boolean | null
  provider_status?: string | null
  masked_provider_order_reference?: string | null
  confirmation_code?: string | null
  last_reconciled_at?: string | null
  created_at?: string | null
}

export type WatchRecord = {
  id: string
  status: string
  criteria: FlightWatchCriteria
  next_run_at: string | null
  last_checked_at: string | null
  run_count: number | null
  consecutive_failures: number | null
  last_error_code: string | null
  latest_match: WatchMatchSummary | null
  latest_notifications: WatchNotificationSummary[]
  created_at?: string
  updated_at?: string
}

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

export const stringValue = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback

const numberValue = (value: unknown, fallback = 0): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback

const localeValue = (value: unknown): 'vi' | 'en' => (value === 'en' ? 'en' : 'vi')

export function asThread(value: unknown): Thread {
  const record = isRecord(value) ? value : {}
  return {
    id: stringValue(record.id),
    user_id: stringValue(record.user_id),
    title: typeof record.title === 'string' ? record.title : null,
    locale: localeValue(record.locale),
    archived: record.archived === true,
    summary: typeof record.summary === 'string' ? record.summary : null,
    summary_version: numberValue(record.summary_version),
    summarized_through_sequence: numberValue(record.summarized_through_sequence),
    created_at: stringValue(record.created_at),
    updated_at: stringValue(record.updated_at),
  }
}

function asMessage(value: unknown): Message {
  const record = isRecord(value) ? value : {}
  const role = record.role === 'assistant' || record.role === 'system' ? record.role : 'user'
  return {
    id: stringValue(record.id),
    user_id: stringValue(record.user_id),
    thread_id: stringValue(record.thread_id),
    role,
    content: stringValue(record.content),
    sequence: numberValue(record.sequence),
    client_message_id:
      typeof record.client_message_id === 'string' ? record.client_message_id : null,
    result: record.result ?? null,
    created_at: stringValue(record.created_at),
  }
}

export function asThreadPage(value: unknown): ThreadPage {
  const record = isRecord(value) ? value : {}
  const items = Array.isArray(record.items) ? record.items.map(asThread) : []
  return {
    items,
    next_cursor: typeof record.next_cursor === 'string' ? record.next_cursor : null,
  }
}

export function asMessagePage(value: unknown): MessagePage {
  const record = isRecord(value) ? value : {}
  const items = Array.isArray(record.items) ? record.items.map(asMessage) : []
  return {
    items,
    next_cursor: typeof record.next_cursor === 'string' ? record.next_cursor : null,
  }
}

export function asThreadEnvelope(value: unknown): ThreadEnvelope {
  const record = isRecord(value) ? value : {}
  return {
    thread: asThread(record.thread),
    checkpoint: record.checkpoint ?? null,
  }
}

export function asMessageTurn(value: unknown): MessageTurn {
  const record = isRecord(value) ? value : {}
  return {
    created: record.created !== false,
    message: asMessage(record.message),
    assistant_message: record.assistant_message ? asMessage(record.assistant_message) : null,
    checkpoint_version:
      typeof record.checkpoint_version === 'number' ? record.checkpoint_version : null,
    result: record.result ?? null,
    errors: Array.isArray(record.errors) ? record.errors : [],
    trace_id: stringValue(record.trace_id, 'unknown'),
  }
}

export function asBookingIntent(value: unknown): BookingIntent {
  const record = isRecord(value) ? value : {}
  const status = [
    'draft',
    'quote_ready',
    'awaiting_confirmation',
    'confirmed',
    'expired',
    'cancelled',
    'failed',
  ].includes(stringValue(record.status))
    ? (stringValue(record.status) as BookingIntentStatus)
    : 'draft'
  return {
    id: stringValue(record.id),
    source_offer_id: stringValue(record.source_offer_id),
    status,
    quote_version: numberValue(record.quote_version),
    traveler_profile_ids: Array.isArray(record.traveler_profile_ids)
      ? record.traveler_profile_ids.filter((item): item is string => typeof item === 'string')
      : [],
    current_quote_id: typeof record.current_quote_id === 'string' ? record.current_quote_id : null,
    current_quote: asBookingQuoteSummary(record.current_quote),
  }
}

function asBookingQuoteSummary(value: unknown): BookingQuoteSummary | null {
  if (!isRecord(value)) return null
  const settlementMode = value.settlement_mode
  if (settlementMode !== 'balance' && settlementMode !== 'external') return null
  if (
    typeof value.quote_version !== 'number' ||
    typeof value.total !== 'string' ||
    typeof value.currency !== 'string' ||
    typeof value.expires_at !== 'string' ||
    typeof value.provider !== 'string' ||
    typeof value.environment !== 'string' ||
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
    settlement_mode: settlementMode,
    payment_required: value.payment_required,
    payment_reference_required: value.payment_reference_required,
  }
}

export function asBooking(value: unknown): Booking {
  const record = isRecord(value) ? value : {}
  return {
    id: stringValue(record.id),
    booking_intent_id: stringValue(record.booking_intent_id),
    status: stringValue(record.status, 'unknown'),
    provider: typeof record.provider === 'string' ? record.provider : null,
    provider_environment:
      typeof record.provider_environment === 'string' ? record.provider_environment : null,
    provider_live_mode:
      typeof record.provider_live_mode === 'boolean' ? record.provider_live_mode : null,
    provider_status: typeof record.provider_status === 'string' ? record.provider_status : null,
    masked_provider_order_reference:
      typeof record.masked_provider_order_reference === 'string'
        ? record.masked_provider_order_reference
        : null,
    confirmation_code:
      typeof record.confirmation_code === 'string' ? record.confirmation_code : null,
    last_reconciled_at:
      typeof record.last_reconciled_at === 'string' ? record.last_reconciled_at : null,
    created_at: typeof record.created_at === 'string' ? record.created_at : null,
  }
}

export function asTraveler(value: unknown): Traveler {
  const record = isRecord(value) ? value : {}
  return {
    id: stringValue(record.id),
    label: stringValue(record.label, 'Traveler'),
    is_default: record.is_default === true,
    legal_name: typeof record.legal_name === 'string' ? record.legal_name : null,
    title: typeof record.title === 'string' ? record.title : null,
    given_name: typeof record.given_name === 'string' ? record.given_name : null,
    family_name: typeof record.family_name === 'string' ? record.family_name : null,
    birth_year: typeof record.birth_year === 'number' ? record.birth_year : null,
    gender_marker: typeof record.gender_marker === 'string' ? record.gender_marker : null,
    masked_email: typeof record.masked_email === 'string' ? record.masked_email : null,
    masked_phone: typeof record.masked_phone === 'string' ? record.masked_phone : null,
    nationality: typeof record.nationality === 'string' ? record.nationality : null,
    passport_ending: typeof record.passport_ending === 'string' ? record.passport_ending : null,
    passport_issuing_country:
      typeof record.passport_issuing_country === 'string' ? record.passport_issuing_country : null,
    passport_expiry_date:
      typeof record.passport_expiry_date === 'string' ? record.passport_expiry_date : null,
    completeness: stringValue(record.completeness, 'incomplete'),
    save_preference: stringValue(record.save_preference, 'ask'),
    version: numberValue(record.version, 1),
    created_at: stringValue(record.created_at),
    updated_at: stringValue(record.updated_at),
  }
}

export function asWatch(value: unknown): WatchRecord {
  const record = isRecord(value) ? value : {}
  const criteria = isRecord(record.criteria) ? record.criteria : {}
  return {
    id: stringValue(record.id ?? record.watch_id),
    status: stringValue(record.status, 'draft'),
    criteria: criteria as FlightWatchCriteria,
    next_run_at: typeof record.next_run_at === 'string' ? record.next_run_at : null,
    last_checked_at: typeof record.last_checked_at === 'string' ? record.last_checked_at : null,
    run_count: typeof record.run_count === 'number' ? record.run_count : null,
    consecutive_failures:
      typeof record.consecutive_failures === 'number' ? record.consecutive_failures : null,
    last_error_code: typeof record.last_error_code === 'string' ? record.last_error_code : null,
    latest_match: asWatchMatch(record.latest_match),
    latest_notifications: Array.isArray(record.latest_notifications)
      ? record.latest_notifications.map(asWatchNotification)
      : [],
    created_at: typeof record.created_at === 'string' ? record.created_at : undefined,
    updated_at: typeof record.updated_at === 'string' ? record.updated_at : undefined,
  }
}

function asWatchMatch(value: unknown): WatchMatchSummary | null {
  if (!isRecord(value)) return null
  return {
    match_id: stringValue(value.match_id),
    offer_id: typeof value.offer_id === 'string' ? value.offer_id : null,
    status: stringValue(value.status, 'matched'),
    price: stringValue(value.price),
    currency: stringValue(value.currency),
    origin: stringValue(value.origin),
    destination: stringValue(value.destination),
    departure_at: stringValue(value.departure_at),
    provider: typeof value.provider === 'string' ? value.provider : null,
    environment: typeof value.environment === 'string' ? value.environment : null,
    expires_at: typeof value.expires_at === 'string' ? value.expires_at : null,
    matched_at: stringValue(value.matched_at),
  }
}

function asWatchNotification(value: unknown): WatchNotificationSummary {
  const record = isRecord(value) ? value : {}
  const channel = ['in_app', 'email', 'sms'].includes(stringValue(record.channel))
    ? (stringValue(record.channel) as WatchNotificationSummary['channel'])
    : 'email'
  return {
    channel,
    status: stringValue(record.status, 'pending'),
    sent_at: typeof record.sent_at === 'string' ? record.sent_at : null,
    error_code: typeof record.error_code === 'string' ? record.error_code : null,
  }
}
