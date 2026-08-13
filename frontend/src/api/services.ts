import type { components } from './generated/schema'
import { apiRequest } from './client'
import {
  asBooking,
  asBookingIntent,
  asMessagePage,
  asMessageTurn,
  asThread,
  asThreadEnvelope,
  asThreadPage,
  asTraveler,
  asWatch,
  type Booking,
  type BookingIntent,
  type BookingIntentCreateResponse,
  type BookingWorkflowResponse,
  type DiscoveryResponse,
  type FlightWatchCriteriaInput,
  type MessagePage,
  type MessageTurn,
  type PreferencesPatch,
  type PreferencesState,
  type RepriceResponse,
  type SearchResponse,
  type Thread,
  type ThreadEnvelope,
  type ThreadPage,
  type Traveler,
  type TravelerCreate,
  type TravelerPatch,
  type WatchRecord,
} from '../types/api'

type Schemas = components['schemas']

const query = (params: Record<string, string | number | boolean | undefined>): string => {
  const entries = Object.entries(params).filter(([, value]) => value !== undefined)
  return entries.length
    ? '?' + new URLSearchParams(entries.map(([key, value]) => [key, String(value)])).toString()
    : ''
}

export const createIdempotencyKey = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : 'message-' + Date.now() + '-' + Math.random().toString(16).slice(2)

export async function listThreads(archived = false): Promise<ThreadPage> {
  const response = await apiRequest<unknown>('/v1/threads' + query({ archived, limit: 100 }), {
    csrf: false,
  })
  return asThreadPage(response)
}

export async function createThread(title?: string | null): Promise<Thread> {
  const response = await apiRequest<unknown>('/v1/threads', {
    method: 'POST',
    body: { title: title || null, locale: 'vi' },
  })
  return asThread(response)
}

export async function getThread(threadId: string): Promise<ThreadEnvelope> {
  const response = await apiRequest<unknown>('/v1/threads/' + encodeURIComponent(threadId), {
    csrf: false,
  })
  return asThreadEnvelope(response)
}

export async function renameThread(threadId: string, title: string): Promise<Thread> {
  const response = await apiRequest<unknown>('/v1/threads/' + encodeURIComponent(threadId), {
    method: 'PATCH',
    body: { title },
  })
  return asThread(response)
}

export async function deleteThread(threadId: string): Promise<void> {
  await apiRequest<void>('/v1/threads/' + encodeURIComponent(threadId), {
    method: 'DELETE',
  })
}

export async function listMessages(threadId: string): Promise<MessagePage> {
  const response = await apiRequest<unknown>(
    '/v1/threads/' + encodeURIComponent(threadId) + '/messages?limit=200',
    { csrf: false },
  )
  return asMessagePage(response)
}

export async function sendMessage(
  threadId: string,
  content: string,
  clientMessageId = createIdempotencyKey(),
): Promise<{ turn: MessageTurn; clientMessageId: string }> {
  const response = await apiRequest<unknown>(
    '/v1/threads/' + encodeURIComponent(threadId) + '/messages',
    {
      method: 'POST',
      body: { content, client_message_id: clientMessageId },
    },
  )
  return { turn: asMessageTurn(response), clientMessageId }
}

export async function createFlightSearch(
  body: Schemas['FlightSearchCreateRequest'],
): Promise<SearchResponse> {
  return apiRequest<SearchResponse>('/v1/flight-searches', {
    method: 'POST',
    body,
  })
}

export async function createFlightDiscovery(
  body: Schemas['FlightDiscoveryCreateRequest'],
): Promise<DiscoveryResponse> {
  return apiRequest<DiscoveryResponse>('/v1/flight-discoveries', {
    method: 'POST',
    body,
  })
}

export async function repriceOffer(offerId: string): Promise<RepriceResponse> {
  return apiRequest<RepriceResponse>('/v1/offers/' + encodeURIComponent(offerId) + '/reprice', {
    method: 'POST',
  })
}

export async function listTravelers(): Promise<Traveler[]> {
  const response = await apiRequest<unknown>('/v1/travelers', { csrf: false })
  return Array.isArray(response) ? response.map(asTraveler) : []
}

export async function createTraveler(body: TravelerCreate): Promise<Traveler> {
  const response = await apiRequest<unknown>('/v1/travelers', { method: 'POST', body })
  return asTraveler(response)
}

