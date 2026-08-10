import { useState } from 'react'
import { API_BASE_URL } from '../../shared/constants/app'
import { SurfaceCard } from '../../shared/ui/SurfaceCard'
import { InfoTooltip } from '../../shared/ui/InfoTooltip'
import { BrainCircuit, Server, User, Mail, Phone, Save, MessageSquare, BookOpen, Sparkles, Scale, LayoutList, Check } from 'lucide-react'
import { updateProfileRequest } from '../../shared/services/apiClient'

const PREF_ICONS = {
  tone: <MessageSquare size={15} />,
  audience: <BookOpen size={15} />,
  detail_level: <Sparkles size={15} />,
  language_style: <Scale size={15} />,
  response_format: <LayoutList size={15} />,
}

const PREF_FIELDS = [
  { key: 'tone', label: 'Tom de resposta', desc: 'Como o assistente fala consigo',
    options: [
      { v: 'formal', l: 'Formal', d: 'Sério e profissional' },
      { v: 'didatico', l: 'Explicativo', d: 'Ensina como um professor' },
      { v: 'simples', l: 'Conversa', d: 'Linguagem do dia a dia' },
    ]},
  { key: 'audience', label: 'Nível de conhecimento', desc: 'O quanto sabe sobre leis',
    options: [
      { v: 'auto', l: 'Automático', d: 'O sistema escolhe por si' },
      { v: 'leigo', l: 'Básico', d: 'Não sabe nada de leis' },
      { v: 'tecnico', l: 'Avançado', d: 'É jurista ou advogado' },
    ]},
  { key: 'detail_level', label: 'Tamanho da resposta', desc: 'Quão longa deve ser a explicação',
    options: [
      { v: 'breve', l: 'Curta', d: 'Resposta direta, sem rodeios' },
      { v: 'normal', l: 'Normal', d: 'Nem curta nem longa' },
      { v: 'detalhado', l: 'Completa', d: 'Explicação aprofundada' },
    ]},
  { key: 'language_style', label: 'Tipo de linguagem', desc: 'Palavras técnicas ou simples',
    options: [
      { v: 'juridico', l: 'Jurídico', d: 'Termos técnicos de direito' },
      { v: 'acessivel', l: 'Acessível', d: 'Palavras que todos entendem' },
    ]},
  { key: 'response_format', label: 'Formato da resposta', desc: 'Como a resposta é organizada',
    options: [
      { v: 'auto', l: 'Automático', d: 'O sistema escolhe por si' },
      { v: 'paragrafos', l: 'Texto', d: 'Parágrafos corridos' },
      { v: 'topicos', l: 'Tópicos', d: 'Pontos organizados' },
    ]},
]

