import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Edit3,
  Filter,
  Plus,
  Search,
  ShieldCheck,
  Star,
  Trash2,
  UserRound,
  UsersRound,
} from 'lucide-react'
import { useState } from 'react'
import {
  createTraveler,
  deleteTraveler,
  listTravelers,
  makeDefaultTraveler,
  updateTraveler,
} from '../api/services'
import { queryKeys } from '../api/queryKeys'
import {
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  InfoBanner,
  Input,
  Modal,
  Select,
} from '../components/ui'
import type { Traveler, TravelerPatch } from '../types/api'
import { isApiError } from '../api/errors'
import { relativeDate } from '../lib/format'
import {
  travelerDisplayName,
  travelerReadiness,
  travelerReadinessLabel,
} from '../lib/travelerReadiness'
import { SecureMutationError } from '../components/SecureMutationError'
import { ApiNotice } from '../components/ui'

type TravelerFormState = {
  label: string
  title: string
  givenName: string
  familyName: string
  birthDate: string
  genderMarker: string
  email: string
  phone: string
  nationality: string
  passportNumber: string
  passportIssuingCountry: string
  passportExpiryDate: string
  isDefault: boolean
}

const blankForm: TravelerFormState = {
  label: '',
  title: '',
  givenName: '',
  familyName: '',
  birthDate: '',
  genderMarker: '',
  email: '',
  phone: '',
  nationality: '',
  passportNumber: '',
  passportIssuingCountry: '',
  passportExpiryDate: '',
  isDefault: false,
}

