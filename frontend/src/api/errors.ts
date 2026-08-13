export type ApiErrorDetails = {
  status: number
  code: string
  message: string
  traceId?: string
  retryable: boolean
  validation?: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly traceId?: string
  readonly retryable: boolean
  readonly validation?: unknown

  constructor(details: ApiErrorDetails) {
    super(details.message)
    this.name = 'ApiError'
    this.status = details.status
    this.code = details.code
    this.traceId = details.traceId
    this.retryable = details.retryable
    this.validation = details.validation
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isNotFound(): boolean {
    return this.status === 404
  }
}

export const isApiError = (value: unknown): value is ApiError => value instanceof ApiError

const csrfErrorCodes = new Set([
  'csrf_invalid',
  'csrf_missing',
  'csrf_expired',
  'csrf_session_mismatch',
  'session_integrity',
  'session_integrity_failure',
])

export const isCsrfError = (value: unknown): boolean =>
  isApiError(value) &&
  value.status === 403 &&
  (csrfErrorCodes.has(value.code) || value.code.startsWith('csrf_'))
