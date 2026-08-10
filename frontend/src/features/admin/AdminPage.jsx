import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BriefcaseBusiness,
  CheckCircle2,
  Clock,
  Copy,
  Database,
  FileText,
  FileUp,
  Filter,
  Gavel,
  Gauge,
  KeyRound,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Shield,
  SlidersHorizontal,
  Trash2,
  Upload,
  UserPlus,
  Users,
  X,
  Zap,
} from 'lucide-react'
import { API_BASE_URL } from '../../shared/constants/app'
import { formatHumanTimestamp } from '../../shared/utils/format'
import LegalMarkdown from '../../shared/ui/LegalMarkdown'

const H = (token) => ({ Authorization: `Bearer ${token}` })
const JH = (token) => ({ Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' })

const DEFAULT_SETTINGS = { usage_limits: { daily_message_limit: 10 } }
const DEFAULT_ANALYTICS = {
  daily_messages: [],
  users_by_role: [],
  documents_by_status: [],
  jurisprudence_by_branch: [],
  top_users_today: [],
}

const ADMIN_FETCH_TIMEOUT_MS = 12000
const ADMIN_CACHE_TTL_MS = 60_000
let adminCache = null

function readAdminCache() {
  if (!adminCache) return null
  if (Date.now() - adminCache.timestamp > ADMIN_CACHE_TTL_MS) {
    adminCache = null
    return null
  }
  return adminCache.value
}

function writeAdminCache(value) {
  adminCache = { value, timestamp: Date.now() }
  return value
}

function clearAdminCache() {
  adminCache = null
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), ADMIN_FETCH_TIMEOUT_MS)
  const response = await fetch(url, { ...options, signal: controller.signal })
    .catch((error) => {
      if (error?.name === 'AbortError') {
        throw new Error('Pedido administrativo excedeu o tempo limite')
      }
      throw error
    })
    .finally(() => window.clearTimeout(timeoutId))
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || 'Pedido falhou')
  }
  return payload
}

