import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Compass, SlidersHorizontal } from 'lucide-react'
import { deletePreferences, getPreferences, updatePreferences } from '../api/services'
import { queryKeys } from '../api/queryKeys'
import { Button, EmptyState, ErrorState, Field, InfoBanner, Input, Modal, Select } from './ui'
import type { Cabin, PreferencesPatch, PreferencesState, PreferencesView } from '../types/api'
import { SecureMutationError } from './SecureMutationError'

type PreferenceForm = {
  origin: string
  cabin: Cabin | ''
  maxStops: string
  baggageRequired: boolean
  departureStart: string
  departureEnd: string
  timezone: string
}

const emptyForm: PreferenceForm = {
  origin: '',
  cabin: '',
  maxStops: '',
  baggageRequired: false,
  departureStart: '',
  departureEnd: '',
  timezone: 'Asia/Ho_Chi_Minh',
}

export type TravelPreferencesPanelProps = {
  open: boolean
  onClose: () => void
}

export function TravelPreferencesPanel({ open, onClose }: TravelPreferencesPanelProps) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: queryKeys.preferences, queryFn: getPreferences })
  const [form, setForm] = useState<PreferenceForm>(emptyForm)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (query.data && isConfigured(query.data)) {
      setForm(formFromPreferences(query.data))
    }
  }, [query.data])

  const saveMutation = useMutation({
    mutationFn: () => {
      const current = query.data && isConfigured(query.data) ? query.data : null
      const payload: PreferencesPatch = {
        expected_version: current?.version,
        default_origin_airport: form.origin.trim().toUpperCase() || null,
        preferred_cabin: form.cabin || null,
        max_stops: form.maxStops === '' ? null : Number(form.maxStops),
        baggage_required: form.baggageRequired,
        preferred_departure_start: form.departureStart || null,
        preferred_departure_end: form.departureEnd || null,
        timezone: form.timezone.trim() || null,
      }
      return updatePreferences(payload)
    },
    onSuccess: (response) => {
      client.setQueryData(queryKeys.preferences, response)
      setMessage('Your preferences are saved.')
    },
  })

  const clearMutation = useMutation({
    mutationFn: deletePreferences,
    onSuccess: () => {
      setForm(emptyForm)
      client.setQueryData(queryKeys.preferences, { configured: false, status: 'not_configured' })
      setMessage('Preferences cleared.')
    },
  })

  const disabled = isDisabled(query.data)

  return (
    <Modal
      open={open}
      title="Travel preferences"
      onClose={onClose}
      footer={
        <div className="settings-footer-panel">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {query.data && !disabled ? (
            <div className="inline-actions">
              <Button
                variant="ghost"
                onClick={() => clearMutation.mutate()}
                loading={clearMutation.isPending}
              >
                Clear all
              </Button>
              <Button onClick={() => saveMutation.mutate()} loading={saveMutation.isPending}>
                Save preferences
              </Button>
            </div>
          ) : null}
        </div>
      }
    >
      <div className="form-stack preferences-panel">
        <p className="page-lede">
          These defaults guide ranking. They never override an explicit request in Assistant or
          Search.
        </p>
        {query.isLoading ? <div className="form-skeleton" /> : null}
        {query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : null}
        {disabled ? (
          <EmptyState
            icon={<SlidersHorizontal size={23} />}
            title="Preferences are disabled"
            description="Travel preferences are not available right now. Your explicit search criteria still work."
          />
        ) : null}
        {query.data && !disabled && !isConfigured(query.data) ? (
          <InfoBanner tone="info">
            <Compass size={18} /> No saved preferences yet. Add a few defaults to make results feel
            more like yours.
          </InfoBanner>
        ) : null}
        {message ? (
          <InfoBanner tone="success">
            <Check size={17} /> {message}
          </InfoBanner>
        ) : null}
        {query.data && !disabled ? (
          <>
            <div className="settings-section">
              <div>
                <h2>Search defaults</h2>
                <p>Use airport codes and preferences that are safe to reuse.</p>
              </div>
              <div className="form-grid-two">
                <Field label="Default origin" hint="Three-letter airport code">
                  <Input
                    maxLength={3}
                    value={form.origin}
                    onChange={(event) => setForm({ ...form, origin: event.target.value })}
                    placeholder="SGN"
                    data-autofocus
                  />
                </Field>
                <Field label="Cabin">
                  <Select
                    value={form.cabin}
                    onChange={(event) =>
                      setForm({ ...form, cabin: event.target.value as Cabin | '' })
                    }
                  >
                    <option value="">No preference</option>
                    <option value="economy">Economy</option>
                    <option value="premium_economy">Premium economy</option>
                    <option value="business">Business</option>
                    <option value="first">First</option>
                  </Select>
                </Field>
                <Field label="Maximum stops">
                  <Select
                    value={form.maxStops}
                    onChange={(event) => setForm({ ...form, maxStops: event.target.value })}
                  >
                    <option value="">No preference</option>
                    <option value="0">Direct only</option>
                    <option value="1">Up to 1 stop</option>
                    <option value="2">Up to 2 stops</option>
                  </Select>
                </Field>
                <Field label="Baggage">
                  <label className="check-field">
                    <input
                      type="checkbox"
                      checked={form.baggageRequired}
                      onChange={(event) =>
                        setForm({ ...form, baggageRequired: event.target.checked })
                      }
                    />
                    <span>Prefer checked baggage</span>
                  </label>
                </Field>
                <Field label="Departure window">
                  <div className="form-grid-two">
                    <Input
                      type="time"
                      value={form.departureStart}
                      onChange={(event) => setForm({ ...form, departureStart: event.target.value })}
                      aria-label="Preferred departure start"
                    />
                    <Input
                      type="time"
                      value={form.departureEnd}
                      onChange={(event) => setForm({ ...form, departureEnd: event.target.value })}
                      aria-label="Preferred departure end"
                    />
                  </div>
                </Field>
                <Field label="Timezone">
                  <Input
                    value={form.timezone}
                    onChange={(event) => setForm({ ...form, timezone: event.target.value })}
                  />
                </Field>
              </div>
            </div>
            {saveMutation.isError ? (
              <SecureMutationError
                error={saveMutation.error}
                onRetry={() => saveMutation.mutate()}
                retryLabel="Retry saving preferences"
              />
            ) : null}
            {clearMutation.isError ? (
              <SecureMutationError
                error={clearMutation.error}
                onRetry={() => clearMutation.mutate()}
                retryLabel="Retry clearing preferences"
              />
            ) : null}
            <span className="field-hint">
              Empty fields explicitly clear values. Saved preferences only guide ranking.
            </span>
          </>
        ) : null}
      </div>
    </Modal>
  )
}

