import { useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Eye,
  FileText,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  MessageSquarePlus,
  X,
  Upload,
} from 'lucide-react'
import {
  activateDocumentInChat,
  deleteDocument,
  fetchDocumentPreview,
  renameDocument,
  reprocessDocument,
  triggerIngestion,
  uploadPdfDocument,
} from '../../shared/services/apiClient'
import { ActionButton } from '../../shared/ui/ActionButton'
import { LoadingState } from '../../shared/ui/LoadingState'
import { ErrorBanner } from '../../shared/ui/ErrorBanner'
import { SurfaceCard } from '../../shared/ui/SurfaceCard'
import { InfoTooltip } from '../../shared/ui/InfoTooltip'
import { formatHumanTimestamp } from '../../shared/utils/format'
import { formatArticleLabel, normalizeDisplayText } from '../../shared/utils/markdown'
import { MAX_PDF_UPLOAD_BYTES, MAX_PDF_UPLOAD_MB } from '../../shared/constants/app'

function formatBytes(bytes) {
  if (!bytes) return '0 KB'
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${Math.round(bytes / 1024)} KB`
}

function qualityBadge(status) {
  const config = {
    good: { label: 'Boa qualidade', cls: 'text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-300 dark:bg-emerald-950/40 dark:border-emerald-800/50' },
    ocr: { label: 'OCR', cls: 'text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-300 dark:bg-amber-950/40 dark:border-amber-800/50' },
    ocr_pending: { label: 'OCR pendente', cls: 'text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-300 dark:bg-amber-950/40 dark:border-amber-800/50' },
    ocr_processing: { label: 'OCR em curso', cls: 'text-sky-700 bg-sky-50 border-sky-200 dark:text-sky-300 dark:bg-sky-950/40 dark:border-sky-800/50' },
    ocr_failed: { label: 'OCR falhou', cls: 'text-rose-700 bg-rose-50 border-rose-200 dark:text-rose-300 dark:bg-rose-950/40 dark:border-rose-800/50' },
    partial: { label: 'Parcial', cls: 'text-orange-700 bg-orange-50 border-orange-200 dark:text-orange-300 dark:bg-orange-950/40 dark:border-orange-800/50' },
    empty: { label: 'Baixo texto', cls: 'text-rose-700 bg-rose-50 border-rose-200 dark:text-rose-300 dark:bg-rose-950/40 dark:border-rose-800/50' },
  }
  return config[status] || { label: status || 'N/D', cls: 'text-[color:var(--ink-soft)] bg-[color:var(--panel-muted)] border-[color:var(--stroke)]' }
}

function documentStatusText(document) {
  if (document.status === 'ocr_pending') return 'OCR em fila'
  if (document.status === 'ocr_processing') return 'OCR em processamento'
  if (document.status === 'ocr_ready') return 'Preparado por OCR'
  if (document.status === 'failed') return 'Falha no processamento'
  return 'Pronto para consulta'
}

export function DocumentsPage({
  documents,
  onAddUploadedDocument,
  onRemoveDocument,
  authToken,
  onRefreshDocuments,
  onUseDocument,
  onStartNewConversation,
  onOpenChatSection,
  onToast,
}) {
  const [ingesting, setIngesting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('all')
  const [preview, setPreview] = useState(null)
  const [busyId, setBusyId] = useState('')
  const [ocrModal, setOcrModal] = useState(null)
  const fileInputRef = useRef(null)
  const previewRef = useRef(null)

  const categories = useMemo(() => ['all', ...new Set(documents.map((item) => item.category).filter(Boolean))], [documents])

  const filteredDocuments = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return documents.filter((document) => {
      const matchesSearch =
        !needle ||
        [document.display_name, document.filename, document.summary, document.category]
          .filter(Boolean)
          .some((value) => value.toLowerCase().includes(needle))
      const matchesCategory = selectedCategory === 'all' || document.category === selectedCategory
      return matchesSearch && matchesCategory
    })
  }, [documents, search, selectedCategory])

  const runIngestion = async () => {
    if (ingesting) return
    setIngesting(true)
    try {
      const result = await triggerIngestion(authToken)
      onToast?.({ message: `Ingestão concluída com ${result.total_chunks} chunks em ${result.processed_files.length} ficheiros.`, type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro na ingestão dos documentos.', type: 'error' })
    } finally { setIngesting(false) }
  }

  const handleManualUpload = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.type !== 'application/pdf') {
      onToast?.({ message: 'Apenas ficheiros PDF são suportados neste momento.', type: 'error' })
      return
    }
    if (file.size > MAX_PDF_UPLOAD_BYTES) {
      onToast?.({ message: `O PDF excede o limite máximo de ${MAX_PDF_UPLOAD_MB} MB.`, type: 'error' })
      return
    }
    setUploading(true)
    try {
      const uploaded = await uploadPdfDocument(file, authToken)
      onAddUploadedDocument?.(uploaded)
      try { await onRefreshDocuments?.() } catch { /* keep optimistic */ }
      if (uploaded.status === 'ocr_pending' || uploaded.status === 'ocr_processing') {
        setOcrModal(uploaded)
        onToast?.({ message: `${uploaded.filename} recebido. OCR em segundo plano pode demorar alguns minutos.`, type: 'success' })
      } else {
        onToast?.({ message: `${uploaded.filename} pronto para consulta.`, type: 'success' })
      }
    } catch (error) {
      onToast?.({ message: error.message || 'Falha ao processar o PDF.', type: 'error' })
    } finally { setUploading(false) }
  }

  const handlePreview = async (documentId) => {
    setBusyId(documentId)
    try {
      const payload = await fetchDocumentPreview(documentId, authToken)
      setPreview(payload)
      window.setTimeout(() => { previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }, 50)
    } catch (error) {
      onToast?.({ message: error.message || 'Falha ao carregar pré-visualização.', type: 'error' })
    } finally { setBusyId('') }
  }

  const handleRename = async (document) => {
    const nextName = window.prompt('Novo nome:', document.display_name || document.filename)
    if (!nextName || nextName.trim() === document.display_name) return
    setBusyId(document.id)
    try {
      await renameDocument(document.id, nextName.trim(), authToken)
      await onRefreshDocuments?.()
    } catch (error) {
      onToast?.({ message: error.message || 'Falha ao renomear.', type: 'error' })
    } finally { setBusyId('') }
  }

  const handleDelete = async (document) => {
    if (!window.confirm(`Eliminar "${document.display_name}"?`)) return
    setBusyId(document.id)
    try {
      await deleteDocument(document.id, authToken)
      onRemoveDocument?.(document.id)
      try { await onRefreshDocuments?.() } catch { /* keep optimistic */ }
      if (preview?.document?.id === document.id) setPreview(null)
    } catch (error) {
      onToast?.({ message: error.message || 'Falha ao eliminar.', type: 'error' })
    } finally { setBusyId('') }
  }

  const handleUseInChat = async (document) => {
    setBusyId(document.id)
    try {
      await activateDocumentInChat(document.id, null, authToken)
      onStartNewConversation?.()
      onOpenChatSection?.()
      onUseDocument?.(null, document.id)
      await onRefreshDocuments?.()
    } catch (error) {
      onToast?.({ message: error.message || 'Falha ao ativar documento.', type: 'error' })
    } finally { setBusyId('') }
  }

  const handleReprocess = async (document) => {
    setBusyId(document.id)
    try {
      await reprocessDocument(document.id, authToken)
      await onRefreshDocuments?.()
      onToast?.({ message: `${document.display_name} reprocessado.`, type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Falha ao reprocessar.', type: 'error' })
    } finally { setBusyId('') }
  }

  return (
    <section className="fade-rise space-y-5 py-2">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)] sm:text-3xl">Meus Documentos</h2>
            <InfoTooltip content="Carregue PDFs pessoais (contratos, procurações, peças, legislação de apoio). O texto pesquisável fica preparado rapidamente para consulta no chat; PDFs digitalizados podem exigir OCR." />
          </div>
          <p className="mt-1.5 text-sm text-[color:var(--ink-soft)]">Carregar, organizar e activar o contexto no chat.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ActionButton variant="outline" onClick={() => fileInputRef.current?.click()} disabled={uploading || ingesting}>
            <Upload size={15} /> Enviar PDF
          </ActionButton>
          <ActionButton onClick={runIngestion} disabled={ingesting || uploading}>
            <RefreshCw size={15} /> Processar legislação
          </ActionButton>
          <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={handleManualUpload} />
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SurfaceCard className="p-3.5">
          <div className="text-[10px] font-medium uppercase tracking-widest text-[color:var(--ink-soft)]">Total</div>
          <div className="mt-1 font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)]">{documents.length}</div>
        </SurfaceCard>
        <SurfaceCard className="p-3.5">
          <div className="text-[10px] font-medium uppercase tracking-widest text-[color:var(--ink-soft)]">Uso em chats</div>
          <div className="mt-1 font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)]">{documents.reduce((acc, item) => acc + (item.usage_count || 0), 0)}</div>
        </SurfaceCard>
        <SurfaceCard className="p-3.5">
          <div className="text-[10px] font-medium uppercase tracking-widest text-[color:var(--ink-soft)]">OCR/Parcial</div>
          <div className="mt-1 font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)]">{documents.filter((i) => i.quality_status === 'ocr' || i.quality_status === 'partial').length}</div>
        </SurfaceCard>
        <SurfaceCard className="p-3.5">
          <div className="text-[10px] font-medium uppercase tracking-widest text-[color:var(--ink-soft)]">Categorias</div>
          <div className="mt-1 font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)]">{categories.filter((i) => i !== 'all').length}</div>
        </SurfaceCard>
      </div>

      {/* Search + filter */}
      <div className="flex flex-col gap-2 sm:flex-row">
        <label className="flex flex-1 items-center gap-2 rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel)] px-3 py-2.5">
          <Search size={15} className="shrink-0 text-[color:var(--ink-soft)]" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Pesquisar documentos..."
            className="w-full bg-transparent text-sm outline-none placeholder:text-[color:var(--ink-soft)]/60"
          />
        </label>
        <select
          value={selectedCategory}
          onChange={(event) => setSelectedCategory(event.target.value)}
          className="rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel)] px-3 py-2.5 text-sm outline-none sm:w-48"
        >
          {categories.map((cat) => (
            <option key={cat} value={cat}>{cat === 'all' ? 'Todas categorias' : cat}</option>
          ))}
        </select>
      </div>

      {/* Status messages */}
      {ingesting ? <LoadingState label="Ingestão em curso..." /> : null}
      {uploading ? <LoadingState label="A preparar PDF para consulta..." /> : null}

      {/* Document list */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-[color:var(--ink-soft)]">Documentos ({filteredDocuments.length})</h3>
        </div>

        {filteredDocuments.length ? (
          <div className="space-y-2">
            {filteredDocuments.map((document) => {
              const qb = qualityBadge(document.quality_status)
              return (
                <SurfaceCard key={document.id} className="p-4 transition-all hover:shadow-[var(--shadow-2)]">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-[var(--radius-md)] bg-[color:var(--accent-soft)] text-[color:var(--accent)]">
                          <FileText size={16} />
                        </div>
                        <div className="min-w-0 flex-1">
                          <h4 className="truncate text-sm font-semibold text-[color:var(--ink)]">{document.display_name || document.filename}</h4>
                          <div className="mt-1 flex flex-wrap items-center gap-1.5">
                            {document.category ? (
                              <span className="rounded-full bg-[color:var(--panel-muted)] px-2 py-0.5 text-[10px] font-medium text-[color:var(--ink-soft)]">{document.category}</span>
                            ) : null}
                            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${qb.cls}`}>{qb.label}</span>
                          </div>
                          <p className="mt-1.5 text-xs text-[color:var(--ink-soft)]">
                            {formatBytes(document.size_bytes)} · {document.page_count} pag. · {document.chunks_created} páginas preparadas · {documentStatusText(document)}
                          </p>
                          {document.summary ? <p className="mt-2 text-sm leading-relaxed text-[color:var(--ink)]">{document.summary}</p> : null}
                          <div className="mt-2 text-[11px] text-[color:var(--ink-soft)]">
                            {document.usage_count || 0} uso(s) · {formatHumanTimestamp(document.created_at)}
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-1.5 lg:flex-col lg:w-auto">
                      <ActionButton variant="primary" onClick={() => handleUseInChat(document)} disabled={busyId === document.id}>
                        <MessageSquarePlus size={14} /> Usar no chat
                      </ActionButton>
                      <ActionButton variant="subtle" onClick={() => handlePreview(document.id)} disabled={busyId === document.id}>
                        <Eye size={14} /> Ver
                      </ActionButton>
                      <ActionButton variant="subtle" onClick={() => handleRename(document)} disabled={busyId === document.id}>
                        <Pencil size={14} />
                      </ActionButton>
                      <ActionButton variant="subtle" onClick={() => handleReprocess(document)} disabled={busyId === document.id}>
                        <RefreshCw size={14} />
                      </ActionButton>
                      <ActionButton variant="danger" onClick={() => handleDelete(document)} disabled={busyId === document.id}>
                        <Trash2 size={14} />
                      </ActionButton>
                    </div>
                  </div>
                </SurfaceCard>
              )
            })}
          </div>
        ) : (
          <div className="rounded-[var(--radius-md)] border border-dashed border-[color:var(--stroke)] p-8 text-center text-sm text-[color:var(--ink-soft)]">
            Nenhum documento encontrado.
          </div>
        )}
      </div>

      {/* Preview panel */}
      {preview ? (
        <div ref={previewRef}>
          <SurfaceCard className="p-5">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="font-[family-name:var(--font-serif)] text-lg font-semibold text-[color:var(--ink)]">Pre-visualizacao</h3>
              <ActionButton variant="subtle" onClick={() => setPreview(null)}><X size={14} /> Fechar</ActionButton>
            </div>

            <div className="rounded-[var(--radius-md)] bg-[color:var(--panel-muted)] p-4">
              <div className="text-base font-semibold text-[color:var(--ink)]">{normalizeDisplayText(preview.document.display_name || preview.document.filename)}</div>
              {preview.document.preview_text ? <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[color:var(--ink)]">{normalizeDisplayText(preview.document.preview_text)}</p> : null}
            </div>

            {preview.chunks?.length ? (
              <div className="mt-3 space-y-2">
                {preview.chunks.map((chunk, index) => (
                  <article key={`${chunk.page}-${index}`} className="rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-3.5">
                    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[color:var(--ink-soft)]">
                      Pag. {chunk.page || 'N/D'} {chunk.article_number ? `· ${formatArticleLabel(chunk.article_number)}` : ''}
                    </div>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-[color:var(--ink)]">{normalizeDisplayText(chunk.text)}</p>
                  </article>
                ))}
              </div>
            ) : null}
          </SurfaceCard>
        </div>
      ) : null}

      {/* OCR disclaimer modal */}
      {ocrModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={() => setOcrModal(null)}>
          <div className="mx-4 w-full max-w-md rounded-[var(--radius-xl)] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-6 shadow-[var(--shadow-3)] animate-[fadeIn_0.2s_ease-out]" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-start gap-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400">
                <AlertTriangle size={18} />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-[color:var(--ink)]">OCR em segundo plano</h3>
                <p className="mt-1 text-xs leading-relaxed text-[color:var(--ink-soft)]">
                  O PDF <strong>{ocrModal.filename}</strong> parece digitalizado. Como está em Modo Pro, será preparado por reconhecimento ótico de caracteres.
                </p>
              </div>
            </div>
            <div className="space-y-2 rounded-[var(--radius-md)] bg-[color:var(--panel-muted)] p-3.5 text-xs leading-relaxed text-[color:var(--ink-soft)]">
              <p><strong>O que isto significa:</strong> o upload foi aceite, mas o conteúdo textual ainda pode demorar alguns minutos a ficar disponível.</p>
              <p><strong>Atenção:</strong> o OCR pode conter erros de reconhecimento. Verifique citações, datas e artigos antes de usar em contexto jurídico.</p>
              <p>Estado: <span className="font-medium text-amber-600 dark:text-amber-400">{documentStatusText(ocrModal)}</span> · Páginas: {ocrModal.page_count}</p>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setOcrModal(null)}
                className="rounded-[var(--radius-md)] px-3.5 py-2 text-xs font-medium text-[color:var(--ink-soft)] transition-colors hover:bg-[color:var(--panel-muted)]"
              >
                Fechar
              </button>
              <button
                onClick={() => setOcrModal(null)}
                className="rounded-[var(--radius-md)] bg-[color:var(--accent)] px-3.5 py-2 text-xs font-medium text-white transition-all hover:bg-[color:var(--accent-hover)] active:scale-[0.97]"
              >
                Compreendo, continuar
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