function normalizeText(value) {
  return String(value || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
}

function formatNumber(value) {
  const number = Number(value || 0)
  return new Intl.NumberFormat('pt-AO').format(number)
}

export default function AdminPage({ authToken, onToast }) {
  const [stats, setStats] = useState(null)
  const [analytics, setAnalytics] = useState(DEFAULT_ANALYTICS)
  const [users, setUsers] = useState([])
  const [documents, setDocuments] = useState([])
  const [queries, setQueries] = useState([])
  const [conversations, setConversations] = useState([])
  const [juris, setJuris] = useState({ items: [], total: 0, courts: [], branches: [] })
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)
  const [limitDraft, setLimitDraft] = useState('10')
  const [tab, setTab] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [editUser, setEditUser] = useState(null)
  const [newUser, setNewUser] = useState(null)
  const [newJuris, setNewJuris] = useState(null)
  const [selectedConversation, setSelectedConversation] = useState(null)
  const [conversationLoading, setConversationLoading] = useState(false)
  const [uploadFiles, setUploadFiles] = useState([])
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [userSearch, setUserSearch] = useState('')
  const [userRoleFilter, setUserRoleFilter] = useState('all')
  const [documentSearch, setDocumentSearch] = useState('')
  const [documentStatusFilter, setDocumentStatusFilter] = useState('all')
  const [querySearch, setQuerySearch] = useState('')
  const [jurisFilters, setJurisFilters] = useState({ search: '', court: '', branch: '' })
  const [jurisLoading, setJurisLoading] = useState(false)

  const dailyLimit = settings?.usage_limits?.daily_message_limit ?? 10
  const limitDisabled = dailyLimit === 0

  const applyAdminPayload = (payload = {}) => {
    setStats(payload.stats || {})
    setAnalytics({ ...DEFAULT_ANALYTICS, ...(payload.analytics || {}) })
    setUsers(Array.isArray(payload.users) ? payload.users : [])
    setDocuments(Array.isArray(payload.documents) ? payload.documents : [])
    setQueries(Array.isArray(payload.queries) ? payload.queries : [])
    setConversations(Array.isArray(payload.conversations) ? payload.conversations : [])
    setJuris(payload.juris || { items: [], total: 0, courts: [], branches: [] })
    setSettings(payload.settings || DEFAULT_SETTINGS)
    setLimitDraft(String(payload.settings?.usage_limits?.daily_message_limit ?? 10))
  }

  const fetchJurisprudence = async (filters = jurisFilters) => {
    setJurisLoading(true)
    try {
      const params = new URLSearchParams({ limit: '80' })
      if (filters.search) params.set('search', filters.search)
      if (filters.court) params.set('court', filters.court)
      if (filters.branch) params.set('branch', filters.branch)
      const data = await fetchJson(`${API_BASE_URL}/admin/jurisprudence?${params.toString()}`, {
        headers: H(authToken),
      })
      setJuris(data || { items: [], total: 0, courts: [], branches: [] })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao carregar jurisprudência', type: 'error' })
    } finally {
      setJurisLoading(false)
    }
  }

  const fetchAll = async ({ silent = false } = {}) => {
    if (silent) setRefreshing(true)
    else {
      const cached = readAdminCache()
      if (cached) {
        applyAdminPayload(cached)
        setLoading(false)
        setRefreshing(true)
      } else {
        setLoading(true)
      }
    }
    try {
      const requests = await Promise.allSettled([
        fetchJson(`${API_BASE_URL}/admin/stats`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/admin/analytics`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/admin/users`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/docs`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/admin/queries?limit=60`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/admin/conversations?limit=100`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/admin/jurisprudence?limit=80`, { headers: H(authToken) }),
        fetchJson(`${API_BASE_URL}/admin/settings`, { headers: H(authToken) }),
      ])
      const [statsRes, analyticsRes, usersRes, docsRes, queriesRes, conversationsRes, jurisRes, settingsRes] = requests.map((result) => (
        result.status === 'fulfilled' ? result.value : null
      ))
      const payload = {
        stats: statsRes || {},
        analytics: analyticsRes || {},
        users: Array.isArray(usersRes) ? usersRes : [],
        documents: Array.isArray(docsRes?.items) ? docsRes.items : [],
        queries: Array.isArray(queriesRes) ? queriesRes : [],
        conversations: Array.isArray(conversationsRes) ? conversationsRes : [],
        juris: jurisRes || { items: [], total: 0, courts: [], branches: [] },
        settings: settingsRes || DEFAULT_SETTINGS,
      }
      applyAdminPayload(writeAdminCache(payload))
      if (requests.some((result) => result.status === 'rejected')) {
        onToast?.({ message: 'Alguns dados administrativos não carregaram; o painel continua funcional', type: 'error' })
      }
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao carregar dados do painel', type: 'error' })
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    fetchAll()
  }, [])

  const filteredUsers = useMemo(() => {
    const needle = normalizeText(userSearch)
    return users.filter((user) => {
      const hasPro = user.professional_profile?.status === 'active'
      const matchesRole =
        userRoleFilter === 'all' ||
        (userRoleFilter === 'pro' ? hasPro : (user.role || 'user') === userRoleFilter)
      const haystack = normalizeText(`${user.name} ${user.email} ${user.phone}`)
      return matchesRole && (!needle || haystack.includes(needle))
    })
  }, [users, userSearch, userRoleFilter])

  const filteredDocuments = useMemo(() => {
    const needle = normalizeText(documentSearch)
    return documents.filter((document) => {
      const status = document.status || 'unknown'
      const matchesStatus = documentStatusFilter === 'all' || status === documentStatusFilter
      const haystack = normalizeText(`${document.display_name || document.filename} ${document.category} ${document.summary}`)
      return matchesStatus && (!needle || haystack.includes(needle))
    })
  }, [documents, documentSearch, documentStatusFilter])

  const filteredQueries = useMemo(() => {
    const needle = normalizeText(querySearch)
    return queries.filter((query) => {
      if (!needle) return true
      return normalizeText(`${query.question} ${query.answer}`).includes(needle)
    })
  }, [queries, querySearch])

  const filteredConversations = useMemo(() => {
    const needle = normalizeText(querySearch)
    return conversations.filter((conversation) => {
      if (!needle) return true
      return normalizeText(`
        ${conversation.title}
        ${conversation.user?.name}
        ${conversation.user?.email}
        ${conversation.last_question}
        ${conversation.last_answer_preview}
      `).includes(needle)
    })
  }, [conversations, querySearch])

  const adminSummary = useMemo(() => {
    const admins = users.filter((user) => user.role === 'admin').length
    const commonUsers = users.length - admins
    const usedToday = users.reduce((sum, user) => sum + Number(user.messages_used_today || 0), 0)
    const nearLimit = !limitDisabled
      ? users.filter((user) => user.role !== 'admin' && Number(user.messages_used_today || 0) >= Math.max(1, dailyLimit * 0.8)).length
      : 0
    return { admins, commonUsers, usedToday, nearLimit }
  }, [users, dailyLimit, limitDisabled])

  const handleDeleteUser = async (userId) => {
    if (!confirm('Remover este utilizador e respectivos tokens de acesso?')) return
    try {
      await fetchJson(`${API_BASE_URL}/admin/users/${userId}`, { method: 'DELETE', headers: H(authToken) })
      clearAdminCache()
      setUsers((prev) => prev.filter((user) => user.id !== userId))
      onToast?.({ message: 'Utilizador removido', type: 'success' })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao remover utilizador', type: 'error' })
    }
  }

  const handleSaveUser = async () => {
    if (!editUser) return
    try {
      await fetchJson(`${API_BASE_URL}/admin/users/${editUser.id}`, {
        method: 'PUT',
        headers: JH(authToken),
        body: JSON.stringify({
          name: editUser.name,
          email: editUser.email,
          phone: editUser.phone,
          role: editUser.role,
        }),
      })
      if (editUser.password?.trim()) {
        await fetchJson(`${API_BASE_URL}/admin/users/${editUser.id}/password`, {
          method: 'PUT',
          headers: JH(authToken),
          body: JSON.stringify({ password: editUser.password.trim() }),
        })
      }
      clearAdminCache()
      setEditUser(null)
      onToast?.({ message: 'Utilizador actualizado', type: 'success' })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao actualizar utilizador', type: 'error' })
    }
  }

  const handleSetProfessionalProfile = async (user, status) => {
    try {
      await fetchJson(`${API_BASE_URL}/admin/users/${user.id}/professional-profile`, {
        method: 'POST',
        headers: JH(authToken),
        body: JSON.stringify({
          status,
          display_name: user.name || '',
          professional_title: user.professional_profile?.professional_title || 'Profissional jurídico',
          organization_name: user.professional_profile?.organization_name || '',
          license_number: user.professional_profile?.license_number || '',
        }),
      })
      clearAdminCache()
      onToast?.({
        message: status === 'active' ? 'Modo Pro ativado' : 'Modo Pro atualizado',
        type: 'success',
      })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao atualizar Modo Pro', type: 'error' })
    }
  }

  const handleCreateUser = async () => {
    if (!newUser?.name?.trim() || !newUser?.email?.trim() || !newUser?.password?.trim()) {
      onToast?.({ message: 'Preencha nome, email e senha', type: 'error' })
      return
    }
    try {
      await fetchJson(`${API_BASE_URL}/admin/users`, {
        method: 'POST',
        headers: JH(authToken),
        body: JSON.stringify(newUser),
      })
      clearAdminCache()
      setNewUser(null)
      onToast?.({ message: 'Utilizador criado', type: 'success' })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao criar utilizador', type: 'error' })
    }
  }

  const handleDeleteDoc = async (docId) => {
    if (!confirm('Remover documento e todos os chunks indexados?')) return
    try {
      await fetchJson(`${API_BASE_URL}/admin/docs/remove/${docId}`, { method: 'POST', headers: H(authToken) })
      clearAdminCache()
      setDocuments((prev) => prev.filter((document) => document.id !== docId))
      onToast?.({ message: 'Documento removido', type: 'success' })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao remover documento', type: 'error' })
    }
  }

  const handleUploadFiles = async () => {
    if (uploadFiles.length === 0) return
    setUploading(true)
    setUploadProgress(`0/${uploadFiles.length}`)
    let failed = 0
    for (let index = 0; index < uploadFiles.length; index += 1) {
      const formData = new FormData()
      formData.append('file', uploadFiles[index])
      try {
        await fetchJson(`${API_BASE_URL}/docs/upload`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${authToken}` },
          body: formData,
        })
      } catch {
        failed += 1
      }
      setUploadProgress(`${index + 1}/${uploadFiles.length}`)
    }
    setUploading(false)
    setUploadFiles([])
    setUploadProgress('')
    clearAdminCache()
    onToast?.({
      message: failed ? `${failed} ficheiro(s) falharam no upload` : 'Upload concluído',
      type: failed ? 'error' : 'success',
    })
    fetchAll({ silent: true })
  }

  const handleIngest = async () => {
    setIngesting(true)
    try {
      await fetchJson(`${API_BASE_URL}/admin/ingest`, { method: 'POST', headers: H(authToken) })
      clearAdminCache()
      onToast?.({ message: 'Ingestão iniciada em background', type: 'success' })
      setTimeout(() => fetchAll({ silent: true }), 5000)
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao iniciar ingestão', type: 'error' })
    } finally {
      setIngesting(false)
    }
  }

  const handleDeleteJuris = async (id) => {
    if (!confirm('Remover este acórdão da base de jurisprudência?')) return
    try {
      await fetchJson(`${API_BASE_URL}/admin/jurisprudence/${id}`, { method: 'DELETE', headers: H(authToken) })
      clearAdminCache()
      setJuris((prev) => ({ ...prev, items: prev.items.filter((item) => item.id !== id), total: Math.max(0, prev.total - 1) }))
      onToast?.({ message: 'Acórdão removido', type: 'success' })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao remover acórdão', type: 'error' })
    }
  }

  const handleAddJuris = async () => {
    if (!newJuris?.title?.trim() || !newJuris?.court?.trim()) {
      onToast?.({ message: 'Preencha tribunal e título do acórdão', type: 'error' })
      return
    }
    try {
      await fetchJson(`${API_BASE_URL}/admin/jurisprudence`, {
        method: 'POST',
        headers: JH(authToken),
        body: JSON.stringify(newJuris),
      })
      clearAdminCache()
      setNewJuris(null)
      onToast?.({ message: 'Acórdão adicionado', type: 'success' })
      fetchJurisprudence()
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao adicionar acórdão', type: 'error' })
    }
  }

  const handleSaveUsageLimit = async () => {
    const parsed = Number(limitDraft)
    if (!Number.isFinite(parsed) || parsed < 0) {
      onToast?.({ message: 'Introduza um limite válido, igual ou superior a 0', type: 'error' })
      return
    }
    setSavingSettings(true)
    try {
      const payload = await fetchJson(`${API_BASE_URL}/admin/settings`, {
        method: 'PUT',
        headers: JH(authToken),
        body: JSON.stringify({ daily_message_limit: parsed }),
      })
      clearAdminCache()
      setSettings(payload || DEFAULT_SETTINGS)
      setLimitDraft(String(payload?.usage_limits?.daily_message_limit ?? parsed))
      onToast?.({
        message: parsed === 0 ? 'Limite diário desactivado' : `Limite diário actualizado para ${parsed} mensagens`,
        type: 'success',
      })
      fetchAll({ silent: true })
    } catch (error) {
      onToast?.({ message: error.message || 'Erro ao guardar o limite', type: 'error' })
    } finally {
      setSavingSettings(false)
    }
  }

  const copyText = async (text, label = 'Texto copiado') => {
    try {
      await navigator.clipboard.writeText(text)
      onToast?.({ message: label, type: 'success' })
    } catch {
      onToast?.({ message: 'Não foi possível copiar', type: 'error' })
    }
  }

  const openConversation = async (conversation) => {
    if (!conversation?.id) return
    setConversationLoading(true)
    setSelectedConversation({ ...conversation, messages: [] })
    try {
      const detail = await fetchJson(`${API_BASE_URL}/admin/conversations/${conversation.id}`, {
        headers: H(authToken),
      })
      setSelectedConversation(detail)
    } catch (error) {
      setSelectedConversation(null)
      onToast?.({ message: error.message || 'Erro ao abrir timeline da conversa', type: 'error' })
    } finally {
      setConversationLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex items-center gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.03] px-4 py-3 text-sm text-white/55">
          <RefreshCw size={18} className="animate-spin text-[color:var(--accent)]" />
          A carregar painel administrativo...
        </div>
      </div>
    )
  }

  const tabs = [
    { id: 'overview', label: 'Visão Geral', icon: BarChart3 },
    { id: 'limits', label: 'Limites', icon: SlidersHorizontal },
    { id: 'users', label: 'Utilizadores', icon: Users },
    { id: 'documents', label: 'Documentos', icon: FileText },
    { id: 'ingestion', label: 'Ingestão', icon: Upload },
    { id: 'jurisprudence', label: 'Jurisprudência', icon: Gavel },
    { id: 'queries', label: 'Consultas', icon: MessageSquare },
  ]

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[radial-gradient(circle_at_top_left,rgba(59,130,246,0.10),transparent_34rem)]">
      <Header refreshing={refreshing} onRefresh={() => fetchAll({ silent: true })} />
      <TabNav tabs={tabs} activeTab={tab} onChange={setTab} />

      <main className="min-h-0 flex-1 overflow-y-auto custom-scroll px-3 pb-8 pt-4 sm:px-6 lg:px-8">
        {tab === 'overview' && (
          <Overview
            stats={stats}
            analytics={analytics}
            adminSummary={adminSummary}
            dailyLimit={dailyLimit}
            documents={documents}
            queries={queries}
          />
        )}

        {tab === 'limits' && (
          <LimitsPanel
            dailyLimit={dailyLimit}
            limitDraft={limitDraft}
            setLimitDraft={setLimitDraft}
            savingSettings={savingSettings}
            onSave={handleSaveUsageLimit}
            users={users}
            adminSummary={adminSummary}
          />
        )}

        {tab === 'users' && (
          <UsersPanel
            users={filteredUsers}
            totalUsers={users.length}
            dailyLimit={dailyLimit}
            limitDisabled={limitDisabled}
            search={userSearch}
            roleFilter={userRoleFilter}
            setSearch={setUserSearch}
            setRoleFilter={setUserRoleFilter}
            onCreate={() => setNewUser({ name: '', email: '', password: '', phone: '', role: 'user' })}
            onEdit={(user) => setEditUser({ ...user, password: '' })}
            onDelete={handleDeleteUser}
            onSetProfessionalProfile={handleSetProfessionalProfile}
          />
        )}

        {tab === 'documents' && (
          <DocumentsPanel
            documents={filteredDocuments}
            allDocuments={documents}
            search={documentSearch}
            statusFilter={documentStatusFilter}
            setSearch={setDocumentSearch}
            setStatusFilter={setDocumentStatusFilter}
            onDelete={handleDeleteDoc}
          />
        )}

        {tab === 'ingestion' && (
          <IngestionPanel
            stats={stats}
            uploadFiles={uploadFiles}
            setUploadFiles={setUploadFiles}
            uploading={uploading}
            uploadProgress={uploadProgress}
            onUpload={handleUploadFiles}
            ingesting={ingesting}
            onIngest={handleIngest}
          />
        )}

        {tab === 'jurisprudence' && (
          <JurisprudencePanel
            juris={juris}
            filters={jurisFilters}
            setFilters={setJurisFilters}
            loading={jurisLoading}
            onApply={() => fetchJurisprudence(jurisFilters)}
            onClear={() => {
              const clean = { search: '', court: '', branch: '' }
              setJurisFilters(clean)
              fetchJurisprudence(clean)
            }}
            onCreate={() => setNewJuris({ court: 'Tribunal Supremo', case_number: '', title: '', decision_date: '', legal_branch: '', summary: '', url: '' })}
            onDelete={handleDeleteJuris}
          />
        )}

        {tab === 'queries' && (
          <QueriesPanel
            conversations={filteredConversations}
            totalConversations={conversations.length}
            legacyQueries={filteredQueries}
            search={querySearch}
            setSearch={setQuerySearch}
            onCopy={copyText}
            onOpen={openConversation}
          />
        )}
      </main>

      {editUser && (
        <Modal title="Editar Utilizador" onClose={() => setEditUser(null)} onSave={handleSaveUser} saveLabel="Guardar alterações">
          <Field label="Nome" value={editUser.name || ''} onChange={(value) => setEditUser({ ...editUser, name: value })} />
          <Field label="Email" value={editUser.email || ''} onChange={(value) => setEditUser({ ...editUser, email: value })} />
          <Field label="Telefone" value={editUser.phone || ''} onChange={(value) => setEditUser({ ...editUser, phone: value })} />
          <SelectField label="Perfil" value={editUser.role || 'user'} onChange={(value) => setEditUser({ ...editUser, role: value })} options={[['user', 'Utilizador'], ['admin', 'Administrador']]} />
          <Field label="Nova senha (opcional)" value={editUser.password || ''} onChange={(value) => setEditUser({ ...editUser, password: value })} type="password" icon={KeyRound} placeholder="Deixe vazio para manter a actual" />
        </Modal>
      )}

      {newUser && (
        <Modal title="Novo Utilizador" onClose={() => setNewUser(null)} onSave={handleCreateUser} saveLabel="Criar conta">
          <Field label="Nome" value={newUser.name} onChange={(value) => setNewUser({ ...newUser, name: value })} />
          <Field label="Email" value={newUser.email} onChange={(value) => setNewUser({ ...newUser, email: value })} />
          <Field label="Senha inicial" value={newUser.password} onChange={(value) => setNewUser({ ...newUser, password: value })} type="password" icon={KeyRound} />
          <Field label="Telefone" value={newUser.phone || ''} onChange={(value) => setNewUser({ ...newUser, phone: value })} />
          <SelectField label="Perfil" value={newUser.role} onChange={(value) => setNewUser({ ...newUser, role: value })} options={[['user', 'Utilizador'], ['admin', 'Administrador']]} />
        </Modal>
      )}

      {newJuris && (
        <Modal title="Novo Acórdão" onClose={() => setNewJuris(null)} onSave={handleAddJuris} saveLabel="Adicionar">
          <Field label="Tribunal" value={newJuris.court} onChange={(value) => setNewJuris({ ...newJuris, court: value })} />
          <Field label="Título" value={newJuris.title} onChange={(value) => setNewJuris({ ...newJuris, title: value })} />
          <Field label="N.º do processo" value={newJuris.case_number} onChange={(value) => setNewJuris({ ...newJuris, case_number: value })} />
          <Field label="Data da decisão" value={newJuris.decision_date} onChange={(value) => setNewJuris({ ...newJuris, decision_date: value })} />
          <Field label="Ramo jurídico" value={newJuris.legal_branch} onChange={(value) => setNewJuris({ ...newJuris, legal_branch: value })} />
          <TextAreaField label="Sumário" value={newJuris.summary} onChange={(value) => setNewJuris({ ...newJuris, summary: value })} />
          <Field label="URL oficial" value={newJuris.url} onChange={(value) => setNewJuris({ ...newJuris, url: value })} />
        </Modal>
      )}

      {selectedConversation && (
        <ConversationTimelineModal
          conversation={selectedConversation}
          loading={conversationLoading}
          onClose={() => setSelectedConversation(null)}
          onCopy={copyText}
        />
      )}
    </div>
  )
}

function Header({ refreshing, onRefresh }) {
  return (
    <header className="border-b border-white/[0.06] bg-[color:var(--bg)]/80 px-3 py-4 backdrop-blur-xl sm:px-6 lg:px-8">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[color:var(--accent)]/15 ring-1 ring-[color:var(--accent)]/20">
            <Shield size={21} className="text-[color:var(--accent)]" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-white/90">Administração</h1>
              <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">Online</span>
            </div>
            <p className="text-xs text-white/40">Operação, utilizadores, limites, documentos e jurisprudência</p>
          </div>
        </div>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.03] px-4 text-xs font-medium text-white/60 transition hover:bg-white/[0.06] hover:text-white/80 disabled:opacity-60"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          Actualizar painel
        </button>
      </div>
    </header>
  )
}

function TabNav({ tabs, activeTab, onChange }) {
  return (
    <nav className="border-b border-white/[0.06] bg-[color:var(--bg)]/60 px-3 backdrop-blur-xl sm:px-6 lg:px-8">
      <div className="flex gap-1 overflow-x-auto custom-scroll py-2">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={`inline-flex h-10 shrink-0 items-center gap-2 rounded-xl px-3 text-xs font-medium transition ${
              activeTab === tab.id
                ? 'bg-[color:var(--accent)] text-white shadow-lg shadow-[color:var(--accent)]/15'
                : 'text-white/45 hover:bg-white/[0.05] hover:text-white/75'
            }`}
          >
            <tab.icon size={14} />
            {tab.label}
          </button>
        ))}
      </div>
    </nav>
  )
}

function Overview({ stats, analytics, adminSummary, dailyLimit, documents, queries }) {
  const last7Total = analytics.daily_messages.reduce((sum, day) => sum + Number(day.user_messages || 0), 0)
  const maxDay = Math.max(1, ...analytics.daily_messages.map((day) => Number(day.user_messages || 0)))
  const readyDocs = documents.filter((document) => document.status === 'ready').length
  const pendingDocs = documents.length - readyDocs

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard icon={MessageSquare} label="Consultas registadas" value={formatNumber(stats?.total_queries)} hint={`${formatNumber(last7Total)} mensagens de utilizador nos últimos 7 dias`} tone="blue" />
        <MetricCard icon={Database} label="Chunks indexados" value={formatNumber(stats?.total_chunks)} hint={`${formatNumber(stats?.total_documents)} diplomas/fontes oficiais`} tone="emerald" />
        <MetricCard icon={Users} label="Contas" value={formatNumber(stats?.total_accounts)} hint={`${adminSummary.admins} admin · ${adminSummary.commonUsers} utilizadores`} tone="amber" />
        <MetricCard icon={Gauge} label="Tempo médio" value={`${stats?.avg_response_time_s ?? 0}s`} hint="Baseado nas conversas recentes" tone="violet" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
        <Panel title="Actividade dos últimos 7 dias" icon={Activity}>
          <div className="flex h-52 items-end gap-2 pt-4">
            {analytics.daily_messages.map((day) => {
              const value = Number(day.user_messages || 0)
              const height = Math.max(8, Math.round((value / maxDay) * 100))
              return (
                <div key={day.day} className="flex min-w-0 flex-1 flex-col items-center gap-2">
                  <div className="flex h-36 w-full items-end rounded-xl bg-white/[0.025] p-1">
                    <div className="w-full rounded-lg bg-gradient-to-t from-[color:var(--accent)] to-sky-300/80" style={{ height: `${height}%` }} />
                  </div>
                  <span className="text-[10px] text-white/35">{day.day?.slice(5)}</span>
                  <span className="text-[10px] font-semibold text-white/65">{value}</span>
                </div>
              )
            })}
          </div>
        </Panel>

        <Panel title="Saúde operacional" icon={CheckCircle2}>
          <div className="space-y-3">
            <HealthRow label="Documentos prontos" value={`${readyDocs}/${documents.length || 0}`} ok={pendingDocs === 0} />
            <HealthRow label="Limite diário" value={dailyLimit === 0 ? 'Ilimitado' : `${dailyLimit}/dia`} ok={dailyLimit > 0} />
            <HealthRow label="Utilizadores perto do limite" value={adminSummary.nearLimit} ok={adminSummary.nearLimit === 0} />
            <HealthRow label="Mensagens hoje" value={adminSummary.usedToday} ok />
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Utilizadores mais activos hoje" icon={Users}>
          <div className="space-y-2">
            {analytics.top_users_today.length ? analytics.top_users_today.map((user) => (
              <div key={user.id} className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-white/75">{user.name || 'Sem nome'}</p>
                  <p className="truncate text-xs text-white/35">{user.email || user.role || 'utilizador'}</p>
                </div>
                <span className="rounded-full bg-white/[0.06] px-2 py-1 text-xs font-semibold text-white/70">{user.messages_used_today}</span>
              </div>
            )) : <EmptyState icon={Users} title="Sem actividade hoje" />}
          </div>
        </Panel>

        <Panel title="Consultas recentes" icon={Clock}>
          <div className="space-y-2">
            {queries.slice(0, 5).map((query, index) => (
              <div key={query.id || index} className="rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-2">
                <p className="line-clamp-1 text-xs font-medium text-white/70">{query.question}</p>
                {query.timestamp ? <p className="mt-1 text-[10px] text-white/30">{formatHumanTimestamp(query.timestamp)}</p> : null}
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  )
}

function LimitsPanel({ dailyLimit, limitDraft, setLimitDraft, savingSettings, onSave, users, adminSummary }) {
  const commonUsers = users.filter((user) => user.role !== 'admin')
  const cappedUsers = dailyLimit > 0 ? commonUsers.filter((user) => Number(user.messages_used_today || 0) >= dailyLimit).length : 0
  const quickLimits = [0, 5, 10, 15, 20]

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <Panel title="Política de uso diário" icon={SlidersHorizontal}>
        <div className="space-y-5">
          <div>
            <p className="text-sm font-medium text-white/85">Mensagens por utilizador comum / dia</p>
            <p className="mt-1 text-xs leading-relaxed text-white/40">Administradores ficam isentos. Use 0 para desactivar o limite durante demonstrações privadas.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
            <Field label="Limite diário" value={limitDraft} onChange={setLimitDraft} type="number" />
            <button onClick={onSave} disabled={savingSettings} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[color:var(--accent)] px-5 text-sm font-medium text-white transition hover:bg-[color:var(--accent-hover)] disabled:opacity-50">
              {savingSettings ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
              Guardar
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {quickLimits.map((value) => (
              <button key={value} onClick={() => setLimitDraft(String(value))} className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${String(value) === String(limitDraft) ? 'bg-[color:var(--accent)] text-white' : 'bg-white/[0.05] text-white/45 hover:bg-white/[0.08] hover:text-white/75'}`}>
                {value === 0 ? 'Ilimitado' : `${value}/dia`}
              </button>
            ))}
          </div>
        </div>
      </Panel>

      <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
        <MetricCard icon={Shield} label="Estado" value={dailyLimit === 0 ? 'Livre' : 'Activo'} hint="Aplicado só a utilizadores comuns" tone={dailyLimit === 0 ? 'amber' : 'emerald'} />
        <MetricCard icon={AlertTriangle} label="No limite hoje" value={cappedUsers} hint={`${commonUsers.length} utilizadores comuns`} tone={cappedUsers ? 'amber' : 'emerald'} />
        <MetricCard icon={MessageSquare} label="Uso hoje" value={adminSummary.usedToday} hint="Mensagens de utilizador registadas" tone="blue" />
      </div>
    </div>
  )
}

