import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { ArrowDown, BookOpen, Gavel, MessageCircle, Scale, ShieldAlert, Sparkles } from 'lucide-react'
import { ErrorBanner } from '../../shared/ui/ErrorBanner'
import { StreamingLoader } from '../../shared/ui/StreamingLoader'
import { EmptyState } from '../../shared/ui/EmptyState'
import { useVoice } from '../../shared/hooks/useVoice'
import { ChatMessage } from '../../shared/ui/ChatMessage'
import { ChatComposer } from '../../shared/ui/ChatComposer'
import { InfoTooltip } from '../../shared/ui/InfoTooltip'
import { formatNow } from '../../shared/utils/format'
import { cleanAnswerBody } from '../../shared/utils/markdown'
import { sendChatQuestionStream, uploadPdfDocument } from '../../shared/services/apiClient'
import { API_BASE_URL, MAX_PDF_UPLOAD_BYTES, MAX_PDF_UPLOAD_MB } from '../../shared/constants/app'

let _ttsAudio = null
function stopTTS() { try { if (_ttsAudio) { _ttsAudio.pause(); _ttsAudio = null } } catch {} }

const SUGGESTED_BASE_QUESTIONS = [
  {
    icon: Scale,
    tag: 'Laboral',
    text: 'Quais são os direitos do trabalhador em caso de despedimento?',
    description: 'Uma dúvida prática e muito comum para começar a usar a app.',
  },
  {
    icon: BookOpen,
    tag: 'Penal',
    text: 'O que diz o Código Penal sobre o crime de burla?',
    description: 'Boa para validar resposta com base legal directa e artigos citados.',
  },
  {
    icon: MessageCircle,
    tag: 'Processual',
    text: 'Como funciona a prisão preventiva em Angola?',
    description: 'Ajuda a testar explicações mais técnicas em linguagem acessível.',
  },
  {
    icon: Gavel,
    tag: 'Administrativo',
    text: 'Qual o prazo para contestar um acto administrativo?',
    description: 'Exemplo útil de prazo e orientação prática.',
  },
]

function cleanForTTS(text) {
  if (!text) return ''
  return text
    .replace(/```[\s\S]*?```/g, ' ')           // code blocks
    .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1')   // links -> text
    .replace(/\*\*([^*]+)\*\*/g, '$1')          // bold
    .replace(/\*([^*]+)\*/g, '$1')              // italic
    .replace(/^#{1,6}\s*/gm, '')                // headers
    .replace(/^[*-]\s+/gm, '')                  // bullet lists
    .replace(/^[0-9]+\.\s+/gm, '')              // numbered lists
    .replace(/\n{3,}/g, '. ')                   // multiple newlines
    .replace(/\n/g, '. ')                       // single newlines
    .replace(/\s{2,}/g, ' ')                    // extra spaces
    .replace(/^[.;,]\s*/g, '')                  // leading punctuation
    .trim()
}

async function speakTTS(text, token) {
  stopTTS()
  if (!text || !token) return
  try {
    const resp = await fetch(`${API_BASE_URL}/api/tts/speak`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'text/plain' },
      body: text.slice(0, 800),
    })
    if (!resp.ok) return
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    _ttsAudio = new Audio(url)
    _ttsAudio.play().catch(() => {})
    _ttsAudio.onended = () => { _ttsAudio = null }
  } catch (e) { _ttsAudio = null }
}