export function TravelersPage() {
  const queryClient = useQueryClient()
  const travelersQuery = useQuery({ queryKey: queryKeys.travelers, queryFn: listTravelers })
  const [form, setForm] = useState<TravelerFormState>(blankForm)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Traveler | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Traveler | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  const refresh = async (nextMessage = 'Traveler profiles are up to date.'): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.travelers })
    setMessage(nextMessage)
  }

  const createMutation = useMutation({
    mutationFn: () =>
      createTraveler({
        label: form.label.trim(),
        is_default: form.isDefault,
        title: nullable(form.title)?.toLowerCase() || null,
        given_name: nullable(form.givenName),
        family_name: nullable(form.familyName),
        birth_date: nullable(form.birthDate),
        gender_marker: nullable(form.genderMarker),
        email: nullable(form.email),
        phone: nullable(form.phone),
        nationality: nullable(form.nationality)?.toUpperCase() || null,
        passport_number: nullable(form.passportNumber),
        passport_issuing_country: nullable(form.passportIssuingCountry)?.toUpperCase() || null,
        passport_expiry_date: nullable(form.passportExpiryDate),
        save_preference: 'ask',
        consent_version: 'traveler-profile-v1',
      }),
    onSuccess: async () => {
      setForm(blankForm)
      setModalOpen(false)
      await refresh()
    },
  })

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!editing) throw new Error('No traveler selected.')
      const patch: TravelerPatch = {
        label: form.label.trim(),
        title: nullable(form.title)?.toLowerCase() || null,
        given_name: nullable(form.givenName),
        family_name: nullable(form.familyName),
        gender_marker: nullable(form.genderMarker),
        nationality: nullable(form.nationality)?.toUpperCase() || null,
      }
      if (form.birthDate.trim()) patch.birth_date = form.birthDate.trim()
      if (form.email.trim()) patch.email = form.email.trim()
      if (form.phone.trim()) patch.phone = form.phone.trim()
      if (form.passportNumber.trim()) {
        if (!form.passportIssuingCountry.trim() || !form.passportExpiryDate.trim()) {
          throw new Error(
            'Passport number, issuing country, and expiry date must be provided together.',
          )
        }
        patch.passport_number = form.passportNumber.trim()
        patch.passport_issuing_country = form.passportIssuingCountry.trim().toUpperCase()
        patch.passport_expiry_date = form.passportExpiryDate.trim()
      }
      return updateTraveler(editing.id, editing.version, patch)
    },
    onSuccess: async (updated) => {
      queryClient.setQueryData<Traveler[]>(queryKeys.travelers, (current) =>
        current?.map((item) => (item.id === updated.id ? updated : item)),
      )
      await queryClient.invalidateQueries({ queryKey: queryKeys.travelers })
      setEditing(null)
      setModalOpen(false)
      setForm(blankForm)
      setMessage(
        `Traveler saved. Email: ${updated.masked_email || 'not set'}. Phone: ${updated.masked_phone || 'not set'}.`,
      )
    },
    onError: async (error) => {
      if (isApiError(error) && error.status === 409) {
        await travelersQuery.refetch()
        setMessage(
          'This traveler changed on the server. Review the refreshed profile before saving again.',
        )
      }
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (traveler: Traveler) => deleteTraveler(traveler.id),
    onSuccess: async () => {
      setDeleteTarget(null)
      await refresh()
    },
  })

  const defaultMutation = useMutation({
    mutationFn: (traveler: Traveler) => makeDefaultTraveler(traveler.id),
    onSuccess: async () => {
      await refresh()
    },
  })

  const closeModal = (): void => {
    setEditing(null)
    setForm(blankForm)
    setModalOpen(false)
  }

  const openCreate = (): void => {
    setEditing(null)
    setForm(blankForm)
    setModalOpen(true)
  }

  const openEdit = (traveler: Traveler): void => {
    setEditing(traveler)
    setModalOpen(true)
    setForm({
      label: traveler.label,
      title: traveler.title || '',
      givenName: traveler.given_name || '',
      familyName: traveler.family_name || '',
      birthDate: '',
      genderMarker: traveler.gender_marker || '',
      email: '',
      phone: '',
      nationality: traveler.nationality || '',
      passportNumber: '',
      passportIssuingCountry: traveler.passport_issuing_country || '',
      passportExpiryDate: traveler.passport_expiry_date || '',
      isDefault: traveler.is_default,
    })
  }

  const retryDelete = (): void => {
    if (deleteMutation.variables) deleteMutation.mutate(deleteMutation.variables)
  }

  const retryDefault = (): void => {
    if (defaultMutation.variables) defaultMutation.mutate(defaultMutation.variables)
  }

  const updateConflict = isApiError(updateMutation.error) && updateMutation.error.status === 409

  const travelers = travelersQuery.data || []
  const totalTravelers = travelers.length
  const defaultTraveler = travelers.find((t) => t.is_default)
  const readyCount = travelers.filter((t) => travelerReadiness(t) !== 'incomplete').length

  const filteredTravelers = travelers.filter((t) => {
    if (!searchQuery.trim()) return true
    const q = searchQuery.toLowerCase()
    return (
      t.label.toLowerCase().includes(q) ||
      (t.given_name && t.given_name.toLowerCase().includes(q)) ||
      (t.family_name && t.family_name.toLowerCase().includes(q)) ||
      (t.nationality && t.nationality.toLowerCase().includes(q))
    )
  })

  return (
    <div className="page travelers-page-wrapper">
      {/* Header */}
      <div className="page-header compact-page-header">
        <div>
          <div className="destinations-badge">
            <UsersRound size={14} />
            <span>Passenger Profiles & Identity</span>
          </div>
          <h1>Traveler Profiles</h1>
          <p className="section-subtitle">
            Securely store passport and loyalty details for instant 1-click booking intents and auto-fill.
          </p>
        </div>
        <Button onClick={openCreate} variant="primary">
          <Plus size={16} /> Add New Traveler
        </Button>
      </div>

      {/* Summary KPI Cards */}
      <div className="bookings-summary-grid">
        <div className="summary-stat-card">
          <div className="summary-stat-icon icon-emerald">
            <UsersRound size={22} />
          </div>
          <div className="summary-stat-info">
            <span className="summary-stat-label">Saved Travelers</span>
            <strong className="summary-stat-val">{totalTravelers}</strong>
            <span className="summary-stat-hint">Encrypted AES-GCM</span>
          </div>
        </div>

        <div className="summary-stat-card">
          <div className="summary-stat-icon icon-amber">
            <Star size={22} />
          </div>
          <div className="summary-stat-info">
            <span className="summary-stat-label">Primary Passenger</span>
            <strong className="summary-stat-val">
              {defaultTraveler ? defaultTraveler.label : 'None'}
            </strong>
            <span className="summary-stat-hint">Default 1-click buyer</span>
          </div>
        </div>

        <div className="summary-stat-card">
          <div className="summary-stat-icon icon-green">
            <ShieldCheck size={22} />
          </div>
          <div className="summary-stat-info">
            <span className="summary-stat-label">Booking Readiness</span>
            <strong className="summary-stat-val text-success">
              {totalTravelers ? `${Math.round((readyCount / totalTravelers) * 100)}%` : '100%'}
            </strong>
            <span className="summary-stat-hint">IATA verified details</span>
          </div>
        </div>
      </div>

      {/* Search Bar */}
      {totalTravelers > 0 ? (
        <div className="travelers-search-bar">
          <div className="bookings-search-input-wrap">
            <Search size={15} className="search-icon" />
            <input
              type="text"
              placeholder="Search traveler by name, label, or nationality..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bookings-search-input"
            />
          </div>
        </div>
      ) : null}

      {message ? (
        <InfoBanner tone="success">
          <Check size={17} /> {message}
        </InfoBanner>
      ) : null}
      {createMutation.isError ? (
        <SecureMutationError
          error={createMutation.error}
          onRetry={() => createMutation.mutate()}
          retryLabel="Retry traveler creation"
        />
      ) : null}
      {updateMutation.isError ? (
        <>
          <ApiNotice error={updateMutation.error} />
          <SecureMutationError
            error={updateMutation.error}
            onRetry={updateConflict ? undefined : () => updateMutation.mutate()}
            retryLabel="Retry traveler update"
          />
        </>
      ) : null}
      {deleteMutation.isError ? (
        <SecureMutationError
          error={deleteMutation.error}
          onRetry={retryDelete}
          retryLabel="Retry traveler deletion"
        />
      ) : null}
      {defaultMutation.isError ? (
        <SecureMutationError
          error={defaultMutation.error}
          onRetry={retryDefault}
          retryLabel="Retry make default"
        />
      ) : null}
      {travelersQuery.isLoading ? (
        <div className="profile-grid">
          <div className="card-skeleton" />
          <div className="card-skeleton" />
        </div>
      ) : null}
      {travelersQuery.isError ? (
        <ErrorState error={travelersQuery.error} onRetry={() => void travelersQuery.refetch()} />
      ) : null}
      {travelers.length === 0 && !travelersQuery.isLoading && !travelersQuery.isError ? (
        <EmptyState
          icon={<UserRound size={28} />}
          title="No traveler profiles yet"
          description="Create a profile once and reuse it for future booking intents without re-typing passport information."
          action={
            <Button onClick={openCreate} variant="primary">
              <Plus size={16} /> Add Your First Traveler
            </Button>
          }
        />
      ) : null}
      {filteredTravelers.length ? (
        <div className="profile-grid">
          {filteredTravelers.map((traveler) => (
            <TravelerCard
              key={traveler.id}
              traveler={traveler}
              onEdit={() => openEdit(traveler)}
              onDelete={() => setDeleteTarget(traveler)}
              onDefault={() => defaultMutation.mutate(traveler)}
              loading={defaultMutation.isPending && defaultMutation.variables?.id === traveler.id}
            />
          ))}
        </div>
      ) : null}

      {totalTravelers > 0 && filteredTravelers.length === 0 ? (
        <EmptyState
          icon={<Filter size={24} />}
          title="No travelers match your search"
          description="Try searching with a different name or clear the search query."
          action={
            <Button variant="secondary" onClick={() => setSearchQuery('')}>
              Clear Search
            </Button>
          }
        />
      ) : null}

      <Modal
        title={editing ? 'Edit traveler profile' : 'Add traveler profile'}
        open={modalOpen}
        onClose={closeModal}
        footer={
          <>
            <Button variant="ghost" onClick={closeModal}>
              Cancel
            </Button>
            <Button
              loading={createMutation.isPending || updateMutation.isPending}
              onClick={() => (editing ? updateMutation.mutate() : createMutation.mutate())}
              disabled={!form.label.trim()}
            >
              {editing ? 'Save changes' : 'Create profile'}
            </Button>
          </>
        }
      >
        <TravelerForm form={form} setForm={setForm} editingTraveler={editing} />
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete traveler profile?"
        message="This removes the profile from future booking flows. Existing booking records keep their own safe snapshot."
        confirmLabel="Delete profile"
        danger
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget)
        }}
        loading={deleteMutation.isPending}
      />
    </div>
  )
}

