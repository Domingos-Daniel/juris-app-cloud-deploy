import { Menu, X, MessageSquarePlus } from 'lucide-react'
import { PanelRightClose, PanelRightOpen, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { StatusBadge } from '../shared/ui/StatusBadge'
import { LegalNoticeBanner, SidebarNav, ShellActionButtons, TopBar } from '../shared/ui/AppShellParts'
import { ArticleViewer } from '../shared/ui/ArticleViewer'
import { ReferenceList } from '../shared/ui/ReferenceList'
import { ChatWorkspace } from '../features/chat/ChatWorkspace'
import { DocumentsPage } from '../features/documents/DocumentsPage'
import { LibraryPage } from '../features/library/LibraryPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import AdminPage from '../features/admin/AdminPage'
import ProPage from '../features/pro/ProPage'

function IconButton({ onClick, label, className = '', children, mobileLabel }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-col items-center justify-center gap-0.5 rounded-[var(--radius-sm)] border border-[color:var(--stroke)] bg-[color:var(--panel)] text-[color:var(--ink-soft)] transition-all hover:bg-[color:var(--panel-muted)] hover:text-[color:var(--ink)] active:scale-95 ${mobileLabel ? 'h-12 w-12' : 'h-8 w-8'} ${className}`}
      aria-label={label}
    >
      {children}
      {mobileLabel ? <span className="text-[8px] leading-none text-[color:var(--ink-soft)]/70">{mobileLabel}</span> : null}
    </button>
  )
}

export function AppShell({
  healthOk,
  theme,
  onToggleTheme,
  state,
  selectedConversation,
  sidebarConversations,
  selectedMotor,
  setMotor,
  setActiveSection,
  appendMessagePair,
  selectConversation,
  deleteConversation,
  deleteAllConversations,
  startNewConversation,
  renameConversation,
  setConversationActiveDocument,
  addUploadedDocument,
  removeDocument,
  authToken,
  currentUser,
  onLogout,
  onHydrateFromServer,
  onPreferencesUpdate,
  onToast,
  onUpdateMessageContent,
  onNavigateEditVersion,
}) {
  const location = useLocation()
  const navigate = useNavigate()
  const [rightPanelVisible, setRightPanelVisible] = useState(true)
  const [highlightArticle, setHighlightArticle] = useState(false)
  const [selectedSourceRef, setSelectedSourceRef] = useState(null)
  const [mobileLeftOpen, setMobileLeftOpen] = useState(false)
  const [mobileRightOpen, setMobileRightOpen] = useState(false)
  const [mobileChromeVisible, setMobileChromeVisible] = useState(true)
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true)

  // Sync URL ← activeSection + select conversation on load/back-button
  useEffect(() => {
    const path = location.pathname.replace(/^\/+/, '')
    const known = ['chat', 'documents', 'library', 'settings', 'pro', 'admin']

    if (known.includes(path)) {
      if (state.activeSection !== path) setActiveSection(path)
    } else if (path.length > 20) {
      // Path looks like a chat ID (UUID)
      if (state.activeSection !== 'chat') setActiveSection('chat')
      if (state.activeConversationId !== path) selectConversation(path)
    } else if (state.activeSection !== 'chat') {
      setActiveSection('chat')
    }
  }, [location.pathname, state.conversations.length])

  // Push chat ID to URL when a new chat gets saved to DB
  const prevChatIdRef = useRef(selectedConversation?.id)
  useEffect(() => {
    const currentId = selectedConversation?.id || null
    const prevId = prevChatIdRef.current
    prevChatIdRef.current = currentId
    if (currentId && currentId !== prevId && !prevId) {
      // Chat was just created (went from null/id-null to a real ID)
      navigate(`/${currentId}`, { replace: true })
    } else if (currentId && location.pathname !== `/${currentId}` && location.pathname === '/' && state.activeSection === 'chat') {
      navigate(`/${currentId}`, { replace: true })
    }
  }, [selectedConversation?.id, state.activeSection])

  // Route-aware section change handler
  const handleSectionChange = (section) => {
    setMobileLeftOpen(false)
    setMobileChromeVisible(true)
    setActiveSection(section)
    const path = section === 'chat' ? '/' : `/${section}`
    if (location.pathname !== path) {
      navigate(path, { replace: true })
    }
  }

  const handleSelectSourceRef = (source) => {
    setSelectedSourceRef(source)
    setHighlightArticle(true)
    const isDesktop = typeof window !== 'undefined' && window.matchMedia('(min-width: 1280px)').matches
    if (isDesktop) {
      setRightPanelVisible(true)
    } else {
      setMobileRightOpen(true)
    }
  }

  useEffect(() => {
    if (!highlightArticle) {
      return undefined
    }
    const timer = setTimeout(() => setHighlightArticle(false), 750)
    return () => clearTimeout(timer)
  }, [highlightArticle])

  // Page title from conversation
  useEffect(() => {
    const sectionTitles = {
      chat: selectedConversation?.title || 'Nova Consulta',
      documents: 'Meus Documentos',
      library: 'Biblioteca Jurídica',
      pro: 'Modo Pro',
      settings: 'Definições',
      admin: 'Administração',
    }
    const rawTitle = sectionTitles[state.activeSection] || 'jURIS-APP'
    const title = rawTitle.length > 64 ? `${rawTitle.slice(0, 61)}...` : rawTitle
    document.title = title === 'jURIS-APP' ? title : `${title} — jURIS-APP`
  }, [selectedConversation?.title, selectedConversation?.id, state.activeSection])

  const latestAssistant =
    selectedConversation?.messages
      ?.slice()
      .reverse()
      .find((message) => message.role === 'assistant') || null
  const activeSource = selectedSourceRef || latestAssistant?.sources?.[0] || null
  const hasAnySources = Boolean(latestAssistant?.sources?.length)
  const hasSources = hasAnySources && rightPanelVisible
  const isChatSection = state.activeSection === 'chat'

  const handleSelectConversation = (id) => {
    setMobileChromeVisible(true)
    setMobileLeftOpen(false)
    selectConversation(id)
    navigate(`/${id}`, { replace: true })
  }
  const handleNewConversation = () => {
    setMobileChromeVisible(true)
    startNewConversation()
    navigate('/', { replace: true })
    setMobileLeftOpen(false)
    setActiveSection('chat')
  }

  return (
    <div className="relative flex h-[100dvh] w-full overflow-hidden bg-[color:var(--bg)] text-[color:var(--ink)]">
      {/* Mobile overlay */}
      <button
        type="button"
        onClick={() => { setMobileLeftOpen(false); setMobileRightOpen(false) }}
        className={`fixed inset-0 z-30 bg-black/40 backdrop-blur-[2px] transition-opacity duration-200 xl:hidden ${mobileLeftOpen || mobileRightOpen ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'}`}
        aria-label="Fechar paineis"
      />

      {/* Desktop sidebar — slide animation */}
      <div className={`hidden xl:block h-full shrink-0 transition-[margin] duration-300 ease-out ${desktopSidebarOpen ? 'ml-0' : 'ml-[-280px]'}`}>
        <div className="w-[280px]">
          <SidebarNav
            activeSection={state.activeSection}
            onSectionChange={handleSectionChange}
            conversations={sidebarConversations}
            activeConversationId={selectedConversation?.id || null}
            onSelectConversation={handleSelectConversation}
            onNewConversation={handleNewConversation}
            onRenameConversation={renameConversation}
            onDeleteConversation={deleteConversation}
            onDeleteAllConversations={deleteAllConversations}
            motor={state.motor}
            onMotorChange={setMotor}
            currentUser={currentUser}
            onLogout={onLogout}
            className="flex shrink-0 sticky top-0"
          />
        </div>
      </div>

      {/* Mobile sidebar */}
      <SidebarNav
        activeSection={state.activeSection}
        onSectionChange={handleSectionChange}
        conversations={sidebarConversations}
        activeConversationId={selectedConversation?.id || null}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onRenameConversation={renameConversation}
        onDeleteConversation={deleteConversation}
        motor={state.motor}
        onMotorChange={setMotor}
        currentUser={currentUser}
        onLogout={onLogout}
        onClose={() => setMobileLeftOpen(false)}
        className={`fixed left-0 top-0 z-40 flex xl:hidden transition-transform duration-300 ease-out ${mobileLeftOpen ? 'translate-x-0' : '-translate-x-full'}`}
      />

      {/* Main content */}
      <div className="flex flex-1 flex-col min-w-0 min-h-0">
        {/* Top bar */}
        <TopBar
          className={`shrink-0 overflow-hidden transition-[max-height,transform,opacity,padding] duration-200 ease-[cubic-bezier(.22,1,.36,1)] will-change-[max-height,transform,opacity] motion-reduce:transition-none md:h-auto md:max-h-20 md:min-h-0 md:translate-y-0 md:py-2 md:opacity-100 ${
            mobileChromeVisible
              ? 'h-auto max-h-20 translate-y-0 opacity-100'
              : 'h-0 max-h-0 min-h-0 -translate-y-3 border-b-0 py-0 opacity-0'
          }`}
          leftNode={
            <>
              <IconButton onClick={() => setDesktopSidebarOpen((prev) => !prev)} label="Alternar menu lateral" className="hidden xl:flex" mobileLabel={desktopSidebarOpen ? 'Fechar' : 'Menu'}>
                {desktopSidebarOpen ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
              </IconButton>
              <IconButton onClick={() => setMobileLeftOpen(true)} label="Abrir menu lateral" className="xl:hidden" mobileLabel="Menu">
                <Menu size={18} />
              </IconButton>
              <IconButton onClick={handleNewConversation} label="Novo chat" className="xl:hidden" mobileLabel="Novo">
                <MessageSquarePlus size={18} />
              </IconButton>
            </>
          }
          centerNode={
            isChatSection && selectedConversation?.title ? (
              <span className="block max-w-[34vw] truncate text-center text-[11px] font-semibold leading-tight text-[color:var(--ink)] sm:max-w-[300px] sm:text-xs sm:text-[color:var(--ink-soft)]">
                {selectedConversation.title}
              </span>
            ) : (
              <StatusBadge healthy={healthOk} />
            )
          }
          rightNode={
            <>
              {isChatSection ? (
                <IconButton
                  onClick={() => {
                    const isDesktop = typeof window !== 'undefined' && window.matchMedia('(min-width: 1280px)').matches
                    if (isDesktop) { setRightPanelVisible((prev) => !prev) } else { setMobileRightOpen(true) }
                  }}
                  label={rightPanelVisible ? 'Ocultar referencias' : 'Mostrar referencias'}
                  mobileLabel="Fontes"
                >
                  {rightPanelVisible ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
                </IconButton>
              ) : null}
            </>
          }
        />

        {/* Content area */}
        <div className="flex flex-1 min-h-0 min-w-0">
          <section className="flex flex-1 min-h-0 min-w-0 flex-col px-3 pb-3 sm:px-4 sm:pb-4 lg:px-6">



            {isChatSection ? (
              <div className="flex-1 min-h-0">
                <ChatWorkspace
                  selectedConversation={selectedConversation}
                  editSessions={selectedConversation?.id ? (state.editSessions || {})[selectedConversation.id] : null}
                  draftActiveDocumentId={state.draftActiveDocumentId}
                  documents={state.documents || []}
                  provider={selectedMotor.provider}
                  onAppendMessagePair={appendMessagePair}
                  onSelectSourceRef={handleSelectSourceRef}
                  onSetConversationActiveDocument={setConversationActiveDocument}
                  onAddUploadedDocument={addUploadedDocument}
                  authToken={authToken}
                  currentUser={currentUser}
                  onRefreshAppState={onHydrateFromServer}
                  onToast={onToast}
                  onUpdateMessageContent={onUpdateMessageContent}
                  onNavigateEditVersion={onNavigateEditVersion}
                  mobileChromeVisible={mobileChromeVisible}
                  onMobileChromeVisibilityChange={setMobileChromeVisible}
                />
              </div>
            ) : null}

            {state.activeSection === 'documents' ? (
              <div className="flex-1 min-h-0 overflow-y-auto custom-scroll">
                <DocumentsPage documents={state.documents || []} onAddUploadedDocument={addUploadedDocument} onRemoveDocument={removeDocument} authToken={authToken} onRefreshDocuments={onHydrateFromServer} onUseDocument={setConversationActiveDocument} onStartNewConversation={startNewConversation} onOpenChatSection={() => setActiveSection('chat')} onToast={onToast} />
              </div>
            ) : null}
            {state.activeSection === 'library' ? (
              <div className="flex-1 min-h-0 overflow-y-auto custom-scroll">
                <LibraryPage />
              </div>
            ) : null}
            {state.activeSection === 'settings' ? (
              <div className="flex-1 min-h-0 overflow-y-auto custom-scroll">
                <SettingsPage motor={state.motor} onMotorChange={setMotor} currentUser={currentUser} authToken={authToken} onProfileUpdate={onHydrateFromServer} onPreferencesUpdate={onPreferencesUpdate} onToast={onToast} />
              </div>
            ) : null}
            {state.activeSection === 'pro' && (currentUser?.professional_profile?.status === 'active' || currentUser?.role === 'admin') ? (
              <div className="flex-1 min-h-0 overflow-y-auto custom-scroll">
                <ProPage
                  authToken={authToken}
                  currentUser={currentUser}
                  documents={state.documents || []}
                  conversations={sidebarConversations}
                  onToast={onToast}
                  onOpenChat={(chatId) => {
                    selectConversation(chatId)
                    setActiveSection('chat')
                    navigate(`/${chatId}`, { replace: true })
                  }}
                  onRefreshAppState={onHydrateFromServer}
                />
              </div>
            ) : null}
            {state.activeSection === 'pro' && !(currentUser?.professional_profile?.status === 'active' || currentUser?.role === 'admin') ? (
              <div className="grid flex-1 place-items-center">
                <div className="max-w-md rounded-[var(--radius-xl)] border border-[color:var(--stroke)] bg-[color:var(--panel)] p-6 text-center shadow-[var(--shadow-1)]">
                  <h2 className="text-lg font-semibold text-[color:var(--ink)]">Modo Pro indisponível</h2>
                  <p className="mt-2 text-sm leading-6 text-[color:var(--ink-soft)]">Este módulo é reservado a utilizadores com perfil profissional ativo. Peça ao administrador para ativar o Modo Pro.</p>
                </div>
              </div>
            ) : null}
            {state.activeSection === 'admin' && currentUser?.role === 'admin' ? (
              <div className="flex-1 min-h-0 overflow-y-auto custom-scroll">
                <AdminPage authToken={authToken} onToast={onToast} />
              </div>
            ) : null}
          </section>

          {/* Desktop right panel — slide animation */}
          <div className={`hidden xl:block h-full shrink-0 transition-all duration-300 ease-out ${isChatSection && hasSources ? 'w-[340px] opacity-100' : 'w-0 opacity-0 overflow-hidden'}`}>
            <div className="w-[340px] h-full">
              <aside className="custom-scroll h-full space-y-3 overflow-y-auto border-l border-[color:var(--stroke)] bg-[color:var(--bg-elev)] p-4">
                <ArticleViewer source={activeSource} highlight={highlightArticle} />
                <ReferenceList
                  sources={latestAssistant?.sources || []}
                  onSelectSource={(source) => {
                    setSelectedSourceRef(source)
                    setHighlightArticle(true)
                  }}
                />
              </aside>
            </div>
          </div>
        </div>
      </div>

      {/* Mobile right panel */}
      {isChatSection && hasAnySources ? (
        <aside
          className={`custom-scroll fixed right-0 top-0 z-40 h-screen w-[88vw] max-w-[360px] space-y-3 overflow-y-auto border-l border-[color:var(--stroke)] bg-[color:var(--panel)] p-4 shadow-[var(--shadow-3)] xl:hidden transition-transform duration-300 ease-out ${mobileRightOpen ? 'translate-x-0' : 'translate-x-full'}`}
        >
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-widest text-[color:var(--ink-soft)]">Referencias</h3>
            <IconButton onClick={() => setMobileRightOpen(false)} label="Fechar referencias">
              <X size={14} />
            </IconButton>
          </div>
          <ArticleViewer source={activeSource} highlight={highlightArticle} />
          <ReferenceList
            sources={latestAssistant.sources}
            onSelectSource={(source) => {
              setSelectedSourceRef(source)
              setHighlightArticle(true)
            }}
          />
        </aside>
      ) : null}
    </div>
  )
}