function UsersPanel({ users, totalUsers, dailyLimit, limitDisabled, search, roleFilter, setSearch, setRoleFilter, onCreate, onEdit, onDelete, onSetProfessionalProfile }) {
  return (
    <div className="space-y-4">
      <Toolbar
        title="Gestão de utilizadores"
        subtitle={`${users.length}/${totalUsers} contas visíveis`}
        search={search}
        setSearch={setSearch}
        searchPlaceholder="Pesquisar nome, email ou telefone"
        actionLabel="Novo utilizador"
        actionIcon={UserPlus}
        onAction={onCreate}
      >
        <SegmentedFilter value={roleFilter} onChange={setRoleFilter} options={[['all', 'Todos'], ['admin', 'Admins'], ['user', 'Users'], ['pro', 'Modo Pro']]} />
      </Toolbar>

      <div className="grid gap-3 xl:grid-cols-2">
        {users.map((user) => (
          <UserCard key={user.id} user={user} dailyLimit={dailyLimit} limitDisabled={limitDisabled} onEdit={() => onEdit(user)} onDelete={() => onDelete(user.id)} onSetProfessionalProfile={onSetProfessionalProfile} />
        ))}
      </div>
      {!users.length && <EmptyState icon={Users} title="Nenhum utilizador encontrado" description="Ajuste os filtros ou crie uma nova conta." />}
    </div>
  )
}

