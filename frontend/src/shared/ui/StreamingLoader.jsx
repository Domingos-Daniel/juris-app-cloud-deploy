import { FileText, Search, ShieldCheck, PenLine, Sparkles } from 'lucide-react'
import { cleanAnswerBody } from '../utils/markdown'
import LegalMarkdown from './LegalMarkdown'

const PHASES = [
  { key: 'uploading', label: 'A preparar o documento', helper: 'A extrair o texto do anexo antes da análise jurídica.', Icon: FileText },
  { key: 'classifying', label: 'A analisar o pedido', helper: 'A identificar o ramo jurídico, o objectivo e o nível de detalhe adequado.', Icon: Search },
  { key: 'retrieving', label: 'A localizar fundamentos', helper: 'A cruzar legislação, artigos e contexto relevante.', Icon: ShieldCheck },
  { key: 'composing', label: 'A estruturar a resposta', helper: 'A organizar a orientação em linguagem clara e fundamentada.', Icon: PenLine },
]

const MOBILE_SKELETON_WIDTHS = ['92%', '84%', '88%']
const DESKTOP_SKELETON_WIDTHS = ['88%', '72%', '81%']

function cleanStreamText(text) {
  if (!text) return ''
  return cleanAnswerBody(text)
    .replace(/\{\s*"token"\s*:\s*"/g, '')
    .replace(/\{\s*"sources?"\s*:\s*/g, '')
    .replace(/\{\s*"confidence"\s*:\s*/g, '')
    .replace(/\{\s*"legal_basis"\s*:\s*/g, '')
    .replace(/\{\s*"answer_mode"\s*:\s*/g, '')
    .replace(/^\s*[\{\[]\s*/, '')
    .replace(/\s*["\}\]]\s*$/g, '')
    .replace(/\\n/g, '\n')
    .replace(/\\"/g, '"')
    .replace(/\\\\/g, '\\')
    .trim()
}

function formatElapsed(ms = 0) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000))
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

function PhasePill({ active, done, label, Icon }) {
  return (
    <div
      className={`flex min-w-0 items-center gap-2 rounded-full border px-3 py-1.5 text-[11px] transition-all sm:text-[10px] ${
        done
          ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-200'
          : active
            ? 'border-[color:var(--accent)]/30 bg-[color:var(--accent)]/12 text-white'
            : 'border-white/[0.06] bg-white/[0.03] text-white/38'
      }`}
    >
      <Icon size={12} />
      <span className="truncate font-medium">{label}</span>
    </div>
  )
}

function SkeletonLines() {
  return (
    <div className="space-y-2.5">
      {MOBILE_SKELETON_WIDTHS.map((mobileWidth, index) => (
        <div
          key={mobileWidth}
          className="h-3.5 rounded-full bg-gradient-to-r from-white/[0.08] via-white/[0.16] to-white/[0.08] bg-[length:220%_100%] animate-[pulse_1.6s_ease-in-out_infinite]"
          style={{
            width: mobileWidth,
            maxWidth: DESKTOP_SKELETON_WIDTHS[index],
          }}
        />
      ))}
    </div>
  )
}

export function StreamingLoader({ content = '', phase = 'idle', elapsedMs = 0 }) {
  const cleanContent = cleanStreamText(content)
  const hasContent = cleanContent.length > 0
  const currentPhase = hasContent ? 'composing' : phase === 'idle' ? 'classifying' : phase
  const activePhaseIndex = PHASES.findIndex((item) => item.key === currentPhase)
  const activePhase = PHASES[Math.max(0, activePhaseIndex)] || PHASES[0]

  return (
    <div className="fade-rise w-full rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--chat-assistant)] px-3.5 py-3.5 shadow-[var(--shadow-1)] sm:rounded-bl-md sm:px-5 sm:py-5">
      <div className="flex items-start gap-2.5 sm:gap-3">
        <div className="mt-0.5 hidden h-9 w-9 shrink-0 place-items-center rounded-2xl border border-[color:var(--accent)]/20 bg-[color:var(--accent)]/10 text-[color:var(--accent)] shadow-[var(--shadow-xs)] sm:grid">
          <Sparkles size={16} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full bg-[color:var(--accent)] animate-pulse sm:hidden" />
            <p className="min-w-0 flex-1 truncate text-[13px] font-semibold text-white/82 sm:text-[14px] sm:text-white/90">
              {hasContent ? 'Resposta em curso' : activePhase.label}
            </p>
            <span className="shrink-0 rounded-full border border-white/[0.08] bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium tabular-nums text-white/45 sm:py-1">
              {formatElapsed(elapsedMs)}
            </span>
          </div>
          <p className="mt-1 text-[12px] leading-5 text-white/42 sm:text-[12px] sm:text-white/48">
            {hasContent ? 'Estou a escrever à medida que valido a resposta final.' : activePhase.helper}
          </p>

          <div className="mt-3 hidden flex-wrap gap-2 sm:flex">
            {PHASES.map((item, index) => (
              <PhasePill
                key={item.key}
                active={index === activePhaseIndex}
                done={index < activePhaseIndex}
                label={item.label}
                Icon={item.Icon}
              />
            ))}
          </div>

          <div className="mt-3 rounded-2xl border border-white/[0.06] bg-white/[0.025] p-3 sm:mt-4 sm:p-4">
            {hasContent ? (
              <div className="space-y-3">
                <div className="relative pr-2">
                  <LegalMarkdown text={cleanContent} />
                  <span className="ml-1 inline-block h-[18px] w-1.5 animate-pulse rounded-sm bg-[color:var(--accent)] align-middle" />
                </div>
                <div className="flex items-center gap-2 text-[11px] text-white/30">
                  <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--accent)] animate-pulse" />
                  A transmitir em tempo real
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <SkeletonLines />
                <div className="flex items-center gap-2 text-[11px] text-white/28">
                  <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--accent)] animate-pulse" />
                  A processar o pedido
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
