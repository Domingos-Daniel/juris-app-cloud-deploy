import { useCallback, useEffect, useMemo, useState } from 'react'
import { MOTORS, STORAGE_KEYS } from '../constants/app'
import { fetchChats, fetchDocuments, deleteChat, deleteAllChats, fetchEditVersions, saveEditVersion } from '../services/apiClient'
import { normalizeDisplayText } from '../utils/markdown'

function uuid() {
  try { return crypto.randomUUID() } catch {}
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

const defaultState = {
  motor: 'motorD',
  activeSection: 'chat',
  conversations: [],
  editSessions: {},
  documents: [],
  activeConversationId: null,
  isDraftConversation: false,
  draftActiveDocumentId: null,
}

function readState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.appState)
    if (!raw) {
      return defaultState
    }
    const parsed = JSON.parse(raw)
    // Migration: strip editSessions from conversation objects (now stored at top level)
    if (parsed.conversations) {
      parsed.conversations = parsed.conversations.map((c) => {
        if (c.editSessions) {
          const { editSessions, ...rest } = c
          return {
            ...rest,
            title: normalizeDisplayText(rest.title),
            messages: (rest.messages || []).map((message) => ({
              ...message,
              content: normalizeDisplayText(message.content),
              sources: (message.sources || []).map((source) => ({
                ...source,
                title: normalizeDisplayText(source.title),
                source: normalizeDisplayText(source.source),
                excerpt: normalizeDisplayText(source.excerpt),
                attribution_text: normalizeDisplayText(source.attribution_text),
              })),
            })),
          }
        }
        return {
          ...c,
          title: normalizeDisplayText(c.title),
          messages: (c.messages || []).map((message) => ({
            ...message,
            content: normalizeDisplayText(message.content),
            sources: (message.sources || []).map((source) => ({
              ...source,
              title: normalizeDisplayText(source.title),
              source: normalizeDisplayText(source.source),
              excerpt: normalizeDisplayText(source.excerpt),
              attribution_text: normalizeDisplayText(source.attribution_text),
            })),
          })),
        }
      })
    }
    return {
      ...defaultState,
      ...parsed,
      conversations: (parsed.conversations || []).map((conversation) => ({
        ...conversation,
        title: normalizeDisplayText(conversation.title),
        messages: (conversation.messages || []).map((message) => ({
          ...message,
          content: normalizeDisplayText(message.content),
        })),
      })),
      documents: (parsed.documents || []).map((document) => ({
        ...document,
        filename: normalizeDisplayText(document.filename),
        display_name: normalizeDisplayText(document.display_name),
        summary: normalizeDisplayText(document.summary),
        preview_text: normalizeDisplayText(document.preview_text),
        category: normalizeDisplayText(document.category),
      })),
    }
  } catch {
    return defaultState
  }
}

function trimConversation(conv) {
  if (!conv.messages || conv.messages.length <= 20) return conv
  return { ...conv, messages: conv.messages.slice(-20) }
}

function aggressiveTrim(conv) {
  const msgs = (conv.messages || []).slice(-10).map((m) => ({
    ...m,
    content: m.content.length > 500 ? m.content.slice(0, 500) + '...' : m.content,
  }))
  return { ...conv, messages: msgs }
}

function mapChatFromApi(chat) {
  return {
    id: chat.id,
    title: normalizeDisplayText(chat.title),
    createdAt: chat.created_at,
    updatedAt: chat.updated_at,
    activeDocumentId: chat.active_document_id || null,
    messages: (chat.messages || []).map((message) => ({
      id: message.id,
      role: message.role,
      content: normalizeDisplayText(message.content),
      createdAt: message.created_at,
      sources: (message.sources || []).map((source) => ({
        ...source,
        title: normalizeDisplayText(source.title),
        source: normalizeDisplayText(source.source),
        excerpt: normalizeDisplayText(source.excerpt),
        attribution_text: normalizeDisplayText(source.attribution_text),
      })),
      provider_used: message.provider_used || null,
      answer_mode: message.answer_mode || null,
      confidence: message.confidence || null,
      validation_issues: message.validation_issues || [],
      clarifying_questions: message.clarifying_questions || [],
      clarification_request: message.clarification_request || null,
      legal_basis: message.legal_basis || [],
      verified_articles: message.verified_articles || [],
      classification: message.classification || null,
    })),
  }
}

