import { useState, useEffect } from 'react'
import { SurfaceCard } from '../../shared/ui/SurfaceCard'
import { InfoTooltip } from '../../shared/ui/InfoTooltip'
import { fetchCatalog, fetchJurisprudence } from '../../shared/services/apiClient'
import { useAuth } from '../../shared/hooks/useAuth'
import { CheckCircle, Clock, BookOpen, Gavel, Search, X, ExternalLink } from 'lucide-react'

function CatalogTab({ token }) {
  const [catalog, setCatalog] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let mounted = true
    async function load() {
      try {
        setIsLoading(true)
        const response = await fetchCatalog(token)
        if (mounted) setCatalog(response.items || [])
      } catch (err) {
        if (mounted) setError('Nao foi possivel carregar o catalogo.')
      } finally {
        if (mounted) setIsLoading(false)
      }
    }
    load()
    return () => { mounted = false }
  }, [token])

  const validatedItems = catalog.filter(item => item.status === 'Validado no corpus')
  const pendingItems = catalog.filter(item => item.status !== 'Validado no corpus')

  if (isLoading) return <div className="flex items-center justify-center py-12 text-sm text-[color:var(--ink-soft)]"><span className="mr-2 h-4 w-4 rounded-full border-2 border-[color:var(--accent)] border-t-transparent animate-spin" />A carregar catalogo...</div>
  if (error) return <div className="rounded-[var(--radius-md)] border border-red-200 bg-[color:var(--danger-soft)] px-4 py-3 text-sm text-red-700">{error}</div>

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <SurfaceCard className="p-4">
          <div className="flex items-center gap-2 text-[color:var(--success)]">
            <CheckCircle size={16} />
            <span className="text-xs font-medium uppercase tracking-wider">Validados</span>
          </div>
          <div className="mt-2 font-[family-name:var(--font-serif)] text-3xl font-semibold text-[color:var(--ink)]">{validatedItems.length}</div>
        </SurfaceCard>
        <SurfaceCard className="p-4">
          <div className="flex items-center gap-2 text-[color:var(--warning)]">
            <Clock size={16} />
            <span className="text-xs font-medium uppercase tracking-wider">Pendentes</span>
          </div>
          <div className="mt-2 font-[family-name:var(--font-serif)] text-3xl font-semibold text-[color:var(--ink)]">{pendingItems.length}</div>
        </SurfaceCard>
        <SurfaceCard className="col-span-2 p-4 sm:col-span-1">
          <div className="flex items-center gap-2 text-[color:var(--accent)]">
            <BookOpen size={16} />
            <span className="text-xs font-medium uppercase tracking-wider">Total</span>
          </div>
          <div className="mt-2 font-[family-name:var(--font-serif)] text-3xl font-semibold text-[color:var(--ink)]">{catalog.length}</div>
        </SurfaceCard>
      </div>

      {validatedItems.length > 0 ? (
        <div>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-widest text-[color:var(--ink-soft)]">Cobertura validada</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {validatedItems.map((item) => (
              <SurfaceCard key={item.title} className="group p-4 transition-all hover:shadow-[var(--shadow-2)]">
                <div className="mb-3 inline-flex items-center gap-1 rounded-full bg-[color:var(--success-soft)] px-2 py-0.5 text-[11px] font-medium text-[color:var(--success)]">
                  <CheckCircle size={11} />
                  {item.status}
                </div>
                <h4 className="font-[family-name:var(--font-serif)] text-lg font-semibold leading-tight text-[color:var(--ink)]">{item.title}</h4>
                <p className="mt-2 text-sm leading-relaxed text-[color:var(--ink-soft)]">{item.scope}</p>
              </SurfaceCard>
            ))}
          </div>
        </div>
      ) : (
        <div className="rounded-[var(--radius-md)] border border-dashed border-[color:var(--stroke)] p-8 text-center text-sm text-[color:var(--ink-soft)]">Nenhum diploma validado ainda.</div>
      )}

      {pendingItems.length > 0 ? (
        <div>
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-widest text-[color:var(--ink-soft)]">Em expansao</h3>
          <div className="space-y-2">
            {pendingItems.map((item) => (
              <div key={item.title} className="flex items-center gap-3 rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel-muted)] px-4 py-3">
                <Clock size={14} className="shrink-0 text-[color:var(--warning)]" />
                <div className="min-w-0">
                  <span className="text-sm font-medium text-[color:var(--ink)]">{item.title}</span>
                  <span className="ml-2 text-sm text-[color:var(--ink-soft)]">— {item.scope}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function JurisprudenceTab({ token }) {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [courts, setCourts] = useState([])
  const [branches, setBranches] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [courtFilter, setCourtFilter] = useState('')
  const [branchFilter, setBranchFilter] = useState('')

  async function load() {
    try {
      setIsLoading(true)
      const params = {}
      if (search.trim()) params.search = search.trim()
      if (courtFilter) params.court = courtFilter
      if (branchFilter) params.branch = branchFilter
      const response = await fetchJurisprudence(token, params)
      setItems(response.items || [])
      setTotal(response.total || 0)
      if (response.courts) setCourts(response.courts)
      if (response.branches) setBranches(response.branches)
    } catch (err) {
      setError('Nao foi possivel carregar a jurisprudencia.')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => { load() }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  function handleSearch(e) {
    e.preventDefault()
    load()
  }

  function handleClearFilters() {
    setSearch('')
    setCourtFilter('')
    setBranchFilter('')
    setTimeout(() => load(), 0)
  }

  const hasFilters = search || courtFilter || branchFilter

  return (
    <div className="space-y-4">
      {/* Filters */}
      <form onSubmit={handleSearch} className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--ink-soft)]" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Pesquisar acórdãos..."
            className="w-full rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel)] py-2 pl-9 pr-3 text-sm text-[color:var(--ink)] placeholder:text-[color:var(--ink-soft)] outline-none focus:border-[color:var(--accent)]"
          />
          {search && (
            <button type="button" onClick={() => { setSearch(''); setTimeout(() => load(), 0) }} className="absolute right-3 top-1/2 -translate-y-1/2 text-[color:var(--ink-soft)] hover:text-[color:var(--ink)]">
              <X size={14} />
            </button>
          )}
        </div>
        <select value={courtFilter} onChange={e => setCourtFilter(e.target.value)} className="rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel)] px-3 py-2 text-sm text-[color:var(--ink)] outline-none focus:border-[color:var(--accent)]">
          <option value="">Todos os tribunais</option>
          {courts.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={branchFilter} onChange={e => setBranchFilter(e.target.value)} className="rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel)] px-3 py-2 text-sm text-[color:var(--ink)] outline-none focus:border-[color:var(--accent)]">
          <option value="">Todos os ramos</option>
          {branches.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        <button type="submit" className="rounded-[var(--radius-md)] bg-[color:var(--accent)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity">Filtrar</button>
        {hasFilters && (
          <button type="button" onClick={handleClearFilters} className="rounded-[var(--radius-md)] border border-[color:var(--stroke)] px-3 py-2 text-sm text-[color:var(--ink-soft)] hover:text-[color:var(--ink)] transition-colors">Limpar</button>
        )}
      </form>

      {isLoading ? (
        <div className="flex items-center justify-center py-12 text-sm text-[color:var(--ink-soft)]">
          <span className="mr-2 h-4 w-4 rounded-full border-2 border-[color:var(--accent)] border-t-transparent animate-spin" />
          A carregar jurisprudencia...
        </div>
      ) : error ? (
        <div className="rounded-[var(--radius-md)] border border-red-200 bg-[color:var(--danger-soft)] px-4 py-3 text-sm text-red-700">{error}</div>
      ) : items.length === 0 ? (
        <div className="rounded-[var(--radius-md)] border border-dashed border-[color:var(--stroke)] p-8 text-center text-sm text-[color:var(--ink-soft)]">
          {hasFilters ? 'Nenhum acordao encontrado com esses filtros.' : 'Nenhum acordao disponivel. Execute a importacao de jurisprudencia primeiro.'}
        </div>
      ) : (
        <div>
          <p className="mb-3 text-xs text-[color:var(--ink-soft)]">{total} acordao{total !== 1 ? 's' : ''} encontrado{total !== 1 ? 's' : ''}</p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((item) => (
              <SurfaceCard key={item.id} className="group p-4 transition-all hover:shadow-[var(--shadow-2)]">
                <div className="mb-2 flex items-center gap-2">
                  <Gavel size={14} className="shrink-0 text-[color:var(--gold)]" />
                  <span className="text-xs font-medium uppercase tracking-wider text-[color:var(--gold)]">{item.court}</span>
                </div>
                <h4 className="font-[family-name:var(--font-serif)] text-base font-semibold leading-tight text-[color:var(--ink)]">{item.title}</h4>
                {item.case_number && <p className="mt-1 text-xs text-[color:var(--ink-soft)]">{item.case_number}</p>}
                {item.summary && <p className="mt-2 line-clamp-3 text-sm leading-relaxed text-[color:var(--ink-soft)]">{item.summary}</p>}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {item.legal_branch && (
                    <span className="inline-flex items-center rounded-full bg-[color:var(--accent-soft)] px-2 py-0.5 text-[11px] font-medium text-[color:var(--accent)]">{item.legal_branch}</span>
                  )}
                  {item.decision_date && (
                    <span className="text-[11px] text-[color:var(--ink-soft)]">{new Date(item.decision_date).toLocaleDateString('pt-PT')}</span>
                  )}
                </div>
                <a href={item.url} target="_blank" rel="noopener noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-[color:var(--accent)] opacity-0 transition-opacity group-hover:opacity-100">
                  <ExternalLink size={12} /> Ver original
                </a>
              </SurfaceCard>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function LibraryPage() {
  const { token } = useAuth()
  const [activeTab, setActiveTab] = useState('catalog')

  const tabs = [
    { id: 'catalog', label: 'Catalogo Legislativo', icon: BookOpen },
    { id: 'jurisprudence', label: 'Jurisprudencia', icon: Gavel },
  ]

  return (
    <section className="fade-rise space-y-5 py-2">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2">
          <h2 className="font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)] sm:text-3xl">Biblioteca Juridica</h2>
          <InfoTooltip content="Explore a legislacao angolana e a jurisprudencia dos tribunais superiores. Os diplomas validados estao disponiveis para consulta no chat." />
        </div>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-[color:var(--ink-soft)]">
          Diplomas legislativos e acordãos do Tribunal Supremo e Tribunal Constitucional de Angola.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--panel-muted)] p-0.5">
        {tabs.map((tab) => {
          const Icon = tab.icon
          const isActive = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`flex flex-1 items-center justify-center gap-2 rounded-[calc(var(--radius-md)-2px)] px-3 py-2 text-sm font-medium transition-all ${
                isActive ? 'bg-[color:var(--panel)] text-[color:var(--ink)] shadow-[var(--shadow-1)]' : 'text-[color:var(--ink-soft)] hover:text-[color:var(--ink)]'
              }`}
            >
              <Icon size={16} />
              <span className="hidden sm:inline">{tab.label}</span>
            </button>
          )
        })}
      </div>

      {/* Content */}
      {activeTab === 'catalog' ? <CatalogTab token={token} /> : <JurisprudenceTab token={token} />}
    </section>
  )
}
