import { cloneElement, isValidElement } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Gavel, Link2 } from 'lucide-react'
import { formatArticleLabel, normalizeDisplayText } from '../utils/markdown'

const CITE_MARKER = '\uE000'

function normalizeBrackets(text) {
  if (!text) return ''
  return text
    .replace(/[\u241b-\u241f]/g, '')
    // Double brackets — now also match juris citations
    .replace(/\[\[(?!Art)(.{3,200}?)\]\]/g, (_, inner) => `${CITE_MARKER}${inner}${CITE_MARKER}`)
    .replace(/\[\[(Art(?:igo)?\S*\s*\d+\S*\s*[,\s][^\]]{3,200}?)\]\]/g, (_, inner) => `${CITE_MARKER}${inner}${CITE_MARKER}`)
    .replace(/\[\[([^\]\n]{3,200}?)\]/g, (_, inner) => `${CITE_MARKER}${inner}${CITE_MARKER}`)
    // Jurisprudence bracketless citations
    .replace(/(?:^|(?<=[\s(]))(Processo\s+n\.?[º°]?\s*\d+\/\d{2,4})/gi, (match) => `${CITE_MARKER}${match.trim()}${CITE_MARKER}`)
    .replace(/(?:^|(?<=[\s(]))(Ac(?:órdão|orda[oã])?\s*(?:do\s+)?Tribunal\s+\S+(?:[\s,][^.]{10,100}?)?)/gi, (match) => `${CITE_MARKER}${match.trim()}${CITE_MARKER}`)
    // Single brackets — Art citations
    .replace(/\[(Art(?:\S+)?\s*\d+\S*\s*[,\s][^\]]{3,180}?)\]/g, (_, inner) => `${CITE_MARKER}${inner}${CITE_MARKER}`)
    // Bare Art. N references
    .replace(/(?:^|(?<=[\s(]))(Art(?:igo)?\.?\s*(?:n\.?[º°]?\s*)?\d+(?:\s*\.?\s*[º°ª])?(?:\s*(?:[,;]\s*[^.]{4,80}?)?(?:\s*[,;]\s*p\.?\s*\d+)?)?)/gi, (match) => `${CITE_MARKER}${match.trim()}${CITE_MARKER}`)
    .replace(/\(\(/g, '(')
    .replace(/\)\)/g, ')')
    .replace(/^\s*[.•·]+\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function parseCitation(text) {
  const cleaned = normalizeDisplayText(text).trim()
  // Jurisprudence citations
  const jurisMatch = cleaned.match(/^(Ac(?:órdão|orda[oã])|Processo|Proc\.?)\s*(.*)$/i)
  if (jurisMatch) {
    return { juris: true, text: cleaned }
  }
  // Legislation: Art. X, Diploma, p. Y
  const artStripped = cleaned.replace(/^Art(?:igo|s|\.)?\.?\s*(?:n\.?[º°]?\s*)?/i, '')
  if (!artStripped) return null
  const am = artStripped.match(/^(\d+(?:\.?[º°ª]\d*)?)/)
  if (!am) return null
  const article = am[1]
  const rest = artStripped.slice(am[0].length).replace(/^[\s,;.]+/, '')
  const pm = rest.match(/[,;]?\s*p(?:\s*[aá]g)?\.?\s*(\d+)\s*$/)
  let page = null
  let diploma = rest
  if (pm) {
    page = pm[1]
    diploma = rest.slice(0, pm.index).replace(/^[\s,;.]+/, '').replace(/[\s,;.]+$/, '')
  } else {
    diploma = rest.replace(/^[\s,;.]+/, '').replace(/[\s,;.]+$/, '') || null
  }
  if (!diploma) diploma = null
  return { article, diploma, page }
}

function CitationReference({ text, sourceRefs, onSelectRef }) {
  const citation = parseCitation(text)
  if (!citation) {
    return <span className="font-semibold text-[var(--color-accent)]">{text}</span>
  }
  if (citation.juris) {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-[color:var(--gold)]/10 px-1.5 py-0.5 text-xs font-medium text-[color:var(--gold)] cursor-default" title={citation.text}>
        <Gavel size={11} />
        {citation.text.length > 60 ? citation.text.slice(0, 58) + '...' : citation.text}
      </span>
    )
  }
  if (!citation?.article) {
    return <span className="font-semibold text-[var(--color-accent)]">{text}</span>
  }
  const cleanNum = citation.article.replace(/[^\d]/g, '')
  const sourceIndex = sourceRefs.findIndex(s => {
    if (!s?.article_number) return false
    return String(s.article_number).replace(/[^\d]/g, '').includes(cleanNum)
  })

  const label = formatArticleLabel(citation.article)

  return (
    <span>
      <span className="font-semibold text-[var(--color-accent)]">{label}</span>
      {sourceIndex >= 0 && (
        <button
          type="button"
          data-ref-index={sourceIndex + 1}
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onSelectRef?.(sourceRefs[sourceIndex]) }}
          className="inline-flex h-[15px] min-w-[15px] items-center justify-center rounded-sm bg-[var(--color-accent)]/15 px-1 text-[9px] font-bold text-[var(--color-accent)] transition-colors hover:bg-[var(--color-accent)]/25 align-super leading-none ml-1"
          aria-label={`Ver fonte ${sourceIndex + 1}`}
          title={citation.diploma ? `${normalizeDisplayText(citation.diploma)}${citation.page ? ' · pág. ' + citation.page : ''}` : text}
        >
          <Link2 size={9} strokeWidth={2.5} aria-hidden="true" />
        </button>
      )}
    </span>
  )
}

