export function ErrorBanner({ message, onRetry }) {
  return (
    <div className="fade-rise rounded-[var(--radius-md)] border border-red-200 bg-[color:var(--danger-soft)] px-4 py-3 dark:border-red-800/50">
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 text-sm text-red-700 dark:text-red-300">{message}</p>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="shrink-0 rounded-[var(--radius-sm)] px-2.5 py-1 text-xs font-medium text-red-700 transition-colors hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-900/30"
          >
            Tentar novamente
          </button>
        ) : null}
      </div>
    </div>
  )
}
