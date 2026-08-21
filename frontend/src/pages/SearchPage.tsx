import { useMutation } from '@tanstack/react-query'
import {
  ArrowLeftRight,
  ArrowRight,
  CalendarDays,
  ChevronDown,
  Clock,
  Compass,
  Filter,
  MapPin,
  Search,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { createFlightSearch } from '../api/services'
import {
  ApiNotice,
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from '../components/ui'
import { JourneyContext } from '../components/JourneyContext'
import { OfferBookingDialog } from '../components/OfferBookingDialog'
import { OfferList } from '../components/FlightOfferCard'
import { AirportInputDropdown } from '../components/AirportInputDropdown'
import type { Cabin, Offer, SearchResponse } from '../types/api'

type SearchResult = SearchResponse

const dateAfter = (days: number): string => {
  const value = new Date()
  value.setDate(value.getDate() + days)
  return value.toISOString().slice(0, 10)
}

export function SearchPage() {
  const navigate = useNavigate()
  const { restoreSecureSession, isRestoringSession } = useAuth()
  const [origin, setOrigin] = useState('SGN')
  const [destination, setDestination] = useState('HAN')
  const [departureDate, setDepartureDate] = useState(dateAfter(14))
  const [returnDate, setReturnDate] = useState('')
  const [tripType, setTripType] = useState<'oneway' | 'roundtrip'>('oneway')
  const [cabin, setCabin] = useState<Cabin>('economy')
  const [adults, setAdults] = useState('1')
  const [children, setChildren] = useState('0')
  const [infants, setInfants] = useState('0')
  const [maxStops, setMaxStops] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [selectedOffer, setSelectedOffer] = useState<Offer | null>(null)

  const swapAirports = () => {
    const temp = origin
    setOrigin(destination)
    setDestination(temp)
  }

  const searchMutation = useMutation({
    mutationFn: async (): Promise<SearchResult> => {
      const normalizedOrigin = origin.trim().toUpperCase()
      if (normalizedOrigin.length !== 3) throw new Error('Enter a valid 3-letter origin airport (e.g., SGN, HAN, SIN, BKK, DAD).')
      const stopCount = maxStops === '' ? null : Number(maxStops)
      const normalizedDestination = destination.trim().toUpperCase()
      if (normalizedDestination.length !== 3)
        throw new Error('Enter a valid 3-letter destination airport (e.g., HAN, SGN, SIN, NRT, DAD).')
      if (!departureDate) throw new Error('Choose a departure date.')
      return createFlightSearch({
        origin: normalizedOrigin,
        destination: normalizedDestination,
        departure_date: departureDate,
        return_date: tripType === 'roundtrip' && returnDate ? returnDate : null,
        adults: Number(adults),
        children: Number(children),
        infants: Number(infants),
        cabin,
        currency: 'VND',
        max_stops: stopCount,
      })
    },
    onSuccess: (data) => {
      setResult(data)
      setFormError(null)
    },
    onError: (error) =>
      setFormError(error instanceof Error ? error.message : 'Search could not be completed.'),
  })

  const submit = (): void => {
    setFormError(null)
    searchMutation.mutate()
  }

  return (
    <div className="page search-page">
      {/* Search Header */}
      <div className="search-header-hero">
        <div className="search-header-badge">
          <Sparkles size={14} />
          <span>Real-Time GDS Airline Aggregation</span>
        </div>
        <h1>Find the Best Flight Deals</h1>
        <p>Compare real-time fares from Vietnam Airlines, VietJet, Bamboo Airways, Singapore Airlines, and 300+ global carriers.</p>
      </div>

      {/* Modern Search Panel */}
      <Card className="search-panel chat-bar-glow">
        {/* Trip Type Tabs */}
        <div className="trip-type-tabs">
          <button
            type="button"
            className={'trip-type-btn ' + (tripType === 'oneway' ? 'active' : '')}
            onClick={() => {
              setTripType('oneway')
              setReturnDate('')
            }}
          >
            One-way
          </button>
          <button
            type="button"
            className={'trip-type-btn ' + (tripType === 'roundtrip' ? 'active' : '')}
            onClick={() => {
              setTripType('roundtrip')
              if (!returnDate) setReturnDate(dateAfter(21))
            }}
          >
            Round-trip
          </button>
          <span className="cabin-indicator">{cabin === 'economy' ? 'Economy Class' : cabin}</span>
        </div>

        {/* Primary Input Grid */}
        <div className={`search-primary-grid ${tripType === 'roundtrip' ? 'is-roundtrip' : 'is-oneway'}`}>
          <div className="airports-composite">
            <AirportInputDropdown
              label="Từ (From)"
              icon="origin"
              value={origin}
              onChange={setOrigin}
              placeholder="SGN"
              required
              className="origin-card"
            />

            {/* Floating Swap Button */}
            <button
              type="button"
              className="swap-airports-btn"
              onClick={swapAirports}
              title="Đổi chiều chuyến bay (SGN ⇄ HAN)"
              aria-label="Swap origin and destination"
            >
              <ArrowLeftRight size={16} />
            </button>

            <AirportInputDropdown
              label="Đến (To)"
              icon="dest"
              value={destination}
              onChange={setDestination}
              placeholder="HAN"
              required
              className="dest-card"
            />
          </div>

          {/* Departure Date Card */}
          <div className="travel-input-card date-card">
            <div className="travel-input-header">
              <span className="travel-input-label">
                <CalendarDays size={12} className="label-icon" /> Ngày đi (Departure)
              </span>
            </div>
            <div className="travel-input-body">
              <input
                type="date"
                value={departureDate}
                onChange={(event) => setDepartureDate(event.target.value)}
                className="travel-date-picker-input"
                aria-label="Departure date"
              />
              <span className="travel-date-subtext">
                {departureDate
                  ? new Date(departureDate + 'T00:00:00').toLocaleDateString('vi-VN', {
                      weekday: 'short',
                      day: '2-digit',
                      month: '2-digit',
                      year: 'numeric',
                    })
                  : 'Chọn ngày'}
              </span>
            </div>
          </div>

          {/* Return Date Card (if Roundtrip) */}
          {tripType === 'roundtrip' ? (
            <div className="travel-input-card date-card">
              <div className="travel-input-header">
                <span className="travel-input-label">
                  <CalendarDays size={12} className="label-icon" /> Ngày về (Return)
                </span>
                {returnDate ? (
                  <button
                    type="button"
                    className="clear-input-x-btn"
                    onClick={() => {
                      setReturnDate('')
                      setTripType('oneway')
                    }}
                    title="Chuyển sang 1 chiều"
                    aria-label="Clear return date"
                  >
                    <X size={13} />
                  </button>
                ) : null}
              </div>
              <div className="travel-input-body">
                <input
                  type="date"
                  value={returnDate}
                  min={departureDate}
                  onChange={(event) => setReturnDate(event.target.value)}
                  className="travel-date-picker-input"
                  aria-label="Return date"
                />
                <span className="travel-date-subtext">
                  {returnDate
                    ? new Date(returnDate + 'T00:00:00').toLocaleDateString('vi-VN', {
                        weekday: 'short',
                        day: '2-digit',
                        month: '2-digit',
                        year: 'numeric',
                      })
                    : 'Chọn ngày'}
                </span>
              </div>
            </div>
          ) : null}
        </div>

        {/* Centered Search Action Button Row */}
        <div className="search-action-row">
          <Button
            className="search-submit-btn"
            size="lg"
            variant="primary"
            loading={searchMutation.isPending}
            onClick={submit}
            aria-label="Search flights"
          >
            <Search size={19} />
            <span>Tìm Kiếm Chuyến Bay</span>
          </Button>
        </div>

        {/* Advanced Options Accordion */}
        <details className="search-options">
          <summary>
            <span className="summary-title">
              <Filter size={15} /> Passengers & Flight Filters
            </span>
            <ChevronDown size={16} className="chevron" />
          </summary>
          <div className="search-secondary-grid">
            <Field label="Cabin Class">
              <Select
                value={cabin}
                onChange={(event) => setCabin(event.target.value as Cabin)}
                aria-label="Cabin"
              >
                <option value="economy">Economy</option>
                <option value="premium_economy">Premium Economy</option>
                <option value="business">Business</option>
                <option value="first">First Class</option>
              </Select>
            </Field>

            <Field label="Adults (12+ yrs)">
              <Input
                type="number"
                min={1}
                max={9}
                value={adults}
                onChange={(event) => setAdults(event.target.value)}
                aria-label="Adults"
              />
            </Field>

            <Field label="Children (2-11 yrs)">
              <Input
                type="number"
                min={0}
                max={8}
                value={children}
                onChange={(event) => setChildren(event.target.value)}
                aria-label="Children"
              />
            </Field>

            <Field label="Infants (<2 yrs)">
              <Input
                type="number"
                min={0}
                max={8}
                value={infants}
                onChange={(event) => setInfants(event.target.value)}
                aria-label="Infants"
              />
            </Field>

            <Field label="Max Stops">
              <Select value={maxStops} onChange={(event) => setMaxStops(event.target.value)}>
                <option value="">Any stops</option>
                <option value="0">Direct flights only</option>
                <option value="1">Up to 1 stop</option>
                <option value="2">Up to 2 stops</option>
              </Select>
            </Field>
          </div>
        </details>

        {formError ? (
          <div className="form-error-banner">
            <span>{formError}</span>
          </div>
        ) : null}

        {searchMutation.isError ? (
          <ApiNotice
            error={searchMutation.error}
            onRestore={() => void restoreSecureSession()}
            restoring={isRestoringSession}
          />
        ) : null}
      </Card>

      {/* Loading Skeleton */}
      {searchMutation.isPending ? <SearchLoading /> : null}

      {/* Results View */}
      {result && !searchMutation.isPending ? (
        <SearchResults
          result={result}
          onReview={setSelectedOffer}
          reviewingId={selectedOffer?.offer_id}
        />
      ) : null}

      {/* Default Visual Showcase: Trending Destinations & Perks (When no search performed yet) */}
      {!result && !searchMutation.isPending ? (
        <>
          <TrendingDestinationsSection
            onSelectDestination={(destCode) => {
              setOrigin('SGN')
              setDestination(destCode)
              setTripType('oneway')
              setFormError(null)
              window.scrollTo({ top: 0, behavior: 'smooth' })
              searchMutation.mutate()
            }}
          />
          <WhyBookWithUsSection />
        </>
      ) : null}

      <OfferBookingDialog
        offer={selectedOffer}
        onClose={() => setSelectedOffer(null)}
        onIntentCreated={(intentId) => {
          setSelectedOffer(null)
          navigate('/booking-intents/' + intentId)
        }}
      />
    </div>
  )
}

const DESTINATIONS = [
  {
    code: 'DAD',
    city: 'Đà Nẵng & Hội An',
    country: 'Việt Nam',
    tag: 'Bãi Biển & Di Sản',
    image: '/images/destinations/danang.jpg',
    startingPrice: '680,000 ₫',
    duration: '1h 20m bay thẳng',
    description: 'Cầu Vàng Bà Nà Hills, bãi biển Mỹ Khê & phố cổ Hội An lung linh đèn lồng.',
  },
  {
    code: 'PQC',
    city: 'Phú Quốc',
    country: 'Việt Nam',
    tag: 'Đảo Thiên Đường',
    image: '/images/destinations/phuquoc.jpg',
    startingPrice: '790,000 ₫',
    duration: '1h 05m bay thẳng',
    description: 'Lặn ngắm san hô Bãi Sao, cáp treo Hòn Thơm & hoàng hôn rực rỡ sắc tím.',
  },
  {
    code: 'NRT',
    city: 'Tokyo',
    country: 'Nhật Bản',
    tag: 'Mùa Hoa Anh Đào',
    image: '/images/destinations/tokyo.jpg',
    startingPrice: '4,850,000 ₫',
    duration: '5h 30m bay thẳng',
    description: 'Ngắm đỉnh núi Phú Sĩ tuyết phủ, chùa Asakusa và phố đêm Shinjuku sôi động.',
  },
  {
    code: 'SIN',
    city: 'Singapore',
    country: 'Singapore',
    tag: 'Thành Phố Xanh',
    image: '/images/destinations/singapore.jpg',
    startingPrice: '1,950,000 ₫',
    duration: '2h 10m bay thẳng',
    description: 'Vịnh Marina Bay Sands, thác nước Jewel Changi & công viên Gardens by the Bay.',
  },
]

function TrendingDestinationsSection({
  onSelectDestination,
}: {
  onSelectDestination: (code: string) => void
}) {
  return (
    <section className="destinations-showcase-section" aria-label="Trending Travel Destinations">
      <div className="destinations-section-header">
        <div>
          <div className="destinations-badge">
            <Compass size={14} />
            <span>Khám Phá Điểm Đến Thịnh Hành</span>
          </div>
          <h2>Chặng Bay Được Yêu Thích Nhất 2026</h2>
          <p>Giá vé ưu đãi độc quyền từ Vietnam Airlines, Bamboo Airways và các hãng bay quốc tế.</p>
        </div>
      </div>

      <div className="destinations-cards-grid">
        {DESTINATIONS.map((dest) => (
          <div
            key={dest.code}
            className="destination-card group"
            onClick={() => onSelectDestination(dest.code)}
            role="button"
            tabIndex={0}
          >
            <div className="destination-image-box">
              <img src={dest.image} alt={dest.city} className="destination-img" />
              <div className="destination-overlay-gradient" />
              <span className="destination-tag-pill">{dest.tag}</span>
              <div className="destination-price-floating">
                <span className="price-label">Từ</span>
                <strong>{dest.startingPrice}</strong>
              </div>
            </div>

            <div className="destination-info-body">
              <div className="destination-header-row">
                <div>
                  <h3 className="destination-name">
                    <MapPin size={16} className="inline-icon" /> {dest.city}
                  </h3>
                  <span className="destination-country">{dest.country}</span>
                </div>
                <span className="destination-airport-code">{dest.code}</span>
              </div>

              <p className="destination-desc">{dest.description}</p>

              <div className="destination-footer-row">
                <span className="destination-duration">
                  <Clock size={13} /> {dest.duration}
                </span>
                <button
                  type="button"
                  className="destination-action-btn"
                  onClick={(e) => {
                    e.stopPropagation()
                    onSelectDestination(dest.code)
                  }}
                >
                  <span>Tìm vé ngay</span>
                  <ArrowRight size={14} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function WhyBookWithUsSection() {
  return (
    <section className="travel-perks-section" aria-label="Travel Benefits">
      <div className="perks-container">
        <div className="perk-card">
          <div className="perk-icon-wrap perk-icon-emerald">
            <Zap size={22} />
          </div>
          <div className="perk-content">
            <h4>Giá Trực Tiếp Duffel & IATA</h4>
            <p>Kết nối 300+ hãng hàng không toàn cầu thời gian thực. Không phụ phí ẩn, minh bạch 100%.</p>
          </div>
        </div>

        <div className="perk-card">
          <div className="perk-icon-wrap perk-icon-amber">
            <Sparkles size={22} />
          </div>
          <div className="perk-content">
            <h4>Multi-Agent AI Concierge</h4>
            <p>Trợ lý AI tự động so sánh chặng bay ngắn nhất, tối ưu chi phí và hỗ trợ sau bán vé 24/7.</p>
          </div>
        </div>

        <div className="perk-card">
          <div className="perk-icon-wrap perk-icon-blue">
            <ShieldCheck size={22} />
          </div>
          <div className="perk-content">
            <h4>Bảo Mật Giao Dịch Cấp Ngân Hàng</h4>
            <p>Mã hóa dữ liệu hành khách chuẩn AES-GCM-256 & xác thực OIDC Keycloak an toàn tuyệt đối.</p>
          </div>
        </div>
      </div>
    </section>
  )
}

function SearchResults({
  result,
  onReview,
  reviewingId,
}: {
  result: SearchResult
  onReview: (offer: Offer) => void
  reviewingId?: string | null
}) {
  const offers = result.offers ? [...result.offers] : []
  return (
    <>
      <section className="results-section">
        <div className="results-heading">
          <div>
            <h2>
              Found {result.returned_results} verified flight{result.returned_results === 1 ? '' : 's'}
            </h2>
            <span className="results-subhead">Prices include all mandatory taxes and carrier surcharges.</span>
          </div>
        </div>

        {offers.length ? (
          <OfferList offers={offers} onReview={onReview} reviewingId={reviewingId} />
        ) : (
          <EmptyState
            icon={<CalendarDays size={24} />}
            title="No flights found for this route and date"
            description="Try adjusting your departure date or removing stop restrictions."
          />
        )}
      </section>

      <JourneyContext
        recommendation={result.destination_recommendations}
        weather={result.weather}
        showWeatherStatus={offers.length > 0}
      />
    </>
  )
}

function SearchLoading() {
  return (
    <section className="results-section" aria-label="Searching flights">
      <div className="results-loading-header">
        <Sparkles size={18} className="animate-spin text-primary" />
        <span>Searching 300+ airlines for verified fares...</span>
      </div>
      <div className="offer-list">
        <Skeleton className="offer-skeleton" />
        <Skeleton className="offer-skeleton" />
        <Skeleton className="offer-skeleton" />
      </div>
    </section>
  )
}
