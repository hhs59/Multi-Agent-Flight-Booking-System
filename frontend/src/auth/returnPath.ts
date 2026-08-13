const returnPathKey = 'flight-web.return-path'

export function toInternalReturnPath(value: unknown): string | null {
  if (
    typeof value !== 'string' ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.startsWith('/\\')
  ) {
    return null
  }
  if (value.includes('\r') || value.includes('\n')) return null
  try {
    const parsed = new URL(value, window.location.origin)
    return parsed.origin === window.location.origin
      ? parsed.pathname + parsed.search + parsed.hash
      : null
  } catch {
    return null
  }
}

export function rememberReturnPath(value: unknown): void {
  const path = toInternalReturnPath(value)
  if (path) window.sessionStorage.setItem(returnPathKey, path)
  else window.sessionStorage.removeItem(returnPathKey)
}

export function consumeReturnPath(): string | null {
  const value = window.sessionStorage.getItem(returnPathKey)
  window.sessionStorage.removeItem(returnPathKey)
  return toInternalReturnPath(value)
}

export function returnPathFromLocationState(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  return toInternalReturnPath((value as Record<string, unknown>).from)
}
