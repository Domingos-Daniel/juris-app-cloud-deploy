import {
  AlertTriangle,
  BriefcaseBusiness,
  Check,
  Clock,
  Pencil,
  Trash2,
  X,
  FolderOpen,
  Landmark,
  LogOut,
  MessageSquarePlus,
  Settings,
  ChevronRight,
  ChevronsRight,
  Shield,
} from 'lucide-react'
import { useState } from 'react'
import { NAV_ITEMS } from '../constants/app'
import { clipText } from '../utils/format'
import { formatHumanTimestamp } from '../utils/format'
import { classNames } from '../utils/format'
import { BatchDeleteModal, ConfirmDialog } from './ConfirmDialog'

const VISIBLE_CONVERSATIONS = 4

const iconBySection = {
  chat: MessageSquarePlus,
  documents: FolderOpen,
  library: Landmark,
  settings: Settings,
  pro: BriefcaseBusiness,
  admin: Shield,
}

/* ─── Conversation item (shared between sidebar and modal) ─── */
function ConversationItem({
  chat,
  selected,
  isEditing,
  draftTitle,
  setDraftTitle,
  onSelect,
  onStartRename,
  onSaveRename,
  onCancelRename,
  onDelete,
  compact = false,
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <article
      className={classNames(
        'group rounded-[var(--radius-sm)] px-2.5 py-2 transition-all',
        selected
          ? 'bg-[color:var(--sidebar-active)]'
          : 'hover:bg-[color:var(--panel-muted)]',
      )}
    >
      <div className="flex items-center gap-2">
        {/* Small chat icon */}
        <div className={classNames(
          'grid h-6 w-6 shrink-0 place-items-center rounded-[var(--radius-sm)]',
          selected ? 'bg-[color:var(--accent-soft)] text-[color:var(--accent)]' : 'bg-[color:var(--panel-muted)] text-[color:var(--ink-soft)]'
        )}>
          <MessageSquarePlus size={12} />
        </div>

        {isEditing ? (
          <input
            autoFocus
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') { event.preventDefault(); onSaveRename() }
              if (event.key === 'Escape') { event.preventDefault(); onCancelRename() }
            }}
            className="w-full rounded border border-[color:var(--stroke)] bg-[color:var(--panel)] px-2 py-1 text-sm text-[color:var(--ink)] outline-none"
          />
        ) : (
          <button onClick={onSelect} className="min-w-0 flex-1 text-left">
            <div className={classNames('truncate text-[13px]', selected ? 'font-medium text-[color:var(--ink)]' : 'text-[color:var(--ink-soft)]')}>
              {clipText(chat.title, compact ? 48 : 28)}
            </div>
            <div className="mt-0.5 text-[10px] text-[color:var(--ink-soft)]">{formatHumanTimestamp(chat.updatedAt)}</div>
          </button>
        )}

        <div className={classNames('flex shrink-0 gap-0.5', isEditing ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity')}>
          {isEditing ? (
            <>
              <button onClick={onSaveRename} className="rounded p-1 text-[color:var(--success)] hover:bg-[color:var(--success-soft)]" aria-label="Salvar">
                <Check size={13} />
              </button>
              <button onClick={onCancelRename} className="rounded p-1 text-[color:var(--danger)] hover:bg-[color:var(--danger-soft)]" aria-label="Cancelar">
                <X size={13} />
              </button>
            </>
          ) : (
            <>
              <button onClick={onStartRename} className="rounded p-1 text-[color:var(--ink-soft)] hover:text-[color:var(--ink)]" aria-label="Renomear">
                <Pencil size={12} />
              </button>
              <button onClick={() => setConfirmDelete(true)} className="rounded p-1 text-[color:var(--ink-soft)] hover:text-[color:var(--danger)]" aria-label="Eliminar">
                <Trash2 size={12} />
              </button>
            </>
          )}
        </div>
      </div>
      <ConfirmDialog
        open={confirmDelete}
        title="Eliminar conversa?"
        message={`"${(chat.title || 'Sem titulo').substring(0, 50)}" sera permanentemente removida com todas as mensagens.`}
        confirmLabel="Eliminar"
        variant="danger"
        onConfirm={() => onDelete?.(chat.id)}
        onCancel={() => setConfirmDelete(false)}
      />
    </article>
  )
}

const MODAL_PAGE_SIZE = 10

