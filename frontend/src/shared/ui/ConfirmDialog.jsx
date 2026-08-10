import { useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Trash2, X } from 'lucide-react'

export function ConfirmDialog({ open, title, message, confirmLabel = 'Eliminar', onConfirm, onCancel, variant = 'danger' }) {
  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative w-full max-w-md rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--panel)] p-6 shadow-2xl animate-in fade-in zoom-in-95">
        <div className="flex items-start gap-4">
          <div className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${variant === 'danger' ? 'bg-rose-500/15' : 'bg-amber-500/15'}`}>
            <AlertTriangle size={18} className={variant === 'danger' ? 'text-rose-400' : 'text-amber-400'} />
          </div>
          <div className="min-w-0">
            <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-white/90">{title}</h3>
            <p className="mt-1.5 text-[13px] leading-relaxed text-white/55">{message}</p>
          </div>
        </div>
        <div className="mt-6 flex items-center justify-end gap-2.5">
          <button type="button" onClick={onCancel} className="rounded-xl bg-white/[0.04] px-4 py-2 text-[13px] font-medium text-white/60 transition-colors hover:bg-white/[0.08] hover:text-white/80">
            Cancelar
          </button>
          <button type="button" onClick={() => { onConfirm(); onCancel() }}
            className={`rounded-xl px-4 py-2 text-[13px] font-medium transition-colors ${variant === 'danger' ? 'bg-rose-500/20 text-rose-300 hover:bg-rose-500/30' : 'bg-amber-500/20 text-amber-300 hover:bg-amber-500/30'}`}>
            <span className="flex items-center gap-1.5"><Trash2 size={13} />{confirmLabel}</span>
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

export function BatchDeleteModal({ conversations, onDelete, onClose, onDeleteAll }) {
  const [selected, setSelected] = useState(new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmMode, setConfirmMode] = useState(null)

  const toggle = (id) => {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  const toggleAll = () => {
    if (selected.size === conversations.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(conversations.map((c) => c.id)))
    }
  }

  const handleDeleteSelected = () => {
    selected.forEach((id) => onDelete(id))
    setSelected(new Set())
    setConfirmOpen(false)
  }

  const handleDeleteAll = () => {
    onDeleteAll()
    setConfirmOpen(false)
    onClose()
  }

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--panel)] shadow-2xl animate-in fade-in zoom-in-95">
        <div className="flex items-center justify-between border-b border-[color:var(--stroke)] px-5 py-4">
          <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-white/90">Gerir conversas</h3>
          <button type="button" onClick={onClose} className="grid h-7 w-7 place-items-center rounded-lg text-white/35 transition-colors hover:bg-white/[0.06] hover:text-white/70">
            <X size={15} />
          </button>
        </div>

        <div className="px-5 py-3 border-b border-[color:var(--stroke)]">
          <label className="flex items-center gap-2.5 text-[13px] text-white/70 cursor-pointer select-none">
            <input type="checkbox" checked={selected.size === conversations.length && conversations.length > 0} onChange={toggleAll}
              className="h-4 w-4 rounded border-white/20 bg-white/[0.04] accent-[var(--color-accent)] cursor-pointer" />
            Seleccionar todas ({conversations.length})
          </label>
        </div>

        <div className="max-h-64 overflow-y-auto px-3 py-2 space-y-0.5">
          {conversations.map((conv) => (
            <label key={conv.id}
              className={`flex items-center gap-2.5 rounded-lg px-2 py-2 cursor-pointer select-none transition-colors ${selected.has(conv.id) ? 'bg-[var(--color-accent)]/8' : 'hover:bg-white/[0.04]'}`}>
              <input type="checkbox" checked={selected.has(conv.id)} onChange={() => toggle(conv.id)}
                className="h-4 w-4 shrink-0 rounded border-white/20 bg-white/[0.04] accent-[var(--color-accent)] cursor-pointer" />
              <span className="text-[13px] text-white/75 truncate">{conv.title || 'Sem titulo'}</span>
            </label>
          ))}
        </div>

        <div className="flex items-center justify-between border-t border-[color:var(--stroke)] px-5 py-4">
          <button type="button" onClick={() => { setConfirmMode('all'); setConfirmOpen(true) }}
            className="rounded-xl bg-rose-500/[0.07] px-3 py-1.5 text-[12px] font-medium text-rose-400/80 transition-colors hover:bg-rose-500/15">
            Eliminar todas
          </button>
          <button type="button" disabled={selected.size === 0} onClick={() => { setConfirmMode('selected'); setConfirmOpen(true) }}
            className={`rounded-xl px-4 py-1.5 text-[12px] font-medium transition-colors ${selected.size > 0 ? 'bg-[var(--color-accent)]/15 text-[var(--color-accent)] hover:bg-[var(--color-accent)]/25' : 'bg-white/[0.02] text-white/15 cursor-not-allowed'}`}>
            Eliminar {selected.size > 0 ? `(${selected.size})` : 'seleccionadas'}
          </button>
        </div>

        <ConfirmDialog open={confirmOpen}
          title={confirmMode === 'all' ? 'Eliminar todas as conversas?' : `Eliminar ${selected.size} conversa${selected.size > 1 ? 's' : ''}?`}
          message="Esta acção é irreversível. Todo o historico, mensagens e documentos associados serao permanentemente removidos."
          confirmLabel={confirmMode === 'all' ? 'Eliminar todas' : `Eliminar ${selected.size}`}
          variant="danger"
          onConfirm={confirmMode === 'all' ? handleDeleteAll : handleDeleteSelected}
          onCancel={() => { setConfirmOpen(false); setConfirmMode(null) }} />
      </div>
    </div>,
    document.body
  )
}
