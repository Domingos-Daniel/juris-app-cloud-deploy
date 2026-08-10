import { HelpCircle } from 'lucide-react'

export function ClarifyingQuestions({ questions, onSelect }) {
  if (!questions || questions.length === 0) return null

  return (
    <div className="mt-2 rounded-[var(--radius-lg)] border border-amber-500/20 bg-amber-500/5 px-4 py-3">
      <div className="flex items-start gap-2">
        <HelpCircle size={16} className="mt-0.5 shrink-0 text-amber-500" />
        <div className="min-w-0">
          <p className="mb-2 text-sm font-medium text-amber-700 dark:text-amber-300">
            Para eu responder com precisao juridica e utilidade pratica, indique um destes pontos:
          </p>
          <div className="flex flex-col gap-1.5">
            {questions.map((q, i) => (
              <button
                key={i}
                type="button"
                onClick={() => onSelect?.(q)}
                className="w-full rounded-[var(--radius-sm)] border border-amber-400/30 bg-amber-50 px-3 py-2 text-left text-[13px] text-amber-900 transition-all hover:border-amber-400/60 hover:bg-amber-100 dark:bg-amber-950/30 dark:text-amber-200 dark:hover:bg-amber-900/40"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
