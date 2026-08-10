import { BookOpenCheck, ChevronDown, ExternalLink, Gavel, Pencil, Scale, User } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'
import { cleanAnswerBody, formatArticleLabel, normalizeDisplayText } from '../utils/markdown'
import { ClarifyingQuestions } from './ClarifyingQuestions'
import { formatHumanTimestamp } from '../utils/format'
import LegalMarkdown from './LegalMarkdown'

import SuggestedActions from './SuggestedActions'

function EditMessageForm({ initialContent, onSave, onCancel, messageId }) {
  const [text, setText] = useState(initialContent)
  const textareaRef = useRef(null)

  useEffect(() => {
    const ta = textareaRef.current
    if (ta) {
      ta.focus()
      ta.setSelectionRange(ta.value.length, ta.value.length)
    }
  }, [])

  const handleSave = () => {
    const trimmed = text.trim()
    if (trimmed.length >= 5) {
      onSave(messageId, trimmed)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSave()
    }
    if (e.key === 'Escape') {
      onCancel()
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <textarea
        ref={textareaRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={Math.min(Math.max(text.split('\n').length, 2), 8)}
        className="w-full resize-none rounded-lg border border-white/10 bg-white/5 p-2.5 text-[15px] leading-7 text-white/85 outline-none transition-colors focus:border-white/20 focus:bg-white/10 sm:text-sm sm:leading-relaxed"
      />
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-2.5 py-1 text-[11px] font-medium text-white/40 transition-colors hover:bg-white/5 hover:text-white/60"
        >
          Cancelar
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={text.trim().length < 5}
          className="rounded-md bg-white/10 px-3 py-1 text-[11px] font-medium text-white/70 transition-colors hover:bg-white/15 hover:text-white/90 disabled:opacity-40"
        >
          Guardar
        </button>
      </div>
    </div>
  )
}

function CitationCard({ item, index, verification }) {
  const verificationText = verification?.status === 'confirmed' || verification?.status === 'confirmed_in_text'
    ? 'confirmado'
    : verification?.status
      ? 'pendente'
      : item.confirmed
        ? 'confirmado'
        : 'prudencial'

  return (
    <article className="group rounded-xl border border-white/[0.07] bg-white/[0.03] px-4 py-3 transition-colors hover:border-white/[0.12] hover:bg-white/[0.05]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[14px] font-semibold tracking-[-0.01em] text-white/90 sm:text-[13px]">
            {item.article ? `Art. ${item.article}` : 'Base normativa'}
          </p>
          <p className="mt-0.5 text-[13px] leading-6 text-white/50 sm:text-[12px] sm:leading-5">
            {item.diploma}
            {item.page ? <span className="ml-1 tabular-nums">· pág. {item.page}</span> : ''}
          </p>
        </div>
        <span className="shrink-0 rounded-md bg-white/[0.06] px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.08em] text-white/40 sm:text-[10px]">
          {verificationText}
        </span>
      </div>
      {item.excerpt ? (
        <p className="mt-2.5 border-t border-white/[0.05] pt-2.5 text-[13px] leading-6 tracking-[0.01em] text-white/55 font-light sm:text-[12px] sm:leading-[1.6]">
          {item.excerpt}
        </p>
      ) : null}
      {item.deep_link ? (
        <a
          href={item.deep_link}
          target="_blank"
          rel="noreferrer"
          className="mt-2.5 inline-flex items-center gap-1 text-[11px] font-medium text-[var(--color-accent)] transition-colors hover:opacity-80"
        >
          Ver excerto <ExternalLink size={10} />
        </a>
      ) : null}
    </article>
  )
}

export function ChatMessage({ role, content, createdAt, sourceRefs = [], answerMode = null, clarifyingQuestions = [], legalBasis = [], verifiedArticles = [], classification = null, validationIssues = [], confidence = null, onSelectRef, onClarifyingSelect, onEdit, messageId, isEditing, onSaveEdit, onCancelEdit, versionInfo, onNavigateVersion, suggestedActions = [], onSelectAction }) {
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [legalBasisOpen, setLegalBasisOpen] = useState(false)
  const isUser = role === 'user'
  const displayText = isUser ? content : cleanAnswerBody(content)
  const isClarifying = answerMode === 'clarifying'
  const isRefused = answerMode === 'refused'
  const clarifyingLead = displayText || 'Para garantir rigor juridico, preciso de um pouco mais de contexto sobre o seu caso.'
  const hasLegalBasis = legalBasis.length > 0
  const hasSources = sourceRefs.length > 0

  return (
    <article className={`fade-rise flex gap-2 sm:gap-3 ${isUser ? 'flex-row-reverse group' : 'group'}`}>
      <div className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[13px] font-semibold ${
        isUser
          ? 'bg-[color:var(--chat-user)] text-[color:var(--ink)]'
          : 'hidden bg-[color:var(--color-accent)] text-white sm:grid'
      }`}>
        {isUser ? <User size={13} strokeWidth={2.5} /> : (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275Z"/>
          </svg>
        )}
      </div>

      <div className={`min-w-0 ${
        isUser
          ? 'max-w-[86%] rounded-2xl rounded-br-md bg-[color:var(--chat-user)] px-3.5 py-2.5 sm:max-w-[78%] sm:px-4 sm:py-3'
          : 'w-full max-w-none rounded-2xl bg-[color:var(--chat-assistant)] border border-[color:var(--stroke)] px-3.5 py-3.5 sm:max-w-[78%] sm:rounded-bl-md sm:px-5 sm:py-4'
      }`}>
        {isUser ? (
          isEditing ? (
            <EditMessageForm initialContent={content} onSave={onSaveEdit} onCancel={onCancelEdit} messageId={messageId} />
          ) : (
            <>
          <div className="flex items-start gap-2">
                <p className="flex-1 whitespace-pre-wrap text-[15px] leading-7 text-white/85 sm:text-sm sm:leading-relaxed">{normalizeDisplayText(content)}</p>
                {onEdit ? (
                  <button type="button" onClick={onEdit} className="mt-0.5 shrink-0 rounded p-0.5 text-white/30 opacity-0 max-sm:opacity-60 transition-all hover:bg-white/10 hover:text-white/60 group-hover:opacity-100" aria-label="Editar mensagem" title="Editar">
                    <Pencil size={12} />
                  </button>
                ) : null}
              </div>
              {versionInfo && onNavigateVersion && (
                <div className="flex items-center justify-end gap-1.5 mt-1.5 border-t border-white/[0.06] pt-1.5">
                  <button type="button" onClick={() => onNavigateVersion(-1)} disabled={versionInfo.current === 0}
                    className="rounded p-0.5 text-white/30 transition-all hover:bg-white/10 hover:text-white/60 disabled:opacity-20 disabled:pointer-events-none"
                    aria-label="Versao anterior">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6"/></svg>
                  </button>
                  <span className="text-[10px] font-medium tabular-nums text-white/35">{versionInfo.current + 1}/{versionInfo.count}</span>
                  <button type="button" onClick={() => onNavigateVersion(1)} disabled={versionInfo.current === versionInfo.count - 1}
                    className="rounded p-0.5 text-white/30 transition-all hover:bg-white/10 hover:text-white/60 disabled:opacity-20 disabled:pointer-events-none"
                    aria-label="Proxima versao">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>
                  </button>
                </div>
              )}
              {createdAt ? (
                <p className="text-right text-[10px] tabular-nums text-white/35 mt-1.5">
                  {formatHumanTimestamp(createdAt)}
                </p>
              ) : null}
            </>
          )
        ) : isClarifying ? (
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <span className="mt-1 hidden h-2 w-2 shrink-0 rounded-full bg-[color:var(--accent)] sm:block" />
              <div className="min-w-0">
                <p className="text-[15px] font-semibold leading-7 tracking-[-0.01em] text-white/88 sm:text-[13px] sm:leading-6">
                  {clarifyingLead}
                </p>
                <p className="mt-1 text-[12px] leading-5 text-white/42">
                  Responda só ao ponto que souber; eu continuo a análise neste mesmo chat.
                </p>
              </div>
            </div>
            {clarifyingQuestions.length > 0 ? (
              <ClarifyingQuestions questions={clarifyingQuestions} onSelect={onClarifyingSelect} />
            ) : null}
          </div>
        ) : isRefused ? (
          <p className="text-[15px] leading-7 text-rose-200/80 sm:text-[13px] sm:leading-relaxed">
            O corpus jurídico actual não contém informação suficiente para responder a esta questão com bases legais verificadas.
            Recomendamos consultar um profissional ou reformular a pergunta com mais contexto.
          </p>
        ) : (
          <>
            {isEditing ? (
              <EditMessageForm initialContent={displayText} onSave={onSaveEdit} onCancel={onCancelEdit} messageId={messageId} />
            ) : (
              <div className="group/ai relative">
                {displayText ? (
                  <LegalMarkdown text={displayText} sourceRefs={sourceRefs} onSelectRef={onSelectRef} />
                ) : null}
              </div>
            )}

            {suggestedActions.length > 0 && onSelectAction ? (
              <SuggestedActions actions={suggestedActions} onSelect={onSelectAction} />
            ) : null}

            {hasLegalBasis ? (
              <section className="mt-4 border-t border-white/[0.06] pt-3">
                <button
                  type="button"
                  onClick={() => setLegalBasisOpen(!legalBasisOpen)}
                  className="flex w-full items-center justify-between gap-2 text-[12px] font-medium uppercase tracking-[0.12em] text-white/35 transition-colors hover:text-white/55 sm:text-[11px]"
                >
                  <span className="flex items-center gap-1.5">
                    <Scale size={11} />
                    Fundamentos legais ({legalBasis.length})
                  </span>
                  <ChevronDown size={12} className={`transition-transform duration-200 ${legalBasisOpen ? 'rotate-180' : ''}`} />
                </button>
                {legalBasisOpen ? (
                  <div className="mt-2.5 space-y-1.5">
                    {legalBasis.slice(0, 4).map((item, index) => {
                      const verification = verifiedArticles.find(
                        (candidate) => String(candidate.article || '').replace('.', '') === String(item.article || '').replace('.', ''),
                      )
                      return <CitationCard key={`${item.diploma}-${item.article}-${index}`} item={item} index={index} verification={verification} />
                    })}
                  </div>
                ) : null}
              </section>
            ) : null}

            {hasSources ? (
              <div className="mt-3 border-t border-white/[0.06] pt-3">
                <button
                  type="button"
                  onClick={() => setSourcesOpen(!sourcesOpen)}
                  className="flex w-full items-center justify-between gap-2 text-[12px] font-medium tracking-[0.06em] text-white/35 transition-colors hover:text-white/55 sm:text-[11px]"
                >
                  <span className="flex items-center gap-1.5">
                    <BookOpenCheck size={12} />
                    {sourceRefs.length} fonte{sourceRefs.length > 1 ? 's' : ''} consultada{sourceRefs.length > 1 ? 's' : ''}
                  </span>
                  <ChevronDown size={12} className={`transition-transform duration-200 ${sourcesOpen ? 'rotate-180' : ''}`} />
                </button>

                {sourcesOpen ? (
                  <ul className="mt-2 space-y-0.5">
                    {sourceRefs.map((source, i) => {
                      const isJuris = source.source_kind === 'jurisprudence'
                      return (
                      <li key={`${source.source}-${source.page}-${i}`}>
                        <button
                          type="button"
                          onClick={() => onSelectRef?.(source)}
                          className="w-full rounded-lg px-2.5 py-2 text-left text-[13px] leading-6 text-white/55 transition-colors hover:bg-white/[0.04] hover:text-white/75 sm:py-1.5 sm:text-[12px]"
                        >
                          <span className="font-medium">{source.article_number ? formatArticleLabel(source.article_number) : normalizeDisplayText(source.title?.slice(0, 50))}</span>
                          <span className="ml-1.5 text-white/25 tabular-nums">· pag. {source.page || 'N/D'}</span>
                          {isJuris ? <span className="ml-1.5 inline-flex items-center gap-0.5 text-[color:var(--gold)]"><Gavel size={10} /> Jurisprudencia</span> : null}
                        </button>
                      </li>
                      )
                    })}
                  </ul>
                ) : null}
              </div>
            ) : null}
            {createdAt ? (
              <div className="mt-3 flex items-center justify-end gap-2 border-t border-white/[0.06] pt-2 text-[10px] text-white/30">
                <span className="tabular-nums">{formatHumanTimestamp(createdAt)}</span>
              </div>
            ) : null}
          </>
        )} {/* end assistant bubble */}
      </div>
    </article>
  )
}
