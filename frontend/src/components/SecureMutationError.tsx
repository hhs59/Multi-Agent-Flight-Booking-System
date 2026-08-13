import { useAuth } from '../auth/AuthProvider'
import { isCsrfError } from '../api/errors'
import { ApiNotice, ErrorState } from './ui'

export function SecureMutationError({
  error,
  onRetry,
  retryLabel = 'Try again',
}: {
  error: unknown
  onRetry?: () => void
  retryLabel?: string
}) {
  const { restoreSecureSession, isRestoringSession } = useAuth()
  if (!error) return null

  const csrfError = isCsrfError(error)
  return (
    <div className="secure-mutation-error">
      {csrfError ? (
        <ApiNotice
          error={error}
          onRestore={() => void restoreSecureSession()}
          restoring={isRestoringSession}
        />
      ) : null}
      <ErrorState error={error} compact onRetry={onRetry} retryLabel={retryLabel} />
    </div>
  )
}
