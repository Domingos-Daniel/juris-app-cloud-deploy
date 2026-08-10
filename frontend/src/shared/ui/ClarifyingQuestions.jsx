import { ArrowUpRight, ListChecks } from 'lucide-react'

export function ClarifyingQuestions({ questions, onSelect }) {
  if (!questions || questions.length === 0) return null

  return (
    <div className="mt-3 overflow-hidden rounded-2xl border border-white/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.055),rgba(255,255,255,0.025))] shadow-[0_18px_55px_rgba(0,0,0,0.18)]">
      <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-[color:var(--accent)]/25 bg-[color:var(--accent)]/10 text-[color:var(--accent)]">
            <ListChecks size={15} />
          </span>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-semibold tracking-[-0.01em] text-white/88">
              Responder com um detalhe
            </p>
            <p className="text-[11px] leading-4 text-white/42">
              Ao escolher uma opção, eu continuo a análise automaticamente.
            </p>
          </div>
        </div>
        <span className="hidden rounded-full border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] font-medium uppercase tracking-[0.12em] text-white/36 sm:inline-flex">
          Clarificar
        </span>
      </div>

      <div className="grid gap-1.5 p-2">
        {questions.slice(0, 3).map((question, index) => (
          <button
            key={`${question}-${index}`}
            type="button"
            onClick={() => onSelect?.(question)}
            className="group flex w-full items-start gap-3 rounded-xl border border-transparent px-3 py-3 text-left transition-all duration-150 hover:border-white/[0.08] hover:bg-white/[0.045] focus-visible:border-[color:var(--accent)]/45 focus-visible:bg-white/[0.05] focus-visible:outline-none"
          >
            <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-white/[0.06] text-[11px] font-semibold tabular-nums text-white/48 transition-colors group-hover:bg-[color:var(--accent)]/14 group-hover:text-[color:var(--accent)]">
              {index + 1}
            </span>
            <span className="min-w-0 flex-1 text-[14px] leading-6 text-white/76 transition-colors group-hover:text-white/92 sm:text-[13px] sm:leading-5">
              {question}
            </span>
            <ArrowUpRight size={14} className="mt-1 shrink-0 text-white/20 transition-all group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[color:var(--accent)]" />
          </button>
        ))}
      </div>
    </div>
  )
}
