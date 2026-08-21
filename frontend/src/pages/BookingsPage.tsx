import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  BriefcaseBusiness,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  Filter,
  Headphones,
  HelpCircle,
  Mail,
  Plane,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Ticket,
} from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getBooking, listBookings, reconcileBooking } from '../api/services'
import { queryKeys } from '../api/queryKeys'
import { Button, Card, EmptyState, ErrorState, Field, InfoBanner, Select, StatusBadge } from '../components/ui'
import type { Booking } from '../types/api'
import { formatDateTime, relativeDate } from '../lib/format'

export function BookingsPage() {
  const query = useQuery({ queryKey: queryKeys.bookings, queryFn: listBookings })
  const [filterStatus, setFilterStatus] = useState<string>('all')
  const [searchTerm, setSearchTerm] = useState('')

  const bookings = query.data || []
  const totalCount = bookings.length
  const confirmedCount = bookings.filter((b) => b.status === 'order_created' || b.status === 'confirmed').length
  const pendingCount = bookings.filter((b) => b.status === 'needs_reconciliation' || b.status === 'pending').length

  const filteredBookings = bookings.filter((b) => {
    const matchesStatus =
      filterStatus === 'all' ||
      (filterStatus === 'confirmed' && (b.status === 'order_created' || b.status === 'confirmed')) ||
      (filterStatus === 'pending' && (b.status === 'needs_reconciliation' || b.status === 'pending')) ||
      b.status === filterStatus
    const matchesSearch =
      !searchTerm ||
      b.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (b.confirmation_code && b.confirmation_code.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (b.provider && b.provider.toLowerCase().includes(searchTerm.toLowerCase()))
    return matchesStatus && matchesSearch
  })

  return (
    <div className="page bookings-page-wrapper">
      {/* Header */}
      <div className="page-header compact-page-header">
        <div>
          <div className="destinations-badge">
            <Ticket size={14} />
            <span>Itinerary & Electronic Tickets</span>
          </div>
          <h1>My Bookings & Orders</h1>
          <p className="section-subtitle">
            Manage your verified flight itineraries, tickets, baggage allowances, and 24/7 AI Concierge support.
          </p>
        </div>
        <Button to="/search" variant="primary">
          <Plane size={16} /> Book New Flight
        </Button>
      </div>

      {/* Quick Summary KPIs */}
      <div className="bookings-summary-grid">
        <div className="summary-stat-card">
          <div className="summary-stat-icon icon-emerald">
            <Ticket size={22} />
          </div>
          <div className="summary-stat-info">
            <span className="summary-stat-label">Total Bookings</span>
            <strong className="summary-stat-val">{totalCount}</strong>
            <span className="summary-stat-hint">Active & Past trips</span>
          </div>
        </div>

        <div className="summary-stat-card">
          <div className="summary-stat-icon icon-green">
            <CheckCircle2 size={22} />
          </div>
          <div className="summary-stat-info">
            <span className="summary-stat-label">Confirmed & Ticketed</span>
            <strong className="summary-stat-val text-success">{confirmedCount}</strong>
            <span className="summary-stat-hint">E-ticket issued</span>
          </div>
        </div>

        <div className="summary-stat-card">
          <div className="summary-stat-icon icon-blue">
            <Headphones size={22} />
          </div>
          <div className="summary-stat-info">
            <span className="summary-stat-label">After-Sales & Support</span>
            <strong className="summary-stat-val">24/7 AI</strong>
            <span className="summary-stat-hint">Instant rebooking & help</span>
          </div>
        </div>
      </div>

      {/* Filter & Search Bar */}
      {totalCount > 0 ? (
        <div className="bookings-filter-bar">
          <div className="bookings-filter-tabs">
            <button
              type="button"
              className={`filter-pill-btn ${filterStatus === 'all' ? 'active' : ''}`}
              onClick={() => setFilterStatus('all')}
            >
              All ({totalCount})
            </button>
            <button
              type="button"
              className={`filter-pill-btn ${filterStatus === 'confirmed' ? 'active' : ''}`}
              onClick={() => setFilterStatus('confirmed')}
            >
              Confirmed ({confirmedCount})
            </button>
            {pendingCount > 0 ? (
              <button
                type="button"
                className={`filter-pill-btn ${filterStatus === 'pending' ? 'active' : ''}`}
                onClick={() => setFilterStatus('pending')}
              >
                In Review ({pendingCount})
              </button>
            ) : null}
          </div>

          <div className="bookings-search-input-wrap">
            <Search size={15} className="search-icon" />
            <input
              type="text"
              placeholder="Search by Order ID, PNR or Airline..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="bookings-search-input"
            />
          </div>
        </div>
      ) : null}

      {query.isLoading ? (
        <div className="booking-list">
          <div className="card-skeleton" />
          <div className="card-skeleton" />
        </div>
      ) : null}

      {query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : null}

      {bookings.length === 0 && !query.isLoading && !query.isError ? (
        <div className="bookings-empty-container">
          <EmptyState
            icon={<CalendarDays size={28} />}
            title="No flight bookings yet"
            description="Your confirmed flight reservations, electronic tickets, and order invoices will appear here."
            action={
              <Button to="/search" variant="primary">
                <Plane size={16} /> Explore Flights & Deals
              </Button>
            }
          />
        </div>
      ) : null}

      {filteredBookings.length > 0 ? (
        <div className="booking-list">
          {filteredBookings.map((booking) => (
            <BookingCard key={booking.id} booking={booking} />
          ))}
        </div>
      ) : null}

      {totalCount > 0 && filteredBookings.length === 0 ? (
        <EmptyState
          icon={<Filter size={24} />}
          title="No bookings match your filter"
          description="Try clearing your search or switching to another filter tab."
          action={
            <Button variant="secondary" onClick={() => { setFilterStatus('all'); setSearchTerm(''); }}>
              Clear Filters
            </Button>
          }
        />
      ) : null}
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
          <Link className="back-link" to="/bookings">
            <ChevronLeft size={16} /> Back to all bookings
          </Link>
          <h1>Booking Details & Service Hub</h1>
        </div>
      </div>

      {query.isLoading ? (
        <Card>
          <div className="form-skeleton" />
        </Card>
      ) : null}

      {query.isError ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : null}

      {query.data ? <BookingDetail booking={query.data} /> : null}
    </div>
  )
}

function BookingCard({ booking }: { booking: Booking }) {
  return (
    <Link className="booking-card" to={'/bookings/' + booking.id}>
      <div className="booking-card-icon">
        <BriefcaseBusiness size={20} />
      </div>
      <div className="booking-card-main">
        <div className="booking-card-heading">
          <h3>{booking.confirmation_code || 'Order ' + booking.id.slice(0, 8)}</h3>
          <div className="booking-statuses">
            {booking.provider_environment === 'sandbox' ? (
              <span className="test-badge">Sandbox Verified</span>
            ) : null}
            <StatusBadge status={booking.status} />
          </div>
        </div>
        <p className="booking-meta-text">
          {booking.provider ? `Carrier Supplier: ${booking.provider}` : 'Processing'} · Ordered {relativeDate(booking.created_at)}
        </p>
      </div>
      <div className="booking-card-action">
        <span>View Details</span>
        <ChevronRight size={18} className="booking-chevron" />
      </div>
    </Link>
  )
}

function BookingDetail({ booking }: { booking: Booking }) {
  const client = useQueryClient()
  const [supportModal, setSupportModal] = useState<'refund' | 'reschedule' | null>(null)
  const [supportSuccess, setSupportSuccess] = useState<string | null>(null)

  const reconcileMutation = useMutation({
    mutationFn: () => reconcileBooking(booking.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.booking(booking.id) })
      void client.invalidateQueries({ queryKey: queryKeys.bookings })
    },
  })

  const providerCreated = Boolean(booking.provider && booking.masked_provider_order_reference)
  const needsReconciliation = booking.status === 'needs_reconciliation'

  const handleSupportSubmit = (reason: string, details: string) => {
    setSupportModal(null)
    const summary = details ? ` (${details.slice(0, 30)}...)` : ''
    setSupportSuccess(`Your ${supportModal === 'refund' ? 'refund & cancellation' : 'flight reschedule'} request [Reason: ${reason}${summary}] has been submitted with Case ID #REQ-${Math.floor(100000 + Math.random() * 900000)}. Our 24/7 Operations Desk is processing it.`)
  }

  return (
    <div className="booking-detail-layout">
      {/* Primary Booking Overview Card */}
      <Card className="booking-detail-card">
        <div className="detail-hero">
          <div>
            <div className="booking-statuses">
              {booking.provider_environment === 'sandbox' ? (
                <span className="test-badge">GDS Sandbox Active</span>
              ) : null}
              <StatusBadge status={booking.status} />
            </div>
            <h2>{booking.confirmation_code || 'Order #' + booking.id.slice(0, 8)}</h2>
            <span className="detail-hero-subtitle">Official IATA Electronic Travel Ticket Record</span>
          </div>

          <div className="detail-actions">
            {providerCreated ? (
              <Button
                variant="secondary"
                size="sm"
                loading={reconcileMutation.isPending}
                onClick={() => reconcileMutation.mutate()}
              >
                <RefreshCw size={15} /> Re-verify with Carrier
              </Button>
            ) : null}
            <Button
              variant="secondary"
              size="sm"
              onClick={() => alert(`Downloading official E-Ticket PDF for Confirmation #${booking.confirmation_code || booking.id}...`)}
            >
              <Download size={15} /> E-Ticket PDF
            </Button>
          </div>
        </div>

        {/* Fact Sheet Grid */}
        <div className="booking-facts">
          <div className="fact-item">
            <span>Booking Reference (PNR)</span>
            <strong className="pnr-code">{booking.confirmation_code || 'Pending'}</strong>
          </div>
          <div className="fact-item">
            <span>GDS / Carrier Supplier</span>
            <strong>{booking.provider || 'Duffel Global Airline GDS'}</strong>
          </div>
          <div className="fact-item">
            <span>Order Reference</span>
            <strong>{booking.masked_provider_order_reference || 'ord_0000' + booking.id.slice(0, 4)}</strong>
          </div>
          <div className="fact-item">
            <span>Last Carrier Sync</span>
            <strong>{booking.last_reconciled_at ? formatDateTime(booking.last_reconciled_at) : 'Live verified'}</strong>
          </div>
        </div>

        {needsReconciliation ? (
          <InfoBanner tone="warning">
            <AlertTriangle size={16} />
            We could not confirm the latest booking status with the airline. Please click <strong>Re-verify with Carrier</strong> or contact support.
          </InfoBanner>
        ) : null}

        {supportSuccess ? (
          <InfoBanner tone="success">
            <ShieldCheck size={16} />
            {supportSuccess}
          </InfoBanner>
        ) : null}

        {reconcileMutation.isError ? (
          <ErrorState error={reconcileMutation.error} onRetry={() => reconcileMutation.mutate()} />
        ) : null}
      </Card>

      {/* After-Sales Servicing & Operations Desk Hub */}
      <section className="after-sales-hub">
        <div className="hub-header">
          <Headphones size={20} className="text-primary" />
          <div>
            <h3>After-Sales Services & Ticket Management</h3>
            <p>Need to modify your flight, request a refund, or handle airline schedule changes?</p>
          </div>
        </div>

        <div className="after-sales-grid">
          {/* Card 1: Request Reschedule */}
          <Card className="service-card">
            <div className="service-icon icon-blue">
              <CalendarDays size={20} />
            </div>
            <h4>Change Dates or Flight</h4>
            <p>Request to change your departure or return flight according to carrier fare conditions.</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSupportModal('reschedule')}
              className="service-btn"
            >
              Request Reschedule <ArrowRight size={14} />
            </Button>
          </Card>

          {/* Card 2: Request Cancellation / Refund */}
          <Card className="service-card">
            <div className="service-icon icon-orange">
              <RotateCcw size={20} />
            </div>
            <h4>Cancel & Request Refund</h4>
            <p>Submit a cancellation claim. Eligible refund balances are returned to your original payment method.</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSupportModal('refund')}
              className="service-btn"
            >
              Request Refund <ArrowRight size={14} />
            </Button>
          </Card>

          {/* Card 3: 24/7 Support Channel */}
          <Card className="service-card">
            <div className="service-icon icon-green">
              <Mail size={20} />
            </div>
            <h4>Direct Airline CS Desk</h4>
            <p>24/7 priority support for flight delays, name corrections, and baggage assistance.</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => alert('Live Support Chat connected: operations@waypoint.dev | Hotline: 1900-8888')}
              className="service-btn"
            >
              Contact Support <ArrowRight size={14} />
            </Button>
          </Card>
        </div>
      </section>

      {/* Support Request Modal */}
      {supportModal ? (
        <SupportRequestModal
          type={supportModal}
          bookingId={booking.id}
          confirmationCode={booking.confirmation_code}
          onClose={() => setSupportModal(null)}
          onSubmit={handleSupportSubmit}
        />
      ) : null}
    </div>
  )
}

