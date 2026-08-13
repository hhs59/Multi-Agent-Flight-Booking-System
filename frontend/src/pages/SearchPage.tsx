import { useMutation } from '@tanstack/react-query'
import { CalendarDays, ChevronDown, Search } from 'lucide-react'
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
  const [cabin, setCabin] = useState<Cabin>('economy')
  const [adults, setAdults] = useState('1')
  const [children, setChildren] = useState('0')
  const [infants, setInfants] = useState('0')
  const [maxStops, setMaxStops] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [result, setResult] = useState<SearchResult | null>(null)
  const [selectedOffer, setSelectedOffer] = useState<Offer | null>(null)

  const searchMutation = useMutation({
    mutationFn: async (): Promise<SearchResult> => {
      const normalizedOrigin = origin.trim().toUpperCase()
      if (normalizedOrigin.length !== 3) throw new Error('Enter a three-letter origin airport.')
      const stopCount = maxStops === '' ? null : Number(maxStops)
      const normalizedDestination = destination.trim().toUpperCase()
      if (normalizedDestination.length !== 3)
        throw new Error('Enter a three-letter destination airport.')
      if (!departureDate) throw new Error('Choose a departure date.')
      return createFlightSearch({
        origin: normalizedOrigin,
        destination: normalizedDestination,
        departure_date: departureDate,
        return_date: returnDate || null,
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
      <div className="page-header compact-page-header">
        <h1>Flights</h1>
      </div>
      <Card className="search-panel">
        <div className="search-primary-grid">
          <Field className="search-origin" label="From" required>
            <Input
              value={origin}
              onChange={(event) => setOrigin(event.target.value)}
              maxLength={3}
              placeholder="SGN"
              aria-label="Origin airport"
            />
          </Field>
          <Field
            className="search-destination"
            label="To"
            required
          >
            <Input
              value={destination}
              onChange={(event) => setDestination(event.target.value)}
              placeholder="HAN"
              aria-label="Destination airport"
            />
          </Field>
          <Field className="search-date" label="Departure" required>
            <Input
              type="date"
              value={departureDate}
              onChange={(event) => setDepartureDate(event.target.value)}
            />
          </Field>
          <Field className="search-date" label="Return">
            <Input
              type="date"
              value={returnDate}
              min={departureDate}
              onChange={(event) => setReturnDate(event.target.value)}
            />
          </Field>
          <Button
            className="search-submit"
            size="lg"
            loading={searchMutation.isPending}
            onClick={submit}
            aria-label="Search flights"
          >
            <Search size={18} /> Search
          </Button>
        </div>
        <details className="search-options">
          <summary>
            <span>Travelers and filters</span>
            <ChevronDown size={16} />
          </summary>
          <div className="search-secondary-grid">
            <Field label="Cabin">
              <Select
                value={cabin}
                onChange={(event) => setCabin(event.target.value as Cabin)}
                aria-label="Cabin"
              >
                <option value="economy">Economy</option>
                <option value="premium_economy">Premium economy</option>
                <option value="business">Business</option>
                <option value="first">First</option>
              </Select>
            </Field>
            <Field label="Adults">
              <Input
                type="number"
                min={1}
                max={9}
                value={adults}
                onChange={(event) => setAdults(event.target.value)}
                aria-label="Adults"
              />
            </Field>
            <Field label="Children">
              <Input
                type="number"
                min={0}
                max={8}
                value={children}
                onChange={(event) => setChildren(event.target.value)}
                aria-label="Children"
              />
            </Field>
            <Field label="Infants">
              <Input
                type="number"
                min={0}
                max={8}
                value={infants}
                onChange={(event) => setInfants(event.target.value)}
                aria-label="Infants"
              />
            </Field>
            <Field label="Stops">
              <Select value={maxStops} onChange={(event) => setMaxStops(event.target.value)}>
                <option value="">Any</option>
                <option value="0">Direct only</option>
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
      {searchMutation.isPending ? <SearchLoading /> : null}
      {result && !searchMutation.isPending ? (
        <SearchResults
          result={result}
          onReview={setSelectedOffer}
          reviewingId={selectedOffer?.offer_id}
        />
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
          <h2>
            {result.returned_results} flight{result.returned_results === 1 ? '' : 's'}
          </h2>
        </div>
        {offers.length ? (
          <OfferList offers={offers} onReview={onReview} reviewingId={reviewingId} />
        ) : (
          <EmptyState
            icon={<CalendarDays size={22} />}
            title="No flights found"
            description="Try different dates or fewer filters."
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
      <div className="offer-list">
        <Skeleton className="offer-skeleton" />
        <Skeleton className="offer-skeleton" />
      </div>
    </section>
  )
}