export function ChatWorkspace({
  selectedConversation,
  editSessions,
  draftActiveDocumentId,
  documents,
  provider,
  onAppendMessagePair,
  onSelectSourceRef,
  onSetConversationActiveDocument,
  onAddUploadedDocument,
  authToken,
  currentUser,
  onRefreshAppState,
  onToast,
  onUpdateMessageContent,
  onNavigateEditVersion,
  mobileChromeVisible = true,
  onMobileChromeVisibilityChange,
}) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [voiceState, setVoiceState] = useState('idle')
  const [pendingUserQuestion, setPendingUserQuestion] = useState('')
  const [loadingElapsedMs, setLoadingElapsedMs] = useState(0)
  const [showBackToBottom, setShowBackToBottom] = useState(false)
  const [pendingAttachment, setPendingAttachment] = useState(null)
  const [clarifyingChosen, setClarifyingChosen] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [streamingPhase, setStreamingPhase] = useState('idle')
  const [conversationLoading, setConversationLoading] = useState(false)
  const [editingMsgId, setEditingMsgId] = useState(null)
  const [composerFocused, setComposerFocused] = useState(false)
  const [usageMeta, setUsageMeta] = useState(currentUser?.usage || null)
  const prevConversationIdRef = useRef(null)
  const questionRef = useRef('')
  const editIndexRef = useRef(-1)
  const voiceModeRef = useRef(false)
  const voiceSubmittedRef = useRef(false)

  // Voice hook
  const {
    voiceState: voiceHookState,
    interimText,
    analyserNode,
    startListening,
    stopListening,
    cancelListening,
    getTranscript,
  } = useVoice(authToken)
  const loadingStartedAtRef = useRef(0)
  const scrollContainerRef = useRef(null)
  const shouldAutoScrollRef = useRef(true)
  const fileInputRef = useRef(null)
  const activeStreamRef = useRef(null)
  const abortRef = useRef(null)
  const responseStartRef = useRef(null)
  const pendingResponseScrollRef = useRef(false)
  const finalResponseScrollRef = useRef(false)
  const lastScrollTopRef = useRef(0)
  const touchStartYRef = useRef(null)
  const chromeVisibilityLockUntilRef = useRef(0)
  const mobileChromeVisibleRef = useRef(mobileChromeVisible)
  const composerFocusedRef = useRef(false)
  const pendingAttachmentRef = useRef(null)

  useEffect(() => {
    mobileChromeVisibleRef.current = mobileChromeVisible
  }, [mobileChromeVisible])

  useEffect(() => {
    composerFocusedRef.current = composerFocused
  }, [composerFocused])

  useEffect(() => {
    pendingAttachmentRef.current = pendingAttachment
  }, [pendingAttachment])

  useEffect(() => {
    setUsageMeta(currentUser?.usage || null)
  }, [currentUser?.usage])

  const activeDocumentIdForDisplay = selectedConversation?.activeDocumentId || draftActiveDocumentId || null
  const activeDocument = useMemo(
    () => documents.find((document) => document.id === activeDocumentIdForDisplay) || null,
    [documents, activeDocumentIdForDisplay],
  )

  const userMessages = useMemo(() => {
    if (!selectedConversation?.messages?.length) return []
    return selectedConversation.messages
      .filter(m => m.role === 'user')
      .map(m => m.content)
  }, [selectedConversation])

  const emptyStateSuggestions = useMemo(() => {
    const latestUserTopics = userMessages.slice(-2)
    const activeDocumentLabel = activeDocument?.display_name || activeDocument?.title || 'o documento ativo'

    if (activeDocument) {
      return [
        {
          icon: BookOpen,
          tag: 'Documento',
          text: `Resume os pontos jurídicos principais de ${activeDocumentLabel}.`,
          description: 'Extrai ideias centrais, artigos e riscos mais relevantes.',
        },
        {
          icon: Scale,
          tag: 'Análise',
          text: `Quais obrigações, prazos ou riscos aparecem em ${activeDocumentLabel}?`,
          description: 'Foca-se em deveres, datas-limite e eventuais incumprimentos.',
        },
        {
          icon: Gavel,
          tag: 'Comparação',
          text: `Compara ${activeDocumentLabel} com a legislação angolana aplicável.`,
          description: 'Cruza o documento com a base legal oficial da app.',
        },
        {
          icon: ShieldAlert,
          tag: 'Próximo passo',
          text: `Que próximos passos jurídicos devo seguir com base em ${activeDocumentLabel}?`,
          description: 'Sugere a sequência prática mais útil para continuar.',
        },
      ]
    }

    if (latestUserTopics.length > 0) {
      return [
        {
          icon: Sparkles,
          tag: 'Follow-up',
          text: `Explica isto em linguagem mais simples: ${latestUserTopics[latestUserTopics.length - 1]}`,
          description: 'Boa opção para transformar uma dúvida anterior em resposta mais acessível.',
        },
        {
          icon: MessageCircle,
          tag: 'Checklist',
          text: 'Que documentos ou informações preciso reunir antes de avançar com o meu caso?',
          description: 'Ajuda um cidadão comum a preparar-se antes de procurar apoio formal.',
        },
        ...SUGGESTED_BASE_QUESTIONS.slice(0, 2),
      ]
    }

    return SUGGESTED_BASE_QUESTIONS
  }, [activeDocument, userMessages])

  const quotaReached = Boolean(
    usageMeta &&
    !usageMeta.daily_limit_exempt &&
    (usageMeta.daily_message_limit || 0) > 0 &&
    (usageMeta.messages_remaining_today || 0) <= 0,
  )

  const quotaMessage = useMemo(() => {
    if (!usageMeta || usageMeta.daily_limit_exempt) return ''
    const limit = usageMeta.daily_message_limit || 0
    const used = usageMeta.messages_used_today || 0
    const remaining = usageMeta.messages_remaining_today ?? Math.max(0, limit - used)
    if (limit <= 0) return ''
    if (remaining <= 0) {
      return `Atingiu o limite diário de ${limit} mensagens. Volta amanhã ou pede ajuste ao administrador.`
    }
    return `${remaining} de ${limit} mensagens disponíveis hoje.`
  }, [usageMeta])

  const consumeQuotaLocally = () => {
    setUsageMeta((prev) => {
      if (!prev || prev.daily_limit_exempt || (prev.daily_message_limit || 0) <= 0) return prev
      const nextUsed = (prev.messages_used_today || 0) + 1
      const nextRemaining = Math.max(0, (prev.daily_message_limit || 0) - nextUsed)
      return {
        ...prev,
        messages_used_today: nextUsed,
        messages_remaining_today: nextRemaining,
      }
    })
  }

  const scrollToBottom = (smooth = true) => {
    const element = scrollContainerRef.current
    if (!element) return
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }

  const scrollToResponseStart = (smooth = true) => {
    const container = scrollContainerRef.current
    const target = responseStartRef.current
    if (!container || !target) return false
    const containerTop = container.getBoundingClientRect().top
    const targetTop = target.getBoundingClientRect().top
    const offset = isMobileViewport() ? 10 : 16
    const nextTop = container.scrollTop + targetTop - containerTop - offset
    container.scrollTo({ top: Math.max(0, nextTop), behavior: smooth ? 'smooth' : 'auto' })
    return true
  }

  const isMobileViewport = () => (
    typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
  )

  const setMobileChromeVisibleIfNeeded = (visible) => {
    if (Date.now() < chromeVisibilityLockUntilRef.current) return
    if (mobileChromeVisibleRef.current === visible) return
    mobileChromeVisibleRef.current = visible
    chromeVisibilityLockUntilRef.current = Date.now() + 260
    if (typeof window !== 'undefined') {
      window.requestAnimationFrame(() => onMobileChromeVisibilityChange?.(visible))
    } else {
      onMobileChromeVisibilityChange?.(visible)
    }
  }

  const handleMobileScrollIntent = (direction) => {
    if (!isMobileViewport()) return
    const element = scrollContainerRef.current
    if (!element) return
    const isEmptyConversation = !loading && (!selectedConversation || (selectedConversation.messages?.length || 0) === 0)
    if (isEmptyConversation) return
    const hasScrollableContent = element.scrollHeight > element.clientHeight + 56
    if (!hasScrollableContent) return
    if (composerFocusedRef.current || pendingAttachmentRef.current) {
      setMobileChromeVisibleIfNeeded(true)
      return
    }
    const currentTop = Math.max(0, element.scrollTop)
    const distanceToBottom = element.scrollHeight - currentTop - element.clientHeight
    if (direction === 'down' && currentTop > 72 && distanceToBottom > 28) {
      setMobileChromeVisibleIfNeeded(false)
    } else if (direction === 'up' || currentTop < 32) {
      setMobileChromeVisibleIfNeeded(true)
    }
  }

  const handleMessagesScroll = () => {
    const element = scrollContainerRef.current
    if (!element) return
    const isEmptyConversation = !loading && (!selectedConversation || (selectedConversation.messages?.length || 0) === 0)
    if (isEmptyConversation) {
      setShowBackToBottom(false)
      shouldAutoScrollRef.current = true
      setMobileChromeVisibleIfNeeded(true)
      lastScrollTopRef.current = 0
      return
    }
    const thresholdPx = 72
    const currentTop = Math.max(0, element.scrollTop)
    const distanceToBottom = element.scrollHeight - currentTop - element.clientHeight
    const hasScrollableContent = element.scrollHeight > element.clientHeight + 24
    const isNearBottom = distanceToBottom <= thresholdPx
    setShowBackToBottom(hasScrollableContent && !isNearBottom)
    shouldAutoScrollRef.current = isNearBottom
    const scrollDelta = currentTop - lastScrollTopRef.current
    if (hasScrollableContent && Math.abs(scrollDelta) > 18) {
      handleMobileScrollIntent(scrollDelta > 0 ? 'down' : 'up')
    }
    lastScrollTopRef.current = currentTop
  }

  useEffect(() => {
    if (!loading) {
      loadingStartedAtRef.current = 0
      return undefined
    }
    if (!loadingStartedAtRef.current) loadingStartedAtRef.current = Date.now()
    const tick = () => setLoadingElapsedMs(Math.max(0, Date.now() - loadingStartedAtRef.current))
    tick()
    const id = window.setInterval(tick, 300)
    return () => window.clearInterval(id)
  }, [loading])

  const messages = useMemo(() => {
    if (!selectedConversation) return []
    const raw = selectedConversation.messages || []
    const sessions = editSessions || []
    const editVersionMap = {}
    sessions.forEach(s => {
      editVersionMap[s.editIndex] = {
        count: s.versions.length,
        current: s.activeVersion ?? (s.versions.length - 1),
        editIndex: s.editIndex,
      }
    })
    const persisted = raw.map((message, idx) => ({
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: message.createdAt,
      sources: message.sources || [],
      answer_mode: message.answer_mode || null,
      confidence: message.confidence || null,
      validation_issues: message.validation_issues || [],
      clarifying_questions: message.clarifying_questions || [],
      legal_basis: message.legal_basis || [],
      verified_articles: message.verified_articles || [],
      classification: message.classification || null,
      suggested_actions: message.suggested_actions || [],
      versionInfo: editVersionMap[idx] || null,
    }))
    if (loading && pendingUserQuestion) {
      return [...persisted, { id: 'pending-user', role: 'user', content: pendingUserQuestion, createdAt: '', sources: [] }]
    }
    return persisted
  }, [selectedConversation, provider, loading, pendingUserQuestion, editSessions])

  useEffect(() => {
    if (loading && pendingResponseScrollRef.current) {
      const id = window.requestAnimationFrame(() => {
        if (scrollToResponseStart(false)) {
          shouldAutoScrollRef.current = false
          pendingResponseScrollRef.current = false
          handleMessagesScroll()
        }
      })
      return () => window.cancelAnimationFrame(id)
    }
    if (!loading && finalResponseScrollRef.current) {
      const id = window.requestAnimationFrame(() => {
        if (scrollToResponseStart(true)) {
          shouldAutoScrollRef.current = false
          finalResponseScrollRef.current = false
          handleMessagesScroll()
        }
      })
      return () => window.cancelAnimationFrame(id)
    }
    if (shouldAutoScrollRef.current) scrollToBottom(loading ? false : true)
    return undefined
  }, [loading, messages.length])

  useEffect(() => {
    const id = window.setTimeout(() => handleMessagesScroll(), 0)
    return () => window.clearTimeout(id)
  }, [messages.length, loading, streamingContent])

  useEffect(() => {
    const element = scrollContainerRef.current
    if (!element) return undefined
    const onResize = () => handleMessagesScroll()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    const currentId = selectedConversation?.id || null
    lastScrollTopRef.current = 0
    mobileChromeVisibleRef.current = true
    onMobileChromeVisibilityChange?.(true)
    if (prevConversationIdRef.current !== null && currentId !== prevConversationIdRef.current) {
      const hasMessages = selectedConversation?.messages?.length > 0
      if (hasMessages) {
        setConversationLoading(true)
        const timer = window.setTimeout(() => setConversationLoading(false), 300)
        prevConversationIdRef.current = currentId
        return () => window.clearTimeout(timer)
      }
    }
    prevConversationIdRef.current = currentId
    shouldAutoScrollRef.current = true
    scrollToBottom(false)
  }, [selectedConversation?.id])

  const conversationHistory = useMemo(() => {
    if (!selectedConversation?.messages?.length) return []
    return selectedConversation.messages.slice(-10).map((message) => {
      const prefix = message.role === 'assistant' ? 'Assistente' : 'Utilizador'
      const compact = String(message.content || '')
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1')
        .replace(/\s+/g, ' ')
        .trim()
      const snippet = compact.length > 2000 ? `${compact.slice(0, 2000)}...` : compact
      return `${prefix}: ${snippet}`
    })
  }, [selectedConversation])

  const handleEditUserMessage = useCallback((msgId) => {
    setEditingMsgId(msgId)
  }, [])

  const cancelStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    if (activeStreamRef.current) {
      activeStreamRef.current.cancelled = true
    }
    setLoading(false)
    setLoadingElapsedMs(0)
    setStreamingPhase('idle')
    setStreamingContent('')
  }, [])

  const handleClarifyingSelect = (text) => {
    setQuestion(text)
    setClarifyingChosen(true)
  }

  const handleSubmit = async (event) => {
    if (event) event.preventDefault()
    const normalized = (questionRef.current || question).trim()
    if ((normalized.length < 5 && !pendingAttachment) || loading || quotaReached) {
      if (quotaReached) {
        setError(quotaMessage || 'Limite diário de mensagens atingido.')
      }
      return
    }
    const attachmentToUpload = pendingAttachment

    setQuestion('')  // Clear immediately for UX

    let uploadedDocument = null
    setLoading(true)
    loadingStartedAtRef.current = Date.now()
    shouldAutoScrollRef.current = true
    pendingResponseScrollRef.current = true
    finalResponseScrollRef.current = false
    setError('')
    setPendingUserQuestion(normalized || (attachmentToUpload ? `A processar ${attachmentToUpload.name}` : 'Processar PDF anexado'))
    setClarifyingChosen(false)
    setStreamingContent('')
    setStreamingPhase(attachmentToUpload ? 'uploading' : 'classifying')

    let accumulated = ''
    const ctx = { cancelled: false }
    activeStreamRef.current = ctx

    try {
      if (attachmentToUpload) {
        setStreamingPhase('uploading')
        uploadedDocument = await uploadPdfDocument(attachmentToUpload, authToken)
        const mapped = onAddUploadedDocument?.(uploadedDocument) || uploadedDocument
        try { await onRefreshAppState?.() } catch { /* keep optimistic */ }
        if (selectedConversation?.id) {
          onSetConversationActiveDocument?.(selectedConversation.id, mapped.id)
        } else {
          onSetConversationActiveDocument?.(null, mapped.id)
        }
        setPendingAttachment(null)
        setStreamingPhase('classifying')
      }

      const activeDocumentId = uploadedDocument?.id || selectedConversation?.activeDocumentId || draftActiveDocumentId || null

      // Cancel any previous request
      if (abortRef.current) abortRef.current.abort()
      const abortController = new AbortController()
      abortRef.current = abortController

      const stream = await sendChatQuestionStream(
        normalized || 'Analise o PDF anexado e aguarde instrucoes posteriores.',
        provider,
        conversationHistory,
        selectedConversation?.id || null,
        activeDocumentId,
        authToken,
        abortController.signal,
      )

      setStreamingPhase('retrieving')
      let finalMeta = null

      for await (const chunk of stream) {
        if (ctx.cancelled) break
        if (chunk.phase) {
          setStreamingPhase(chunk.phase)
        }
        if (chunk.token) {
          setStreamingPhase('composing')
          accumulated += chunk.token
          setStreamingContent(accumulated)
        }
        if (chunk.done) {
          finalMeta = chunk
        }
      }

      if (!ctx.cancelled && finalMeta) {
        const cleanAnswer = cleanAnswerBody(finalMeta.answer || accumulated || '')
        consumeQuotaLocally()
        finalResponseScrollRef.current = true
        onAppendMessagePair({
          chat_id: finalMeta.chat_id,
          question: normalized || 'Analise o PDF anexado e aguarde instrucoes posteriores.',
          answer: cleanAnswer,
          sources: finalMeta.sources || [],
          provider_used: finalMeta.provider_used,
          createdAt: formatNow(),
          active_document_id: finalMeta.active_document_id,
          answer_mode: finalMeta.answer_mode || 'limited',
          confidence: finalMeta.confidence,
          validation_issues: finalMeta.validation_issues || [],
          clarifying_questions: finalMeta.clarifying_questions || [],
          legal_basis: finalMeta.legal_basis || [],
          verified_articles: finalMeta.verified_articles || [],
          classification: finalMeta.classification || null,
          suggested_actions: finalMeta.suggested_actions || [],
          editMessageIndex: editIndexRef.current,
        })
        if (voiceModeRef.current && cleanAnswer) {
          voiceModeRef.current = false
          speakTTS(cleanForTTS(cleanAnswer), authToken)
        }
        } else if (accumulated) {
          consumeQuotaLocally()
          finalResponseScrollRef.current = true
          onAppendMessagePair({
          chat_id: selectedConversation?.id || '',
          question: normalized || 'Analise o PDF anexado e aguarde instrucoes posteriores.',
          answer: cleanAnswerBody(accumulated),
          sources: [],
          provider_used: provider || 'deepseek',
          createdAt: formatNow(),
          active_document_id: activeDocumentId,
          answer_mode: 'grounded',
          confidence: null,
          validation_issues: [],
          clarifying_questions: [],
          legal_basis: [],
          verified_articles: [],
          classification: null,
          editMessageIndex: editIndexRef.current,
        })
      }
      setQuestion('')
      setVoiceState('idle')
      setPendingUserQuestion('')
      setStreamingContent('')
      setStreamingPhase('idle')
      editIndexRef.current = -1
      questionRef.current = ''
    } catch (err) {
      setError(err.message || 'Falha ao consultar o backend.')
      if ((err.message || '').includes('Limite diário')) {
        setUsageMeta((prev) => (
          prev
            ? { ...prev, messages_remaining_today: 0 }
            : prev
        ))
        onToast?.({ message: err.message || 'Limite diário atingido', type: 'error' })
      }
      setPendingUserQuestion('')
      setStreamingContent('')
      setStreamingPhase('idle')
      questionRef.current = ''
    } finally {
      setLoading(false)
      setLoadingElapsedMs(0)
      activeStreamRef.current = null
      abortRef.current = null
    }
  }

  const handleCancelEdit = () => {
    setEditingMsgId(null)
  }

  const handleSaveEdit = (msgId, newText) => {
    const msgs = selectedConversation?.messages || []
    const idx = msgs.findIndex(m => m.id === msgId)
    if (idx < 0) return
    editIndexRef.current = idx
    setEditingMsgId(null)
    questionRef.current = newText
    setQuestion(newText)
    handleSubmit(null)
  }

  const handleAISaveEdit = (msgId, newText) => {
    setEditingMsgId(null)
    onUpdateMessageContent(msgId, newText)
  }

  const handleVoiceToggle = () => {
    if (voiceHookState === 'idle') {
      voiceSubmittedRef.current = false
      startListening({
        onTranscript: (text) => {
          if (voiceSubmittedRef.current) return
          voiceSubmittedRef.current = true
          const processed = text.trim()
          if (!processed) return
          setQuestion(processed)
          questionRef.current = processed
          voiceModeRef.current = true
          handleSubmit(null)
        },
        onError: (msg) => setError(msg),
      })
    } else if (voiceHookState === 'listening') {
      stopListening()
    } else {
      cancelListening()
    }
  }

  const handlePdfSelection = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (file.type !== 'application/pdf') {
      setError('Apenas ficheiros PDF são suportados.')
      return
    }
    if (file.size > MAX_PDF_UPLOAD_BYTES) {
      setError(`O PDF excede ${MAX_PDF_UPLOAD_MB} MB.`)
      return
    }
    setError('')
    setPendingAttachment(file)
  }

  return (
    <section className="relative flex h-full min-h-0 w-full flex-col">
      {error ? <div className="mb-2"><ErrorBanner message={error} onRetry={() => setError('')} /></div> : null}

      <div
        data-chat-scroll
        ref={scrollContainerRef}
        onScroll={handleMessagesScroll}
        onWheel={(event) => {
          if (event.deltaY > 8) handleMobileScrollIntent('down')
          else if (event.deltaY < -8) handleMobileScrollIntent('up')
        }}
        onTouchStart={(event) => {
          touchStartYRef.current = event.touches?.[0]?.clientY ?? null
        }}
        onTouchMove={(event) => {
          const currentY = event.touches?.[0]?.clientY ?? null
          if (currentY === null || touchStartYRef.current === null) return
          const deltaY = currentY - touchStartYRef.current
          if (deltaY < -12) handleMobileScrollIntent('down')
          else if (deltaY > 12) handleMobileScrollIntent('up')
          if (Math.abs(deltaY) > 12) touchStartYRef.current = currentY
        }}
        className="custom-scroll flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-2 py-4 sm:px-2"
      >
        {(!selectedConversation || (selectedConversation && selectedConversation.messages.length === 0)) && !loading ? (
          <EmptyState
            title="Pronto para nova consulta"
            description="Envie uma pergunta jurídica ou anexe um PDF para receber resposta fundamentada na legislação angolana."
            suggestions={emptyStateSuggestions}
            onSuggestionClick={(text) => setQuestion(text)}
          />
        ) : null}

        {conversationLoading ? (
          <div className="mx-auto max-w-3xl flex items-center justify-center py-20">
            <div className="flex items-center gap-3 text-[color:var(--ink-soft)]">
              <span className="h-5 w-5 rounded-full border-2 border-[color:var(--accent)] border-t-transparent animate-spin" />
              <span className="text-sm">A carregar conversa...</span>
            </div>
          </div>
        ) : null}

        <div className={conversationLoading ? 'hidden' : 'mx-auto max-w-3xl space-y-4'}>
          {messages.map((message, idx) => {
            const isLatestFinalAssistant = !loading && message.role === 'assistant' && idx === messages.length - 1
            return (
              <div key={message.id} ref={isLatestFinalAssistant ? responseStartRef : null}>
                <ChatMessage
                  role={message.role}
                  content={message.content}
                  createdAt={message.createdAt}
                  sourceRefs={message.role === 'assistant' ? message.sources || [] : []}
                  answerMode={message.answer_mode}
                  confidence={message.confidence}
                  validationIssues={message.validation_issues}
                  clarifyingQuestions={message.clarifying_questions}
                  legalBasis={message.legal_basis}
                  verifiedArticles={message.verified_articles}
                  classification={message.classification}
                  onSelectRef={onSelectSourceRef}
                  onClarifyingSelect={handleClarifyingSelect}
                  onEdit={!loading && message.id !== 'pending-user' ? () => handleEditUserMessage(message.id) : undefined}
                  messageId={message.id}
                  isEditing={editingMsgId === message.id}
                  onSaveEdit={message.role === 'user' ? handleSaveEdit : handleAISaveEdit}
                  onCancelEdit={handleCancelEdit}
                  versionInfo={message.versionInfo}
                  suggestedActions={message.role === 'assistant' ? (message.suggested_actions || []) : []}
                  onNavigateVersion={onNavigateEditVersion ? (dir) => onNavigateEditVersion(selectedConversation?.id, message.versionInfo.editIndex, dir) : undefined}
                  onSelectAction={(prompt) => { setQuestion(prompt); questionRef.current = prompt; handleSubmit(null) }}
                />
              </div>
            )
          })}

          {loading ? (
            <div ref={responseStartRef} className="w-full sm:pl-10">
              <StreamingLoader
                content={streamingContent}
                phase={streamingPhase}
                elapsedMs={loadingElapsedMs}
              />
            </div>
          ) : null}
        </div>
      </div>

      {showBackToBottom && !composerFocused ? (
        <button
          type="button"
          onClick={() => { shouldAutoScrollRef.current = true; scrollToBottom(true); setShowBackToBottom(false) }}
          className={`absolute right-4 z-30 inline-flex items-center gap-1.5 rounded-full border border-[color:var(--stroke)] bg-[color:var(--panel)] px-3 py-1.5 text-xs font-medium text-[color:var(--ink-soft)] shadow-[var(--shadow-2)] transition-all duration-300 hover:text-[color:var(--ink)] ${mobileChromeVisible ? 'bottom-44 left-auto translate-x-0' : 'bottom-4 left-auto translate-x-0 md:bottom-20'}`}
        >
          <ArrowDown size={13} />
          Voltar ao fim
        </button>
      ) : null}

      <div data-chat-composer-shell className={`shrink-0 overflow-hidden px-2 transition-[max-height,transform,opacity,padding] duration-200 ease-[cubic-bezier(.22,1,.36,1)] will-change-[max-height,transform,opacity] motion-reduce:transition-none sm:px-2 md:max-h-[260px] md:translate-y-0 md:scale-100 md:pb-2 md:pt-2 md:opacity-100 md:pointer-events-auto ${
        mobileChromeVisible
          ? 'max-h-[260px] translate-y-0 scale-100 pb-2 pt-2 opacity-100'
          : 'max-h-0 translate-y-3 scale-[0.985] pb-0 pt-0 opacity-0 pointer-events-none'
      }`}>
        <div className="mx-auto max-w-3xl">
          {quotaMessage ? (
            <div className={`mb-2 rounded-2xl border px-3 py-2 text-[11px] leading-relaxed sm:text-xs ${
              quotaReached
                ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
                : 'border-white/[0.08] bg-white/[0.03] text-[color:var(--ink-soft)]'
            }`}>
              {quotaMessage}
            </div>
          ) : null}
          <ChatComposer
            value={voiceHookState === 'listening' && interimText ? interimText : question}
            onChange={setQuestion}
            onSubmit={handleSubmit}
            loading={loading}
            sendDisabled={quotaReached}
            onCancel={cancelStream}
            voiceState={voiceHookState}
            voiceAnalyserNode={analyserNode}
            onVoiceToggle={handleVoiceToggle}
            onOpenPdfPicker={() => fileInputRef.current?.click()}
            activeDocument={activeDocument}
            pendingAttachment={pendingAttachment}
            onClearActiveDocument={() => onSetConversationActiveDocument?.(selectedConversation?.id, null)}
            onClearPendingAttachment={() => setPendingAttachment(null)}
            userMessages={userMessages}
            onFocus={() => setComposerFocused(true)}
            onBlur={() => setComposerFocused(false)}
          />
          <p className="mt-2 text-center text-[10px] leading-relaxed text-[color:var(--ink-soft)]/70">
            O jURIS-APP pode cometer erros. Não substitui aconselhamento jurídico profissional. <InfoTooltip content="Este assistente utiliza inteligencia artificial para pesquisar legislacao angolana. As respostas sao geradas com base no corpus de diplomas indexados e devem ser verificadas por um profissional." />
          </p>
          <input ref={fileInputRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={handlePdfSelection} />
        </div>
      </div>
    </section>
  )
}
