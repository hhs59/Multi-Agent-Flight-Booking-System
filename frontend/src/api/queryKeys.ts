export const queryKeys = {
  threads: ['threads'] as const,
  thread: (threadId: string) => ['threads', threadId] as const,
  messages: (threadId: string) => ['threads', threadId, 'messages'] as const,
  travelers: ['travelers'] as const,
  preferences: ['preferences'] as const,
  search: (searchId: string) => ['search', searchId] as const,
  bookingIntent: (intentId: string) => ['booking-intents', intentId] as const,
  bookings: ['bookings'] as const,
  booking: (bookingId: string) => ['bookings', bookingId] as const,
  watches: ['watches'] as const,
}
