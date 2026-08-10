import { Paperclip, SendHorizontal, Square, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

export function ChatComposer({
  value,
  onChange,
  onSubmit,
  loading,
  sendDisabled = false,
  onCancel,
  onOpenPdfPicker,
  activeDocument,
  pendingAttachment,
  onClearActiveDocument,
  onClearPendingAttachment,
  userMessages = [],
  onFocus,
  onBlur,
}) {
  const textareaRef = useRef(null)
  const historyIdxRef = useRef(-1)
  const draftRef = useRef('')

  const autoResize = () => {
    const el = textareaRef.current
    if (!el) return
    const mobileMaxHeight = 112
    const desktopMaxHeight = 160
    const maxHeight = window.matchMedia('(max-width: 767px)').matches ? mobileMaxHeight : desktopMaxHeight
    if (!value.trim()) {
      el.style.height = ''
      el.style.overflowY = 'hidden'
      return
    }
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, maxHeight) + 'px'
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }

  useEffect(() => {
    autoResize()
    if (!value.trim()) {
      const id = window.requestAnimationFrame(autoResize)
      return () => window.cancelAnimationFrame(id)
    }
    return undefined
  }, [value])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit(e)
      return
    }
    // Keyboard history navigation: ↑/↓ when cursor at start/end
    if (e.key === 'ArrowUp' && !e.shiftKey) {
      const ta = textareaRef.current
      if (ta) {
        const before = ta.value.slice(0, ta.selectionStart)
        if (before.includes('\n') || ta.selectionStart > 0) return
      }
      e.preventDefault()
      if (historyIdxRef.current === -1) {
        draftRef.current = value
        historyIdxRef.current = userMessages.length - 1
        if (userMessages[historyIdxRef.current]) onChange(userMessages[historyIdxRef.current])
      } else if (historyIdxRef.current > 0) {
        historyIdxRef.current--
        if (userMessages[historyIdxRef.current]) onChange(userMessages[historyIdxRef.current])
      }
    } else if (e.key === 'ArrowDown' && !e.shiftKey) {
      if (historyIdxRef.current === -1) return
      e.preventDefault()
      if (historyIdxRef.current < userMessages.length - 1) {
        historyIdxRef.current++
        if (userMessages[historyIdxRef.current]) onChange(userMessages[historyIdxRef.current])
      } else {
        historyIdxRef.current = -1
        onChange(draftRef.current || '')
        draftRef.current = ''
      }
    }
  }

  // Reset history index when user modifies the text
  useEffect(() => {
    if (historyIdxRef.current >= 0 && value !== userMessages[historyIdxRef.current]) {
      historyIdxRef.current = -1
    }
  }, [value, userMessages])

  return (
    <form className="mx-auto w-full max-w-3xl rounded-[var(--radius-xl)] border border-[color:var(--stroke)] bg-[color:var(--panel)] shadow-[var(--shadow-2)] transition-shadow focus-within:shadow-[var(--shadow-3)]" onSubmit={onSubmit}>
      {/* Active document context chip */}
      {activeDocument ? (
        <div className="flex items-center gap-2 border-b border-[color:var(--stroke)] px-4 py-2.5 sm:px-5">
          <Paperclip size={13} className="shrink-0 text-[color:var(--accent)]" />
          <span className="min-w-0 flex-1 truncate text-xs text-[color:var(--ink-soft)]">
            <span className="font-medium text-[color:var(--ink)]">Contexto:</span> {activeDocument.filename}
          </span>
          <button type="button" onClick={onClearActiveDocument} className="shrink-0 rounded-full p-0.5 text-[color:var(--ink-soft)] transition-colors hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)]">
            <X size={13} />
          </button>
        </div>
      ) : null}

      {/* Pending attachment chip */}
      {pendingAttachment ? (
        <div className="flex items-center gap-2 border-b border-dashed border-[color:var(--stroke)] px-4 py-2.5 sm:px-5">
          <Paperclip size={13} className="shrink-0 text-[color:var(--gold)]" />
          <span className="min-w-0 flex-1 truncate text-xs text-[color:var(--ink-soft)]">
            <span className="font-medium text-[color:var(--ink)]">Anexo:</span> {pendingAttachment.name}
          </span>
          <button type="button" onClick={onClearPendingAttachment} className="shrink-0 rounded-full p-0.5 text-[color:var(--ink-soft)] transition-colors hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)]">
            <X size={13} />
          </button>
        </div>
      ) : null}

      {/* Input row */}
      <div className="flex items-end gap-1 px-3 py-3 sm:gap-2 sm:px-5 sm:py-3">
        <button
          type="button"
          onClick={onOpenPdfPicker}
          className="grid h-10 w-10 shrink-0 place-items-center rounded-[var(--radius-md)] text-[color:var(--ink-soft)] transition-all hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--accent)] active:scale-95 sm:h-9 sm:w-9"
          aria-label="Anexar PDF"
        >
          <Paperclip size={17} />
        </button>

        <div className="relative flex-1">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder="Escreva a sua pergunta jurídica..."
            className="max-h-[112px] w-full resize-none bg-transparent px-3 py-2.5 text-[16px] leading-relaxed text-[color:var(--ink)] outline-none placeholder:text-[color:var(--ink-soft)]/50 sm:max-h-[160px] sm:px-4 sm:py-2 sm:text-sm"
            style={{ overflowY: 'hidden' }}
            onInput={autoResize}
            onFocus={onFocus}
            onBlur={onBlur}
          />
        </div>

        {loading ? (
          <button
            type="button"
            onClick={onCancel}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-[var(--radius-md)] bg-rose-500 text-white transition-all hover:bg-rose-600 active:scale-95 sm:h-9 sm:w-9"
            aria-label="Parar geração"
          >
            <Square size={15} />
          </button>
        ) : (
          <button
            type="submit"
            disabled={sendDisabled || value.trim().length < 5}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-[var(--radius-md)] bg-[color:var(--accent)] text-white transition-all hover:bg-[color:var(--accent-hover)] active:scale-95 disabled:opacity-40 disabled:pointer-events-none sm:h-9 sm:w-9"
            aria-label="Enviar pergunta"
          >
            <SendHorizontal size={17} />
          </button>
        )}
      </div>

    </form>
  )
}
