const PHASES = [
  'A recuperar legislação relevante',
  'A validar artigos e coerência jurídica',
  'A redigir resposta fundamentada',
]

function resolvePhase(elapsedMs = 0) {
  if (elapsedMs < 1800) return 0
  if (elapsedMs < 3800) return 1
  return 2
}

export function LoadingState({ label, elapsedMs = 0 }) {
  const active = resolvePhase(elapsedMs)

  return (
    <div className="fade-rise w-full max-w-2xl rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-4 shadow-[var(--shadow-1)]">
      <div className="mb-3 flex items-center gap-2.5">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full rounded-full bg-[color:var(--accent)] opacity-75 animate-ping" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[color:var(--accent)]" />
        </span>
        <p className="text-sm font-medium text-[color:var(--ink)]">
          {label || 'Processamento jurídico em curso'}
        </p>
      </div>

      <div className="space-y-1.5">
        {PHASES.map((phase, index) => (
          <div key={phase} className="flex items-center gap-2.5 text-sm">
            <span
              className={`h-1.5 w-1.5 rounded-full transition-colors duration-300 ${
                index < active ? 'bg-[color:var(--success)]' : index === active ? 'bg-[color:var(--accent)] pulse-glow' : 'bg-[color:var(--stroke)]'
              }`}
            />
            <span className={`transition-colors duration-300 ${
              index <= active ? 'text-[color:var(--ink)]' : 'text-[color:var(--ink-soft)]'
            }`}>
              {phase}
              {index < active ? ' ✓' : ''}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-[color:var(--panel-muted)]">
        <div
          className="h-full rounded-full bg-[color:var(--accent)] transition-[width] duration-700 ease-out"
          style={{ width: `${Math.min(100, (active + 1) * 33)}%` }}
        />
      </div>
    </div>
  )
}