export async function updateTraveler(
  travelerId: string,
  expectedVersion: number,
  patch: TravelerPatch,
): Promise<Traveler> {
  const response = await apiRequest<unknown>('/v1/travelers/' + encodeURIComponent(travelerId), {
    method: 'PATCH',
    body: { expected_version: expectedVersion, patch },
  })
  return asTraveler(response)
}

export async function deleteTraveler(travelerId: string): Promise<void> {
  await apiRequest<void>('/v1/travelers/' + encodeURIComponent(travelerId), { method: 'DELETE' })
}

export async function makeDefaultTraveler(travelerId: string): Promise<Traveler> {
  const response = await apiRequest<unknown>(
    '/v1/travelers/' + encodeURIComponent(travelerId) + '/make-default',
    {
      method: 'POST',
      body: { is_default: true },
    },
  )
  return asTraveler(response)
}

export async function getPreferences(): Promise<PreferencesState> {
  return apiRequest<PreferencesState>('/v1/travel-preferences', { csrf: false })
}

export async function updatePreferences(body: PreferencesPatch): Promise<PreferencesState> {
  return apiRequest<PreferencesState>('/v1/travel-preferences', {
    method: 'PATCH',
    body,
  })
}

export async function deletePreferences(): Promise<void> {
  await apiRequest<void>('/v1/travel-preferences', { method: 'DELETE' })
}

export async function createBookingIntent(
  offerId: string,
  travelerProfileIds: string[],
  threadId?: string,
  idempotencyKey = createIdempotencyKey(),
): Promise<BookingIntentCreateResponse> {
  return apiRequest<BookingIntentCreateResponse>('/v1/booking-intents', {
    method: 'POST',
    body: {
      source_offer_id: offerId,
      traveler_profile_ids: travelerProfileIds,
      thread_id: threadId,
    },
    idempotencyKey,
  })
}

export async function getBookingIntent(intentId: string): Promise<BookingIntent> {
  const response = await apiRequest<unknown>(
    '/v1/booking-intents/' + encodeURIComponent(intentId),
    { csrf: false },
  )
  return asBookingIntent(response)
}

export async function prepareBooking(
  intentId: string,
  travelerProfileIds: string[],
  international: boolean,
): Promise<BookingWorkflowResponse> {
  return apiRequest<BookingWorkflowResponse>(
    '/v1/booking-intents/' + encodeURIComponent(intentId) + '/prepare',
    {
      method: 'POST',
      body: { traveler_profile_ids: travelerProfileIds, international },
    },
  )
}

export async function confirmBooking(
  intentId: string,
  body: Schemas['BookingConfirmBody'],
  idempotencyKey = createIdempotencyKey(),
): Promise<BookingWorkflowResponse> {
  return apiRequest<BookingWorkflowResponse>(
    '/v1/booking-intents/' + encodeURIComponent(intentId) + '/confirm',
    {
      method: 'POST',
      body,
      idempotencyKey,
    },
  )
}

export async function reconcileBooking(bookingId: string): Promise<BookingWorkflowResponse> {
  return apiRequest<BookingWorkflowResponse>(
    '/v1/bookings/' + encodeURIComponent(bookingId) + '/reconcile',
    { method: 'POST' },
  )
}

export async function listBookings(): Promise<Booking[]> {
  const response = await apiRequest<unknown>('/v1/bookings', { csrf: false })
  return Array.isArray(response) ? response.map(asBooking) : []
}

export async function getBooking(bookingId: string): Promise<Booking> {
  const response = await apiRequest<unknown>('/v1/bookings/' + encodeURIComponent(bookingId), {
    csrf: false,
  })
  return asBooking(response)
}

export async function listWatches(): Promise<WatchRecord[]> {
  const response = await apiRequest<unknown>('/v1/watches', { csrf: false })
  return Array.isArray(response) ? response.map(asWatch) : []
}

export async function createWatch(criteria: FlightWatchCriteriaInput): Promise<WatchRecord> {
  const response = await apiRequest<unknown>('/v1/watches', {
    method: 'POST',
    body: criteria,
  })
  return asWatch(response)
}

export async function deleteWatch(watchId: string): Promise<void> {
  await apiRequest<void>('/v1/watches/' + encodeURIComponent(watchId), { method: 'DELETE' })
}

export async function transitionWatch(
  watchId: string,
  action: 'activate' | 'pause' | 'resume' | 'cancel',
): Promise<WatchRecord> {
  const response = await apiRequest<unknown>(
    '/v1/watches/' + encodeURIComponent(watchId) + '/' + action,
    { method: 'POST' },
  )
  return asWatch(response)
}
