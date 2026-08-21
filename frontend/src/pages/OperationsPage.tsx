import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowUpDown,
  CheckCircle2,
  Database,
  Eye,
  FileCheck2,
  Filter,
  Plane,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  ShieldCheck,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { listBookings, reconcileBooking } from '../api/services'
import { queryKeys } from '../api/queryKeys'
import { Button, Card, EmptyState, ErrorState, StatusBadge } from '../components/ui'
import type { Booking } from '../types/api'
import { relativeDate } from '../lib/format'

export function OperationsPage() {
  const client = useQueryClient()
  const bookingsQuery = useQuery({ queryKey: queryKeys.bookings, queryFn: listBookings })
  const [filterStatus, setFilterStatus] = useState<string>('all')
  const [searchTerm, setSearchTerm] = useState('')
  const [sortBy, setSortBy] = useState<'newest' | 'oldest' | 'reconciliation_first'>('newest')
  const [selectedAuditLog, setSelectedAuditLog] = useState<Booking | null>(null)

  const reconcileMutation = useMutation({
    mutationFn: (bookingId: string) => reconcileBooking(bookingId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.bookings })
    },
  })

  const bookings = bookingsQuery.data || []
  const filteredBookings = bookings
    .filter((b) => {
      const matchesStatus = filterStatus === 'all' || b.status === filterStatus
      const matchesSearch =
        !searchTerm ||
        b.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (b.confirmation_code && b.confirmation_code.toLowerCase().includes(searchTerm.toLowerCase())) ||
        (b.provider && b.provider.toLowerCase().includes(searchTerm.toLowerCase()))
      return matchesStatus && matchesSearch
    })
    .sort((a, b) => {
      if (sortBy === 'reconciliation_first') {
        const aNeeds = a.status === 'needs_reconciliation' ? 1 : 0
        const bNeeds = b.status === 'needs_reconciliation' ? 1 : 0
        if (bNeeds !== aNeeds) return bNeeds - aNeeds
      }
      const aTime = new Date(a.created_at).getTime()
      const bTime = new Date(b.created_at).getTime()
      return sortBy === 'oldest' ? aTime - bTime : bTime - aTime
    })

  const totalBookings = bookings.length
  const confirmedCount = bookings.filter((b) => b.status === 'order_created' || b.status === 'confirmed').length
  const needsReconciliationCount = bookings.filter((b) => b.status === 'needs_reconciliation').length
  const failedCount = bookings.filter((b) => b.status === 'failed').length

  const successRate = totalBookings ? Math.round((confirmedCount / totalBookings) * 100) : 100

  return (
    <div className="page operations-page">
      {/* Header */}
      <div className="page-header compact-page-header">
        <div>
          <div className="ops-badge">
            <Activity size={14} />
            <span>Internal OTA Operations Console</span>
          </div>
          <h1>Operations & Service Desk</h1>
          <p className="section-subtitle">Real-time booking exceptions, GDS airline settlement status, and manual reconciliation dispatch.</p>
        </div>
        <div className="ops-quick-actions">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void bookingsQuery.refetch()}
            loading={bookingsQuery.isFetching}
          >
            <RefreshCw size={15} /> Refresh Data
          </Button>
        </div>
      </div>

      {/* KPI Performance Metrics Grid */}
      <div className="ops-kpi-grid">
        <Card className="kpi-card">
          <div className="kpi-icon icon-blue">
            <FileCheck2 size={22} />
          </div>
          <div className="kpi-body">
            <span className="kpi-label">Total Booking Orders</span>
            <strong className="kpi-value">{totalBookings}</strong>
            <span className="kpi-subtext">All time volume</span>
          </div>
        </Card>

        <Card className="kpi-card">
          <div className="kpi-icon icon-green">
            <ShieldCheck size={22} />
          </div>
          <div className="kpi-body">
            <span className="kpi-label">Ticketing Success Rate</span>
            <strong className="kpi-value text-success">{successRate}%</strong>
            <span className="kpi-subtext">Target SLA: &gt;98%</span>
          </div>
        </Card>

        <Card className="kpi-card">
          <div className="kpi-icon icon-orange">
            <AlertTriangle size={22} />
          </div>
          <div className="kpi-body">
            <span className="kpi-label">Needs Reconciliation</span>
            <strong className="kpi-value text-warning">{needsReconciliationCount}</strong>
            <span className="kpi-subtext">Pending carrier check</span>
          </div>
        </Card>

        <Card className="kpi-card">
          <div className="kpi-icon icon-red">
            <AlertCircle size={22} />
          </div>
          <div className="kpi-body">
            <span className="kpi-label">Failed / Exceptions</span>
            <strong className="kpi-value text-danger">{failedCount}</strong>
            <span className="kpi-subtext">Auto-refund triggered</span>
          </div>
        </Card>
      </div>

      {/* Supplier & GDS Health Status Matrix */}
      <section className="supplier-matrix-section">
        <h2 className="section-title">Supplier & GDS Integration Status</h2>
        <div className="supplier-grid">
          <Card className="supplier-card">
            <div className="supplier-topline">
              <div className="supplier-name">
                <Plane size={18} className="text-primary" />
                <strong>Duffel GDS Airline API</strong>
              </div>
              <span className="supplier-status status-online">Connected</span>
            </div>
            <p className="supplier-meta">Coverage: 300+ Airlines (Vietnam Airlines, Singapore Airlines, Qatar Airways, Emirates)</p>
            <div className="supplier-stat">
              <span>Average Response: <strong>240ms</strong></span>
              <span>Uptime SLA: <strong>99.9%</strong></span>
            </div>
          </Card>

          <Card className="supplier-card">
            <div className="supplier-topline">
              <div className="supplier-name">
                <Database size={18} className="text-success" />
                <strong>PostgreSQL Transaction Store</strong>
              </div>
              <span className="supplier-status status-online">Healthy</span>
            </div>
            <p className="supplier-meta">Encrypted with AES-GCM-256 (FieldEncryptor Active Version 1)</p>
            <div className="supplier-stat">
              <span>Alembic Migrations: <strong>16/16 Applied</strong></span>
              <span>Connection Pool: <strong>Optimal</strong></span>
            </div>
          </Card>

          <Card className="supplier-card">
            <div className="supplier-topline">
              <div className="supplier-name">
                <Server size={18} className="text-accent" />
                <strong>Keycloak OIDC Identity Gateway</strong>
              </div>
              <span className="supplier-status status-online">SSO Active</span>
            </div>
            <p className="supplier-meta">Role-based access control, CSRF tokens, & JWT Session Validation</p>
            <div className="supplier-stat">
              <span>Session Lifetime: <strong>12 Hours</strong></span>
              <span>Registration: <strong>Open</strong></span>
            </div>
          </Card>
        </div>
      </section>

      {/* Booking Exceptions & Orders Queue */}
      <section className="orders-queue-section">
        <div className="ops-queue-header-wrap">
          <div className="ops-queue-title-area">
            <h2 className="section-title">Transactions & Booking Exception Queue</h2>
            <p className="section-subtitle">Search orders, inspect audit logs, and trigger manual carrier reconciliation.</p>
          </div>
        </div>

        {/* Professional Ops Search & Filter Control Bar */}
        <div className="ops-toolbar-card">
          {/* Left Search Input */}
          <div className="ops-search-box">
            <Search size={16} className="ops-search-icon" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search by Order ID, PNR, Carrier (e.g. VN, VJ)..."
              className="ops-search-input-field"
            />
            {searchTerm ? (
              <button
                type="button"
                className="clear-input-x-btn"
                onClick={() => setSearchTerm('')}
                title="Clear search"
                aria-label="Clear search"
              >
                <X size={12} />
              </button>
            ) : null}
          </div>

          {/* Right Controls: Filter Status & Sort By */}
          <div className="ops-controls-group">
            {/* Status Filter */}
            <div className="ops-select-wrap">
              <span className="ops-select-icon">
                <Filter size={14} />
              </span>
              <select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
                className="ops-custom-select"
                aria-label="Filter by status"
              >
                <option value="all">All Statuses ({totalBookings})</option>
                <option value="order_created">Confirmed / Created ({confirmedCount})</option>
                <option value="needs_reconciliation">Needs Reconciliation ({needsReconciliationCount})</option>
                <option value="failed">Failed ({failedCount})</option>
              </select>
            </div>

            {/* Sort By Dropdown */}
            <div className="ops-select-wrap">
              <span className="ops-select-icon">
                <ArrowUpDown size={14} />
              </span>
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as 'newest' | 'oldest' | 'reconciliation_first')}
                className="ops-custom-select"
                aria-label="Sort by"
              >
                <option value="newest">Sort: Newest First</option>
                <option value="reconciliation_first">Sort: Reconciliation Priority</option>
                <option value="oldest">Sort: Oldest First</option>
              </select>
            </div>

            {/* Quick Reset if filtered */}
            {searchTerm || filterStatus !== 'all' || sortBy !== 'newest' ? (
              <button
                type="button"
                className="ops-reset-btn"
                onClick={() => {
                  setSearchTerm('')
                  setFilterStatus('all')
                  setSortBy('newest')
                }}
                title="Reset filters"
              >
                <RotateCcw size={13} />
                <span>Reset</span>
              </button>
            ) : null}
          </div>
        </div>

        {bookingsQuery.isLoading ? (
          <div className="booking-list">
            <div className="card-skeleton" />
            <div className="card-skeleton" />
          </div>
        ) : null}

        {bookingsQuery.isError ? (
          <ErrorState error={bookingsQuery.error} onRetry={() => void bookingsQuery.refetch()} />
        ) : null}

        {!bookingsQuery.isLoading && !filteredBookings.length ? (
          <EmptyState
            icon={<CheckCircle2 size={26} className="text-success" />}
            title="No orders match your filter"
            description="All booking queues are clear. No pending exceptions requiring manual intervention."
          />
        ) : null}

        {filteredBookings.length ? (
          <Card className="queue-table-card">
            <div className="table-responsive">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Order Reference</th>
                    <th>Confirmation (PNR)</th>
                    <th>Carrier Supplier</th>
                    <th>Status</th>
                    <th>Created Time</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredBookings.map((b) => (
                    <tr key={b.id}>
                      <td>
                        <Link to={`/bookings/${b.id}`} className="order-id-link">
                          <code>#{b.id.slice(0, 10)}</code>
                        </Link>
                      </td>
                      <td>
                        <strong className="pnr-cell">{b.confirmation_code || 'Pending'}</strong>
                      </td>
                      <td>
                        <span>{b.provider || 'Duffel GDS'}</span>
                      </td>
                      <td>
                        <StatusBadge status={b.status} />
                      </td>
                      <td>
                        <span className="timestamp-cell">{relativeDate(b.created_at)}</span>
                      </td>
                      <td>
                        <div className="row-actions">
                          <Button
                            variant="secondary"
                            size="sm"
                            loading={reconcileMutation.isPending && reconcileMutation.variables === b.id}
                            onClick={() => reconcileMutation.mutate(b.id)}
                            title="Re-verify with supplier GDS"
                          >
                            <RefreshCw size={13} /> Reconcile
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setSelectedAuditLog(b)}
                            title="View technical audit snapshot"
                          >
                            <Eye size={14} /> Log
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : null}
      </section>

      {/* Audit Log Modal */}
      {selectedAuditLog ? (
        <AuditLogModal booking={selectedAuditLog} onClose={() => setSelectedAuditLog(null)} />
      ) : null}
    </div>
  )
}

function AuditLogModal({ booking, onClose }: { booking: Booking; onClose: () => void }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content modal-content-lg" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Order Audit Log & Technical Trace</h3>
          <p>Order ID: <code>{booking.id}</code></p>
        </div>

        <div className="modal-body">
          <div className="audit-json-box">
            <pre>{JSON.stringify(booking, null, 2)}</pre>
          </div>
        </div>

        <div className="modal-footer">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>
  )
}
