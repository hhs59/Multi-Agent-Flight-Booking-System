import { AlertCircle, Check, ChevronDown, Info, LoaderCircle, X } from 'lucide-react'
import clsx from 'clsx'
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { isApiError, isCsrfError } from '../api/errors'

export const cn = (...values: Array<string | false | null | undefined>): string => clsx(values)

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  asChild?: boolean
  to?: string
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading = false,
  children,
  className,
  disabled,
  to,
  ...props
}: ButtonProps) {
  const classes = cn('button', 'button-' + variant, 'button-' + size, className)
  if (to) {
    return (
      <Link className={classes} to={to}>
        {children}
      </Link>
    )
  }
  return (
    <button className={classes} disabled={disabled || loading} {...props}>
      {loading ? <LoaderCircle className="spin" size={16} aria-label="Loading" /> : children}
    </button>
  )
}

export function Card({
  children,
  className,
  as = 'section',
}: {
  children: ReactNode
  className?: string
  as?: 'section' | 'div' | 'article'
}) {
  const Element = as
  return <Element className={cn('card', className)}>{children}</Element>
}

export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: string
  hint?: string
  error?: string
  required?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <label className={cn('field', className)}>
      <span className="field-label">
        {label} {required ? <span className="required">*</span> : null}
      </span>
      {children}
      {error ? (
        <span className="field-error">{error}</span>
      ) : hint ? (
        <span className="field-hint">{hint}</span>
      ) : null}
    </label>
  )
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('input', props.className)} {...props} />
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="select-wrap">
      <select className={cn('input', props.className)} {...props} />
      <ChevronDown size={16} aria-hidden="true" />
    </span>
  )
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === 'results' ||
    status === 'completed' ||
    status === 'confirmed' ||
    status === 'active' ||
    status === 'unchanged'
      ? 'success'
      : status === 'provider_unavailable' ||
          status === 'disabled' ||
          status === 'expired' ||
          status === 'failed' ||
          status === 'cancelled'
        ? 'danger'
        : status === 'changed' || status === 'awaiting_confirmation' || status === 'quote_ready'
          ? 'warning'
          : 'neutral'
  return <span className={'status-badge status-' + tone}>{status.replaceAll('_', ' ')}</span>
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state">
      {icon ? <div className="empty-icon">{icon}</div> : null}
      <h3>{title}</h3>
      <p>{description}</p>
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  retryLabel = 'Try again',
  compact = false,
}: {
  error: unknown
  onRetry?: () => void
  retryLabel?: string
  compact?: boolean
}) {
  const message = isApiError(error)
    ? error.message
    : 'Something went wrong while loading this view.'
  const traceId = isApiError(error) ? error.traceId : undefined
  return (
    <div className={cn('error-state', compact && 'error-compact')} role="alert">
      <AlertCircle size={20} />
      <div>
        <strong>We could not load this right now.</strong>
        <p>{message}</p>
        {traceId ? <small>Reference {traceId}</small> : null}
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          {retryLabel}
        </Button>
      ) : null}
    </div>
  )
}