/* ─── All Conversations Modal ─── */
function AllConversationsModal({
  conversations,
  activeConversationId,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  onDeleteAllConversations,
  onClose,
}) {
  const [editingId, setEditingId] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [search, setSearch] = useState('')
  const [visibleCount, setVisibleCount] = useState(MODAL_PAGE_SIZE)
  const [showBatchDelete, setShowBatchDelete] = useState(false)

  const startRename = (c) => { setEditingId(c.id); setDraftTitle(c.title || '') }
  const cancelRename = () => { setEditingId(null); setDraftTitle('') }
  const saveRename = () => { if (editingId) { onRenameConversation(editingId, draftTitle); cancelRename() } }

  const filtered = search.trim()
    ? conversations.filter((c) => (c.title || '').toLowerCase().includes(search.toLowerCase()))
    : conversations

  const visible = filtered.slice(0, visibleCount)
  const hasMore = visibleCount < filtered.length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="fade-rise mx-4 flex max-h-[80vh] w-full max-w-lg flex-col rounded-[var(--radius-xl)] border border-[color:var(--stroke)] bg-[color:var(--panel)] shadow-[var(--shadow-3)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className="flex items-center gap-3 border-b border-[color:var(--stroke)] px-5 py-4">
          <Clock size={18} className="text-[color:var(--accent)]" />
          <h3 className="flex-1 text-base font-semibold text-[color:var(--ink)]">Todas as conversas</h3>
          <span className="rounded-full bg-[color:var(--panel-muted)] px-2.5 py-0.5 text-[11px] font-medium text-[color:var(--ink-soft)]">
            {filtered.length}
          </span>
          {conversations.length > 0 && onDeleteAllConversations ? (
            <button
              onClick={() => setShowBatchDelete(true)}
              className="flex items-center gap-1 rounded-lg bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-medium text-white/50 transition-all hover:bg-rose-500/15 hover:text-rose-400"
            >
              <Trash2 size={12} />
              Gerir
            </button>
          ) : null}
          <button
            onClick={onClose}
            className="grid h-7 w-7 place-items-center rounded-[var(--radius-sm)] text-[color:var(--ink-soft)] transition-colors hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)]"
            aria-label="Fechar"
          >
            <X size={16} />
          </button>
        </div>

        {/* Search */}
        <div className="border-b border-[color:var(--stroke)] px-5 py-3">
          <input
            type="text"
            placeholder="Pesquisar conversas..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setVisibleCount(MODAL_PAGE_SIZE) }}
            className="w-full rounded-[var(--radius-sm)] border border-[color:var(--stroke)] bg-[color:var(--panel-muted)] px-3 py-2 text-sm text-[color:var(--ink)] placeholder-[color:var(--ink-soft)] outline-none transition-colors focus:border-[color:var(--accent)]"
          />
        </div>

        {/* Conversation list with scroll pagination */}
        <div className="custom-scroll flex-1 overflow-y-auto px-3 py-3">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center py-10">
              <div className="grid h-12 w-12 place-items-center rounded-full bg-[color:var(--panel-muted)]">
                <MessageSquarePlus size={20} className="text-[color:var(--ink-soft)]" />
              </div>
              <p className="mt-3 text-sm text-[color:var(--ink-soft)]">Nenhuma conversa encontrada.</p>
            </div>
          ) : (
            <div className="space-y-0.5">
              {visible.map((chat) => (
                <ConversationItem
                  key={chat.id}
                  chat={chat}
                  selected={activeConversationId === chat.id}
                  isEditing={editingId === chat.id}
                  draftTitle={draftTitle}
                  setDraftTitle={setDraftTitle}
                  onSelect={() => { onSelectConversation(chat.id); onClose() }}
                  onStartRename={() => startRename(chat)}
                  onSaveRename={saveRename}
                  onCancelRename={cancelRename}
                  onDelete={onDeleteConversation}
                  compact
                />
              ))}
              {hasMore ? (
                <button
                  onClick={() => setVisibleCount((prev) => prev + MODAL_PAGE_SIZE)}
                  className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-[var(--radius-sm)] border border-[color:var(--stroke)] bg-[color:var(--panel-muted)] px-3 py-2.5 text-xs font-medium text-[color:var(--ink-soft)] transition-all hover:bg-[color:var(--accent-soft)] hover:text-[color:var(--accent)]"
                >
                  <ChevronsRight size={13} />
                  Carregar mais ({filtered.length - visibleCount} restantes)
                </button>
              ) : null}
            </div>
          )}
        </div>
      </div>
      {showBatchDelete ? (
        <BatchDeleteModal
          conversations={conversations}
          onDelete={onDeleteConversation}
          onDeleteAll={onDeleteAllConversations}
          onClose={() => setShowBatchDelete(false)}
        />
      ) : null}
    </div>
  )
}

