import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BriefcaseBusiness, CalendarDays, ChevronLeft, ChevronRight, Plane, RefreshCw } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { getBooking, listBookings, reconcileBooking } from '../api/services'
import { queryKeys } from '../api/queryKeys'
import { Button, Card, EmptyState, ErrorState, InfoBanner, StatusBadge } from '../components/ui'
import type { Booking } from '../types/api'
import { formatDateTime, relativeDate } from '../lib/format'

export function BookingsPage() {
  const query = useQuery({ queryKey: queryKeys.bookings, queryFn: listBookings })
  return (
    <div className="page">
      <div className="page-header compact-page-header">
        <h1>Bookings</h1>
        <Button to="/search"><Plane size={16} /> Find a flight</Button>
      </div>
      {query.isLoading ? <div className="booking-list"><div className="card-skeleton" /><div className="card-skeleton" /></div> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.data?.length === 0 ? (
        <EmptyState icon={<CalendarDays size={23} />} title="No bookings yet" description="Your bookings will appear here." action={<Button to="/search">Search flights</Button>} />
      ) : null}
      {query.data?.length ? <div className="booking-list">{query.data.map((booking) => <BookingCard key={booking.id} booking={booking} />)}</div> : null}
    </div>
  )
}

export function BookingDetailPage() {
  const { bookingId } = useParams()
  const query = useQuery({
    queryKey: queryKeys.booking(bookingId || ''),
    queryFn: () => getBooking(bookingId || ''),
    enabled: Boolean(bookingId),
  })
  return (
    <div className="page booking-detail-page">
      <div className="page-header compact-page-header">
        <div>
          <Link className="back-link" to="/bookings"><ChevronLeft size={16} /> Bookings</Link>
          <h1>Booking</h1>
        </div>
      </div>
      {query.isLoading ? <Card><div className="form-skeleton" /></Card> : null}
      {query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.data ? <BookingDetail booking={query.data} /> : null}
    </div>
  )
}

function BookingCard({ booking }: { booking: Booking }) {
  return (
    <Link className="booking-card" to={'/bookings/' + booking.id}>
      <div className="booking-card-icon"><BriefcaseBusiness size={19} /></div>
      <div className="booking-card-main">
        <div className="booking-card-heading">
          <h3>{booking.confirmation_code || 'Booking ' + booking.id.slice(0, 8)}</h3>
          <div className="booking-statuses">
            {booking.provider_environment === 'sandbox' ? <span className="test-badge">Test booking</span> : null}
            <StatusBadge status={booking.status} />
          </div>
        </div>
        <p>{booking.provider || 'Pending'} · {relativeDate(booking.created_at)}</p>
      </div>
      <ChevronRight size={18} className="booking-chevron" />
    </Link>
  )
}

function BookingDetail({ booking }: { booking: Booking }) {
  const client = useQueryClient()
  const reconcileMutation = useMutation({
    mutationFn: () => reconcileBooking(booking.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.booking(booking.id) })
      void client.invalidateQueries({ queryKey: queryKeys.bookings })
    },
  })
  const providerCreated = Boolean(booking.provider && booking.masked_provider_order_reference)
  const needsReconciliation = booking.status === 'needs_reconciliation'
  return (
    <Card className="booking-detail-card">
      <div className="detail-hero">
        <div>
          <div className="booking-statuses">
            {booking.provider_environment === 'sandbox' ? <span className="test-badge">Test booking</span> : null}
            <StatusBadge status={booking.status} />
          </div>
          <h2>{booking.confirmation_code || 'Booking ' + booking.id.slice(0, 8)}</h2>
        </div>
        {providerCreated ? (
          <Button variant="secondary" loading={reconcileMutation.isPending} onClick={() => reconcileMutation.mutate()}>
            <RefreshCw size={16} /> Refresh status
          </Button>
        ) : null}
      </div>
      <div className="booking-facts">
        <div><span>Confirmation</span><strong>{booking.confirmation_code || 'Pending'}</strong></div>
        <div><span>Provider</span><strong>{booking.provider || 'Pending'}</strong></div>
        <div><span>Order reference</span><strong>{booking.masked_provider_order_reference || 'Pending'}</strong></div>
        <div><span>Last updated</span><strong>{booking.last_reconciled_at ? formatDateTime(booking.last_reconciled_at) : 'Not refreshed'}</strong></div>
      </div>
      {needsReconciliation ? <InfoBanner tone="warning">We could not confirm the latest booking status. Refresh before trying again.</InfoBanner> : null}
      {reconcileMutation.isError ? <ErrorState error={reconcileMutation.error} onRetry={() => reconcileMutation.mutate()} /> : null}
    </Card>
  )
}
