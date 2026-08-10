export function StatusBadge({ healthy }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${healthy ? 'bg-[color:var(--success)]' : 'bg-[color:var(--danger)]'}`} />
      <span className="text-[11px] font-medium text-[color:var(--ink-soft)]">
        {healthy ? 'Online' : 'Offline'}
      </span>
    </div>
  )
}
