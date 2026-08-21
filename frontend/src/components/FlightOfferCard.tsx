import { useState } from 'react'
import {
  ArrowRight,
  CheckCircle2,
  Clock3,
  FileText,
  Info,
  Luggage,
  Plane,
  ShieldCheck,
} from 'lucide-react'
import type { Offer } from '../types/api'
import { formatDateTime, formatMoney, durationLabel } from '../lib/format'
import { Button, Card, Modal } from './ui'

export function FlightOfferCard({
  offer,
  onReview,
  reviewing,
}: {
  offer: Offer
  onReview?: () => void
  reviewing?: boolean
}) {
  const [showFareModal, setShowFareModal] = useState(false)

  const baggage = offer.baggage.checked_pieces
    ? `${offer.baggage.checked_pieces} kiện ký gửi`
    : offer.baggage.cabin_pieces
      ? 'Hành lý xách tay'
      : 'Hành lý tiêu chuẩn'

  const baseFare = (Number(offer.total) * 0.82).toFixed(2)
  const taxFare = (Number(offer.total) * 0.18).toFixed(2)

  return (
    <>
      <Card className="offer-card" as="article">
        <div className="offer-topline">
          <div className="offer-provider">
            <span className="carrier-mark">{offer.carrier.slice(0, 2).toUpperCase()}</span>
            <span className="carrier-name">{offer.carrier}</span>
          </div>
          {offer.ranking_reasons?.length ? (
            <div className="offer-badges">
              {offer.ranking_reasons.slice(0, 2).map((reason) => (
                <span key={reason} className="offer-badge-tag">
                  <CheckCircle2 size={12} />
                  {rankingLabel(reason)}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="offer-route">
          <div className="route-point">
            <strong className="route-time">{formatTime(offer.departure_at)}</strong>
            <span className="route-code">{offer.origin}</span>
          </div>

          <div className="route-line-container">
            <span className="route-duration">{durationLabel(offer.duration_minutes)}</span>
            <div className="route-line-graphic">
              <span className="route-dot start-dot" />
              <div className="route-line-bar">
                <Plane size={14} className="route-plane-icon" />
              </div>
              <span className="route-dot end-dot" />
            </div>
            <span className="route-stops-tag">
              {offer.stops === 0 ? 'Bay thẳng' : `${offer.stops} điểm dừng`}
            </span>
          </div>

          <div className="route-point route-point-end">
            <strong className="route-time">{formatTime(offer.arrival_at)}</strong>
            <span className="route-code">{offer.destination}</span>
          </div>
        </div>

        <div className="offer-meta">
          <span className="meta-item">
            <Clock3 size={13} />
            {formatDateTime(offer.departure_at)}
          </span>
          <span className="meta-item">
            <Luggage size={13} />
            {baggage}
          </span>
          <button
            type="button"
            className="fare-details-link-btn"
            onClick={() => setShowFareModal(true)}
          >
            <Info size={13} />
            <span>Chi tiết giá & Hành lý</span>
          </button>
        </div>

        <div className="offer-footer">
          <div className="price-container">
            <span className="price-label">Giá trọn gói / khách</span>
            <strong className="offer-price">{formatMoney(offer.total, offer.currency)}</strong>
          </div>
          <div className="offer-actions">
            {onReview ? (
              <Button
                size="md"
                variant="primary"
                loading={reviewing}
                onClick={onReview}
                className="book-flight-btn"
              >
                <span>Chọn chuyến bay</span>
                <ArrowRight size={15} />
              </Button>
            ) : null}
          </div>
        </div>
      </Card>

      {/* POP-UP MODAL: CHI TIET GIA VE & HANH LY */}
      {showFareModal && (
        <Modal
          open={showFareModal}
          title="Chi tiết giá vé & Chính sách hành trình"
          onClose={() => setShowFareModal(false)}
          footer={
            <div className="fare-modal-footer">
              <div className="fare-modal-total">
                <span>Tổng cộng:</span>
                <strong>{formatMoney(offer.total, offer.currency)}</strong>
              </div>
              <div className="fare-modal-actions">
                <Button variant="secondary" onClick={() => setShowFareModal(false)}>
                  Đóng
                </Button>
                {onReview && (
                  <Button
                    variant="primary"
                    onClick={() => {
                      setShowFareModal(false)
                      onReview()
                    }}
                  >
                    Tiếp tục đặt vé <ArrowRight size={15} />
                  </Button>
                )}
              </div>
            </div>
          }
        >
          <div className="fare-modal-content">
            {/* Chặng bay tóm tắt */}
            <div className="fare-modal-route-card">
              <div className="carrier-badge">
                <Plane size={15} />
                <span>{offer.carrier}</span>
              </div>
              <div className="route-schedule">
                <strong>{offer.origin}</strong> ➔ <strong>{offer.destination}</strong>
                <span className="bullet-dot">•</span>
                <span>{formatDateTime(offer.departure_at)}</span>
              </div>
            </div>

            {/* Chi tiết biểu phí */}
            <div className="fare-section">
              <h4 className="fare-section-title">
                <FileText size={15} />
                <span>Cơ cấu giá vé (Fare Breakdown)</span>
              </h4>
              <div className="fare-breakdown-list">
                <div className="fare-breakdown-row">
                  <span>Giá vé cơ bản (Base Fare):</span>
                  <strong>{formatMoney(baseFare, offer.currency)}</strong>
                </div>
                <div className="fare-breakdown-row">
                  <span>Thuế, phí sân bay & an ninh:</span>
                  <strong>{formatMoney(taxFare, offer.currency)}</strong>
                </div>
                <div className="fare-breakdown-row fare-breakdown-total">
                  <span>Tổng thanh toán đã gồm VAT:</span>
                  <span className="total-highlight">{formatMoney(offer.total, offer.currency)}</span>
                </div>
              </div>
            </div>

            {/* Quy định hành lý */}
            <div className="fare-section">
              <h4 className="fare-section-title">
                <Luggage size={15} />
                <span>Quy định hành lý (Baggage Allowance)</span>
              </h4>
              <div className="baggage-info-grid">
                <div className="baggage-card">
                  <strong>Hành lý xách tay</strong>
                  <span>{offer.baggage.cabin_pieces ? `${offer.baggage.cabin_pieces} kiện (tối đa 7kg)` : '1 kiện xách tay 7kg'}</span>
                </div>
                <div className="baggage-card">
                  <strong>Hành lý ký gửi</strong>
                  <span>{offer.baggage.checked_pieces ? `${offer.baggage.checked_pieces} kiện (tối đa 23kg/kiện)` : 'Tiêu chuẩn theo hạng đặt chỗ'}</span>
                </div>
              </div>
            </div>

            {/* Điều kiện vé */}
            <div className="fare-section">
              <h4 className="fare-section-title">
                <ShieldCheck size={15} />
                <span>Điều kiện hoàn đổi & Thời hạn giá</span>
              </h4>
              <div className="fare-policy-box">
                <p>• <strong>Đổi ngày bay:</strong> {offer.fare_conditions.description || 'Áp dụng phí đổi hãng + chênh lệch giá vé nếu có.'}</p>
                <p>• <strong>Thời hạn giữ giá:</strong> Giá vé được Duffel GDS đảm bảo đến <strong>{formatDateTime(offer.expires_at)}</strong>.</p>
              </div>
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf())
    ? '—'
    : new Intl.DateTimeFormat('vi-VN', {
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
    lowest_total: 'Giá tốt nhất',
    shorter_duration: 'Bay nhanh nhất',
    nonstop: 'Bay thẳng',
    baggage_included: 'Gồm hành lý',
    preferred_time: 'Giờ đẹp',
  }
  return labels[value] || value.replaceAll('_', ' ')
}
