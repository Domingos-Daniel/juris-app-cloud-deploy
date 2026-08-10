import {
  ArrowUpRight,
  Calculator,
  ClipboardCheck,
  FileText,
  Landmark,
  Scale,
  Search,
  Send,
} from 'lucide-react'

const ACTION_ICONS = {
  legal: Scale,
  draft: FileText,
  research: Search,
  calculation: Calculator,
  checklist: ClipboardCheck,
  notice: Send,
  document: Landmark,
}

function inferActionType(action) {
  const raw = String(action?.icon || '').trim()
  if (ACTION_ICONS[raw]) return raw

  const label = String(action?.label || '').toLowerCase()
  if (/calcular|custas|imposto|pensão|partilha/.test(label)) return 'calculation'
  if (/jurisprud|aprofundar|relacionad/.test(label)) return 'research'
  if (/redigir|gerar|petição|queixa|comunicação|requerimento|recurso/.test(label)) return 'draft'
  if (/checklist|lista|conformidade|registo/.test(label)) return 'checklist'
  if (/notifica/.test(label)) return 'notice'
  if (/documento/.test(label)) return 'document'
  return 'legal'
}

export default function SuggestedActions({ actions = [], onSelect }) {
  const visibleActions = actions.filter((action) => action?.label && action?.prompt).slice(0, 4)
  if (visibleActions.length === 0) return null

  return (
    <section className="mt-4 hidden sm:block sm:rounded-2xl sm:border sm:border-white/[0.07] sm:bg-white/[0.025] sm:p-3 sm:shadow-[0_10px_30px_rgba(0,0,0,0.10)]">
      <div className="mb-2 flex items-center justify-between gap-3 px-0.5 sm:px-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/35">Próximos passos</p>
        <span className="hidden text-[11px] text-white/30 sm:inline">Continue no mesmo contexto</span>
      </div>

      <div className="-mx-1 flex snap-x gap-2 overflow-x-auto px-1 pb-1 custom-scroll sm:mx-0 sm:grid sm:grid-cols-2 sm:overflow-visible sm:px-0 sm:pb-0">
        {visibleActions.map((action, index) => {
          const type = inferActionType(action)
          const Icon = ACTION_ICONS[type] || Scale

          return (
            <button
              key={`${action.label}-${index}`}
              type="button"
              onClick={() => onSelect?.(action.prompt)}
              className="group/action flex min-h-[50px] w-[min(78vw,260px)] shrink-0 snap-start items-center gap-2.5 rounded-xl border border-white/[0.07] bg-[color:var(--panel)]/85 px-3 py-2.5 text-left transition-all duration-200 active:scale-[0.98] sm:min-h-[50px] sm:w-auto sm:shrink sm:gap-3 sm:bg-[color:var(--panel)]/65 sm:px-3 sm:py-2.5 sm:hover:-translate-y-0.5 sm:hover:border-[color:var(--accent)]/35 sm:hover:bg-[color:var(--accent)]/[0.07] sm:hover:shadow-[0_12px_28px_rgba(37,99,235,0.12)] sm:active:translate-y-0"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[color:var(--accent)]/15 bg-[color:var(--accent)]/[0.08] text-[color:var(--accent)] transition-colors group-hover/action:border-[color:var(--accent)]/30 group-hover/action:bg-[color:var(--accent)]/[0.14] sm:h-8 sm:w-8">
                <Icon size={14} strokeWidth={1.9} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block whitespace-normal text-[12px] font-semibold leading-snug text-white/80 transition-colors line-clamp-2 group-hover/action:text-white/92 sm:truncate sm:text-[12px]">
                  {action.label}
                </span>
                <span className="mt-0.5 hidden truncate text-[10px] text-white/32 transition-colors group-hover/action:text-white/45 sm:block">
                  Executar no mesmo contexto
                </span>
              </span>
              <ArrowUpRight size={13} className="shrink-0 text-white/22 transition-all group-hover/action:translate-x-0.5 group-hover/action:-translate-y-0.5 group-hover/action:text-[color:var(--accent)]" />
            </button>
          )
        })}
      </div>
    </section>
  )
}
