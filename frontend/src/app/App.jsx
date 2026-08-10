import { useEffect, useRef, useState } from 'react'
import { Routes, Route } from 'react-router-dom'
import { fetchHealth } from '../shared/services/apiClient'
import { useAppState } from '../shared/hooks/useAppState'
import { useTheme } from '../shared/hooks/useTheme'
import { useAuth } from '../shared/hooks/useAuth'
import { AppShell } from './AppShell'
import { OnboardingTour } from './OnboardingTour'
import { Landmark, Eye, EyeOff, CheckCircle, AlertCircle, Mail, Lock, User, Phone, ArrowLeft } from 'lucide-react'
import { STORAGE_KEYS } from '../shared/constants/app'

function Toast({ toast }) {
  if (!toast?.message) return null
  const isError = toast?.type === 'error'
  return (
    <div className="fixed top-6 left-1/2 z-50 -translate-x-1/2 animate-[fadeIn_0.3s_ease-out]">
      <div className={`flex items-center gap-2.5 rounded-[var(--radius-lg)] border px-5 py-3 text-sm font-medium shadow-[var(--shadow-3)] ${
        isError
          ? 'border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200'
          : 'border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200'
      }`}>
        {isError ? <AlertCircle size={18} /> : <CheckCircle size={18} />}
        {toast.message}
      </div>
    </div>
  )
}

