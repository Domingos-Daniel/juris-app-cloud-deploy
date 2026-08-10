import { Scale, SendHorizontal, BookOpen, MessageCircle, Gavel, Briefcase, Landmark, Sparkles, ShieldAlert } from 'lucide-react'

const SUGGESTIONS = [
  { icon: Scale, text: 'Quais são os direitos do trabalhador em caso de despedimento?', tag: 'Laboral' },
  { icon: BookOpen, text: 'O que diz o Código Penal sobre o crime de burla?', tag: 'Penal' },
  { icon: MessageCircle, text: 'Como funciona a prisão preventiva em Angola?', tag: 'Processual' },
  { icon: Gavel, text: 'Qual o prazo para contestar um acto administrativo?', tag: 'Administrativo' },
  { icon: Briefcase, text: 'Quais os direitos dos sócios minoritários?', tag: 'Comercial' },
  { icon: Landmark, text: 'O que diz o artigo 26 da Constituição angolana?', tag: 'Constitucional' },
]

export function EmptyState({ title, description, suggestions = SUGGESTIONS, onSuggestionClick }) {
  const visibleSuggestions = Array.isArray(suggestions) && suggestions.length > 0 ? suggestions : SUGGESTIONS

  return (
    <div className="fade-rise flex min-h-full w-full flex-col items-center justify-start px-3 py-4 sm:px-4 sm:justify-center sm:py-8">
      <div className="relative mb-4 sm:mb-6">
        <div className="absolute -inset-3 rounded-3xl bg-[color:var(--accent-glow)] blur-xl" />
        <div className="relative grid h-16 w-16 place-items-center rounded-2xl border border-[color:var(--accent)]/20 bg-[color:var(--accent-soft)] text-[color:var(--accent)] shadow-[var(--shadow-2)] sm:h-20 sm:w-20">
          <Scale size={28} strokeWidth={1.5} className="sm:hidden" />
          <Scale size={32} strokeWidth={1.5} className="hidden sm:block" />
        </div>
      </div>

      <h3 className="font-[family-name:var(--font-serif)] text-xl font-bold text-[color:var(--ink)] sm:text-3xl">
        {title}
      </h3>
      <p className="mt-1.5 max-w-md text-center text-[14px] leading-6 text-[color:var(--ink-soft)] sm:mt-2.5 sm:text-sm">
        {description}
      </p>

      <div className="mt-6 grid w-full max-w-3xl gap-2.5 sm:mt-8 sm:grid-cols-2 sm:gap-3 lg:grid-cols-3">
        {visibleSuggestions.map((suggestion, i) => {
          const Icon = suggestion.icon
          return (
            <button
              key={suggestion.text}
              onClick={() => onSuggestionClick?.(suggestion.text)}
              className={`group flex min-h-[96px] flex-col gap-2.5 rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-3.5 text-left transition-all hover:border-[color:var(--accent)]/40 hover:shadow-[var(--shadow-1)] active:scale-[0.98] sm:min-h-[112px] sm:p-4 sm:gap-2.5 ${i > 3 ? 'hidden sm:flex' : ''}`}
            >
              <div className="flex items-center gap-2">
                <div className="grid h-6 w-6 shrink-0 place-items-center rounded-[var(--radius-sm)] bg-[color:var(--accent-soft)] text-[color:var(--accent)] transition-colors group-hover:bg-[color:var(--accent)] group-hover:text-white sm:h-7 sm:w-7">
                  <Icon size={12} strokeWidth={1.5} className="sm:hidden" />
                  <Icon size={14} className="hidden sm:block" />
                </div>
                <span className="rounded-full bg-[color:var(--panel-muted)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[color:var(--ink-soft)]">
                  {suggestion.tag}
                </span>
              </div>
              <span className="text-[14px] leading-6 text-[color:var(--ink)] transition-colors group-hover:text-[color:var(--ink)] sm:text-[13px] sm:leading-relaxed">
                {suggestion.text}
              </span>
              {suggestion.description ? (
                <span className="text-[12px] leading-5 text-[color:var(--ink-soft)]">
                  {suggestion.description}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>

      <div className="mt-4 flex items-center gap-2 rounded-full border border-[color:var(--stroke)] bg-[color:var(--panel)] px-4 py-2 shadow-[var(--shadow-xs)] sm:mt-8">
        <Sparkles size={13} className="text-[color:var(--accent)]" />
        <span className="text-[13px] text-[color:var(--ink-soft)] sm:text-xs">Escreva abaixo ou toque numa sugestão</span>
        <SendHorizontal size={12} className="text-[color:var(--ink-soft)]" />
      </div>
    </div>
  )
}
