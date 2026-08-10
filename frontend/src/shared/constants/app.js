export const STORAGE_KEYS = {
  theme: 'tribunal:theme',
  appState: 'tribunal:app-state',
  auth: 'tribunal:auth',
  onboardingDone: 'tribunal:onboarding-done',
}

export const NAV_ITEMS = [
  { id: 'chat', label: 'Nova Consulta' },
  { id: 'documents', label: 'Meus Documentos' },
  { id: 'library', label: 'Biblioteca Juridica' },
  { id: 'pro', label: 'Modo Pro', proOnly: true },
  { id: 'settings', label: 'Definicoes' },
  { id: 'admin', label: 'Administracao', adminOnly: true },
]

export const MOTORS = [
  { id: 'motorD', label: 'DeepSeek', provider: 'deepseek' },
]

export const MAX_PDF_UPLOAD_MB = 15
export const MAX_PDF_UPLOAD_BYTES = MAX_PDF_UPLOAD_MB * 1024 * 1024

function isLocalHostname(hostname) {
  if (!hostname) return false
  const normalized = hostname.trim().toLowerCase()
  if (normalized === 'localhost' || normalized === '127.0.0.1' || normalized === '::1') {
    return true
  }
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(normalized)) {
    return (
      normalized.startsWith('10.') ||
      normalized.startsWith('192.168.') ||
      /^172\.(1[6-9]|2\d|3[0-1])\./.test(normalized)
    )
  }
  return normalized.endsWith('.local')
}

function resolveRuntimeApiBaseUrl() {
  if (typeof window === 'undefined') return 'http://127.0.0.1:8000'

  const hostname = window.location.hostname
  if (isLocalHostname(hostname)) {
    return `${window.location.protocol}//${hostname}:8000`
  }

  if (hostname === 'jurisapp.pages.dev' || hostname.endsWith('.jurisapp.pages.dev')) {
    return 'https://juris-app-backend.onrender.com'
  }

  return window.location.origin
}

export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL || '').trim() ||
  resolveRuntimeApiBaseUrl()