function mapDocumentFromApi(item) {
  return {
    id: item.id,
    filename: normalizeDisplayText(item.filename),
    display_name: normalizeDisplayText(item.display_name),
    storage_path: item.storage_path,
    mime_type: item.mime_type,
    size_bytes: item.size_bytes,
    status: item.status,
    created_at: item.created_at,
    page_count: item.page_count,
    chunks_created: item.chunks_created,
    extraction_mode: item.extraction_mode,
    quality_status: item.quality_status,
    summary: normalizeDisplayText(item.summary),
    preview_text: normalizeDisplayText(item.preview_text),
    category: normalizeDisplayText(item.category),
    usage_count: item.usage_count,
    last_used_at: item.last_used_at,
  }
}

export function useAppState(token) {
  const [state, setState] = useState(readState)

  const persistWith = (updater) => {
    setState((current) => {
      const next = updater(current)
      try {
        const trimmed = { ...next, conversations: next.conversations.map(trimConversation) }
        localStorage.setItem(STORAGE_KEYS.appState, JSON.stringify(trimmed))
      } catch {
        try {
          const aggressive = { ...next, conversations: next.conversations.map(aggressiveTrim) }
          localStorage.setItem(STORAGE_KEYS.appState, JSON.stringify(aggressive))
        } catch {
          // storage full — keep state in memory only, UI stays functional
        }
      }
      return next
    })
  }

  const hydrateFromServer = useCallback(async () => {
    if (!token) {
      return
    }
    const [chatPayload, documentPayload] = await Promise.all([fetchChats(token), fetchDocuments(token)])
    // Fetch edit versions for each chat from server
    const versionResults = await Promise.all(
      (chatPayload.items || []).map(async (c) => {
        try {
          const versions = await fetchEditVersions(c.id, token)
          return Array.isArray(versions) && versions.length ? { chatId: c.id, versions } : null
        } catch { return null }
      }),
    )
    const versionMap = {}
    for (const vr of versionResults) {
      if (vr) versionMap[vr.chatId] = vr.versions
    }
    setState((current) => {
      const serverConversations = (chatPayload.items || []).map(mapChatFromApi)
      // Keep only the active draft. Persisted chats always come from the
      // authenticated user's server payload, avoiding cross-account history.
      const localOnly = current.conversations.filter(
        (c) => !c.id
      )
      const merged = [...serverConversations, ...localOnly]
      let activeId = current.activeConversationId
      if (activeId && !merged.some((c) => c.id === activeId)) {
        activeId = merged[0]?.id || null
      }
      // Merge edit versions from backend into top-level state (localStorage takes priority)
      const nextEditSessions = { ...current.editSessions }
      for (const [chatId, versions] of Object.entries(versionMap)) {
        if (!nextEditSessions[chatId] || nextEditSessions[chatId].length === 0) {
          nextEditSessions[chatId] = versions
        }
      }
      // Reconstruct conversation messages from editSessions (backend stores ALL messages, not truncated)
      // Apply edits from LOWEST editIndex to HIGHEST so each edit builds on previous ones
      const conversations = merged.map((conv) => {
        const sessions = nextEditSessions[conv.id]
        if (!sessions || sessions.length === 0) return conv
        let result = conv.messages.slice()
        const sorted = [...sessions].sort((a, b) => a.editIndex - b.editIndex)
        for (const sess of sorted) {
          const versionIdx = sess.activeVersion ?? (sess.versions.length - 1)
          const tail = sess.versions[versionIdx].tail
          const prefix = result.slice(0, sess.editIndex)
          result = [...prefix, ...tail]
        }
        return { ...conv, messages: result }
      })
      const next = {
        ...current,
        conversations,
        activeConversationId: activeId || current.activeConversationId,
        editSessions: nextEditSessions,
        documents: (documentPayload.items || []).map(mapDocumentFromApi),
      }
      try {
        const trimmed = { ...next, conversations: next.conversations.map(trimConversation) }
        localStorage.setItem(STORAGE_KEYS.appState, JSON.stringify(trimmed))
      } catch {
        try {
          const aggressive = { ...next, conversations: next.conversations.map(aggressiveTrim) }
          localStorage.setItem(STORAGE_KEYS.appState, JSON.stringify(aggressive))
        } catch {
          // storage full
        }
      }
      return next
    })
  }, [token])

  useEffect(() => {
    const id = window.setTimeout(() => {
      hydrateFromServer().catch(() => {
        // keep current UI state if backend sync fails
      })
    }, 0)
    return () => window.clearTimeout(id)
  }, [hydrateFromServer])

  const setMotor = (motor) => persistWith((current) => ({ ...current, motor }))
  const setActiveSection = (activeSection) => persistWith((current) => ({ ...current, activeSection }))

  const startNewConversation = () => {
    persistWith((current) => ({
      ...current,
      activeSection: 'chat',
      activeConversationId: null,
      isDraftConversation: true,
      draftActiveDocumentId: null,
    }))
  }

  const appendMessagePair = ({ chat_id, question, answer, sources, provider_used, createdAt, active_document_id, answer_mode, confidence, validation_issues, clarifying_questions, clarification_request, legal_basis, verified_articles, classification, suggested_actions, editMessageIndex = -1 }) => {
    persistWith((current) => {
      const active =
        current.conversations.find((item) => item.id === current.activeConversationId) || {
          id: null,
          title: 'Nova Consulta',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          activeDocumentId: current.draftActiveDocumentId || null,
          messages: [],
        }

      const now = new Date().toISOString()
      const userMessage = {
        id: uuid(),
        role: 'user',
        content: question,
        createdAt,
      }
      const assistantMessage = {
        id: uuid(),
        role: 'assistant',
        content: answer,
        createdAt,
        sources: sources || [],
        provider_used,
        answer_mode: answer_mode || null,
        confidence: confidence || null,
        validation_issues: validation_issues || [],
        clarifying_questions: clarifying_questions || [],
        clarification_request: clarification_request || null,
        legal_basis: legal_basis || [],
        verified_articles: verified_articles || [],
        classification: classification || null,
        suggested_actions: suggested_actions || [],
      }

      // If editing, save version history, then trim
      let existingMessages = active.messages
      let editSessions = active.id && (current.editSessions || {})[active.id]
        ? (current.editSessions || {})[active.id].map(s => ({ ...s, versions: s.versions.map(v => ({ ...v })) }))
        : []
      if (editMessageIndex >= 0) {
        const oldTail = active.messages.slice(editMessageIndex)
        const sessionIdx = editSessions.findIndex((s) => s.editIndex === editMessageIndex)
        if (sessionIdx >= 0) {
          // Re-edit at same index: oldTail is already the last version, skip save
        } else {
          // First edit at this index: save old tail as version 0
          editSessions.push({ editIndex: editMessageIndex, versions: [{ tail: oldTail }], activeVersion: null })
        }
        existingMessages = active.messages.slice(0, editMessageIndex)
      }

      let newTail = [userMessage, assistantMessage]

      // Push new tail as latest version and set activeVersion, preserving after-edit messages
      if (editMessageIndex >= 0) {
        const session = editSessions[editSessions.length - 1]
        const afterEdit = active.messages.slice(editMessageIndex + 2)
        newTail = [...newTail, ...afterEdit]
        session.versions.push({ tail: newTail })
        session.activeVersion = session.versions.length - 1
      }

      const nextConversation = {
        ...active,
        id: chat_id || active.id,
        title: existingMessages.length ? active.title : question,
        updatedAt: now,
        activeDocumentId: active_document_id || active.activeDocumentId || null,
        messages: [...existingMessages, ...newTail],
      }

      // Store edit sessions at top level (not on conversation) to survive hydration
      const nextEditSessions = { ...current.editSessions }
      if (editSessions.length > 0 && nextConversation.id) {
        nextEditSessions[nextConversation.id] = editSessions
      }

      // Persist edit versions to backend (fire-and-forget)
      if (editSessions.length > 0 && token && nextConversation.id) {
        const chatId = nextConversation.id
        const sessions = editSessions
        setTimeout(() => {
          for (const session of sessions) {
            for (let vi = 0; vi < session.versions.length; vi++) {
              saveEditVersion(chatId, session.editIndex, vi, session.versions[vi].tail, token).catch(() => {})
            }
          }
        }, 0)
      }

      const remaining = current.conversations.filter((item) => item.id !== nextConversation.id)
      const conversations = [nextConversation, ...remaining]

      return {
        ...current,
        activeSection: 'chat',
        activeConversationId: nextConversation.id,
        isDraftConversation: false,
        draftActiveDocumentId: null,
        conversations,
        editSessions: nextEditSessions,
      }
    })
  }

  const selectConversation = (conversationId) =>
    persistWith((current) => ({ ...current, activeConversationId: conversationId, activeSection: 'chat', isDraftConversation: false }))

  const deleteConversation = (conversationId) => {
    // Remove from state first (optimistic)
    const conversations = state.conversations.filter((conversation) => conversation.id !== conversationId)
    const activeConversationId =
      state.activeConversationId === conversationId ? (conversations[0]?.id ?? null) : state.activeConversationId

    persistWith((current) => {
      const { [conversationId]: _, ...restSessions } = current.editSessions || {}
      return {
        ...current,
        activeSection: 'chat',
        activeConversationId,
        isDraftConversation: false,
        draftActiveDocumentId: null,
        conversations,
        editSessions: restSessions,
      }
    })

    // Delete from backend (fire-and-forget)
    if (conversationId && token) {
      deleteChat(conversationId, token).catch(() => {})
    }
  }

  const deleteAllConversations = async () => {
    if (token) {
      try { await deleteAllChats(token) } catch {}
    }
    persistWith((current) => ({
      ...current,
      activeSection: 'chat',
      activeConversationId: null,
      isDraftConversation: false,
      draftActiveDocumentId: null,
      conversations: [],
      editSessions: {},
    }))
  }

  const renameConversation = (conversationId, title) => {
    const normalized = (title || '').trim()
    if (!normalized) {
      return
    }

    const conversations = state.conversations.map((conversation) =>
      conversation.id === conversationId
        ? {
            ...conversation,
            title: normalized,
            updatedAt: new Date().toISOString(),
          }
        : conversation,
    )

    persistWith((current) => ({ ...current, conversations }))
  }

  const setConversationActiveDocument = (conversationId, documentId) => {
    if (!conversationId) {
      persistWith((current) => ({ ...current, draftActiveDocumentId: documentId || null, isDraftConversation: true, activeSection: 'chat' }))
      return
    }
    persistWith((current) => {
      const conversations = current.conversations.map((conversation) =>
        conversation.id === conversationId ? { ...conversation, activeDocumentId: documentId || null } : conversation,
      )
      return { ...current, conversations }
    })
  }

  const addUploadedDocument = (document) => {
    const mapped = mapDocumentFromApi(document)
    persistWith((current) => {
      const documents = [mapped, ...current.documents.filter((item) => item.id !== mapped.id)]
      return { ...current, documents }
    })
    return mapped
  }

  const removeDocument = (documentId) => {
    persistWith((current) => ({
      ...current,
      documents: current.documents.filter((item) => item.id !== documentId),
      conversations: current.conversations.map((conversation) =>
        conversation.activeDocumentId === documentId ? { ...conversation, activeDocumentId: null } : conversation,
      ),
      draftActiveDocumentId: current.draftActiveDocumentId === documentId ? null : current.draftActiveDocumentId,
    }))
  }

  const selectedConversation = useMemo(() => {
    if (state.isDraftConversation) {
      return {
        id: null,
        title: 'Nova Consulta',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        activeDocumentId: state.draftActiveDocumentId || null,
        messages: [],
      }
    }

    return state.conversations.find((item) => item.id === state.activeConversationId) || state.conversations[0] || null
  }, [state.conversations, state.activeConversationId, state.isDraftConversation, state.draftActiveDocumentId])

  const sidebarConversations = useMemo(
    () =>
      state.conversations.map((conv) => ({
        id: conv.id,
        title: conv.title,
        updatedAt: conv.updatedAt,
        preview:
          conv.messages
            .slice()
            .reverse()
            .find((message) => message.role === 'assistant')?.content || '',
      })),
    [state.conversations],
  )

  const updateMessageContent = (messageId, newContent) =>
    persistWith((current) => {
      const conv = current.conversations.find((c) => c.id === current.activeConversationId)
      if (!conv) return current
      const messages = conv.messages.map((m) =>
        m.id === messageId ? { ...m, content: newContent } : m,
      )
      return {
        ...current,
        conversations: current.conversations.map((c) =>
          c.id === current.activeConversationId ? { ...c, messages } : c,
        ),
      }
    })

  const navigateEditVersion = (conversationId, editIndex, direction) =>
    persistWith((current) => {
      const conv = current.conversations.find((c) => c.id === conversationId)
      if (!conv) return current
      const sessionsForConv = (current.editSessions || {})[conversationId] || []
      const sessionIdx = sessionsForConv.findIndex((s) => s.editIndex === editIndex)
      if (sessionIdx < 0) return current
      const session = sessionsForConv[sessionIdx]
      const currentVersion = session.activeVersion ?? (session.versions.length - 1)
      const newVersion = currentVersion + direction
      if (newVersion < 0 || newVersion >= session.versions.length) return current
      // Build updated messages with selected version's tail
      const prefix = conv.messages.slice(0, editIndex)
      const tail = session.versions[newVersion].tail
      const updatedSession = { ...session, activeVersion: newVersion }
      const updatedSessionsForConv = sessionsForConv.map((s, i) => (i === sessionIdx ? updatedSession : s))
      return {
        ...current,
        conversations: current.conversations.map((c) =>
          c.id === conversationId ? { ...c, messages: [...prefix, ...tail], updatedAt: new Date().toISOString() } : c
        ),
        editSessions: { ...current.editSessions, [conversationId]: updatedSessionsForConv },
      }
    })

  const selectedMotor = MOTORS.find((motor) => motor.id === state.motor) || MOTORS[0]

  return {
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
  }
}