function UserCard({ user, dailyLimit, limitDisabled, onEdit, onDelete, onSetProfessionalProfile }) {
  const used = Number(user.messages_used_today || 0)
  const isAdmin = user.role === 'admin'
  const proStatus = user.professional_profile?.status || 'inactive'
  const isProActive = proStatus === 'active'
  const percent = isAdmin || limitDisabled ? 0 : Math.min(100, Math.round((used / Math.max(1, dailyLimit)) * 100))

  return (
    <article className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4 transition hover:border-white/[0.10] hover:bg-white/[0.04]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold text-white/85">{user.name || 'Sem nome'}</p>
            <RoleChip role={user.role} />
            {isProActive ? <span className="inline-flex items-center gap-1 rounded-full bg-blue-400/12 px-2 py-0.5 text-[10px] font-semibold text-blue-300"><BriefcaseBusiness size={10} /> Pro</span> : null}
            {user.is_seeded ? <span className="rounded-full bg-white/[0.05] px-2 py-0.5 text-[10px] font-medium text-white/30">sistema</span> : null}
          </div>
          <p className="mt-1 truncate text-xs text-white/40">{user.email || 'sem email'}{user.phone ? ` · ${user.phone}` : ''}</p>
          {user.created_at ? <p className="mt-1 text-[10px] text-white/25">Criado {formatHumanTimestamp(user.created_at)}</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button onClick={onEdit} className="rounded-xl p-2 text-white/35 transition hover:bg-white/[0.06] hover:text-white/75" title="Editar">
            <Pencil size={15} />
          </button>
          {!isAdmin ? (
            <button onClick={onDelete} className="rounded-xl p-2 text-white/30 transition hover:bg-red-500/10 hover:text-red-300" title="Remover">
              <Trash2 size={15} />
            </button>
          ) : null}
        </div>
      </div>
      <div className="mt-4 rounded-xl bg-white/[0.025] p-3">
        <div className="flex items-center justify-between text-xs">
          <span className="text-white/40">Uso diário</span>
          <span className="font-medium text-white/70">{isAdmin || limitDisabled ? `${used} · sem limite` : `${used}/${dailyLimit}`}</span>
        </div>
        {!isAdmin && !limitDisabled ? <ProgressBar value={percent} tone={percent >= 90 ? 'danger' : percent >= 70 ? 'warn' : 'ok'} /> : null}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={() => onSetProfessionalProfile?.(user, isProActive ? 'inactive' : 'active')}
          className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-semibold transition ${
            isProActive
              ? 'border-amber-400/20 bg-amber-400/8 text-amber-200 hover:bg-amber-400/12'
              : 'border-blue-400/20 bg-blue-400/8 text-blue-200 hover:bg-blue-400/12'
          }`}
        >
          <BriefcaseBusiness size={14} />
          {isProActive ? 'Suspender Modo Pro' : 'Ativar Modo Pro'}
        </button>
        {user.professional_profile?.status && !isProActive ? (
          <span className="rounded-full bg-white/[0.04] px-2 py-1 text-[10px] font-medium text-white/35">Pro: {user.professional_profile.status}</span>
        ) : null}
      </div>
    </article>
  )
}

function DocumentsPanel({ documents, allDocuments, search, statusFilter, setSearch, setStatusFilter, onDelete }) {
  const statuses = Array.from(new Set(allDocuments.map((document) => document.status || 'unknown')))

  return (
    <div className="space-y-4">
      <Toolbar title="Documentos" subtitle={`${documents.length}/${allDocuments.length} documentos visíveis`} search={search} setSearch={setSearch} searchPlaceholder="Pesquisar documento">
        <SelectFilter value={statusFilter} onChange={setStatusFilter} options={[['all', 'Todos os estados'], ...statuses.map((status) => [status, status])]} />
      </Toolbar>
      <div className="grid gap-3 xl:grid-cols-2">
        {documents.map((document) => (
          <article key={document.id} className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate text-sm font-semibold text-white/85">{document.display_name || document.filename}</p>
                  <StatusChip status={document.status} />
                </div>
                <p className="mt-1 text-xs text-white/40">{formatNumber(document.chunks_created)} chunks{document.page_count ? ` · ${document.page_count} páginas` : ''}{document.extraction_mode ? ` · ${document.extraction_mode}` : ''}</p>
                {document.summary ? <p className="mt-3 line-clamp-2 text-xs leading-relaxed text-white/35">{document.summary}</p> : null}
              </div>
              <button onClick={() => onDelete(document.id)} className="shrink-0 rounded-xl p-2 text-white/30 transition hover:bg-red-500/10 hover:text-red-300">
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        ))}
      </div>
      {!documents.length && <EmptyState icon={FileText} title="Nenhum documento encontrado" />}
    </div>
  )
}

function IngestionPanel({ stats, uploadFiles, setUploadFiles, uploading, uploadProgress, onUpload, ingesting, onIngest }) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <Panel title="Carregar PDFs" icon={FileUp}>
        <div className="rounded-2xl border border-dashed border-white/[0.12] bg-white/[0.02] p-5">
          <input
            type="file"
            multiple
            accept=".pdf"
            onChange={(event) => setUploadFiles([...event.target.files])}
            className="w-full text-xs text-white/45 file:mr-3 file:rounded-xl file:border-0 file:bg-[color:var(--accent)]/15 file:px-4 file:py-2 file:text-xs file:font-medium file:text-[color:var(--accent)] hover:file:bg-[color:var(--accent)]/25"
          />
          {uploadFiles.length ? (
            <div className="mt-4 space-y-2">
              <p className="text-xs font-medium text-white/55">{uploadFiles.length} ficheiro(s) seleccionados</p>
              <div className="max-h-40 space-y-1 overflow-y-auto custom-scroll rounded-xl bg-black/10 p-2">
                {uploadFiles.map((file, index) => (
                  <p key={`${file.name}-${index}`} className="truncate text-xs text-white/35">{file.name}</p>
                ))}
              </div>
              <button onClick={onUpload} disabled={uploading} className="inline-flex h-10 items-center gap-2 rounded-xl bg-[color:var(--accent)] px-4 text-xs font-medium text-white transition hover:bg-[color:var(--accent-hover)] disabled:opacity-50">
                {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                {uploading ? `A enviar ${uploadProgress}` : 'Enviar ficheiros'}
              </button>
            </div>
          ) : (
            <p className="mt-3 text-xs text-white/35">Seleccione PDFs legislativos, acórdãos ou documentos de apoio para indexar.</p>
          )}
        </div>
      </Panel>

      <div className="space-y-4">
        <Panel title="Indexação oficial" icon={Zap}>
          <p className="mb-4 text-xs leading-relaxed text-white/40">Processa a pasta local de legislação e actualiza chunks, embeddings e metadados de recuperação.</p>
          <button onClick={onIngest} disabled={ingesting} className="inline-flex h-10 items-center gap-2 rounded-xl bg-[color:var(--accent)] px-4 text-xs font-medium text-white transition hover:bg-[color:var(--accent-hover)] disabled:opacity-50">
            {ingesting ? <RefreshCw size={15} className="animate-spin" /> : <Zap size={15} />}
            {ingesting ? 'A processar...' : 'Iniciar ingestão'}
          </button>
        </Panel>
        <MetricCard icon={Database} label="Chunks" value={formatNumber(stats?.total_chunks)} hint={`${formatNumber(stats?.total_documents)} fontes oficiais`} tone="emerald" />
      </div>
    </div>
  )
}

function JurisprudencePanel({ juris, filters, setFilters, loading, onApply, onClear, onCreate, onDelete }) {
  return (
    <div className="space-y-4">
      <Toolbar title="Jurisprudência" subtitle={`${formatNumber(juris.total)} acórdãos na base`} actionLabel="Novo acórdão" actionIcon={Plus} onAction={onCreate}>
        <div className="grid w-full gap-2 lg:grid-cols-[minmax(180px,1fr)_180px_180px_auto_auto]">
          <SearchInput value={filters.search} onChange={(value) => setFilters({ ...filters, search: value })} placeholder="Pesquisar acórdão" />
          <SelectFilter value={filters.court} onChange={(value) => setFilters({ ...filters, court: value })} options={[['', 'Todos tribunais'], ...(juris.courts || []).map((court) => [court, court])]} />
          <SelectFilter value={filters.branch} onChange={(value) => setFilters({ ...filters, branch: value })} options={[['', 'Todos ramos'], ...(juris.branches || []).map((branch) => [branch, branch])]} />
          <button onClick={onApply} disabled={loading} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 text-xs font-medium text-white/65 transition hover:bg-white/[0.07] disabled:opacity-50">
            {loading ? <Loader2 size={14} className="animate-spin" /> : <Filter size={14} />}
            Filtrar
          </button>
          <button onClick={onClear} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-white/[0.08] px-4 text-xs font-medium text-white/45 transition hover:bg-white/[0.05] hover:text-white/70">
            <X size={14} />
            Limpar
          </button>
        </div>
      </Toolbar>

      <div className="grid gap-3 xl:grid-cols-2">
        {(juris.items || []).map((item) => (
          <article key={item.id} className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="line-clamp-2 text-sm font-semibold text-white/85">{item.title}</p>
                <p className="mt-1 text-xs text-white/40">{item.court}{item.case_number ? ` · ${item.case_number}` : ''}{item.decision_date ? ` · ${item.decision_date}` : ''}</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {item.legal_branch ? <span className="rounded-full bg-white/[0.05] px-2 py-0.5 text-[10px] text-white/40">{item.legal_branch}</span> : null}
                  {item.url ? <span className="rounded-full bg-sky-400/10 px-2 py-0.5 text-[10px] text-sky-300/70">fonte oficial</span> : null}
                </div>
                {item.summary ? <p className="mt-3 line-clamp-2 text-xs leading-relaxed text-white/35">{item.summary}</p> : null}
              </div>
              <button onClick={() => onDelete(item.id)} className="shrink-0 rounded-xl p-2 text-white/30 transition hover:bg-red-500/10 hover:text-red-300">
                <Trash2 size={15} />
              </button>
            </div>
          </article>
        ))}
      </div>
      {!(juris.items || []).length && <EmptyState icon={Gavel} title="Nenhum acórdão encontrado" description="Ajuste os filtros ou adicione uma decisão manualmente." />}
    </div>
  )
}

function QueriesPanel({ conversations, totalConversations, legacyQueries, search, setSearch, onCopy, onOpen }) {
  return (
    <div className="space-y-4">
      <Toolbar title="Conversas e consultas" subtitle={`${conversations.length}/${totalConversations} conversas visíveis`} search={search} setSearch={setSearch} searchPlaceholder="Pesquisar conversa, utilizador ou resposta" />
      <div className="space-y-3">
        {conversations.map((conversation) => (
          <article key={conversation.id} className="group rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4 transition hover:border-[color:var(--accent)]/30 hover:bg-white/[0.045]">
            <button type="button" onClick={() => onOpen(conversation)} className="block w-full text-left">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="line-clamp-2 text-sm font-semibold text-white/85">{conversation.title || conversation.last_question || 'Conversa sem título'}</p>
                    <span className="rounded-full bg-[color:var(--accent)]/10 px-2 py-0.5 text-[10px] font-medium text-[color:var(--accent)]">{formatNumber(conversation.message_count)} mensagens</span>
                  </div>
                  <p className="mt-1 text-xs text-white/38">
                    {conversation.user?.name || 'Utilizador'}{conversation.user?.email ? ` · ${conversation.user.email}` : ''}
                  </p>
                  {conversation.last_question ? (
                    <p className="mt-3 line-clamp-2 text-xs font-medium leading-relaxed text-white/62">{conversation.last_question}</p>
                  ) : null}
                  {conversation.last_answer_preview ? (
                    <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-white/34">{conversation.last_answer_preview}</p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center justify-between gap-2 sm:flex-col sm:items-end">
                  <span className="text-[10px] text-white/28">{formatHumanTimestamp(conversation.updated_at || conversation.last_message_at)}</span>
                  <span className="inline-flex h-9 items-center gap-2 rounded-xl border border-white/[0.08] px-3 text-xs font-medium text-white/42 transition group-hover:border-[color:var(--accent)]/25 group-hover:text-white/70">
                    <MessageSquare size={14} />
                    Ver timeline
                  </span>
                </div>
              </div>
            </button>
          </article>
        ))}
      </div>
      {!conversations.length && legacyQueries?.length ? (
        <Panel title="Registos antigos sem timeline" icon={Clock}>
          <div className="space-y-3">
            {legacyQueries.slice(0, 12).map((query, index) => (
              <article key={query.id || index} className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-white/80">{query.question}</p>
                    <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-white/38">{String(query.answer || '').slice(0, 420)}</p>
                    {query.timestamp ? <p className="mt-2 text-[10px] text-white/25">{formatHumanTimestamp(query.timestamp)}</p> : null}
                  </div>
                  <button onClick={() => onCopy(query.question, 'Pergunta copiada')} className="shrink-0 rounded-xl p-2 text-white/30 transition hover:bg-white/[0.06] hover:text-white/70">
                    <Copy size={15} />
                  </button>
                </div>
              </article>
            ))}
          </div>
        </Panel>
      ) : null}
      {!conversations.length && !legacyQueries?.length && <EmptyState icon={MessageSquare} title="Nenhuma conversa encontrada" description="Quando os utilizadores enviarem mensagens, a timeline completa aparecerá aqui." />}
    </div>
  )
}

function ConversationTimelineModal({ conversation, loading, onClose, onCopy }) {
  const messages = conversation.messages || []
  const userMessages = messages.filter((message) => message.role === 'user').length
  const assistantMessages = messages.filter((message) => message.role === 'assistant').length

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/65 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <section className="flex max-h-[94dvh] w-full max-w-5xl flex-col overflow-hidden rounded-t-3xl border border-white/[0.08] bg-[color:var(--bg-elevated)] shadow-2xl sm:max-h-[88vh] sm:rounded-3xl">
        <header className="border-b border-white/[0.06] bg-white/[0.025] p-4 sm:p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-[color:var(--accent)]/12 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-[color:var(--accent)]">Timeline</span>
                {conversation.user?.role ? <RoleChip role={conversation.user.role} /> : null}
              </div>
              <h2 className="mt-3 line-clamp-2 text-base font-semibold text-white/90 sm:text-lg">{conversation.title}</h2>
              <p className="mt-1 text-xs text-white/40">
                {conversation.user?.name || 'Utilizador'}{conversation.user?.email ? ` · ${conversation.user.email}` : ''}{conversation.updated_at ? ` · ${formatHumanTimestamp(conversation.updated_at)}` : ''}
              </p>
            </div>
            <button onClick={onClose} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/[0.08] text-white/45 transition hover:bg-white/[0.06] hover:text-white/75">
              <X size={18} />
            </button>
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-4">
            <TimelineStat label="Mensagens" value={messages.length || conversation.message_count || 0} />
            <TimelineStat label="Perguntas" value={userMessages || conversation.user_message_count || 0} />
            <TimelineStat label="Respostas IA" value={assistantMessages || conversation.assistant_message_count || 0} />
            <TimelineStat label="Documento activo" value={conversation.active_document_id ? 'Sim' : 'Não'} />
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto custom-scroll p-3 sm:p-5">
          {loading ? (
            <div className="flex min-h-64 items-center justify-center">
              <div className="flex items-center gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.03] px-4 py-3 text-sm text-white/55">
                <Loader2 size={17} className="animate-spin text-[color:var(--accent)]" />
                A abrir conversa completa...
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {messages.map((message, index) => (
                <TimelineMessage key={message.id || index} message={message} onCopy={onCopy} />
              ))}
              {!messages.length ? <EmptyState icon={MessageSquare} title="Timeline vazia" description="Não foram encontradas mensagens para esta conversa." /> : null}
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

function TimelineStat({ label, value }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-black/10 px-3 py-2">
      <p className="text-[10px] uppercase tracking-[0.16em] text-white/25">{label}</p>
      <p className="mt-1 text-sm font-semibold text-white/75">{value}</p>
    </div>
  )
}

function TimelineMessage({ message, onCopy }) {
  const isUser = message.role === 'user'
  const sources = Array.isArray(message.sources) ? message.sources : []

  return (
    <article className={`rounded-2xl border p-4 ${isUser ? 'border-sky-400/12 bg-sky-400/[0.045]' : 'border-white/[0.06] bg-white/[0.025]'}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] ${isUser ? 'bg-sky-400/12 text-sky-300' : 'bg-emerald-400/12 text-emerald-300'}`}>
            {isUser ? 'Utilizador' : 'IA'}
          </span>
          {message.provider_used ? <span className="text-[10px] text-white/28">{message.provider_used}</span> : null}
        </div>
        <div className="flex items-center gap-2">
          {message.created_at ? <span className="text-[10px] text-white/25">{formatHumanTimestamp(message.created_at)}</span> : null}
          <button onClick={() => onCopy(message.content || '', 'Mensagem copiada')} className="rounded-lg p-1.5 text-white/25 transition hover:bg-white/[0.06] hover:text-white/65">
            <Copy size={13} />
          </button>
        </div>
      </div>
      {isUser ? (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-white/78">{message.content}</p>
      ) : (
        <LegalMarkdown text={message.content || ''} sourceRefs={sources} className="text-sm leading-relaxed text-white/74" />
      )}
      {sources.length ? (
        <div className="mt-4 rounded-xl border border-white/[0.06] bg-black/10 p-3">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-white/28">Fontes usadas</p>
          <div className="flex flex-wrap gap-1.5">
            {sources.slice(0, 8).map((source, index) => (
              <span key={`${source.segment_id || source.id || index}`} className="rounded-full bg-white/[0.05] px-2 py-1 text-[10px] text-white/38">
                {source.article ? `Art. ${source.article}` : source.title || source.diploma || `Fonte ${index + 1}`}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </article>
  )
}

function Toolbar({ title, subtitle, search, setSearch, searchPlaceholder, actionLabel, actionIcon: ActionIcon, onAction, children }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-3 sm:p-4">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <h2 className="text-base font-semibold text-white/85">{title}</h2>
          {subtitle ? <p className="text-xs text-white/35">{subtitle}</p> : null}
        </div>
        {actionLabel ? (
          <button onClick={onAction} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-[color:var(--accent)] px-4 text-xs font-medium text-white transition hover:bg-[color:var(--accent-hover)]">
            {ActionIcon ? <ActionIcon size={15} /> : null}
            {actionLabel}
          </button>
        ) : null}
      </div>
      {(setSearch || children) ? (
        <div className="mt-4 flex flex-col gap-2 lg:flex-row lg:items-center">
          {setSearch ? <SearchInput value={search} onChange={setSearch} placeholder={searchPlaceholder} /> : null}
          {children}
        </div>
      ) : null}
    </div>
  )
}

function SearchInput({ value, onChange, placeholder }) {
  return (
    <label className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-xl border border-white/[0.08] bg-black/10 px-3 text-sm text-white/75 focus-within:border-[color:var(--accent)]/45">
      <Search size={15} className="shrink-0 text-white/30" />
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-white/25" />
      {value ? <button type="button" onClick={() => onChange('')} className="text-white/30 hover:text-white/60"><X size={14} /></button> : null}
    </label>
  )
}

function Panel({ title, icon: Icon, children }) {
  return (
    <section className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4 shadow-[0_12px_40px_rgba(0,0,0,0.10)]">
      <div className="mb-4 flex items-center gap-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-[color:var(--accent)]/10">
          <Icon size={15} className="text-[color:var(--accent)]/75" />
        </div>
        <h3 className="text-sm font-semibold text-white/75">{title}</h3>
      </div>
      {children}
    </section>
  )
}

function MetricCard({ icon: Icon, label, value, hint, tone = 'blue' }) {
  const tones = {
    blue: 'from-sky-400/18 to-blue-500/5 text-sky-300',
    emerald: 'from-emerald-400/18 to-emerald-500/5 text-emerald-300',
    amber: 'from-amber-400/18 to-amber-500/5 text-amber-300',
    violet: 'from-violet-400/18 to-violet-500/5 text-violet-300',
  }
  return (
    <div className={`rounded-2xl border border-white/[0.06] bg-gradient-to-br p-4 shadow-[0_12px_40px_rgba(0,0,0,0.10)] backdrop-blur-sm ${tones[tone] || tones.blue}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-white/40">{label}</p>
          <p className="mt-1 truncate text-2xl font-semibold tabular-nums text-white/90">{value ?? 0}</p>
        </div>
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/[0.06] ${tones[tone]?.split(' ').at(-1) || 'text-sky-300'}`}>
          <Icon size={18} />
        </div>
      </div>
      {hint ? <p className="mt-3 text-xs leading-relaxed text-white/35">{hint}</p> : null}
    </div>
  )
}

function HealthRow({ label, value, ok }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-white/[0.025] px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        {ok ? <CheckCircle2 size={15} className="shrink-0 text-emerald-300/75" /> : <AlertTriangle size={15} className="shrink-0 text-amber-300/75" />}
        <span className="truncate text-xs text-white/45">{label}</span>
      </div>
      <span className="shrink-0 text-xs font-semibold text-white/75">{value}</span>
    </div>
  )
}

function RoleChip({ role }) {
  const isAdmin = role === 'admin'
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${isAdmin ? 'bg-amber-400/12 text-amber-300' : 'bg-white/[0.05] text-white/38'}`}>
      {isAdmin ? 'admin' : 'user'}
    </span>
  )
}

