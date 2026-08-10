import { classNames } from '../utils/format'

export function ActionButton({ children, variant = 'primary', onClick, disabled = false, className = '' }) {
  const base = 'inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-md)] px-3 py-2 text-sm font-medium transition-all active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none'
  const variants = {
    primary: 'bg-[color:var(--accent)] text-white shadow-[var(--shadow-xs)] hover:bg-[color:var(--accent-hover)]',
    outline: 'border border-[color:var(--stroke)] bg-[color:var(--panel)] text-[color:var(--ink)] hover:bg-[color:var(--panel-muted)]',
    subtle: 'text-[color:var(--ink-soft)] hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)]',
    danger: 'text-[color:var(--danger)] hover:bg-[color:var(--danger-soft)]',
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={classNames(base, variants[variant] || variants.primary, className)}
    >
      {children}
    </button>
  )
}
