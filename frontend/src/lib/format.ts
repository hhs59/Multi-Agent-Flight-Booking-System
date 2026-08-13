import { format, formatDistanceToNow } from 'date-fns'

export function formatMoney(total: string, currency: string): string {
  const amount = Number(total)
  if (!Number.isFinite(amount)) return total + ' ' + currency
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(amount)
}

export function formatDateTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.valueOf())) return value
  return format(date, 'EEE, d MMM · HH:mm')
}

export function formatDate(value: string): string {
  if (!value) return '—'
  const date = new Date(value + 'T12:00:00')
  if (Number.isNaN(date.valueOf())) return value
  return format(date, 'd MMM yyyy')
}

export function relativeDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : formatDistanceToNow(date, { addSuffix: true })
}

export function durationLabel(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return hours ? hours + 'h ' + remainder + 'm' : remainder + 'm'
}

export function initials(value: string): string {
  return value
    .split(' ')
    .map((part) => part[0] || '')
    .join('')
    .slice(0, 2)
    .toUpperCase()
}