export function SidebarNav({
  activeSection,
  onSectionChange,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onRenameConversation,
  onDeleteConversation,
  onDeleteAllConversations,
  motor,
  onMotorChange,
  currentUser,
  onLogout,
  className = '',
  onClose,
}) {
  const [editingId, setEditingId] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [showAllModal, setShowAllModal] = useState(false)

  const startRename = (conversation) => {
    setEditingId(conversation.id)
    setDraftTitle(conversation.title || '')
  }

  const cancelRename = () => {
    setEditingId(null)
    setDraftTitle('')
  }

  const saveRename = () => {
    if (!editingId) return
    onRenameConversation(editingId, draftTitle)
    cancelRename()
  }

  const visibleConversations = conversations.slice(0, VISIBLE_CONVERSATIONS)
  const hasMore = conversations.length > VISIBLE_CONVERSATIONS

  return (
    <>
      <aside className={classNames('h-screen w-[280px] max-w-[85vw] flex-col border-r border-[color:var(--stroke)] bg-[color:var(--sidebar)] sm:max-w-[280px] pb-36 sm:pb-0', className)}>
        {/* Header */}
        <header className="flex items-center gap-3 border-b border-[color:var(--stroke)] px-4 py-4">
          <div className="grid h-9 w-9 place-items-center rounded-[var(--radius-md)] bg-[color:var(--accent)] text-white">
            <Landmark size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="font-[family-name:var(--font-serif)] text-base font-semibold leading-tight text-[color:var(--ink)]">jURIS-APP</h1>
            <p className="text-[10px] uppercase tracking-[0.14em] text-[color:var(--ink-soft)]">Assistente Juridico</p>
          </div>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="flex flex-col items-center justify-center gap-0.5 rounded-[var(--radius-sm)] border border-[color:var(--stroke)] bg-[color:var(--panel)] px-2 py-1 text-[color:var(--ink-soft)] transition-colors hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)]"
              aria-label="Fechar menu"
            >
              <X size={15} />
              <span className="text-[8px] leading-none text-[color:var(--ink-soft)]/70">Fechar</span>
            </button>
          ) : null}
        </header>

        {/* Scrollable content: nav + chat history in single container */}
        <div className="custom-scroll min-h-0 flex-1 overflow-y-auto">
          <nav className="space-y-0.5 px-2 py-3">
            {NAV_ITEMS.filter((item) => {
              const hasPro = currentUser?.professional_profile?.status === 'active' || currentUser?.role === 'admin'
              return (!item.adminOnly || currentUser?.role === 'admin') && (!item.proOnly || hasPro)
            }).map((item) => {
              const Icon = iconBySection[item.id]
              const isActive = activeSection === item.id
              return (
                <button
                  key={item.id}
                  onClick={() => {
                    if (item.id === 'chat') { onNewConversation() } else { onSectionChange(item.id) }
                  }}
                  className={classNames(
                    'flex w-full items-center gap-2.5 rounded-[var(--radius-sm)] px-3 py-2 text-left text-sm transition-all',
                    isActive
                      ? 'bg-[color:var(--sidebar-active)] font-medium text-[color:var(--accent)]'
                      : 'text-[color:var(--ink-soft)] hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)]',
                  )}
                >
                  <Icon size={16} />
                  <span>{item.label}</span>
                  {isActive ? <ChevronRight size={14} className="ml-auto opacity-50" /> : null}
                </button>
              )
            })}
          </nav>

          {conversations.length > 0 ? (
            <>
              <div className="mx-3 border-t border-[color:var(--stroke)]" />
              <div className="px-4 py-2.5">
                <h2 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[color:var(--ink-soft)]">Conversas recentes</h2>
              </div>
              <div className="space-y-0.5 px-2 pb-2">
                {visibleConversations.map((chat) => (
                  <ConversationItem
                    key={chat.id}
                    chat={chat}
                    selected={activeConversationId === chat.id}
                    isEditing={editingId === chat.id}
                    draftTitle={draftTitle}
                    setDraftTitle={setDraftTitle}
                    onSelect={() => {
                      onSelectConversation(chat.id)
                      onClose?.()
                    }}
                    onStartRename={() => startRename(chat)}
                    onSaveRename={saveRename}
                    onCancelRename={cancelRename}
                    onDelete={onDeleteConversation}
                  />
                ))}
                {hasMore ? (
                  <button
                    onClick={() => setShowAllModal(true)}
                    className="flex w-full items-center justify-center gap-1.5 rounded-[var(--radius-sm)] px-3 py-2 text-xs font-medium text-[color:var(--accent)] transition-all hover:bg-[color:var(--accent-soft)]"
                  >
                    <ChevronsRight size={13} />
                    Ver mais ({conversations.length - VISIBLE_CONVERSATIONS})
                  </button>
                ) : null}
              </div>
            </>
          ) : null}
        </div>

        {/* Footer */}
        <footer className="border-t border-[color:var(--stroke)] px-3 py-3">
          {currentUser ? (
            <div className="mb-2 flex items-center gap-2.5 rounded-[var(--radius-sm)] px-2 py-1.5">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[color:var(--accent-soft)] text-xs font-semibold text-[color:var(--accent)]">
                {(currentUser.name || 'U')[0].toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-[color:var(--ink)]">{currentUser.name}</div>
                <div className="truncate text-[11px] text-[color:var(--ink-soft)]">@{currentUser.username}</div>
              </div>
            </div>
          ) : null}
          <button onClick={onLogout} className="flex w-full items-center gap-2 rounded-[var(--radius-sm)] border border-[color:var(--stroke)] px-2 py-2 text-sm text-[color:var(--ink-soft)] transition-colors hover:border-[color:var(--danger)] hover:bg-[color:var(--danger-soft)] hover:text-[color:var(--danger)] active:bg-[color:var(--danger-soft)] active:text-[color:var(--danger)]">
            <LogOut size={15} /> Sair
          </button>
        </footer>
      </aside>

      {/* All conversations modal */}
      {showAllModal ? (
        <AllConversationsModal
          conversations={conversations}
          activeConversationId={activeConversationId}
          onSelectConversation={onSelectConversation}
          onRenameConversation={onRenameConversation}
          onDeleteConversation={onDeleteConversation}
          onDeleteAllConversations={onDeleteAllConversations}
          onClose={() => setShowAllModal(false)}
        />
      ) : null}
    </>
  )
}