function StatusChip({ status }) {
  const isReady = status === 'ready'
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${isReady ? 'bg-emerald-400/12 text-emerald-300' : 'bg-amber-400/12 text-amber-300'}`}>
      {status || 'unknown'}
    </span>
  )
}

function ProgressBar({ value, tone = 'ok' }) {
  const colors = {
    ok: 'bg-emerald-400',
    warn: 'bg-amber-400',
    danger: 'bg-red-400',
  }
  return (
    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
      <div className={`h-full rounded-full ${colors[tone] || colors.ok}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}

function SegmentedFilter({ value, onChange, options }) {
  return (
    <div className="flex shrink-0 rounded-xl border border-white/[0.08] bg-black/10 p-1">
      {options.map(([id, label]) => (
        <button key={id} onClick={() => onChange(id)} className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${value === id ? 'bg-white/[0.10] text-white/75' : 'text-white/35 hover:text-white/60'}`}>
          {label}
        </button>
      ))}
    </div>
  )
}

function SelectFilter({ value, onChange, options }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} className="h-10 rounded-xl border border-white/[0.08] bg-black/20 px-3 text-xs text-white/65 outline-none focus:border-[color:var(--accent)]/45">
      {options.map(([id, label]) => <option key={id || label} value={id}>{label}</option>)}
    </select>
  )
}

function EmptyState({ icon: Icon, title, description }) {
  return (
    <div className="flex min-h-44 flex-col items-center justify-center rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.015] p-6 text-center">
      <Icon size={24} className="text-white/25" />
      <p className="mt-3 text-sm font-medium text-white/55">{title}</p>
      {description ? <p className="mt-1 max-w-sm text-xs leading-relaxed text-white/30">{description}</p> : null}
    </div>
  )
}

function Field({ label, value, onChange, type = 'text', icon: Icon, placeholder = '' }) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium text-white/42">{label}</span>
      <div className="mt-1 flex h-11 items-center gap-2 rounded-xl border border-white/[0.08] bg-black/15 px-3 focus-within:border-[color:var(--accent)]/45">
        {Icon ? <Icon size={15} className="shrink-0 text-white/28" /> : null}
        <input type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="min-w-0 flex-1 bg-transparent text-sm text-white/82 outline-none placeholder:text-white/25" />
      </div>
    </label>
  )
}

function TextAreaField({ label, value, onChange }) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium text-white/42">{label}</span>
      <textarea value={value} onChange={(event) => onChange(event.target.value)} rows={4} className="mt-1 w-full resize-none rounded-xl border border-white/[0.08] bg-black/15 px-3 py-2 text-sm text-white/82 outline-none focus:border-[color:var(--accent)]/45" />
    </label>
  )
}

function SelectField({ label, value, onChange, options }) {
  return (
    <label className="block">
      <span className="text-[11px] font-medium text-white/42">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-11 w-full rounded-xl border border-white/[0.08] bg-black/20 px-3 text-sm text-white/82 outline-none focus:border-[color:var(--accent)]/45">
        {options.map(([id, labelText]) => <option key={id} value={id}>{labelText}</option>)}
      </select>
    </label>
  )
}

function Modal({ title, onClose, onSave, saveLabel, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/65 p-0 backdrop-blur-sm sm:items-center sm:p-4" onClick={onClose}>
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-3xl border border-white/[0.08] bg-[color:var(--panel)] p-5 shadow-2xl sm:max-w-lg sm:rounded-3xl sm:p-6" onClick={(event) => event.stopPropagation()}>
        <div className="mb-5 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-white/88">{title}</h2>
          <button onClick={onClose} className="rounded-xl p-2 text-white/35 transition hover:bg-white/[0.06] hover:text-white/70">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-3">{children}</div>
        <div className="mt-6 grid grid-cols-2 gap-2">
          <button onClick={onClose} className="h-11 rounded-xl border border-white/[0.08] text-sm font-medium text-white/50 transition hover:bg-white/[0.04] hover:text-white/70">Cancelar</button>
          <button onClick={onSave} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[color:var(--accent)] text-sm font-medium text-white transition hover:bg-[color:var(--accent-hover)]">
            <Save size={15} />
            {saveLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
