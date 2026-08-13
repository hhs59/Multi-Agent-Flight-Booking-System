import { env } from '../config/env'
import { sessionStore } from '../auth/sessionStore'
import { ApiError } from './errors'

type ApiRequestOptions = {
  method?: string
  body?: unknown
  csrf?: boolean
  idempotencyKey?: string
  signal?: AbortSignal
}

let unauthorizedHandler: (() => void) | undefined

export const setUnauthorizedHandler = (handler: (() => void) | undefined): void => {
  unauthorizedHandler = handler
}

const isMutation = (method: string): boolean =>
  method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS'

const parseResponseBody = async (response: Response): Promise<unknown> => {
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

const getErrorDetails = (
  body: unknown,
): { code: string; message: string; traceId?: string; validation?: unknown } => {
  if (!body || typeof body !== 'object') {
    return { code: 'http_error', message: 'The request could not be completed.' }
  }
  const record = body as Record<string, unknown>
  const detail =
    record.detail && typeof record.detail === 'object'
      ? (record.detail as Record<string, unknown>)
      : record
  return {
    code: typeof detail.code === 'string' ? detail.code : 'http_error',
    message:
      typeof detail.message === 'string'
        ? detail.message
        : typeof detail.detail === 'string'
          ? detail.detail
          : 'The request could not be completed.',
    traceId:
      typeof detail.trace_id === 'string'
        ? detail.trace_id
        : typeof record.trace_id === 'string'
          ? record.trace_id
          : undefined,
    validation: detail,
  }
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const headers = new Headers({ Accept: 'application/json' })
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (options.csrf ?? isMutation(method)) {
    const session = sessionStore.read()
    if (session?.csrfToken) headers.set('X-CSRF-Token', session.csrfToken)
  }
  if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey)

  let response: Response
  try {
    response = await fetch(env.apiBaseUrl + path, {
      method,
      credentials: 'include',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch {
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: 'We could not reach the flight service. Check your connection and try again.',
      retryable: true,
    })
  }

  const body = await parseResponseBody(response)
  if (!response.ok) {
    const details = getErrorDetails(body)
    const code =
      response.status === 403 && isMutation(method) && details.code === 'http_error'
        ? 'csrf_invalid'
        : details.code
    if (response.status === 401) {
      sessionStore.clear()
      unauthorizedHandler?.()
    }
    throw new ApiError({
      status: response.status,
      code,
      message: details.message,
      traceId: details.traceId,
      validation: details.validation,
      retryable: response.status >= 500 || response.status === 429,
    })
  }

  return body as T
}
