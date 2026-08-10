import { ExternalLink } from 'lucide-react'
import { formatArticleLabel, normalizeDisplayText } from '../utils/markdown'

function normalizeExcerpt(text) {
  if (!text) return ''
  let normalized = text
    .replace(/\u00ad/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\s*\n\s*/g, '\n')
    .replace(/([a-zà-ú])\n([a-zà-ú])/gi, '$1 $2')
    .trim()
  normalized = normalized
    .replace(/\s{2,}/g, ' ')
    .replace(/([.!?])\s+/g, '$1\n\n')
  return normalized
}

function statusLabel(status) {
  const normalized = (status || '').toLowerCase()
  if (normalized.includes('em vigor')) return 'Em vigor'
  if (normalized.includes('vigente')) return 'Vigente'
  if (normalized.includes('nao verificado') || normalized.includes('não verificado')) return 'Não verificado'
  if (normalized.includes('documento do utilizador')) return 'Doc. utilizador'
  return status || 'N/D'
}

function statusColor(status) {
  const normalized = (status || '').toLowerCase()
  if (normalized.includes('em vigor') || normalized.includes('vigente')) return 'text-emerald-600 dark:text-emerald-400'
  if (normalized.includes('utilizador')) return 'text-blue-600 dark:text-blue-400'
  return 'text-[color:var(--ink-soft)]'
}

export function ArticleViewer({ source, highlight = false }) {
  if (!source) {
    return (
      <div className="rounded-[var(--radius-lg)] border border-dashed border-[color:var(--stroke)] bg-[color:var(--panel)] p-6 text-center">
        <p className="text-sm text-[color:var(--ink-soft)]">Seleciona uma referencia para visualizar o artigo e a base legal.</p>
      </div>
    )
  }

  return (
    <div className={`rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel)] shadow-[var(--shadow-1)] ${highlight ? 'article-highlight' : ''}`}>
      <header className="flex items-center justify-between border-b border-[color:var(--stroke)] px-4 py-3">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-[color:var(--ink-soft)]">Artigo</h3>
        {source.deep_link ? (
          <a href={source.deep_link} target="_blank" rel="noreferrer" className="rounded p-1 text-[color:var(--ink-soft)] transition-colors hover:text-[color:var(--accent)]">
            <ExternalLink size={14} />
          </a>
        ) : null}
      </header>

      <div className="space-y-3 p-4">
        <div>
          <h4 className="font-[family-name:var(--font-serif)] text-lg font-semibold text-[color:var(--ink)]">
            {source.article_number ? formatArticleLabel(source.article_number) : 'Base Legal'}
          </h4>
          <p className="mt-0.5 text-sm text-[color:var(--ink-soft)]">{normalizeDisplayText(source.title) || 'Fonte legal'}</p>
        </div>

        <div className="rounded-[var(--radius-md)] bg-[color:var(--panel-muted)] p-3.5">
          <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-[color:var(--ink)]">
            {normalizeExcerpt(source.excerpt) || 'Sem trecho disponivel.'}
          </p>
        </div>

        <div className="grid grid-cols-3 gap-2 text-[11px]">
          <div className="rounded-[var(--radius-sm)] bg-[color:var(--panel-muted)] px-2.5 py-2">
            <span className="block uppercase tracking-wider text-[color:var(--ink-soft)]">Pag.</span>
            <span className="mt-0.5 block font-semibold text-[color:var(--ink)]">{source.page || 'N/D'}</span>
          </div>
          <div className="rounded-[var(--radius-sm)] bg-[color:var(--panel-muted)] px-2.5 py-2">
            <span className="block uppercase tracking-wider text-[color:var(--ink-soft)]">Estado</span>
            <span className={`mt-0.5 block font-semibold ${statusColor(source.law_status)}`}>{statusLabel(source.law_status)}</span>
          </div>
          <div className="rounded-[var(--radius-sm)] bg-[color:var(--panel-muted)] px-2.5 py-2">
            <span className="block uppercase tracking-wider text-[color:var(--ink-soft)]">Fonte</span>
            <span className="mt-0.5 block truncate font-semibold text-[color:var(--ink)]" title={source.source || source.title || ''}>
              {source.source_scope === 'user_upload' ? 'Utilizador' : 'Oficial'}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
