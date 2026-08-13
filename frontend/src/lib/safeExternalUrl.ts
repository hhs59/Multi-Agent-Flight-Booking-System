export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null

  let url: URL
  try {
    url = new URL(value)
  } catch {
    return null
  }

  if (url.protocol === 'https:') return url.href
  if (
    url.protocol === 'http:' &&
    (url.hostname.toLowerCase() === 'localhost' || url.hostname === '127.0.0.1')
  ) {
    return url.href
  }
  return null
}