function TravelerCard({
  traveler,
  onEdit,
  onDelete,
  onDefault,
  loading,
}: {
  traveler: Traveler
  onEdit: () => void
  onDelete: () => void
  onDefault: () => void
  loading: boolean
}) {
  return (
    <Card className="profile-card">
      <div className="profile-card-top">
        <div className="profile-avatar">{traveler.label.slice(0, 1).toUpperCase()}</div>
        <div className="profile-card-title">
          <h3>{traveler.label}</h3>
          <p>{travelerDisplayName(traveler)}</p>
        </div>
        {traveler.is_default ? (
          <span className="default-pill">
            <Star size={13} fill="currentColor" /> Default
          </span>
        ) : null}
      </div>
      <div className="profile-facts">
        <span>
          <span
            className={
              'status-badge ' +
              (travelerReadiness(traveler) === 'incomplete' ? 'status-warning' : 'status-success')
            }
          >
            {travelerReadinessLabel(travelerReadiness(traveler))}
          </span>
        </span>
        <span>
          <small>Email</small>
          {traveler.masked_email || 'Not added'}
        </span>
        <span>
          <small>Phone</small>
          {traveler.masked_phone || 'Not added'}
        </span>
        <span>
          <small>Birth year</small>
          {traveler.birth_year || 'Not added'}
        </span>
        <span>
          <small>Passport</small>
          {traveler.passport_ending ? `•••• ${traveler.passport_ending}` : 'Not added'}
        </span>
        {traveler.passport_expiry_date ? (
          <span>
            <small>Passport expires</small>
            {traveler.passport_expiry_date}
          </span>
        ) : null}
      </div>
      <div className="profile-card-footer">
        <span className="updated-label">Updated {relativeDate(traveler.updated_at)}</span>
        <div className="inline-actions">
          <Button variant="ghost" size="sm" onClick={onEdit}>
            <Edit3 size={14} /> Edit
          </Button>
          {!traveler.is_default ? (
            <Button variant="ghost" size="sm" loading={loading} onClick={onDefault}>
              <Star size={14} /> Make default
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            aria-label={'Delete ' + traveler.label}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      </div>
    </Card>
  )
}

function TravelerForm({
  form,
  setForm,
  editingTraveler,
}: {
  form: TravelerFormState
  setForm: (value: TravelerFormState) => void
  editingTraveler: Traveler | null
}) {
  const update = (key: keyof TravelerFormState, value: string | boolean): void =>
    setForm({ ...form, [key]: value })
  return (
    <div className="form-stack">
      <div className="form-grid-two">
        <Field label="Profile label" required hint="For example, Me or Alex">
          <Input
            value={form.label}
            maxLength={80}
            onChange={(event) => update('label', event.target.value)}
            data-autofocus
            placeholder="My profile"
          />
        </Field>
        <Field label="Title" hint="Duffel uses a bounded title set for booking">
          <Select value={form.title} onChange={(event) => update('title', event.target.value)}>
            <option value="">Select when booking</option>
            <option value="mr">Mr</option>
            <option value="mrs">Mrs</option>
            <option value="ms">Ms</option>
            <option value="miss">Miss</option>
            <option value="dr">Dr</option>
          </Select>
        </Field>
      </div>
      <div className="form-divider">
        <span>Name exactly as shown on travel document</span>
        <small>Do not guess how to divide a multi-part name.</small>
      </div>
      <div className="form-grid-two">
        <Field label="Given name" hint="As printed in the given-name field of the travel document">
          <Input
            value={form.givenName}
            onChange={(event) => update('givenName', event.target.value)}
            placeholder="First / given name"
          />
        </Field>
        <Field
          label="Surname / family name"
          hint="As printed in the surname field of the travel document"
        >
          <Input
            value={form.familyName}
            onChange={(event) => update('familyName', event.target.value)}
            placeholder="Surname / family name"
          />
        </Field>
        <Field label="Birth date">
          <Input
            type="date"
            value={form.birthDate}
            onChange={(event) => update('birthDate', event.target.value)}
          />
        </Field>
        <Field
          label="Gender marker"
          hint="Optional while saving a profile; required by the current flight-booking provider."
        >
          <Select
            value={form.genderMarker}
            onChange={(event) => update('genderMarker', event.target.value)}
          >
            <option value="">Select before booking</option>
            <option value="m">Male</option>
            <option value="f">Female</option>
          </Select>
        </Field>
        <Field
          label="Email"
          hint={
            editingTraveler
              ? `Current email: ${editingTraveler.masked_email || 'not set'}. Leave blank to keep the current email.`
              : undefined
          }
        >
          <Input
            type="email"
            value={form.email}
            onChange={(event) => update('email', event.target.value)}
            placeholder="name@example.com"
          />
        </Field>
        <Field
          label="Phone (Số điện thoại 10 số)"
          hint={
            editingTraveler
              ? `Current phone: ${editingTraveler.masked_phone || 'not set'}. Leave blank to keep current phone.`
              : 'Số điện thoại gồm tối đa 10 chữ số (chỉ nhập số)'
          }
        >
          <Input
            type="tel"
            inputMode="numeric"
            maxLength={10}
            value={form.phone}
            onChange={(event) => {
              const digitsOnly = event.target.value.replace(/\D/g, '').slice(0, 10)
              update('phone', digitsOnly)
            }}
            placeholder="Nhập số điện thoại (tối đa 10 số)..."
          />
        </Field>
        <Field label="Nationality" hint="Two-letter country code">
          <Input
            value={form.nationality}
            maxLength={2}
            onChange={(event) => update('nationality', event.target.value)}
            placeholder="VN"
          />
        </Field>
      </div>
      <div className="form-divider">
        <span>Passport details</span>
        <small>Required together for international booking validation.</small>
      </div>
      <div className="form-grid-three">
        <Field label="Passport number">
          <Input
            value={form.passportNumber}
            onChange={(event) => update('passportNumber', event.target.value)}
            autoComplete="off"
          />
        </Field>
        <Field label="Issuing country">
          <Input
            value={form.passportIssuingCountry}
            maxLength={2}
            onChange={(event) => update('passportIssuingCountry', event.target.value)}
            placeholder="VN"
          />
        </Field>
        <Field label="Expiry date">
          <Input
            type="date"
            value={form.passportExpiryDate}
            onChange={(event) => update('passportExpiryDate', event.target.value)}
          />
        </Field>
      </div>
      {!editingTraveler ? (
        <label className="check-field">
          <input
            type="checkbox"
            checked={form.isDefault}
            onChange={(event) => update('isDefault', event.target.checked)}
          />
          <span>Use as my default traveler</span>
        </label>
      ) : null}
      <InfoBanner tone="info">
        Sensitive details are encrypted for booking. After saving, this page shows only a safe
        preview; edit the profile to replace a value.
      </InfoBanner>
    </div>
  )
}

function nullable(value: string): string | null {
  return value.trim() || null
}