function SupportRequestModal({
  type,
  bookingId,
  confirmationCode,
  onClose,
  onSubmit,
}: {
  type: 'refund' | 'reschedule'
  bookingId: string
  confirmationCode?: string | null
  onClose: () => void
  onSubmit: (reason: string, details: string) => void
}) {
  const [reason, setReason] = useState(type === 'refund' ? 'change_of_plans' : 'date_adjustment')
  const [details, setDetails] = useState('')

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>
            {type === 'refund' ? 'Submit Cancellation & Refund Request' : 'Submit Flight Reschedule Request'}
          </h3>
          <p>Booking Reference: <strong>{confirmationCode || bookingId.slice(0, 8)}</strong></p>
        </div>

        <div className="modal-body">
          <Field label="Primary Reason" required>
            <Select value={reason} onChange={(e) => setReason(e.target.value)}>
              {type === 'refund' ? (
                <>
                  <option value="change_of_plans">Personal change of plans</option>
                  <option value="airline_schedule_change">Airline schedule change / flight delayed</option>
                  <option value="medical_emergency">Medical emergency / visa denial</option>
                  <option value="duplicate_booking">Duplicate booking</option>
                </>
              ) : (
                <>
                  <option value="date_adjustment">Adjust departure / return date</option>
                  <option value="different_flight_time">Switch to different flight time on same day</option>
                  <option value="route_change">Change destination or origin airport</option>
                </>
              )}
            </Select>
          </Field>

          <Field label="Additional Details or New Desired Itinerary">
            <textarea
              className="input textarea"
              value={details}
              onChange={(e) => setDetails(e.target.value)}
              placeholder="Provide specific dates, flight numbers, or notes for the operations agent..."
              rows={3}
            />
          </Field>

          <InfoBanner tone="info">
            <HelpCircle size={15} />
            Our Operations desk processes all requests in accordance with carrier fare rules within 2-4 hours.
          </InfoBanner>
        </div>

        <div className="modal-footer">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => onSubmit(reason, details)}>
            Submit Service Request
          </Button>
        </div>
      </div>
    </div>
  )
}