export function SettingsPage({ motor, onMotorChange, currentUser, authToken, onProfileUpdate, onPreferencesUpdate, onToast }) {
  const [profile, setProfile] = useState({
    name: currentUser?.name || '',
    email: currentUser?.email || '',
    phone: currentUser?.phone || '',
  })

  const [prefs, setPrefs] = useState({
    tone: currentUser?.ai_preferences?.tone || 'formal',
    audience: currentUser?.ai_preferences?.audience || 'auto',
    detail_level: currentUser?.ai_preferences?.detail_level || 'normal',
    language_style: currentUser?.ai_preferences?.language_style || 'acessivel',
    response_format: currentUser?.ai_preferences?.response_format || 'auto',
  })

  const saveProfile = async () => {
    try {
      await updateProfileRequest(authToken, profile.name, profile.email, profile.phone)
      onProfileUpdate?.()
      onToast?.({ message: 'Perfil atualizado', type: 'success' })
    } catch { onToast?.({ message: 'Erro ao guardar perfil', type: 'error' }) }
  }

  const savePrefs = async () => {
    try {
      await onPreferencesUpdate?.(authToken, prefs)
      onToast?.({ message: 'Preferências guardadas', type: 'success' })
    } catch { onToast?.({ message: 'Erro ao guardar preferências', type: 'error' }) }
  }

  return (
    <section className="fade-rise space-y-5 py-2">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="font-[family-name:var(--font-serif)] text-2xl font-semibold text-[color:var(--ink)] sm:text-3xl">Definições</h2>
          <InfoTooltip content="Configure o perfil pessoal, as preferências de IA e as informações do backend." />
        </div>
        <p className="mt-1.5 text-sm text-[color:var(--ink-soft)]">Personaliza o comportamento da interface e do assistente jurídico.</p>
      </div>

      {/* Profile */}
      <SurfaceCard className="p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[color:var(--accent)]/10">
            <User size={18} className="text-[color:var(--accent)]" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-[color:var(--ink)]">Perfil</h3>
            <p className="text-xs text-[color:var(--ink-soft)]">Os seus dados pessoais</p>
          </div>
        </div>
        <div className="space-y-3">
          <InputRow icon={<User size={14} />} label="Nome completo" value={profile.name} onChange={(v) => setProfile((p) => ({ ...p, name: v }))} />
          <InputRow icon={<Mail size={14} />} label="Email" value={profile.email} onChange={(v) => setProfile((p) => ({ ...p, email: v }))} type="email" />
          <InputRow icon={<Phone size={14} />} label="Telefone" value={profile.phone} onChange={(v) => setProfile((p) => ({ ...p, phone: v }))} />
          <div className="flex items-center gap-3">
            <button onClick={saveProfile} className="flex items-center gap-1.5 rounded-[var(--radius-md)] bg-[color:var(--accent)] px-3.5 py-2 text-xs font-medium text-white transition-all hover:bg-[color:var(--accent-hover)] active:scale-[0.97]">
              <Save size={12} /> Guardar perfil
            </button>
          </div>
        </div>
      </SurfaceCard>

      {/* AI Preferences */}
      <SurfaceCard className="p-5">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[color:var(--gold)]/10">
            <BrainCircuit size={18} className="text-[color:var(--gold)]" />
          </div>
          <div>
          <h3 className="text-sm font-semibold text-[color:var(--ink)]">Como a IA responde</h3>
          <p className="text-xs text-[color:var(--ink-soft)]">Personalize o comportamento do assistente</p>
          </div>
        </div>
        <div className="space-y-3">
          {PREF_FIELDS.map(({ key, label, desc, options }) => (
            <div key={key} className="rounded-[var(--radius-lg)] border border-[color:var(--stroke)] bg-[color:var(--panel-muted)] p-3.5 transition-all">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-[color:var(--gold)]">{PREF_ICONS[key]}</span>
                <div>
                  <p className="text-sm font-semibold text-[color:var(--ink)]">{label}</p>
                  <p className="text-[11px] text-[color:var(--ink-soft)]">{desc}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {options.map((opt) => {
                  const selected = prefs[key] === opt.v
                  return (
                    <button
                      key={opt.v}
                      type="button"
                      onClick={() => setPrefs((p) => ({ ...p, [key]: opt.v }))}
                      className={`flex items-center gap-1.5 rounded-[var(--radius-md)] border px-2.5 py-2 text-[11px] font-medium transition-all sm:gap-2 sm:px-3 sm:py-2.5 sm:text-xs ${
                        selected
                          ? 'border-[color:var(--gold)] bg-[color:var(--gold)]/10 text-[color:var(--gold)] shadow-[var(--shadow-1)]'
                          : 'border-[color:var(--stroke)] bg-[color:var(--bg)] text-[color:var(--ink-soft)] hover:border-[color:var(--ink-soft)]/30'
                      }`}
                    >
                      <div className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border-2 transition-all sm:h-5 sm:w-5 ${
                        selected
                          ? 'border-[color:var(--gold)] bg-[color:var(--gold)] text-white'
                          : 'border-[color:var(--stroke)]'
                      }`}>
                        {selected && <Check size={10} strokeWidth={3} />}
                      </div>
                      <span>{opt.l}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
          <div className="flex items-center gap-3">
            <button onClick={savePrefs} className="flex items-center gap-1.5 rounded-[var(--radius-md)] bg-[color:var(--gold)] px-3.5 py-2 text-xs font-medium text-white transition-all hover:brightness-110 active:scale-[0.97]">
              <Save size={12} /> Guardar preferências
            </button>
          </div>
        </div>
      </SurfaceCard>

      {/* Motor / API */}
      <SurfaceCard className="p-5">
        <div className="flex items-center gap-3">
          <Server size={20} className="text-[color:var(--ink-soft)]" />
          <div>
            <h3 className="text-sm font-semibold text-[color:var(--ink)]">Backend API</h3>
            <p className="mt-0.5 text-xs font-mono text-[color:var(--accent)]">{API_BASE_URL}</p>
          </div>
        </div>
      </SurfaceCard>
    </section>
  )
}

function InputRow({ icon, label, value, onChange, type }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="shrink-0 text-[color:var(--ink-soft)]">{icon}</span>
      <div className="flex-1">
        <span className="text-[10px] font-medium text-[color:var(--ink-soft)]">{label}</span>
        <input type={type || 'text'} value={value} onChange={(e) => onChange(e.target.value)}
          className="mt-0.5 w-full rounded-[var(--radius-sm)] border border-[color:var(--stroke)] bg-[color:var(--bg)] px-2.5 py-1.5 text-sm outline-none focus:border-[color:var(--accent)]" />
      </div>
    </div>
  )
}