export function TopBar({ leftNode, centerNode, rightNode, className = '' }) {
  return (
    <header data-app-topbar className={`sticky top-0 z-20 flex items-center border-b border-[color:var(--stroke)] bg-[color:var(--panel)]/95 px-3 py-2 backdrop-blur-sm sm:px-4 relative ${className}`}>
      <div className="flex min-w-0 flex-1 items-center gap-2">{leftNode}</div>
      <div className="absolute left-1/2 -translate-x-1/2">{centerNode}</div>
      <div className="flex min-w-0 flex-1 items-center justify-end gap-1.5">{rightNode}</div>
    </header>
  )
}

export function LegalNoticeBanner({ onDismiss }) {
  return (
    <div className="fade-rise rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel)] px-4 py-3.5 shadow-[var(--shadow-1)]">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[color:var(--warning-soft)]">
          <AlertTriangle size={14} className="text-[color:var(--warning)]" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-[color:var(--ink)]">Aviso importante</p>
          <p className="mt-1 text-[13px] leading-relaxed text-[color:var(--ink-soft)]">
            Este sistema oferece apoio informativo baseado na legislação angolana e não substitui aconselhamento jurídico profissional.
          </p>
        </div>
        {onDismiss ? (
          <button
            type="button"
            onClick={onDismiss}
            className="shrink-0 rounded-[var(--radius-sm)] border border-[color:var(--stroke)] bg-[color:var(--panel-muted)] px-3 py-1.5 text-xs font-medium text-[color:var(--ink)] transition-all hover:bg-[color:var(--accent)] hover:text-white hover:border-[color:var(--accent)]"
          >
            Entendi
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function ShellActionButtons() {
  return null
}