function isConfigured(value: PreferencesState | undefined): value is PreferencesView {
  return Boolean(value && 'configured' in value && value.configured === true)
}

function isDisabled(value: PreferencesState | undefined): boolean {
  return Boolean(value && 'status' in value && value.status === 'feature_disabled')
}

export function preferenceSummary(value: PreferencesState | undefined): string {
  if (!value || isDisabled(value)) return 'Preferences unavailable'
  if (!isConfigured(value)) return 'No preferences saved'
  const parts = [value.default_origin_airport, value.preferred_cabin]
    .filter((item): item is string => Boolean(item))
    .map((item) => item.replaceAll('_', ' '))
  if (value.max_stops === 0) parts.push('Direct')
  else if (value.max_stops !== null && value.max_stops !== undefined)
    parts.push(`≤${value.max_stops} stops`)
  if (value.baggage_required) parts.push('Checked bag')
  return parts.length ? parts.join(' · ') : 'No preferences saved'
}

function formFromPreferences(value: PreferencesView): PreferenceForm {
  return {
    origin: value.default_origin_airport || '',
    cabin: value.preferred_cabin || '',
    maxStops:
      value.max_stops === null || value.max_stops === undefined ? '' : String(value.max_stops),
    baggageRequired: value.baggage_required === true,
    departureStart: value.preferred_departure_start || '',
    departureEnd: value.preferred_departure_end || '',
    timezone: value.timezone || 'Asia/Ho_Chi_Minh',
  }
}