export function InfoBanner({
  children,
  tone = 'info',
}: {
  children: ReactNode
  tone?: 'info' | 'success' | 'warning' | 'danger'
}) {
  const Icon =
    tone === 'success' ? Check : tone === 'warning' ? Info : tone === 'danger' ? AlertCircle : Info
  return (
    <div className={'info-banner banner-' + tone}>
      <Icon size={18} />
      <div>{children}</div>
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton', className)} aria-hidden="true" />
}

export function LoadingScreen({ label = 'Loading...' }: { label?: string }) {
  return (
    <div className="loading-screen">
      <LoaderCircle className="spin" size={28} />
      <span>{label}</span>
    </div>
  )
}

export function Modal({
  open,
  title,
  children,
  onClose,
  footer,
  disableEscape = false,
  disableClose = false,
}: {
  open: boolean
  title: string
  children: ReactNode
  onClose: () => void
  footer?: ReactNode
  disableEscape?: boolean
  disableClose?: boolean
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)
  const titleId = useId()
  const onCloseRef = useRef(onClose)
  const disableEscapeRef = useRef(disableEscape)
  const disableCloseRef = useRef(disableClose)

  useEffect(() => {
    onCloseRef.current = onClose
    disableEscapeRef.current = disableEscape
    disableCloseRef.current = disableClose
  }, [disableClose, disableEscape, onClose])

  useEffect(() => {
    if (!open) return
    previousFocus.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const focusableSelector = [
      'button:not([disabled])',
      '[href]',
      'input:not([disabled])',
      'select:not([disabled])',
      'textarea:not([disabled])',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',')
    const focusFirst = (): void => {
      const preferred = dialog?.querySelector<HTMLElement>('[data-autofocus]:not([disabled])')
      const first = preferred || dialog?.querySelector<HTMLElement>(focusableSelector)
      ;(first || dialog)?.focus({ preventScroll: true })
    }
    const focusableElements = (): HTMLElement[] =>
      dialog ? Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)) : []
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !disableEscapeRef.current && !disableCloseRef.current) {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const elements = focusableElements()
      if (!elements.length) {
        event.preventDefault()
        dialog?.focus()
        return
      }
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const onFocusIn = (event: FocusEvent): void => {
      if (dialog && event.target instanceof Node && !dialog.contains(event.target)) {
        event.preventDefault()
        focusFirst()
      }
    }
    const frame = window.requestAnimationFrame(focusFirst)
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('focusin', onFocusIn)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('focusin', onFocusIn)
      previousFocus.current?.focus()
      previousFocus.current = null
    }
  }, [open])

  if (!open) return null
  return createPortal(
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!disableCloseRef.current) onCloseRef.current()
      }}
    >
      <div
        ref={dialogRef}
        className="modal"
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <button
            className="icon-button"
            type="button"
            onClick={() => onCloseRef.current()}
            disabled={disableClose}
            aria-label="Close dialog"
          >
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  )
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Xác nhận',
  cancelLabel = 'Hủy bỏ',
  danger = false,
  onCancel,
  onConfirm,
  loading = false,
}: {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onCancel: () => void
  onConfirm: () => void
  loading?: boolean
}) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      disableEscape={loading}
      disableClose={loading}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            loading={loading}
            onClick={onConfirm}
            data-autofocus
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="confirm-dialog-content">
        {danger ? (
          <div className="confirm-icon-danger">
            <AlertCircle size={22} />
          </div>
        ) : null}
        <p className="modal-message">{message}</p>
      </div>
    </Modal>
  )
}

export function PromptDialog({
  open,
  title,
  subtitle,
  defaultValue = '',
  placeholder = '',
  confirmLabel = 'Lưu thay đổi',
  cancelLabel = 'Hủy bỏ',
  onCancel,
  onConfirm,
  loading = false,
}: {
  open: boolean
  title: string
  subtitle?: string
  defaultValue?: string
  placeholder?: string
  confirmLabel?: string
  cancelLabel?: string
  onCancel: () => void
  onConfirm: (value: string) => void
  loading?: boolean
}) {
  const [val, setVal] = useState(defaultValue)

  useEffect(() => {
    if (open) setVal(defaultValue)
  }, [open, defaultValue])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (val.trim()) onConfirm(val.trim())
  }

  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      disableEscape={loading}
      disableClose={loading}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant="primary"
            loading={loading}
            disabled={!val.trim()}
            onClick={handleSubmit}
            data-autofocus
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="prompt-dialog-form">
        {subtitle ? <p className="prompt-dialog-subtitle">{subtitle}</p> : null}
        <Input
          value={val}
          onChange={(e) => setVal(e.target.value)}
          placeholder={placeholder}
          autoFocus
          className="prompt-dialog-input"
        />
      </form>
    </Modal>
  )
}

export function LinkButton({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link className="text-link" to={to}>
      {children}
    </Link>
  )
}

export function ApiNotice({
  error,
  onRestore,
  restoring = false,
}: {
  error: unknown
  onRestore?: () => void
  restoring?: boolean
}) {
  if (!isApiError(error)) return null
  if (isCsrfError(error)) {
    return (
      <InfoBanner tone="warning">
        <span>Your secure session needs refreshing. Retry this action once.</span>
        {onRestore ? (
          <Button variant="secondary" size="sm" loading={restoring} onClick={onRestore}>
            Restore secure session
          </Button>
        ) : null}
      </InfoBanner>
    )
  }
  if (error.status === 409) {
    return (
      <InfoBanner tone="warning">
        This item changed on the server. Refresh the view before trying again.
      </InfoBanner>
    )
  }
  return null
}
