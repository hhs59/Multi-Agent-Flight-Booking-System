import { CloudSun, Droplets } from 'lucide-react'
import type { DestinationRecommendation } from './DestinationRecommendations'
import { DestinationRecommendations } from './DestinationRecommendations'
import { isRecord } from '../types/api'

type WeatherSummary = {
  status: 'available' | 'unavailable'
  destinationAirport: string
  city: string
  requestedAt: string
  forecastAt: string | null
  temperatureC: string | null
  description: string | null
  precipitationProbability: string | null
  source: string
  updatedAt: string
  reason: string | null
}

export function parseWeather(value: unknown): WeatherSummary | null {
  if (!isRecord(value)) return null
  if (value.status !== 'available' && value.status !== 'unavailable') return null
  if (typeof value.destination_airport !== 'string' || typeof value.city !== 'string') return null
  return {
    status: value.status,
    destinationAirport: value.destination_airport,
    city: value.city,
    requestedAt: typeof value.requested_at === 'string' ? value.requested_at : '',
    forecastAt: typeof value.forecast_at === 'string' ? value.forecast_at : null,
    temperatureC: finiteNumericString(value.temperature_c, -100, 100),
    description: typeof value.description === 'string' ? value.description : null,
    precipitationProbability: finiteNumericString(value.precipitation_probability, 0, 1),
    source: typeof value.source === 'string' ? value.source : 'Weather',
    updatedAt: typeof value.updated_at === 'string' ? value.updated_at : '',
    reason: typeof value.reason === 'string' ? value.reason : null,
  }
}

function finiteNumericString(value: unknown, minimum: number, maximum: number): string | null {
  if (typeof value !== 'string' || !value.trim()) return null
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric >= minimum && numeric <= maximum ? value : null
}

export function JourneyContext({
  recommendation,
  weather,
  showWeatherStatus = false,
}: {
  recommendation?: DestinationRecommendation | null
  weather?: unknown
  showWeatherStatus?: boolean
}) {
  const forecast = parseWeather(weather)
  const hasPlaces = recommendation?.status === 'completed' && Boolean(recommendation.places?.length)
  if (!forecast && !hasPlaces && !showWeatherStatus) return null

  return (
    <section className="journey-context" aria-label="Plan your stay">
      {forecast ? (
        <WeatherRow weather={forecast} />
      ) : showWeatherStatus ? (
        <MissingWeatherRow />
      ) : null}
      {hasPlaces ? <DestinationRecommendations recommendation={recommendation} /> : null}
    </section>
  )
}

function MissingWeatherRow() {
  return (
    <div className="weather-row weather-row-unavailable">
      <CloudSun size={18} />
      <span>Weather forecast unavailable</span>
    </div>
  )
}

function WeatherRow({ weather }: { weather: WeatherSummary }) {
  const rain = weather.precipitationProbability
    ? Math.round(Number(weather.precipitationProbability) * 100)
    : null
  const forecastDate = weather.forecastAt || weather.requestedAt
  const outsideWindow = weather.reason?.includes('outside the supported time window')
  if (weather.status === 'unavailable') {
    return (
      <div className="weather-row weather-row-unavailable">
        <CloudSun size={18} />
        <span>
          {outsideWindow
            ? 'Weather forecast is not available yet'
            : 'Weather is unavailable right now'}
        </span>
      </div>
    )
  }
  return (
    <div className="weather-row">
      <CloudSun size={19} />
      <div>
        <strong>Weather near arrival · {weather.city}</strong>
        <span>
          {sentenceCase(weather.description || 'Forecast available')} ·{' '}
          {formatForecastDate(forecastDate)}
          {rain !== null ? (
            <>
              {' '}
              · <Droplets size={13} /> {rain}% rain
            </>
          ) : null}
        </span>
      </div>
      {weather.temperatureC ? <b>{Math.round(Number(weather.temperatureC))}°</b> : null}
    </div>
  )
}

function sentenceCase(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value
}

function formatForecastDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return 'Near arrival'
  return new Intl.DateTimeFormat('en', { weekday: 'short', month: 'short', day: 'numeric' }).format(
    date,
  )
}