const markdownComponents = {
  h1: ({ children, ...props }) => <h2 className="mt-5 mb-2.5 text-[18px] font-semibold tracking-[-0.015em] text-white/92 first:mt-0 sm:text-[15px]" {...props}>{children}</h2>,
  h2: ({ children, ...props }) => <h3 className="mt-4 mb-2 text-[16px] font-semibold tracking-[-0.012em] text-white/88 first:mt-0 sm:text-sm" {...props}>{children}</h3>,
  h3: ({ children, ...props }) => <h4 className="mt-3 mb-1.5 text-[15px] font-semibold text-white/82 first:mt-0 sm:text-[13px]" {...props}>{children}</h4>,
  p: ({ children, ...props }) => <p className="mt-2 text-[16px] leading-[1.78] tracking-[0.005em] text-white/80 first:mt-0 sm:text-sm sm:leading-[1.68]" {...props}>{children}</p>,
  ul: ({ children, ...props }) => <ul className="mt-2 mb-2 list-disc space-y-1.5 pl-5" {...props}>{children}</ul>,
  ol: ({ children, ...props }) => <ol className="mt-2 mb-2 list-decimal space-y-1.5 pl-5" {...props}>{children}</ol>,
  li: ({ children, ...props }) => <li className="text-[16px] leading-[1.72] text-white/78 sm:text-sm sm:leading-relaxed" {...props}>{children}</li>,
  strong: ({ children, ...props }) => <strong className="font-semibold text-white/92" {...props}>{children}</strong>,
  em: ({ children, ...props }) => <em className="italic text-white/65" {...props}>{children}</em>,
  code: ({ children, ...props }) => <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[12px] font-normal text-[var(--color-accent)]/80 sm:text-[12px]" {...props}>{children}</code>,
  blockquote: ({ children, ...props }) => <blockquote className="my-3 border-l-2 border-white/[0.08] pl-3 text-[14px] italic text-white/55 sm:text-[13px]" {...props}>{children}</blockquote>,
  hr: (props) => <hr className="my-4 border-white/[0.05]" {...props} />,
  a: ({ href, children, ...props }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-[#7aa2f7] hover:text-[#9fc4ff] underline underline-offset-2 decoration-white/20" {...props}>
      {children}
    </a>
  ),
}

export default function LegalMarkdown({ text = '', sourceRefs = [], onSelectRef, className = '' }) {
  // Strip unit separator chars/symbols the LLM may accidentally output
  const cleaned = (text || '')
    .replace(/[\x1b-\x1f]/g, '')
    .replace(/[\u241b-\u241f]/g, '')
  const normalized = normalizeDisplayText(normalizeBrackets(normalizeDisplayText(cleaned)))

  // Override p/li to re-split on CITE_MARKER so citations stay inline.
  function inlineTextChildren(children) {
    if (Array.isArray(children)) {
      return children.flatMap((child) => {
        const value = inlineTextChildren(child)
        return Array.isArray(value) ? value : [value]
      })
    }
    if (isValidElement(children)) {
      return cloneElement(children, {
        ...children.props,
        children: inlineTextChildren(children.props.children),
      })
    }
    if (typeof children !== 'string') return children
    const parts = children.split(CITE_MARKER)
    if (parts.length < 2) return normalizeDisplayText(children)
    return parts.map((part, i) => {
      if (i % 2 === 1) {
        return <CitationReference key={i} text={part} sourceRefs={sourceRefs} onSelectRef={onSelectRef} />
      }
      part = normalizeDisplayText(part).replace(/^\s*[.•·]+\s*$/gm, '').replace(/\n{3,}/g, '\n\n')
      return part || null
    })
  }

  const inlineComponents = {
    ...markdownComponents,
    p: ({ children, ...props }) => (
      <p className="mt-2 text-[16px] leading-[1.78] tracking-[0.005em] text-white/80 first:mt-0 sm:text-sm sm:leading-[1.68]" {...props}>
        {inlineTextChildren(children)}
      </p>
    ),
    li: ({ children, ...props }) => (
      <li className="text-[16px] leading-[1.72] text-white/78 sm:text-sm sm:leading-relaxed" {...props}>
        {inlineTextChildren(children)}
      </li>
    ),
  }

  return (
    <div className={`legal-answer ${className}`}>
      <Markdown remarkPlugins={[remarkGfm]} components={inlineComponents}>
        {normalized}
      </Markdown>
    </div>
  )
}
