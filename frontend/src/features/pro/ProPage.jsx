import { useEffect, useMemo, useState } from 'react'
import {
  Archive,
  ArrowRight,
  AlertTriangle,
  BriefcaseBusiness,
  CalendarClock,
  CheckCircle2,
  CheckSquare,
  ChevronRight,
  Clock3,
  Download,
  FileText,
  FolderKanban,
  Link2,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  NotebookPen,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  UserRound,
  Users,
} from 'lucide-react'
import {
  archiveProCase,
  archiveProClient,
  createProNote,
  fetchProCase,
  fetchProCases,
  fetchProClients,
  exportProCase,
  fetchProDashboard,
  linkProCaseChat,
  linkProCaseDocument,
  saveProCase,
  saveProClient,
  saveProDeadline,
  saveProTask,
} from '../../shared/services/apiClient'
import { classNames, formatHumanTimestamp } from '../../shared/utils/format'

const EMPTY_CLIENT = {
  client_type: 'individual',
  name: '',
  email: '',
  phone: '',
  identification_number: '',
  address: '',
  notes: '',
  conflict_terms: '',
  status: 'active',
}

const EMPTY_CASE = {
  client_id: '',
  title: '',
  case_number: '',
  court: '',
  opposing_party: '',
  legal_branch: '',
  status: 'open',
  priority: 'normal',
  opened_at: '',
  next_deadline_at: '',
  summary: '',
  metadata: {},
}

const CASE_STATUS = [
  ['open', 'Aberto'],
  ['pending', 'Pendente'],
  ['waiting', 'A aguardar'],
  ['closed', 'Encerrado'],
]

const PRIORITIES = [
  ['normal', 'Normal'],
  ['high', 'Alta'],
  ['urgent', 'Urgente'],
]

const LEGAL_BRANCHES = [
  ['', 'Selecionar área'],
  ['laboral', 'Laboral'],
  ['civil', 'Civil'],
  ['penal', 'Penal'],
  ['familia', 'Família'],
  ['comercial', 'Comercial'],
  ['administrativo', 'Administrativo'],
  ['tributario', 'Tributário'],
  ['constitucional', 'Constitucional'],
]

const STATUS_LABELS = {
  open: 'Aberto',
  pending: 'Pendente',
  waiting: 'A aguardar',
  closed: 'Encerrado',
  archived: 'Arquivado',
  active: 'Ativo',
  inactive: 'Inativo',
  suspended: 'Suspenso',
  done: 'Concluído',
}

const PRIORITY_LABELS = {
  normal: 'Normal',
  high: 'Alta',
  urgent: 'Urgente',
}

const BRANCH_LABELS = Object.fromEntries(LEGAL_BRANCHES.filter(([value]) => value).map(([value, label]) => [value, label]))

function statusLabel(value) {
  return STATUS_LABELS[value] || value || '—'
}

function priorityLabel(value) {
  return PRIORITY_LABELS[value] || value || 'Normal'
}

function branchLabel(value) {
  return BRANCH_LABELS[value] || value || ''
}

function inferCaseBranch(item = {}) {
  const text = [item.title, item.summary, item.opposing_party, item.court, item.case_number].filter(Boolean).join(' ').toLowerCase()
  const tests = [
    ['laboral', ['desped', 'trabalhador', 'contrato de trabalho', 'salário', 'salario', 'empregador', 'lei geral do trabalho']],
    ['penal', ['desacato', 'crime', 'arguido', 'acusado', 'acusação', 'acusacao', 'detido', 'prisão', 'prisao', 'polícia', 'policia', 'policial', 'filmar', 'filmagem', 'gravação', 'gravacao', 'código penal', 'codigo penal']],
    ['familia', ['divórcio', 'divorcio', 'alimentos', 'menor', 'guarda']],
    ['tributario', ['imposto', 'tribut', 'fiscal', 'iva']],
    ['administrativo', ['acto administrativo', 'licença', 'licenca', 'concurso público', 'concurso publico']],
    ['comercial', ['sociedade', 'sócio', 'socio', 'quota', 'assembleia']],
    ['civil', ['contrato civil', 'arrendamento', 'dívida', 'divida', 'responsabilidade civil']],
  ]
  return tests.find(([, terms]) => terms.some((term) => text.includes(term)))?.[0] || item.legal_branch || ''
}

function effectiveBranchLabel(item = {}) {
  return branchLabel(inferCaseBranch(item))
}

function hasBranchMismatch(item = {}) {
  const inferred = inferCaseBranch(item)
  return Boolean(inferred && item.legal_branch && inferred !== item.legal_branch)
}

function eventLabel(value) {
  const labels = {
    professional_profile_activated: 'Perfil profissional ativado',
    client_saved: 'Cliente guardado',
    client_archived: 'Cliente arquivado',
    case_saved: 'Caso guardado',
    case_archived: 'Caso arquivado',
    chat_linked: 'Chat associado',
    document_linked: 'Documento associado',
    task_saved: 'Tarefa guardada',
    deadline_saved: 'Prazo guardado',
    note_created: 'Nota criada',
  }
  return labels[value] || String(value || 'Evento').replaceAll('_', ' ')
}

function toDateTimeLocal(value) {
  if (!value) return ''
  return String(value).slice(0, 16)
}

const PRO_CACHE_TTL_MS = 60_000
const proDashboardCache = new Map()
const proListCache = new Map()
const proCaseCache = new Map()

function cacheKey(authToken, suffix = '') {
  return `${authToken || 'anon'}:${suffix}`
}

function readCache(cache, key) {
  const entry = cache.get(key)
  if (!entry) return null
  if (Date.now() - entry.timestamp > PRO_CACHE_TTL_MS) {
    cache.delete(key)
    return null
  }
  return entry.value
}

function writeCache(cache, key, value) {
  cache.set(key, { value, timestamp: Date.now() })
  return value
}