function LoginScreen({ onLogin, onSwitchToRegister, loading, theme, onToast }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  const handleSubmit = async (event) => {
    event.preventDefault()
    try { await onLogin(email, password) }
    catch (err) { onToast?.({ message: err.message || 'Falha ao autenticar', type: 'error' }) }
  }

  return (
    <div data-theme={theme} className="relative flex min-h-[100dvh] bg-[color:var(--bg)] text-[color:var(--ink)]">
      {/* Background image — mobile only; desktop uses left-panel bg */}
      <div
        className="absolute inset-0 bg-cover bg-center opacity-[0.08] lg:hidden"
        style={{ backgroundImage: `url(/bg-auth.png)` }}
      />
      <div className="absolute inset-0 bg-gradient-to-b from-black/20 to-black/40 lg:hidden" />
      {/* Left panel — branding + background (desktop only) */}
      <div className="relative hidden w-1/2 lg:flex items-center justify-center overflow-hidden bg-[color:var(--panel-muted)]">
        <div
          className="absolute inset-0 bg-cover bg-center opacity-40"
          style={{ backgroundImage: `url(/bg-auth.png)` }}
        />
        <div className="absolute inset-0 bg-gradient-to-br from-[color:var(--accent)]/30 to-transparent" />
        <div className="relative z-10 flex flex-col items-center px-12 text-center">
          <div className="grid h-20 w-20 place-items-center rounded-[2rem] bg-[color:var(--accent)] text-white shadow-[var(--shadow-3)]">
            <Landmark size={36} />
          </div>
          <h1 className="mt-6 font-[family-name:var(--font-serif)] text-4xl font-semibold text-[color:var(--ink)]">jURIS-APP</h1>
          <p className="mt-3 max-w-xs text-base leading-relaxed text-[color:var(--ink-soft)]">
            Assistente juridico angolano com inteligencia artificial. Pesquise leis, carregue documentos, consulte jurisprudencia.
          </p>
        </div>
      </div>

      {/* Right panel — form */}
      <div className="relative z-10 flex w-full items-center justify-center px-4 lg:w-1/2">
      <div className="w-full max-w-[420px]">
        {/* Mobile-only logo */}
        <div className="mb-8 flex items-center justify-center gap-3 lg:hidden">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[color:var(--accent)] text-white shadow-[var(--shadow-2)]">
            <Landmark size={22} />
          </div>
          <h1 className="font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)]">jURIS-APP</h1>
        </div>
        <p className="-mt-5 mb-6 text-center text-sm text-[color:var(--ink-soft)] lg:hidden">Assistente Juridico Angolano</p>
        <form onSubmit={handleSubmit} className="animate-[fadeIn_0.4s_ease-out] rounded-[var(--radius-xl)] border border-[color:var(--stroke)] bg-[color:var(--panel)]/95 p-6 shadow-[var(--shadow-3)] backdrop-blur-sm sm:p-8 lg:bg-[color:var(--panel)]">
          <h2 className="text-lg font-semibold text-[color:var(--ink)]">Entrar na plataforma</h2>
          <div className="mt-5 space-y-3.5">
            <label className="block">
              <span className="text-xs font-medium text-[color:var(--ink-soft)]">Email ou utilizador</span>
              <div className="mt-1 flex items-center gap-2 rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3 py-2.5">
                <Mail size={15} className="text-[color:var(--ink-soft)]" />
                <input type="text" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="admin ou seu@email.com" className="w-full bg-transparent text-sm outline-none placeholder:text-[color:var(--ink-soft)]/50" />
              </div>
            </label>
            <label className="block">
              <span className="text-xs font-medium text-[color:var(--ink-soft)]">Senha</span>
              <div className="mt-1 flex items-center gap-2 rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3 py-2.5">
                <Lock size={15} className="text-[color:var(--ink-soft)]" />
                <input type={showPassword ? 'text' : 'password'} value={password} onChange={(e) => setPassword(e.target.value)} required placeholder="••••••" className="w-full bg-transparent text-sm outline-none placeholder:text-[color:var(--ink-soft)]/50" />
                <button type="button" onClick={() => setShowPassword(!showPassword)} className="text-[color:var(--ink-soft)]">{showPassword ? <EyeOff size={15} /> : <Eye size={15} />}</button>
              </div>
            </label>
          </div>
          <button type="submit" disabled={loading} className="mt-4 flex w-full items-center justify-center gap-2 rounded-[var(--radius-md)] bg-[color:var(--accent)] px-4 py-2.5 text-sm font-medium text-white transition-all hover:bg-[color:var(--accent-hover)] active:scale-[0.97] disabled:opacity-50">
            {loading ? <><span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin" /> A entrar...</> : 'Entrar'}
          </button>
          {onSwitchToRegister ? (
            <button type="button" onClick={onSwitchToRegister} className="mt-3 w-full text-center text-xs text-[color:var(--accent)] hover:underline">Criar uma conta</button>
          ) : null}
        </form>
      </div>
      </div>
    </div>
  )
}

function RegisterScreen({ onRegister, onSwitchToLogin, loading, theme, onToast }) {
  const [form, setForm] = useState({ name: '', email: '', phone: '', password: '', confirm: '' })
  const [showPw, setShowPw] = useState(false)

  const update = (k, v) => setForm((p) => ({ ...p, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (form.password !== form.confirm) { onToast?.({ message: 'Senhas não coincidem', type: 'error' }); return }
    if (form.password.length < 6) { onToast?.({ message: 'Senha deve ter pelo menos 6 caracteres', type: 'error' }); return }
    try { await onRegister(form.name, form.email, form.phone, form.password) }
    catch (err) { onToast?.({ message: err.message || 'Falha ao criar conta', type: 'error' }) }
  }

  return (
    <div data-theme={theme} className="relative flex min-h-[100dvh] bg-[color:var(--bg)] text-[color:var(--ink)]">
      {/* Background image — mobile only; desktop uses left-panel bg */}
      <div
        className="absolute inset-0 bg-cover bg-center opacity-[0.08] lg:hidden"
        style={{ backgroundImage: `url(/bg-auth.png)` }}
      />
      <div className="absolute inset-0 bg-gradient-to-b from-black/20 to-black/40 lg:hidden" />
      {/* Left panel — branding + background (desktop only) */}
      <div className="relative hidden w-1/2 lg:flex items-center justify-center overflow-hidden bg-[color:var(--panel-muted)]">
        <div
          className="absolute inset-0 bg-cover bg-center opacity-40"
          style={{ backgroundImage: `url(/bg-auth.png)` }}
        />
        <div className="absolute inset-0 bg-gradient-to-br from-[color:var(--accent)]/30 to-transparent" />
        <div className="relative z-10 flex flex-col items-center px-12 text-center">
          <div className="grid h-20 w-20 place-items-center rounded-[2rem] bg-[color:var(--accent)] text-white shadow-[var(--shadow-3)]">
            <Landmark size={36} />
          </div>
          <h1 className="mt-6 font-[family-name:var(--font-serif)] text-4xl font-semibold text-[color:var(--ink)]">jURIS-APP</h1>
          <p className="mt-3 max-w-xs text-base leading-relaxed text-[color:var(--ink-soft)]">
            Assistente juridico angolano com inteligencia artificial. Pesquise leis, carregue documentos, consulte jurisprudencia.
          </p>
        </div>
      </div>

      {/* Right panel — register form */}
      <div className="relative z-10 flex w-full items-center justify-center px-4 lg:w-1/2">
      <div className="w-full max-w-[420px]">
        <div className="mb-8 flex items-center justify-center gap-3 lg:hidden">
          <div className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-[color:var(--accent)] text-white shadow-[var(--shadow-2)]">
            <Landmark size={22} />
          </div>
          <h1 className="font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)]">jURIS-APP</h1>
        </div>
        <form onSubmit={handleSubmit} className="animate-[fadeIn_0.4s_ease-out] rounded-[var(--radius-xl)] border border-[color:var(--stroke)] bg-[color:var(--panel)]/95 p-6 shadow-[var(--shadow-3)] backdrop-blur-sm sm:p-8 lg:bg-[color:var(--panel)]">
          <h2 className="text-lg font-semibold text-[color:var(--ink)]">Criar conta</h2>
          <div className="mt-5 space-y-3">
            <Input icon={<User size={15} />} label="Nome completo" value={form.name} onChange={(v) => update('name', v)} required />
            <Input icon={<Mail size={15} />} label="Email" type="email" value={form.email} onChange={(v) => update('email', v)} required />
            <Input icon={<Phone size={15} />} label="Telefone (opcional)" value={form.phone} onChange={(v) => update('phone', v)} placeholder="+244 9XX XXX XXX" />
            <Input icon={<Lock size={15} />} label="Senha" type={showPw ? 'text' : 'password'} value={form.password} onChange={(v) => update('password', v)} required suffix={<button type="button" onClick={() => setShowPw(!showPw)} className="text-[color:var(--ink-soft)]">{showPw ? <EyeOff size={15} /> : <Eye size={15} />}</button>} />
            <Input icon={<Lock size={15} />} label="Confirmar senha" type="password" value={form.confirm} onChange={(v) => update('confirm', v)} required />
          </div>
          <button type="submit" disabled={loading} className="mt-4 flex w-full items-center justify-center gap-2 rounded-[var(--radius-md)] bg-[color:var(--accent)] px-4 py-2.5 text-sm font-medium text-white transition-all hover:bg-[color:var(--accent-hover)] active:scale-[0.97] disabled:opacity-50">
            {loading ? <><span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin" /> A criar conta...</> : 'Criar conta'}
          </button>
          {onSwitchToLogin ? (
            <button type="button" onClick={onSwitchToLogin} className="mt-3 w-full text-center text-xs text-[color:var(--accent)] hover:underline">Ja tenho conta — entrar</button>
          ) : null}
        </form>
      </div>
      </div>
    </div>
  )
}

function Input({ icon, label, type, value, onChange, required, placeholder, suffix }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-[color:var(--ink-soft)]">{label}</span>
      <div className="mt-1 flex items-center gap-2 rounded-[var(--radius-md)] border border-[color:var(--stroke)] bg-[color:var(--bg)] px-3 py-2.5">
        <span className="text-[color:var(--ink-soft)]">{icon}</span>
        <input type={type || 'text'} value={value} onChange={(e) => onChange(e.target.value)} required={required} placeholder={placeholder || ''} className="w-full bg-transparent text-sm outline-none placeholder:text-[color:var(--ink-soft)]/50" />
        {suffix}
      </div>
    </label>
  )
}

export function App() {
  const { theme, toggleTheme } = useTheme()
  const { token, user, loading: authLoading, isAuthenticated, login, register, logout, updatePreferences, getPreferences } = useAuth()
  const {
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
        updateMessageContent,
        navigateEditVersion,
        setConversationActiveDocument,
    addUploadedDocument,
    removeDocument,
    hydrateFromServer,
  } = useAppState(token)
  const [healthOk, setHealthOk] = useState(false)
  const [toast, setToast] = useState(null)
  const [showTour, setShowTour] = useState(false)
  const [authScreen, setAuthScreen] = useState('login')
  const prevAuthRef = useRef(false)

  useEffect(() => {
    let mounted = true
    const checkHealth = () => {
      fetchHealth()
        .then((payload) => {
          if (mounted) setHealthOk(payload.status === 'ok')
        })
        .catch(() => {
          if (mounted) setHealthOk(false)
        })
    }
    checkHealth()
    const timer = setInterval(checkHealth, 30000)
    const onVisible = () => {
      if (document.visibilityState === 'visible') checkHealth()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      mounted = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  useEffect(() => {
    if (isAuthenticated && !prevAuthRef.current && user) {
      setToast({ message: `Bem-vindo, ${user.name}` })
      startNewConversation()
      const onboardingDone = localStorage.getItem(STORAGE_KEYS.onboardingDone)
      const hasProfessionalWorkspace = user?.professional_profile?.status === 'active' || user?.role === 'admin'
      if (!onboardingDone && !hasProfessionalWorkspace) {
        setShowTour(true)
      }
    }
    prevAuthRef.current = isAuthenticated
  }, [isAuthenticated, user])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 3000)
    return () => clearTimeout(timer)
  }, [toast])

  const handleLogin = async (email, password) => {
    try { return await login(email, password) }
    catch (err) { throw err }
  }

  const handleRegister = async (name, email, phone, password) => {
    try {
      const userData = await register(name, email, phone, password)
      localStorage.removeItem(STORAGE_KEYS.onboardingDone)
      setShowTour(true)
      return userData
    } catch (err) { throw err }
  }

  const handleTourFinish = async (prefs) => {
    setShowTour(false)
    localStorage.setItem(STORAGE_KEYS.onboardingDone, 'true')
    if (token) {
      try { await updatePreferences(token, prefs) } catch {}
    }
  }

  if (authLoading) {
    return (
      <div data-theme={theme} className="flex min-h-[100dvh] items-center justify-center bg-[color:var(--bg)] text-[color:var(--ink-soft)]">
        <div className="flex items-center gap-3">
          <span className="h-5 w-5 rounded-full border-2 border-[color:var(--accent)] border-t-transparent animate-spin" />
          <span className="text-sm">A carregar sessao...</span>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <>
        <Toast toast={toast} />
        {authScreen === 'register' ? (
          <RegisterScreen onRegister={handleRegister} onSwitchToLogin={() => setAuthScreen('login')} loading={authLoading} theme={theme} onToast={setToast} />
        ) : (
          <LoginScreen onLogin={handleLogin} onSwitchToRegister={() => setAuthScreen('register')} loading={authLoading} theme={theme} onToast={setToast} />
        )}
      </>
    )
  }

  return (
    <>
      <Toast toast={toast} />
      {showTour ? <OnboardingTour userName={user?.name || ''} onFinish={handleTourFinish} /> : null}
      <Routes>
        <Route path="/*" element={
          <AppShell
        healthOk={healthOk}
        theme={theme}
        onToggleTheme={toggleTheme}
        state={state}
        selectedConversation={selectedConversation}
        sidebarConversations={sidebarConversations}
        selectedMotor={selectedMotor}
        setMotor={setMotor}
        setActiveSection={setActiveSection}
        appendMessagePair={appendMessagePair}
        selectConversation={selectConversation}
        deleteConversation={deleteConversation}
        deleteAllConversations={deleteAllConversations}
        startNewConversation={startNewConversation}
        renameConversation={renameConversation}
        setConversationActiveDocument={setConversationActiveDocument}
        addUploadedDocument={addUploadedDocument}
        removeDocument={removeDocument}
        authToken={token}
        currentUser={user}
        onLogout={logout}
          onHydrateFromServer={hydrateFromServer}
          onPreferencesUpdate={updatePreferences}
          onUpdateMessageContent={updateMessageContent}
          onNavigateEditVersion={navigateEditVersion}
          onToast={setToast}
        />
        } />
      </Routes>
    </>
  )
}
