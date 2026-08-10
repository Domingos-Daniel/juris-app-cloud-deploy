export function SurfaceCard({ className = '', children }) {
  return (
    <div className={`rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel)] shadow-[var(--shadow-1)] ${className}`}>
      {children}
    </div>
  )
}