export default function ProPage({
  authToken,
  currentUser,
  documents = [],
  conversations = [],
  onToast,
  onOpenChat,
  onRefreshAppState,
}) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dashboard, setDashboard] = useState(null)
  const [clients, setClients] = useState([])
  const [cases, setCases] = useState([])
  const [activeView, setActiveView] = useState('overview')
  const [showClientForm, setShowClientForm] = useState(false)
  const [showCaseForm, setShowCaseForm] = useState(false)
  const [editingClientId, setEditingClientId] = useState('')
  const [editingCaseId, setEditingCaseId] = useState('')
  const [clientSearch, setClientSearch] = useState('')
  const [caseSearch, setCaseSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedClientId, setSelectedClientId] = useState('')
  const [clientForm, setClientForm] = useState(EMPTY_CLIENT)
  const [caseForm, setCaseForm] = useState(EMPTY_CASE)
  const [selectedCaseId, setSelectedCaseId] = useState('')
  const [selectedCase, setSelectedCase] = useState(null)
  const [caseTab, setCaseTab] = useState('summary')
  const [taskForm, setTaskForm] = useState({ title: '', description: '', due_at: '', priority: 'normal', status: 'pending' })
  const [deadlineForm, setDeadlineForm] = useState({ title: '', due_at: '', source: '', reminder_days: 3, status: 'open' })
  const [noteBody, setNoteBody] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [chatId, setChatId] = useState('')

  const hasWorkspace = Boolean(dashboard?.workspace?.id)
  const profile = dashboard?.profile || currentUser?.professional_profile
  const totals = dashboard?.totals || {}
  const adminOverview = dashboard?.admin_overview

  const applyCachedProState = () => {
    const dash = readCache(proDashboardCache, cacheKey(authToken, 'dashboard'))
    const lists = readCache(proListCache, cacheKey(authToken, `lists:${clientSearch}:${caseSearch}:${statusFilter}`))
    if (dash) setDashboard(dash)
    if (lists) {
      setClients(lists.clients || [])
      setCases(lists.cases || [])
    }
    if (dash || lists) setLoading(false)
    return Boolean(dash || lists)
  }

  const loadCase = async (caseId, { preferCache = true } = {}) => {
    if (!caseId) return
    setSelectedCaseId(caseId)
    const key = cacheKey(authToken, `case:${caseId}`)
    if (preferCache) {
      const cached = readCache(proCaseCache, key)
      if (cached) {
        setSelectedCase(cached)
        return cached
      }
    }
    try {
      const detail = await fetchProCase(authToken, caseId)
      writeCache(proCaseCache, key, detail)
      setSelectedCase(detail)
      setSelectedCaseId(caseId)
      return detail
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao abrir caso', type: 'error' })
      return null
    }
  }

  const loadAll = async ({ silent = false, force = false } = {}) => {
    if (!silent) {
      const hasCache = !force && applyCachedProState()
      setLoading(!hasCache)
    }
    try {
      const dashKey = cacheKey(authToken, 'dashboard')
      const listKey = cacheKey(authToken, `lists:${clientSearch}:${caseSearch}:${statusFilter}`)
      const cachedDash = !force ? readCache(proDashboardCache, dashKey) : null
      const dash = cachedDash || writeCache(proDashboardCache, dashKey, await fetchProDashboard(authToken))
      setDashboard(dash)
      if (dash?.workspace?.id) {
        const cachedLists = !force ? readCache(proListCache, listKey) : null
        const [clientData, caseData] = cachedLists ? [cachedLists.clientData, cachedLists.caseData] : await Promise.all([
          fetchProClients(authToken, clientSearch),
          fetchProCases(authToken, { search: caseSearch, status: statusFilter }),
        ])
        if (!cachedLists) writeCache(proListCache, listKey, { clientData, caseData, clients: clientData?.items || [], cases: caseData?.items || [] })
        setClients(clientData?.items || [])
        setCases(caseData?.items || [])
        if (selectedCaseId) await loadCase(selectedCaseId, { preferCache: !force })
      }
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao carregar Modo Pro', type: 'error' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAll()
  }, [authToken])

  useEffect(() => {
    if (!clients.length) {
      setSelectedClientId('')
      return
    }
    if (!selectedClientId || !clients.some((client) => client.id === selectedClientId)) {
      setSelectedClientId(clients[0].id)
    }
  }, [clients, selectedClientId])

  useEffect(() => {
    if (!cases.length) {
      setSelectedCaseId('')
      setSelectedCase(null)
      return
    }
    if (!selectedCaseId || !cases.some((item) => item.id === selectedCaseId)) {
      loadCase(cases[0].id)
    }
  }, [cases, selectedCaseId])

  const recentCases = useMemo(() => cases.slice(0, 5), [cases])
  const selectedClient = useMemo(() => clients.find((client) => client.id === selectedClientId) || null, [clients, selectedClientId])
  const selectedClientCases = useMemo(() => {
    if (!selectedClientId) return []
    return cases.filter((item) => item.client_id === selectedClientId)
  }, [cases, selectedClientId])
  const urgentDeadlines = useMemo(() => {
    const deadlines = selectedCase?.deadlines || []
    return deadlines.filter((item) => item.status !== 'done').slice(0, 4)
  }, [selectedCase])
  const clientOptions = useMemo(() => [['', 'Sem cliente associado'], ...clients.map((client) => [client.id, client.name])], [clients])

  const resetClientForm = () => {
    setClientForm(EMPTY_CLIENT)
    setEditingClientId('')
    setShowClientForm(false)
  }

  const resetCaseForm = () => {
    setCaseForm(EMPTY_CASE)
    setEditingCaseId('')
    setShowCaseForm(false)
  }

  const startEditClient = (client) => {
    if (!client) return
    setClientForm({
      client_type: client.client_type || 'individual',
      name: client.name || '',
      email: client.email || '',
      phone: client.phone || '',
      identification_number: client.identification_number || '',
      address: client.address || '',
      notes: client.notes || '',
      conflict_terms: client.conflict_terms || '',
      status: client.status || 'active',
    })
    setEditingClientId(client.id)
    setShowClientForm(true)
  }

  const startEditCase = (item) => {
    if (!item) return
    setCaseForm({
      client_id: item.client_id || '',
      title: item.title || '',
      case_number: item.case_number || '',
      court: item.court || '',
      opposing_party: item.opposing_party || '',
      legal_branch: item.legal_branch || '',
      status: item.status || 'open',
      priority: item.priority || 'normal',
      opened_at: toDateTimeLocal(item.opened_at),
      next_deadline_at: toDateTimeLocal(item.next_deadline_at),
      summary: item.summary || '',
      metadata: item.metadata || {},
    })
    setEditingCaseId(item.id)
    setShowCaseForm(true)
  }

  const submitClient = async (event) => {
    event.preventDefault()
    setSaving(true)
    try {
      await saveProClient(authToken, clientForm, editingClientId || null)
      proListCache.clear()
      proDashboardCache.delete(cacheKey(authToken, 'dashboard'))
      const wasEditing = Boolean(editingClientId)
      resetClientForm()
      await loadAll({ silent: true, force: true })
      onToast?.({ message: wasEditing ? 'Cliente atualizado' : 'Cliente guardado no Modo Pro', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao guardar cliente', type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const submitCase = async (event) => {
    event.preventDefault()
    setSaving(true)
    try {
      const saved = await saveProCase(authToken, { ...caseForm, client_id: caseForm.client_id || null }, editingCaseId || null)
      proListCache.clear()
      if (editingCaseId) proCaseCache.delete(cacheKey(authToken, `case:${editingCaseId}`))
      proDashboardCache.delete(cacheKey(authToken, 'dashboard'))
      const wasEditing = Boolean(editingCaseId)
      resetCaseForm()
      setActiveView('cases')
      await loadAll({ silent: true, force: true })
      await loadCase(saved.id, { preferCache: false })
      onToast?.({ message: wasEditing ? 'Caso atualizado' : 'Caso profissional criado', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao guardar caso', type: 'error' })
    } finally {
      setSaving(false)
    }
  }

  const handleArchiveClient = async (clientId) => {
    if (!confirm('Arquivar este cliente?')) return
    try {
      await archiveProClient(authToken, clientId)
      proListCache.clear()
      proDashboardCache.delete(cacheKey(authToken, 'dashboard'))
      if (selectedClientId === clientId) setSelectedClientId('')
      await loadAll({ silent: true, force: true })
      onToast?.({ message: 'Cliente arquivado', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao arquivar cliente', type: 'error' })
    }
  }

  const handleArchiveCase = async (caseId) => {
    if (!confirm('Arquivar este caso?')) return
    try {
      await archiveProCase(authToken, caseId)
      proListCache.clear()
      proCaseCache.delete(cacheKey(authToken, `case:${caseId}`))
      proDashboardCache.delete(cacheKey(authToken, 'dashboard'))
      setSelectedCase(null)
      setSelectedCaseId('')
      await loadAll({ silent: true, force: true })
      onToast?.({ message: 'Caso arquivado', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao arquivar caso', type: 'error' })
    }
  }

  const createCaseChat = async () => {
    if (!selectedCaseId) return
    try {
      const response = await linkProCaseChat(authToken, selectedCaseId, { title: `Consulta — ${selectedCase?.case?.title || 'Caso'}` })
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await Promise.all([loadCase(selectedCaseId, { preferCache: false }), onRefreshAppState?.()])
      onToast?.({ message: 'Chat criado e associado ao caso', type: 'success' })
      if (response?.chat_id) onOpenChat?.(response.chat_id)
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao criar chat do caso', type: 'error' })
    }
  }

  const linkExistingChat = async () => {
    if (!selectedCaseId || !chatId) return
    try {
      await linkProCaseChat(authToken, selectedCaseId, { chat_id: chatId })
      setChatId('')
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: 'Conversa associada ao caso', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao associar conversa', type: 'error' })
    }
  }

  const linkDocument = async () => {
    if (!selectedCaseId || !documentId) return
    try {
      await linkProCaseDocument(authToken, selectedCaseId, { document_id: documentId })
      setDocumentId('')
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: 'Documento ligado ao caso', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao ligar documento', type: 'error' })
    }
  }

  const submitTask = async (event) => {
    event.preventDefault()
    if (!selectedCaseId) return
    try {
      await saveProTask(authToken, selectedCaseId, taskForm)
      setTaskForm({ title: '', description: '', due_at: '', priority: 'normal', status: 'pending' })
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: 'Tarefa adicionada', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao guardar tarefa', type: 'error' })
    }
  }

  const submitDeadline = async (event) => {
    event.preventDefault()
    if (!selectedCaseId) return
    try {
      await saveProDeadline(authToken, selectedCaseId, deadlineForm)
      setDeadlineForm({ title: '', due_at: '', source: '', reminder_days: 3, status: 'open' })
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: 'Prazo registado', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao guardar prazo', type: 'error' })
    }
  }

  const exportCaseDossier = async () => {
    if (!selectedCaseId || !selectedCase?.case) return
    try {
      const markdown = await exportProCase(authToken, selectedCaseId)
      const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      const safeTitle = (selectedCase.case.title || 'caso').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      anchor.href = url
      anchor.download = `dossie-${safeTitle || selectedCaseId}.md`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      onToast?.({ message: 'Dossiê exportado em Markdown', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao exportar dossiê', type: 'error' })
    }
  }

  const toggleTaskDone = async (task) => {
    if (!selectedCaseId || !task?.id) return
    try {
      await saveProTask(authToken, selectedCaseId, { ...task, status: task.status === 'done' ? 'pending' : 'done' }, task.id)
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: task.status === 'done' ? 'Tarefa reaberta' : 'Tarefa concluída', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao atualizar tarefa', type: 'error' })
    }
  }

  const toggleDeadlineDone = async (deadline) => {
    if (!selectedCaseId || !deadline?.id) return
    try {
      await saveProDeadline(authToken, selectedCaseId, { ...deadline, status: deadline.status === 'done' ? 'open' : 'done' }, deadline.id)
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: deadline.status === 'done' ? 'Prazo reaberto' : 'Prazo concluído', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao atualizar prazo', type: 'error' })
    }
  }

  const submitNote = async (event) => {
    event.preventDefault()
    if (!selectedCaseId || !noteBody.trim()) return
    try {
      await createProNote(authToken, selectedCaseId, { body: noteBody })
      setNoteBody('')
      proCaseCache.delete(cacheKey(authToken, `case:${selectedCaseId}`))
      await loadCase(selectedCaseId, { preferCache: false })
      onToast?.({ message: 'Nota interna guardada', type: 'success' })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao guardar nota', type: 'error' })
    }
  }

  if (loading) {
    return (
      <div className="grid min-h-full place-items-center p-6">
        <div className="w-full max-w-md rounded-[28px] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-6 text-center shadow-[var(--shadow-2)]">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-[color:var(--accent-soft)] text-[color:var(--accent)]">
            <Loader2 size={22} className="animate-spin" />
          </div>
          <h2 className="mt-4 text-base font-semibold text-[color:var(--ink)]">A preparar o escritório digital</h2>
          <p className="mt-1 text-sm text-[color:var(--ink-soft)]">A carregar clientes, casos, prazos e timeline profissional.</p>
        </div>
      </div>
    )
  }

  if (!hasWorkspace) {
    return (
      <div className="mx-auto flex min-h-full w-full max-w-6xl flex-col gap-5 p-3 sm:p-6">
        <ProHero currentUser={currentUser} profile={profile} workspace={dashboard?.workspace} onRefresh={() => loadAll({ force: true })} />
        <section className="rounded-[28px] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-5 shadow-[var(--shadow-1)]">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="flex items-start gap-3">
              <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-[color:var(--accent-soft)] text-[color:var(--accent)]">
                <ShieldCheck size={22} />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-[color:var(--ink)]">Modo Pro disponível para administração</h2>
                <p className="mt-1 max-w-2xl text-sm leading-6 text-[color:var(--ink-soft)]">
                  Ative perfis profissionais no Admin. Por confidencialidade, clientes, casos e notas só aparecem ao profissional dono do workspace.
                </p>
              </div>
            </div>
          </div>
          {adminOverview ? (
            <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Metric label="Perfis ativos" value={adminOverview.active_profiles} icon={<Users size={18} />} tone="blue" />
              <Metric label="Clientes Pro" value={adminOverview.clients} icon={<UserRound size={18} />} tone="green" />
              <Metric label="Casos Pro" value={adminOverview.cases} icon={<BriefcaseBusiness size={18} />} tone="gold" />
              <Metric label="Prazos abertos" value={adminOverview.open_deadlines} icon={<CalendarClock size={18} />} tone="red" />
            </div>
          ) : null}
        </section>
      </div>
    )
  }

  return (
    <div className="mx-auto flex min-h-full w-full max-w-[1520px] flex-col gap-4 p-2 sm:p-4 lg:p-6">
      <ProHero currentUser={currentUser} profile={profile} workspace={dashboard?.workspace} onRefresh={() => loadAll({ force: true })} />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="Clientes ativos" value={totals.clients} icon={<Users size={18} />} tone="blue" />
        <Metric label="Casos em gestão" value={totals.cases} icon={<FolderKanban size={18} />} tone="gold" />
        <Metric label="Tarefas abertas" value={totals.open_tasks} icon={<CheckSquare size={18} />} tone="green" />
        <Metric label="Prazos críticos" value={totals.open_deadlines} icon={<CalendarClock size={18} />} tone="red" />
      </section>

      <WorkspaceNav
        activeView={activeView}
        setActiveView={setActiveView}
        totals={totals}
        onNewClient={() => {
          setActiveView('clients')
          setEditingClientId('')
          setClientForm(EMPTY_CLIENT)
          setShowClientForm(true)
        }}
        onNewCase={() => {
          setActiveView('cases')
          setEditingCaseId('')
          setCaseForm(EMPTY_CASE)
          setShowCaseForm(true)
        }}
      />

      {activeView === 'overview' ? (
        <OverviewWorkspace
          recentCases={recentCases}
          urgentDeadlines={urgentDeadlines}
          selectedCase={selectedCase}
          onOpenCase={(caseId) => {
            setActiveView('cases')
            loadCase(caseId)
          }}
          onCreateChat={createCaseChat}
        />
      ) : null}

      {activeView === 'clients' ? (
        <ClientsWorkspace
          clients={clients}
          selectedClient={selectedClient}
          selectedClientCases={selectedClientCases}
          selectedClientId={selectedClientId}
          onSelectClient={setSelectedClientId}
          clientForm={clientForm}
          setClientForm={setClientForm}
          showClientForm={showClientForm}
          setShowClientForm={setShowClientForm}
          clientSearch={clientSearch}
          setClientSearch={setClientSearch}
          onSearch={() => loadAll({ silent: true })}
          onSubmit={submitClient}
          onCancelForm={resetClientForm}
          onArchive={handleArchiveClient}
          onEditClient={startEditClient}
          onStartCreateClient={() => {
            setEditingClientId('')
            setClientForm(EMPTY_CLIENT)
            setShowClientForm(true)
          }}
          onOpenCase={(caseId) => {
            setActiveView('cases')
            loadCase(caseId)
          }}
          onCreateCaseForClient={(clientId) => {
            setEditingCaseId('')
            setCaseForm({ ...EMPTY_CASE, client_id: clientId })
            setActiveView('cases')
            setShowCaseForm(true)
          }}
          editingClientId={editingClientId}
          saving={saving}
        />
      ) : null}

      {activeView === 'cases' ? (
        <CasesWorkspace
          cases={cases}
          clients={clients}
          clientOptions={clientOptions}
          caseForm={caseForm}
          setCaseForm={setCaseForm}
          showCaseForm={showCaseForm}
          setShowCaseForm={setShowCaseForm}
          editingCaseId={editingCaseId}
          onCancelForm={resetCaseForm}
          caseSearch={caseSearch}
          setCaseSearch={setCaseSearch}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
          onSearch={() => loadAll({ silent: true })}
          onSubmit={submitCase}
          saving={saving}
          selectedCaseId={selectedCaseId}
          selectedCase={selectedCase}
          onOpenCase={loadCase}
          onEditCase={startEditCase}
          caseTab={caseTab}
          setCaseTab={setCaseTab}
          onArchive={handleArchiveCase}
          onCreateChat={createCaseChat}
          onExportCase={exportCaseDossier}
          conversations={conversations}
          chatId={chatId}
          setChatId={setChatId}
          onLinkChat={linkExistingChat}
          documents={documents}
          documentId={documentId}
          setDocumentId={setDocumentId}
          onLinkDocument={linkDocument}
          taskForm={taskForm}
          setTaskForm={setTaskForm}
          onSubmitTask={submitTask}
          onToggleTaskDone={toggleTaskDone}
          deadlineForm={deadlineForm}
          setDeadlineForm={setDeadlineForm}
          onSubmitDeadline={submitDeadline}
          onToggleDeadlineDone={toggleDeadlineDone}
          noteBody={noteBody}
          setNoteBody={setNoteBody}
          onSubmitNote={submitNote}
          onOpenChat={onOpenChat}
        />
      ) : null}
    </div>
  )
}

function ProHero({ currentUser, profile, workspace, onRefresh }) {
  return (
    <header className="overflow-hidden rounded-[30px] border border-[color:var(--stroke)] bg-[radial-gradient(circle_at_top_left,rgba(96,165,250,0.18),transparent_36%),linear-gradient(135deg,var(--panel),var(--bg))] p-4 shadow-[var(--shadow-2)] sm:p-5">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-start gap-4">
          <div className="grid h-14 w-14 shrink-0 place-items-center rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--accent-soft)] text-[color:var(--accent)] shadow-[var(--shadow-1)]">
            <BriefcaseBusiness size={26} />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3 py-1 text-[11px] font-bold uppercase tracking-[0.18em] text-[color:var(--ink-soft)]">Modo Pro</span>
              <span className="rounded-full bg-emerald-500/12 px-3 py-1 text-[11px] font-semibold text-emerald-300">Workspace ativo</span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight text-[color:var(--ink)] sm:text-3xl">Escritório jurídico digital</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[color:var(--ink-soft)]">
              {workspace?.name || profile?.organization_name || profile?.display_name || currentUser?.name || 'Área profissional'} · clientes, processos, documentos, prazos e conversas num só fluxo de trabalho.
            </p>
          </div>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--bg)] px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[color:var(--ink-soft)]">Profissional</p>
            <p className="mt-1 text-sm font-semibold text-[color:var(--ink)]">{profile?.professional_title || 'Perfil profissional'}</p>
          </div>
          <button onClick={onRefresh} className="pro-secondary-btn justify-center">
            <RefreshCw size={15} /> Atualizar
          </button>
        </div>
      </div>
    </header>
  )
}

function WorkspaceNav({ activeView, setActiveView, totals, onNewClient, onNewCase }) {
  const items = [
    { id: 'overview', title: 'Painel', description: 'Resumo executivo', icon: Sparkles, count: totals.cases || 0 },
    { id: 'clients', title: 'Clientes', description: 'Cadastro e conflitos', icon: Users, count: totals.clients || 0 },
    { id: 'cases', title: 'Casos', description: 'Processos e timeline', icon: FolderKanban, count: totals.open_deadlines || 0 },
  ]
  return (
    <section className="rounded-[24px] border border-[color:var(--stroke)] bg-[color:var(--panel)]/95 p-2 shadow-[var(--shadow-1)] backdrop-blur-xl lg:sticky lg:top-2 lg:z-10 lg:rounded-[28px]">
      <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
        <nav className="grid grid-cols-3 gap-1.5 sm:gap-2 xl:flex xl:min-w-0 xl:flex-1">
          {items.map((item) => {
            const Icon = item.icon
            const active = activeView === item.id
            return (
              <button
                key={item.id}
                onClick={() => setActiveView(item.id)}
                className={classNames(
                  'group flex min-w-0 items-center justify-center gap-1.5 rounded-[18px] border px-2 py-2 text-center transition sm:justify-between sm:gap-3 sm:rounded-[22px] sm:px-3 sm:py-3 sm:text-left xl:flex-1',
                  active ? 'border-[color:var(--accent)] bg-[color:var(--accent-soft)] shadow-[var(--shadow-1)]' : 'border-transparent bg-transparent hover:bg-[color:var(--panel-muted)]',
                )}
              >
                <span className="flex min-w-0 flex-col items-center gap-1 sm:flex-row sm:gap-3">
                  <span className={classNames('grid h-9 w-9 shrink-0 place-items-center rounded-2xl sm:h-10 sm:w-10', active ? 'bg-[color:var(--accent)] text-white' : 'bg-[color:var(--bg)] text-[color:var(--accent)]')}>
                    <Icon size={17} />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-semibold text-[color:var(--ink)] sm:text-sm">{item.title}</span>
                    <span className="mt-0.5 hidden truncate text-xs text-[color:var(--ink-soft)] sm:block">{item.description}</span>
                  </span>
                </span>
                <span className="hidden rounded-full bg-[color:var(--bg)] px-2 py-1 text-[11px] font-bold text-[color:var(--ink-soft)] sm:block">{item.count}</span>
              </button>
            )
          })}
        </nav>
        <div className="grid grid-cols-2 gap-2 xl:flex xl:shrink-0">
          <button onClick={onNewClient} className="pro-secondary-btn justify-center">
            <Plus size={15} /> Cliente
          </button>
          <button onClick={onNewCase} className="pro-primary-btn justify-center">
            <Plus size={15} /> Caso
          </button>
        </div>
      </div>
    </section>
  )
}

function OverviewWorkspace({ recentCases, urgentDeadlines, selectedCase, onOpenCase, onCreateChat }) {
  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <Panel title="Casos recentes" icon={<FolderKanban size={17} />} action="Abrir casos importantes e continuar o trabalho">
        <div className="grid gap-3 lg:grid-cols-2">
          {recentCases.map((item) => (
            <CaseCard key={item.id} item={item} active={false} onClick={() => onOpenCase(item.id)} />
          ))}
          {!recentCases.length ? <EmptyState title="Sem casos recentes" text="Crie o primeiro caso profissional para iniciar a gestão." /> : null}
        </div>
      </Panel>
      <aside className="space-y-4">
        <Panel title="Agenda crítica" icon={<CalendarClock size={17} />} compact>
          <List items={urgentDeadlines} empty="Selecione um caso para ver prazos.">
            {(deadline) => <RecordLine title={deadline.title} meta={`${statusLabel(deadline.status)} · ${formatHumanTimestamp(deadline.due_at)}`} tone="warning" />}
          </List>
        </Panel>
        <Panel title="Assistência jurídica do caso" icon={<MessageSquare size={17} />} compact>
          {selectedCase?.case ? (
            <div className="space-y-3">
              <div className="rounded-[22px] border border-[color:var(--accent)]/30 bg-[color:var(--accent-soft)] p-3">
                <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-[color:var(--accent)]">Cliente em destaque</p>
                <p className="mt-1 truncate text-base font-semibold text-[color:var(--ink)]">{selectedCase.case.client_name || 'Sem cliente associado'}</p>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-[color:var(--ink-soft)]">{selectedCase.case.title}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <span className="rounded-full bg-[color:var(--bg)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--accent)]">{effectiveBranchLabel(selectedCase.case) || 'Área não definida'}</span>
                  <span className="rounded-full bg-[color:var(--bg)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--ink-soft)]">{statusLabel(selectedCase.case.status)}</span>
                </div>
              </div>
              <p className="text-sm leading-6 text-[color:var(--ink-soft)]">Crie uma conversa contextualizada para a IA considerar o cliente, área jurídica e resumo do caso.</p>
              <button onClick={onCreateChat} className="pro-primary-btn w-full justify-center">
                <MessageSquarePlus size={16} /> Abrir chat do caso
              </button>
            </div>
          ) : (
            <EmptyState title="Nenhum caso selecionado" text="Abra um caso para criar um chat com contexto profissional." dense />
          )}
        </Panel>
      </aside>
    </section>
  )
}

function ClientsWorkspace({
  clients,
  selectedClient,
  selectedClientCases,
  selectedClientId,
  onSelectClient,
  clientForm,
  setClientForm,
  showClientForm,
  setShowClientForm,
  editingClientId,
  clientSearch,
  setClientSearch,
  onSearch,
  onSubmit,
  onCancelForm,
  onArchive,
  onEditClient,
  onStartCreateClient,
  onOpenCase,
  onCreateCaseForClient,
  saving,
}) {
  return (
    <section className="grid gap-4 xl:grid-cols-[minmax(320px,430px)_minmax(0,1fr)]">
      <Panel title="Carteira de clientes" icon={<Users size={17} />} action={`${clients.length} registos`}>
        <Toolbar>
          <SearchBox value={clientSearch} onChange={setClientSearch} onSearch={onSearch} placeholder="Pesquisar por nome, telefone ou conflito..." />
        </Toolbar>
        <div className="mt-4 max-h-[680px] space-y-3 overflow-y-auto pr-1 custom-scroll">
          {clients.map((client) => (
            <ClientCard
              key={client.id}
              client={client}
              active={selectedClientId === client.id}
              onClick={() => onSelectClient(client.id)}
              onArchive={() => onArchive(client.id)}
            />
          ))}
          {!clients.length ? <EmptyState title="Nenhum cliente encontrado" text="Registe um cliente ou ajuste a pesquisa." /> : null}
        </div>
      </Panel>
      {showClientForm ? (
        <Panel title={editingClientId ? 'Editar cliente' : 'Novo cliente'} icon={<UserRound size={17} />} action="Contactos, conflito e notas internas">
          <form onSubmit={onSubmit} className="space-y-3">
            <Input label="Nome completo / entidade" value={clientForm.name} onChange={(value) => setClientForm({ ...clientForm, name: value })} required placeholder="Ex.: Benvindo Mateus" />
            <div className="grid gap-3 sm:grid-cols-2">
              <Input label="Telefone" value={clientForm.phone} onChange={(value) => setClientForm({ ...clientForm, phone: value })} />
              <Input label="Email" type="email" value={clientForm.email} onChange={(value) => setClientForm({ ...clientForm, email: value })} />
            </div>
            <Input label="Documento / identificação" value={clientForm.identification_number} onChange={(value) => setClientForm({ ...clientForm, identification_number: value })} />
            <Input label="Morada" value={clientForm.address} onChange={(value) => setClientForm({ ...clientForm, address: value })} />
            <Select label="Estado do cliente" value={clientForm.status} onChange={(value) => setClientForm({ ...clientForm, status: value })} options={[['active', 'Ativo'], ['inactive', 'Inativo'], ['suspended', 'Suspenso']]} />
            <Input label="Termos de conflito" value={clientForm.conflict_terms} onChange={(value) => setClientForm({ ...clientForm, conflict_terms: value })} placeholder="Parte contrária, empresa, familiar..." />
            <Textarea label="Notas internas" value={clientForm.notes} onChange={(value) => setClientForm({ ...clientForm, notes: value })} rows={5} />
            <div className="grid gap-2 sm:grid-cols-2">
              <button type="button" onClick={onCancelForm} className="pro-secondary-btn justify-center">Cancelar</button>
              <button disabled={saving} className="pro-primary-btn justify-center disabled:opacity-60">
                <Plus size={16} /> {editingClientId ? 'Guardar alterações' : 'Guardar cliente'}
              </button>
            </div>
          </form>
        </Panel>
      ) : (
        <ClientDetail
          client={selectedClient}
          cases={selectedClientCases}
          onOpenCase={onOpenCase}
          onCreateCase={() => selectedClient && onCreateCaseForClient(selectedClient.id)}
          onEdit={() => onEditClient?.(selectedClient)}
          onStartCreate={onStartCreateClient}
        />
      )}
    </section>
  )
}

function CasesWorkspace(props) {
  return (
    <section className="space-y-4">
      {props.showCaseForm ? (
        <Panel title={props.editingCaseId ? 'Editar caso/processo' : 'Novo caso/processo'} icon={<BriefcaseBusiness size={17} />} action="Cliente, tribunal, partes, área, estado e resumo">
          <form onSubmit={props.onSubmit} className="grid gap-3 lg:grid-cols-4">
            <Input className="lg:col-span-2" label="Título do caso" value={props.caseForm.title} onChange={(value) => props.setCaseForm({ ...props.caseForm, title: value })} required placeholder="Ex.: Despedimento sem processo disciplinar" />
            <Select label="Cliente" value={props.caseForm.client_id} onChange={(value) => props.setCaseForm({ ...props.caseForm, client_id: value })} options={props.clientOptions} />
            <Select label="Área jurídica" value={props.caseForm.legal_branch} onChange={(value) => props.setCaseForm({ ...props.caseForm, legal_branch: value })} options={LEGAL_BRANCHES} />
            <Input label="N.º do processo" value={props.caseForm.case_number} onChange={(value) => props.setCaseForm({ ...props.caseForm, case_number: value })} />
            <Input label="Tribunal/entidade" value={props.caseForm.court} onChange={(value) => props.setCaseForm({ ...props.caseForm, court: value })} />
            <Input label="Parte contrária" value={props.caseForm.opposing_party} onChange={(value) => props.setCaseForm({ ...props.caseForm, opposing_party: value })} />
            <Input label="Próximo prazo" type="datetime-local" value={props.caseForm.next_deadline_at} onChange={(value) => props.setCaseForm({ ...props.caseForm, next_deadline_at: value })} />
            <Select label="Prioridade" value={props.caseForm.priority} onChange={(value) => props.setCaseForm({ ...props.caseForm, priority: value })} options={PRIORITIES} />
            <Select label="Estado" value={props.caseForm.status} onChange={(value) => props.setCaseForm({ ...props.caseForm, status: value })} options={CASE_STATUS} />
            <Textarea className="lg:col-span-4" label="Resumo profissional" value={props.caseForm.summary} onChange={(value) => props.setCaseForm({ ...props.caseForm, summary: value })} rows={3} />
            <div className="grid gap-2 sm:grid-cols-2 lg:col-span-4">
              <button type="button" onClick={props.onCancelForm} className="pro-secondary-btn justify-center">Cancelar</button>
              <button disabled={props.saving} className="pro-primary-btn justify-center disabled:opacity-60">
                <Plus size={16} /> {props.editingCaseId ? 'Guardar alterações' : 'Guardar caso'}
              </button>
            </div>
          </form>
        </Panel>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(320px,430px)_minmax(0,1fr)]">
        <Panel title="Lista de casos" icon={<Search size={17} />} action={`${props.cases.length} casos`}>
          <Toolbar>
            <SearchBox value={props.caseSearch} onChange={props.setCaseSearch} onSearch={props.onSearch} placeholder="Pesquisar caso..." />
            <Select compact label="Estado" value={props.statusFilter} onChange={props.setStatusFilter} options={[['', 'Todos'], ...CASE_STATUS]} />
          </Toolbar>
          <div className="mt-4 max-h-[680px] space-y-3 overflow-y-auto pr-1 custom-scroll">
            {props.cases.map((item) => (
              <CaseCard key={item.id} item={item} active={props.selectedCaseId === item.id} onClick={() => props.onOpenCase(item.id)} />
            ))}
            {!props.cases.length ? <EmptyState title="Nenhum caso encontrado" text="Crie um caso ou ajuste os filtros." dense /> : null}
          </div>
        </Panel>
        <CaseDetail {...props} />
      </div>
    </section>
  )
}

function CaseDetail(props) {
  const detail = props.selectedCase
  if (!detail?.case) {
    return (
      <Panel title="Dossiê do caso" icon={<FileText size={17} />} action="Timeline, prazos, tarefas, documentos e chats">
        <div className="grid min-h-[420px] place-items-center rounded-[24px] border border-dashed border-[color:var(--stroke)] bg-[color:var(--bg)] p-6 text-center">
          <div>
            <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-[color:var(--panel-muted)] text-[color:var(--ink-soft)]">
              <BriefcaseBusiness size={28} />
            </div>
            <h3 className="mt-4 text-base font-semibold text-[color:var(--ink)]">Selecione um caso</h3>
            <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-[color:var(--ink-soft)]">Ao abrir um caso, verá o timeline completo, chats associados, documentos, tarefas, prazos e notas internas.</p>
          </div>
        </div>
      </Panel>
    )
  }

  const item = detail.case
  const client = props.clients?.find((entry) => entry.id === item.client_id)
  const inferredBranch = inferCaseBranch(item)
  const mismatch = hasBranchMismatch(item)
  const [dossierSearch, setDossierSearch] = useState('')
  const tabs = [
    ['summary', 'Resumo', FileText],
    ['chats', 'Chats', MessageSquare],
    ['documents', 'Documentos', FileText],
    ['tasks', 'Tarefas', CheckSquare],
    ['deadlines', 'Prazos', CalendarClock],
    ['notes', 'Notas', NotebookPen],
    ['templates', 'Modelos', FileText],
    ['timeline', 'Timeline', Clock3],
  ]

  return (
    <Panel title="Dossiê do caso" icon={<FileText size={17} />} action={item.case_number || 'Sem número de processo'}>
      <div className="rounded-[24px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap gap-2">
              <StatusPill value={item.priority} />
              <span className="rounded-full bg-[color:var(--panel-muted)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--ink-soft)]">{statusLabel(item.status)}</span>
              {inferredBranch ? <span className="rounded-full bg-[color:var(--accent-soft)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--accent)]">{branchLabel(inferredBranch)}</span> : null}
              {mismatch ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/12 px-2.5 py-1 text-[11px] font-semibold text-amber-300">
                  <AlertTriangle size={12} /> Área registada: {branchLabel(item.legal_branch)}
                </span>
              ) : null}
            </div>
            <h2 className="mt-3 text-xl font-semibold text-[color:var(--ink)]">{item.title}</h2>
            <p className="mt-1 text-sm text-[color:var(--ink-soft)]">{item.opposing_party || 'Parte contrária não definida'}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={props.onCreateChat} className="pro-primary-btn">
              <MessageSquarePlus size={15} /> Chat do caso
            </button>
            <button onClick={props.onExportCase} className="pro-secondary-btn">
              <Download size={15} /> Exportar
            </button>
            <button onClick={() => props.onEditCase?.(item)} className="pro-secondary-btn">
              <Pencil size={15} /> Editar
            </button>
            <button onClick={() => props.onArchive(item.id)} className="pro-danger-btn">
              <Archive size={15} /> Arquivar
            </button>
          </div>
        </div>
      </div>

      <ClientSpotlight client={client} clientName={item.client_name} item={item} />
      <DossierSearch value={dossierSearch} onChange={setDossierSearch} item={item} client={client} detail={detail} onOpenChat={props.onOpenChat} />

      <div className="mt-4 flex gap-2 overflow-x-auto pb-2 custom-scroll">
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => props.setCaseTab(id)}
            className={classNames(
              'inline-flex shrink-0 items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold transition',
              props.caseTab === id ? 'bg-[color:var(--accent)] text-white shadow-[var(--shadow-1)]' : 'bg-[color:var(--bg)] text-[color:var(--ink-soft)] hover:bg-[color:var(--panel-muted)]',
            )}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      <div className="mt-4">
        {props.caseTab === 'summary' ? <SummaryTab item={item} client={client} detail={detail} /> : null}
        {props.caseTab === 'chats' ? <ChatsTab {...props} /> : null}
        {props.caseTab === 'documents' ? <DocumentsTab {...props} /> : null}
        {props.caseTab === 'tasks' ? <TasksTab {...props} /> : null}
        {props.caseTab === 'deadlines' ? <DeadlinesTab {...props} /> : null}
        {props.caseTab === 'notes' ? <NotesTab {...props} /> : null}
        {props.caseTab === 'templates' ? <TemplatesTab item={item} client={client} detail={detail} /> : null}
        {props.caseTab === 'timeline' ? <TimelineTab {...props} /> : null}
      </div>
    </Panel>
  )
}

function DossierSearch({ value, onChange, item, client, detail, onOpenChat }) {
  const query = value.trim().toLowerCase()
  const records = [
    { type: 'Caso', title: item.title, body: [item.summary, item.case_number, item.court, item.opposing_party].filter(Boolean).join(' · ') },
    { type: 'Cliente', title: client?.name || item.client_name, body: [client?.phone, client?.email, client?.identification_number, client?.address, client?.notes, client?.conflict_terms].filter(Boolean).join(' · ') },
    ...(detail.documents || []).map((doc) => ({ type: 'Documento', title: doc.display_name || doc.filename, body: `${doc.status || ''} ${doc.label || ''}` })),
    ...(detail.tasks || []).map((task) => ({ type: 'Tarefa', title: task.title, body: `${statusLabel(task.status)} ${task.priority || ''} ${task.description || ''}` })),
    ...(detail.deadlines || []).map((deadline) => ({ type: 'Prazo', title: deadline.title, body: `${statusLabel(deadline.status)} ${deadline.due_at || ''} ${deadline.source || ''}` })),
    ...(detail.notes || []).map((note) => ({ type: 'Nota', title: note.author_name || 'Nota interna', body: note.body || '' })),
    ...(detail.chats || []).map((chat) => ({ type: 'Chat', title: chat.title, body: `${chat.message_count || 0} mensagens`, chatId: chat.id })),
  ]
  const results = query ? records.filter((record) => `${record.type} ${record.title || ''} ${record.body || ''}`.toLowerCase().includes(query)).slice(0, 8) : []

  return (
    <div className="mt-4 rounded-[24px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-3">
      <SearchBox value={value} onChange={onChange} onSearch={() => {}} placeholder="Pesquisar neste dossiê: prazo, documento, cliente, nota..." />
      {query ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {results.map((record, index) => (
            <button key={`${record.type}-${index}`} onClick={() => record.chatId && onOpenChat?.(record.chatId)} className="rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--panel)] p-3 text-left transition hover:border-[color:var(--accent)]/50">
              <span className="rounded-full bg-[color:var(--accent-soft)] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[color:var(--accent)]">{record.type}</span>
              <p className="mt-2 line-clamp-1 text-sm font-semibold text-[color:var(--ink)]">{record.title || 'Sem título'}</p>
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-[color:var(--ink-soft)]">{record.body || 'Sem detalhes adicionais.'}</p>
            </button>
          ))}
          {!results.length ? <EmptyState title="Nada encontrado no dossiê" text="Tente procurar por outro termo: nome, prazo, documento, parte contrária ou nota." dense /> : null}
        </div>
      ) : null}
    </div>
  )
}


function ClientSpotlight({ client, clientName, item }) {
  const name = client?.name || clientName || 'Sem cliente associado'
  return (
    <div className="mt-4 rounded-[26px] border border-[color:var(--accent)]/35 bg-[linear-gradient(135deg,rgba(96,165,250,0.16),rgba(16,185,129,0.08),transparent)] p-4 shadow-[var(--shadow-1)]">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[color:var(--accent)] text-white shadow-[var(--shadow-1)]">
            <UserRound size={22} />
          </div>
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[color:var(--accent)]">Cliente em destaque</p>
            <h3 className="mt-1 truncate text-2xl font-semibold tracking-tight text-[color:var(--ink)]">{name}</h3>
            <p className="mt-1 text-sm text-[color:var(--ink-soft)]">
              {client?.phone || 'Sem telefone'} · {client?.email || 'Sem email'}
            </p>
          </div>
        </div>
        <div className="grid gap-2 sm:grid-cols-3 md:min-w-[360px]">
          <MiniInfo label="Área efetiva" value={effectiveBranchLabel(item) || '—'} />
          <MiniInfo label="Estado" value={statusLabel(item.status)} />
          <MiniInfo label="Prioridade" value={priorityLabel(item.priority)} />
        </div>
      </div>
      {hasBranchMismatch(item) ? (
        <div className="mt-3 flex gap-2 rounded-2xl border border-amber-500/20 bg-amber-500/10 p-3 text-sm leading-6 text-amber-100">
          <AlertTriangle size={17} className="mt-0.5 shrink-0 text-amber-300" />
          <p>
            Os factos sugerem <strong>{effectiveBranchLabel(item)}</strong>, mas o caso está registado como <strong>{branchLabel(item.legal_branch)}</strong>. Edite a área para manter pesquisa, estatísticas e estratégia alinhadas.
          </p>
        </div>
      ) : null}
    </div>
  )
}

function buildAngolaCaseChecklist(item = {}, client = {}, detail = {}) {
  const branch = inferCaseBranch(item)
  const documents = detail.documents || []
  const tasks = detail.tasks || []
  const deadlines = detail.deadlines || []
  const notes = detail.notes || []
  const chats = detail.chats || []
  const gaps = []
  if (!client?.name && !item.client_name) gaps.push('Associar cliente ao caso')
  if (!client?.phone && !client?.email) gaps.push('Registar contacto do cliente')
  if (!item.summary) gaps.push('Preencher resumo factual objectivo')
  if (!item.opposing_party) gaps.push('Identificar contraparte/entidade')
  if (!documents.length) gaps.push('Ligar documentos de prova ao caso')
  if (!deadlines.length && !item.next_deadline_at) gaps.push('Registar prazo crítico')
  if (!tasks.length) gaps.push('Criar tarefas de seguimento')
  if (hasBranchMismatch(item)) gaps.push('Corrigir área jurídica do caso')

  const branchActions = {
    laboral: [
      'Confirmar contrato, recibos, comunicação de despedimento e processo disciplinar.',
      'Controlar prazo de impugnação e preparar cálculo de créditos/indemnização.',
      'Separar prova documental, testemunhal e cronologia laboral.',
    ],
    penal: [
      'Recolher auto de notícia, notificação, termo de identidade/residência e despacho aplicável.',
      'Separar tipicidade, prova, garantias processuais e linha de defesa.',
      'Preservar vídeos, mensagens, testemunhas e cadeia de custódia.',
    ],
    familia: [
      'Confirmar certidões, filiação, residência habitual, necessidades do menor e rendimentos.',
      'Preparar pedido com foco no interesse superior da criança e prova documental.',
      'Controlar notificações e eventuais medidas provisórias.',
    ],
    civil: [
      'Confirmar contrato, incumprimento, dano, nexo causal e prova de interpelação.',
      'Organizar documentos por data e preparar estratégia de cobrança/responsabilidade.',
      'Avaliar mediação, negociação ou acção judicial conforme risco e prova.',
    ],
    administrativo: [
      'Confirmar acto administrativo, data de notificação e autoridade competente.',
      'Controlar reclamação, recurso hierárquico ou impugnação contenciosa.',
      'Reunir requerimentos, comprovativos e resposta da Administração.',
    ],
    tributario: [
      'Confirmar liquidação, notificação fiscal, prazos e documentos contabilísticos.',
      'Separar matéria de facto, fundamento legal e via de reclamação/recurso.',
      'Validar cálculos antes de qualquer submissão formal.',
    ],
    comercial: [
      'Confirmar estatutos, actas, contratos, poderes de representação e comunicações.',
      'Mapear obrigações, incumprimentos, garantias e riscos societários.',
      'Preparar minuta ou notificação com base nos documentos ligados.',
    ],
  }
  const actions = branchActions[branch] || [
    'Completar factos, documentos, datas e partes antes de pedir estratégia à IA.',
    'Associar um chat do caso para respostas com contexto profissional.',
    'Validar artigos e prazos antes de protocolar qualquer peça.',
  ]

  const readinessItems = [
    Boolean(client?.name || item.client_name),
    Boolean(item.summary),
    Boolean(item.opposing_party),
    Boolean(documents.length),
    Boolean(deadlines.length || item.next_deadline_at),
    Boolean(chats.length),
  ]
  const readiness = Math.round((readinessItems.filter(Boolean).length / readinessItems.length) * 100)

  const prompts = [
    `Faça uma leitura profissional do caso de ${client?.name || item.client_name || 'este cliente'} e indique riscos, prova em falta e próximos actos em Angola.`,
    `Monte uma estratégia jurídica para ${client?.name || item.client_name || 'o cliente'} com base no resumo, documentos, prazos e legislação angolana aplicável.`,
    `Crie uma checklist documental e de diligências para este caso, separando o que é urgente do que pode aguardar.`,
  ]

  return { branch, documents, tasks, deadlines, notes, chats, gaps, actions, readiness, prompts }
}

function SummaryTab({ item, client, detail = {} }) {
  const inferredBranch = inferCaseBranch(item)
  const intelligence = buildAngolaCaseChecklist(item, client, detail)
  return (
    <div className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-2">
        <Info label="Cliente" value={client?.name || item.client_name || 'Sem cliente associado'} />
        <Info label="Área jurídica efetiva" value={branchLabel(inferredBranch) || '—'} />
        <Info label="N.º do processo" value={item.case_number || '—'} />
        <Info label="Tribunal/entidade" value={item.court || '—'} />
        <Info label="Parte contrária" value={item.opposing_party || '—'} />
        <Info label="Próximo prazo" value={item.next_deadline_at ? formatHumanTimestamp(item.next_deadline_at) : '—'} />
        {hasBranchMismatch(item) ? <Info className="lg:col-span-2" label="Atenção sobre classificação" value={`Registado como ${branchLabel(item.legal_branch)}, mas os factos indicam ${branchLabel(inferredBranch)}. Corrija a área jurídica para melhorar retrieval, relatórios e respostas da IA.`} /> : null}
        <Info className="lg:col-span-2" label="Resumo profissional" value={item.summary || 'Sem resumo registado.'} />
      </div>

      <div className="rounded-[24px] border border-[color:var(--accent)]/35 bg-[linear-gradient(135deg,rgba(96,165,250,0.14),rgba(15,23,42,0.08))] p-4 shadow-[var(--shadow-1)]">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[color:var(--accent)]">Dossiê inteligente Angola</p>
            <h3 className="mt-1 text-lg font-semibold text-[color:var(--ink)]">Prontidão profissional: {intelligence.readiness}%</h3>
            <p className="mt-1 text-sm leading-6 text-[color:var(--ink-soft)]">Leitura rápida de completude, prova, prazos e próximos actos para trabalho de escritório jurídico.</p>
          </div>
          <span className={classNames(
            'rounded-full px-3 py-1 text-xs font-bold',
            intelligence.readiness >= 75 ? 'bg-emerald-500/12 text-emerald-300' : intelligence.readiness >= 45 ? 'bg-amber-500/12 text-amber-300' : 'bg-red-500/12 text-red-300',
          )}>
            {intelligence.readiness >= 75 ? 'Bom para análise' : intelligence.readiness >= 45 ? 'Precisa completar' : 'Dossiê frágil'}
          </span>
        </div>
        <div className="mt-4 grid gap-3 xl:grid-cols-3">
          <InsightBlock title="Próximos actos" icon={<ShieldCheck size={16} />} items={intelligence.actions} />
          <InsightBlock title="Lacunas críticas" icon={<AlertTriangle size={16} />} items={intelligence.gaps.length ? intelligence.gaps : ['Sem lacunas críticas no dossiê mínimo.']} tone={intelligence.gaps.length ? 'warning' : 'success'} />
          <InsightBlock title="Prompts profissionais" icon={<Sparkles size={16} />} items={intelligence.prompts} />
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-5">
          <MiniInfo label="Documentos" value={intelligence.documents.length} />
          <MiniInfo label="Tarefas" value={intelligence.tasks.length} />
          <MiniInfo label="Prazos" value={intelligence.deadlines.length} />
          <MiniInfo label="Notas" value={intelligence.notes.length} />
          <MiniInfo label="Chats" value={intelligence.chats.length} />
        </div>
      </div>
    </div>
  )
}

function InsightBlock({ title, icon, items = [], tone = 'default' }) {
  const tones = {
    default: 'border-[color:var(--stroke)] bg-[color:var(--bg)] text-[color:var(--accent)]',
    warning: 'border-amber-500/25 bg-amber-500/10 text-amber-300',
    success: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-300',
  }
  return (
    <div className={classNames('rounded-[20px] border p-4', tones[tone])}>
      <div className="flex items-center gap-2 text-sm font-semibold text-[color:var(--ink)]">
        <span className={tone === 'default' ? 'text-[color:var(--accent)]' : ''}>{icon}</span>
        {title}
      </div>
      <ul className="mt-3 space-y-2">
        {items.slice(0, 4).map((item, idx) => (
          <li key={`${title}-${idx}`} className="flex gap-2 text-xs leading-5 text-[color:var(--ink-soft)]">
            <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-70" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ChatsTab({ selectedCase, conversations, chatId, setChatId, onLinkChat, onCreateChat, onOpenChat }) {
  return (
    <div className="space-y-4">
      <div className="rounded-[22px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
          <Select label="Associar conversa existente" value={chatId} onChange={setChatId} options={[['', 'Selecionar conversa'], ...conversations.map((chat) => [chat.id, chat.title || 'Conversa sem título'])]} />
          <button onClick={onLinkChat} className="pro-secondary-btn self-end justify-center">
            <Link2 size={15} /> Associar
          </button>
          <button onClick={onCreateChat} className="pro-primary-btn self-end justify-center">
            <MessageSquarePlus size={15} /> Novo chat
          </button>
        </div>
      </div>
      <List items={selectedCase.chats} empty="Nenhum chat associado ao caso.">
        {(chat) => (
          <button onClick={() => onOpenChat?.(chat.id)} className="group w-full rounded-[20px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4 text-left transition hover:bg-[color:var(--panel-muted)]">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-[color:var(--ink)]">{chat.title}</p>
                <p className="mt-1 text-xs text-[color:var(--ink-soft)]">{chat.message_count || 0} mensagens · associado {formatHumanTimestamp(chat.linked_at)}</p>
              </div>
              <ArrowRight size={16} className="shrink-0 text-[color:var(--ink-soft)] transition group-hover:translate-x-0.5 group-hover:text-[color:var(--accent)]" />
            </div>
          </button>
        )}
      </List>
    </div>
  )
}

function DocumentsTab({ selectedCase, documents, documentId, setDocumentId, onLinkDocument }) {
  return (
    <div className="space-y-4">
      <div className="rounded-[22px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
          <Select label="Ligar documento do utilizador" value={documentId} onChange={setDocumentId} options={[['', 'Selecionar documento'], ...documents.map((doc) => [doc.id, doc.display_name || doc.filename])]} />
          <button onClick={onLinkDocument} className="pro-primary-btn self-end justify-center">
            <Link2 size={15} /> Ligar
          </button>
        </div>
      </div>
      <List items={selectedCase.documents} empty="Nenhum documento ligado ao caso.">
        {(doc) => <RecordLine title={doc.display_name || doc.filename} meta={`${doc.status || 'documento'} · ligado ${formatHumanTimestamp(doc.linked_at)}`} icon={<FileText size={15} />} />}
      </List>
    </div>
  )
}

function TasksTab({ selectedCase, taskForm, setTaskForm, onSubmitTask, onToggleTaskDone }) {
  return (
    <div className="space-y-4">
      <form onSubmit={onSubmitTask} className="rounded-[22px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_140px_auto]">
          <Input label="Nova tarefa" value={taskForm.title} onChange={(value) => setTaskForm({ ...taskForm, title: value })} required />
          <Input label="Data" type="datetime-local" value={taskForm.due_at} onChange={(value) => setTaskForm({ ...taskForm, due_at: value })} />
          <Select label="Prioridade" value={taskForm.priority} onChange={(value) => setTaskForm({ ...taskForm, priority: value })} options={PRIORITIES} />
          <button className="pro-primary-btn self-end justify-center">
            <Plus size={15} /> Adicionar
          </button>
        </div>
      </form>
      <List items={selectedCase.tasks} empty="Nenhuma tarefa registada.">
        {(task) => (
          <RecordLine
            title={task.title}
            meta={`${statusLabel(task.status)} · ${task.due_at ? formatHumanTimestamp(task.due_at) : 'sem data'}`}
            icon={<CheckSquare size={15} />}
            tone={task.status === 'done' ? 'success' : task.priority === 'urgent' ? 'danger' : task.priority === 'high' ? 'warning' : 'default'}
            actionLabel={task.status === 'done' ? 'Reabrir' : 'Concluir'}
            onAction={() => onToggleTaskDone?.(task)}
          />
        )}
      </List>
    </div>
  )
}

function DeadlinesTab({ selectedCase, deadlineForm, setDeadlineForm, onSubmitDeadline, onToggleDeadlineDone }) {
  return (
    <div className="space-y-4">
      <form onSubmit={onSubmitDeadline} className="rounded-[22px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-3">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_190px_150px_auto]">
          <Input label="Novo prazo" value={deadlineForm.title} onChange={(value) => setDeadlineForm({ ...deadlineForm, title: value })} required />
          <Input label="Data limite" type="datetime-local" value={deadlineForm.due_at} onChange={(value) => setDeadlineForm({ ...deadlineForm, due_at: value })} required />
          <Input label="Fonte" value={deadlineForm.source} onChange={(value) => setDeadlineForm({ ...deadlineForm, source: value })} />
          <button className="pro-primary-btn self-end justify-center">
            <Plus size={15} /> Registar
          </button>
        </div>
      </form>
      <List items={selectedCase.deadlines} empty="Nenhum prazo registado.">
        {(deadline) => (
          <RecordLine
            title={deadline.title}
            meta={`${statusLabel(deadline.status)} · ${formatHumanTimestamp(deadline.due_at)}${deadline.source ? ` · ${deadline.source}` : ''}`}
            icon={<CalendarClock size={15} />}
            tone={deadline.status === 'done' ? 'success' : 'warning'}
            actionLabel={deadline.status === 'done' ? 'Reabrir' : 'Concluir'}
            onAction={() => onToggleDeadlineDone?.(deadline)}
          />
        )}
      </List>
    </div>
  )
}

function NotesTab({ selectedCase, noteBody, setNoteBody, onSubmitNote }) {
  return (
    <div className="space-y-4">
      <form onSubmit={onSubmitNote} className="rounded-[22px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-3">
        <Textarea label="Nova nota interna" value={noteBody} onChange={setNoteBody} rows={4} />
        <button className="pro-primary-btn mt-3">
          <NotebookPen size={15} /> Guardar nota
        </button>
      </form>
      <List items={selectedCase.notes} empty="Nenhuma nota interna.">
        {(note) => (
          <div className="rounded-[20px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4">
            <p className="whitespace-pre-wrap text-sm leading-6 text-[color:var(--ink)]">{note.body}</p>
            <p className="mt-3 text-xs text-[color:var(--ink-soft)]">{note.author_name || 'Utilizador'} · {formatHumanTimestamp(note.created_at)}</p>
          </div>
        )}
      </List>
    </div>
  )
}

function TemplatesTab({ item, client, detail }) {
  const name = client?.name || item.client_name || 'o cliente'
  const branch = inferCaseBranch(item)
  const docs = detail.documents?.length || 0
  const common = [
    {
      title: 'Parecer breve para decisão do cliente',
      body: `Elabore um parecer breve para ${name}, com factos relevantes, questão jurídica, fundamentos angolanos, riscos, prova em falta e próximos passos.`,
    },
    {
      title: 'Checklist de prova e diligências',
      body: `Crie uma checklist de prova para o caso de ${name}, separando documentos já ligados (${docs}), documentos em falta, testemunhas, prazos e diligências urgentes em Angola.`,
    },
    {
      title: 'Explicação simples para cliente',
      body: `Explique a ${name}, em linguagem simples e sem juridiquês, o estado do caso, riscos e o que ele deve fazer nos próximos dias.`,
    },
  ]
  const byBranch = {
    laboral: ['Minuta de reclamação laboral', 'Cálculo orientativo de créditos laborais', 'Estratégia de impugnação de despedimento'],
    penal: ['Estratégia de defesa penal inicial', 'Perguntas para inquirição de testemunhas', 'Checklist de garantias do arguido'],
    civil: ['Minuta de interpelação extrajudicial', 'Mapa de responsabilidade civil', 'Estratégia de cobrança/indemnização'],
    familia: ['Plano de regulação/guarda/alimentos', 'Checklist de documentos familiares', 'Perguntas para audiência familiar'],
    administrativo: ['Reclamação/recurso administrativo', 'Mapa de prazos administrativos', 'Checklist de acto e notificação'],
    tributario: ['Reclamação fiscal inicial', 'Checklist documental fiscal', 'Mapa de risco tributário'],
    comercial: ['Minuta de notificação comercial', 'Checklist societária/contratual', 'Mapa de obrigações e incumprimentos'],
  }
  const branchItems = (byBranch[branch] || ['Minuta profissional inicial', 'Mapa de riscos jurídicos', 'Plano de diligências']).map((title) => ({
    title,
    body: `Prepare ${title.toLowerCase()} para ${name}, considerando o resumo do caso, documentos ligados, prazos, legislação angolana e limites das fontes recuperadas.`,
  }))
  const items = [...common, ...branchItems]

  const copyPrompt = async (prompt) => {
    try {
      await navigator.clipboard?.writeText(prompt)
    } catch (_) {}
  }

  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {items.map((template) => (
        <article key={template.title} className="rounded-[22px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4">
          <p className="text-sm font-semibold text-[color:var(--ink)]">{template.title}</p>
          <p className="mt-2 min-h-20 text-xs leading-5 text-[color:var(--ink-soft)]">{template.body}</p>
          <button onClick={() => copyPrompt(template.body)} className="pro-secondary-btn mt-4 w-full justify-center">
            <FileText size={15} /> Copiar prompt
          </button>
        </article>
      ))}
    </div>
  )
}


function TimelineTab({ selectedCase }) {
  return (
    <div className="relative space-y-3 before:absolute before:left-5 before:top-2 before:h-[calc(100%-16px)] before:w-px before:bg-[color:var(--stroke)]">
      {selectedCase.timeline?.map((event) => (
        <div key={event.id} className="relative flex gap-3 rounded-[20px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4">
          <span className="z-10 grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-[color:var(--accent-soft)] text-[color:var(--accent)]">
            <Clock3 size={16} />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[color:var(--ink)]">{eventLabel(event.event_type)}</p>
            <p className="mt-1 text-xs text-[color:var(--ink-soft)]">{event.actor_name || 'Sistema'} · {formatHumanTimestamp(event.created_at)}</p>
          </div>
        </div>
      ))}
      {!selectedCase.timeline?.length ? <EmptyState title="Sem eventos" text="As ações do caso aparecerão aqui automaticamente." dense /> : null}
    </div>
  )
}

function ClientDetail({ client, cases, onOpenCase, onCreateCase, onEdit, onStartCreate }) {
  if (!client) {
    return (
      <Panel title="Ficha do cliente" icon={<UserRound size={17} />} action="Dados e casos ligados">
        <div className="grid min-h-[460px] place-items-center rounded-[24px] border border-dashed border-[color:var(--stroke)] bg-[color:var(--bg)] p-6 text-center">
          <div>
            <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-[color:var(--panel-muted)] text-[color:var(--ink-soft)]">
              <UserRound size={28} />
            </div>
            <h3 className="mt-4 text-base font-semibold text-[color:var(--ink)]">Selecione um cliente</h3>
            <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-[color:var(--ink-soft)]">Ao clicar num cliente, verá contactos, conflito, notas e casos associados.</p>
            <button onClick={onStartCreate} className="pro-primary-btn mx-auto mt-5 justify-center">
              <Plus size={15} /> Registar cliente
            </button>
          </div>
        </div>
      </Panel>
    )
  }

  return (
    <Panel title="Ficha do cliente" icon={<UserRound size={17} />} action={statusLabel(client.status)}>
      <div className="rounded-[24px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[color:var(--ink-soft)]">Cliente</p>
            <h2 className="mt-1 text-xl font-semibold text-[color:var(--ink)]">{client.name}</h2>
            <p className="mt-1 text-sm text-[color:var(--ink-soft)]">{client.phone || 'Sem telefone'} · {client.email || 'Sem email'}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={onEdit} className="pro-secondary-btn justify-center">
              <Pencil size={15} /> Editar
            </button>
            <button onClick={onCreateCase} className="pro-primary-btn justify-center">
              <Plus size={15} /> Criar caso
            </button>
          </div>
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <Info label="Documento/ID" value={client.identification_number || '—'} />
        <Info label="Morada" value={client.address || '—'} />
        <Info label="Conflito" value={client.conflict_terms || 'Sem conflito registado.'} />
        <Info label="Notas internas" value={client.notes || 'Sem notas registadas.'} />
      </div>
      <div className="mt-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-[color:var(--ink)]">Casos deste cliente</p>
          <span className="rounded-full bg-[color:var(--panel-muted)] px-2.5 py-1 text-[11px] font-semibold text-[color:var(--ink-soft)]">{cases.length}</span>
        </div>
        <List items={cases} empty="Nenhum caso associado a este cliente.">
          {(item) => <CaseCard item={item} active={false} onClick={() => onOpenCase(item.id)} compact />}
        </List>
      </div>
    </Panel>
  )
}

function ClientCard({ client, active, onClick, onArchive }) {
  return (
    <article className={classNames(
      'rounded-[22px] border p-3 transition',
      active ? 'border-[color:var(--accent)] bg-[color:var(--accent-soft)] shadow-[var(--shadow-1)]' : 'border-[color:var(--stroke)] bg-[color:var(--bg)] hover:border-[color:var(--accent)]/50 hover:bg-[color:var(--panel-muted)]',
    )}>
      <div className="flex items-start justify-between gap-3">
        <button type="button" onClick={onClick} className="flex min-w-0 flex-1 items-start gap-3 text-left">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-[color:var(--accent-soft)] text-[color:var(--accent)]">
            <UserRound size={17} />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-[color:var(--ink)]">{client.name}</h3>
            <p className="mt-1 truncate text-xs text-[color:var(--ink-soft)]">{client.phone || client.email || 'Sem contacto registado'}</p>
          </div>
        </button>
        <button type="button" onClick={onArchive} className="rounded-xl p-2 text-[color:var(--ink-soft)] transition hover:bg-red-500/10 hover:text-red-300" title="Arquivar cliente">
          <Archive size={15} />
        </button>
      </div>
      {client.conflict_terms ? <p className="mt-3 rounded-2xl bg-[color:var(--panel)] px-3 py-2 text-xs leading-5 text-[color:var(--ink-soft)]">Conflito: {client.conflict_terms}</p> : null}
      {client.notes ? <p className="mt-3 line-clamp-2 text-xs leading-5 text-[color:var(--ink-soft)]">{client.notes}</p> : null}
    </article>
  )
}

function CaseCard({ item, active, onClick, compact = false }) {
  const inferredBranch = inferCaseBranch(item)
  const mismatch = hasBranchMismatch(item)
  return (
    <button
      onClick={onClick}
      className={classNames(
        'group w-full rounded-[22px] border text-left transition',
        compact ? 'p-3' : 'p-4',
        active ? 'border-[color:var(--accent)] bg-[color:var(--accent-soft)] shadow-[var(--shadow-1)]' : 'border-[color:var(--stroke)] bg-[color:var(--bg)] hover:border-[color:var(--accent)]/50 hover:bg-[color:var(--panel-muted)]',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap gap-2">
            <StatusPill value={item.priority} />
            {inferredBranch ? <span className="rounded-full bg-[color:var(--panel-muted)] px-2 py-0.5 text-[10px] font-bold tracking-wide text-[color:var(--ink-soft)]">{branchLabel(inferredBranch)}</span> : null}
            {mismatch ? <span className="rounded-full bg-amber-500/12 px-2 py-0.5 text-[10px] font-bold text-amber-300">corrigir área</span> : null}
          </div>
          <h3 className="mt-3 line-clamp-2 text-sm font-semibold leading-5 text-[color:var(--ink)]">{item.title}</h3>
          <p className="mt-1 line-clamp-1 text-xs font-semibold text-[color:var(--accent)]">{item.client_name || 'Sem cliente associado'}</p>
          <p className="mt-0.5 line-clamp-1 text-xs text-[color:var(--ink-soft)]">{statusLabel(item.status)} · {item.opposing_party || 'Parte contrária não definida'}</p>
        </div>
        <ArrowRight size={16} className="mt-1 shrink-0 text-[color:var(--ink-soft)] transition group-hover:translate-x-0.5 group-hover:text-[color:var(--accent)]" />
      </div>
      {item.next_deadline_at ? (
        <p className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-amber-500/12 px-2.5 py-1 text-xs font-semibold text-amber-300">
          <CalendarClock size={13} /> {formatHumanTimestamp(item.next_deadline_at)}
        </p>
      ) : null}
    </button>
  )
}

function Metric({ label, value, icon, tone = 'blue' }) {
  const tones = {
    blue: 'bg-blue-500/12 text-blue-300',
    green: 'bg-emerald-500/12 text-emerald-300',
    gold: 'bg-amber-500/12 text-amber-300',
    red: 'bg-red-500/12 text-red-300',
  }
  return (
    <article className="rounded-[24px] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-4 shadow-[var(--shadow-1)]">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[color:var(--ink-soft)]">{label}</p>
        <span className={classNames('grid h-9 w-9 place-items-center rounded-2xl', tones[tone])}>{icon}</span>
      </div>
      <p className="mt-4 text-3xl font-semibold tracking-tight text-[color:var(--ink)]">{value || 0}</p>
    </article>
  )
}

function Panel({ title, icon, action, compact = false, children }) {
  return (
    <section className={classNames('rounded-[28px] border border-[color:var(--stroke)] bg-[color:var(--panel)] shadow-[var(--shadow-1)]', compact ? 'p-4' : 'p-4 sm:p-5')}>
      <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-[color:var(--ink)]">
          <span className="text-[color:var(--accent)]">{icon}</span>
          {title}
        </div>
        {action ? <p className="text-xs text-[color:var(--ink-soft)]">{action}</p> : null}
      </div>
      {children}
    </section>
  )
}

function Toolbar({ children }) {
  return <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_180px]">{children}</div>
}

function Input({ label, value, onChange, type = 'text', required = false, placeholder = '', className = '' }) {
  return (
    <label className={classNames('block', className)}>
      <span className="text-xs font-semibold text-[color:var(--ink-soft)]">{label}</span>
      <input
        type={type}
        value={value || ''}
        onChange={(event) => onChange(event.target.value)}
        required={required}
        placeholder={placeholder}
        className="mt-1.5 w-full rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3.5 py-3 text-[16px] text-[color:var(--ink)] outline-none transition placeholder:text-[color:var(--ink-soft)]/45 focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-glow)] sm:text-sm"
      />
    </label>
  )
}

function Textarea({ label, value, onChange, rows = 3, className = '' }) {
  return (
    <label className={classNames('block', className)}>
      <span className="text-xs font-semibold text-[color:var(--ink-soft)]">{label}</span>
      <textarea
        value={value || ''}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        className="mt-1.5 w-full resize-none rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3.5 py-3 text-[16px] leading-6 text-[color:var(--ink)] outline-none transition focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-glow)] sm:text-sm"
      />
    </label>
  )
}

function Select({ label, value, onChange, options, compact = false }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-[color:var(--ink-soft)]">{label}</span>
      <select
        value={value || ''}
        onChange={(event) => onChange(event.target.value)}
        className={classNames(
          'mt-1.5 w-full rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3.5 text-[16px] text-[color:var(--ink)] outline-none transition focus:border-[color:var(--accent)] focus:ring-2 focus:ring-[color:var(--accent-glow)] sm:text-sm',
          compact ? 'py-3' : 'py-3',
        )}
      >
        {options.map(([optionValue, labelText]) => <option key={optionValue} value={optionValue}>{labelText}</option>)}
      </select>
    </label>
  )
}

function SearchBox({ value, onChange, onSearch, placeholder }) {
  return (
    <div className="flex gap-2">
      <div className="flex flex-1 items-center gap-2 rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3.5 py-3">
        <Search size={15} className="shrink-0 text-[color:var(--ink-soft)]" />
        <input value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && onSearch()} placeholder={placeholder} className="min-w-0 flex-1 bg-transparent text-[16px] text-[color:var(--ink)] outline-none placeholder:text-[color:var(--ink-soft)]/50 sm:text-sm" />
      </div>
      <button onClick={onSearch} className="rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--panel)] px-4 text-sm font-semibold text-[color:var(--ink-soft)] transition hover:bg-[color:var(--panel-muted)]">OK</button>
    </div>
  )
}

function Info({ label, value, className = '' }) {
  return (
    <div className={classNames('rounded-[20px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4', className)}>
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[color:var(--ink-soft)]">{label}</p>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[color:var(--ink)]">{value}</p>
    </div>
  )
}

function MiniInfo({ label, value }) {
  return (
    <div className="rounded-2xl border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3 py-2">
      <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-[color:var(--ink-soft)]">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-[color:var(--ink)]">{value}</p>
    </div>
  )
}

function List({ items = [], empty, children }) {
  return (
    <div className="space-y-2">
      {items.map((item) => <div key={item.id}>{children(item)}</div>)}
      {!items.length ? <EmptyState title={empty} dense /> : null}
    </div>
  )
}

function RecordLine({ title, meta, icon = <CheckCircle2 size={15} />, tone = 'default', actionLabel = '', onAction }) {
  const tones = {
    default: 'bg-[color:var(--panel-muted)] text-[color:var(--ink-soft)]',
    warning: 'bg-amber-500/12 text-amber-300',
    danger: 'bg-red-500/12 text-red-300',
    success: 'bg-emerald-500/12 text-emerald-300',
  }
  return (
    <div className="flex gap-3 rounded-[20px] border border-[color:var(--stroke)] bg-[color:var(--bg)] p-4">
      <span className={classNames('grid h-9 w-9 shrink-0 place-items-center rounded-2xl', tones[tone])}>{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-[color:var(--ink)]">{title}</p>
        <p className="mt-1 text-xs text-[color:var(--ink-soft)]">{meta}</p>
      </div>
      {actionLabel ? (
        <button type="button" onClick={onAction} className="shrink-0 rounded-xl border border-[color:var(--stroke)] bg-[color:var(--panel)] px-3 py-2 text-xs font-semibold text-[color:var(--ink-soft)] transition hover:border-[color:var(--accent)] hover:text-[color:var(--accent)]">
          {actionLabel}
        </button>
      ) : null}
    </div>
  )
}

function StatusPill({ value }) {
  const urgent = value === 'urgent'
  const high = value === 'high'
  return (
    <span className={classNames(
      'shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide',
      urgent ? 'bg-red-500/15 text-red-300' : high ? 'bg-amber-500/15 text-amber-300' : 'bg-emerald-500/12 text-emerald-300',
    )}>
      {priorityLabel(value)}
    </span>
  )
}

function EmptyState({ title, text = '', dense = false }) {
  return (
    <div className={classNames('rounded-[22px] border border-dashed border-[color:var(--stroke)] bg-[color:var(--bg)] text-center', dense ? 'p-4' : 'p-6')}>
      <p className="text-sm font-semibold text-[color:var(--ink)]">{title}</p>
      {text ? <p className="mx-auto mt-1 max-w-sm text-sm leading-6 text-[color:var(--ink-soft)]">{text}</p> : null}
    </div>
  )
}

function viewTitle(activeView) {
  if (activeView === 'clients') return 'Carteira de clientes e conflitos'
  if (activeView === 'cases') return 'Gestão de casos, prazos e chats'
  return 'Resumo executivo do workspace'
}
