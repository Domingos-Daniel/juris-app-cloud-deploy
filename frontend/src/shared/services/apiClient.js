import { API_BASE_URL } from '../constants/app'

async function request(path, options = {}, token = '') {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })

  const contentType = response.headers.get('content-type') || ''
  if (response.status === 204) {
    return { ok: true }
  }

  const readPayload = async () => {
    if (contentType.includes('application/json')) {
      return response.json()
    }
    const text = await response.text()
    return { raw_text: text }
  }

  if (!response.ok) {
    const payload = await readPayload().catch(() => ({}))
    const detail = payload.detail || `Erro HTTP ${response.status}`
    throw new Error(detail)
  }

  const payload = await readPayload()
  if (!contentType.includes('application/json')) {
    throw new Error(`Resposta inesperada da API em ${path}: esperado JSON e recebido ${contentType || 'conteudo sem content-type'}`)
  }
  return payload
}

export async function fetchHealth() {
  return request('/health')
}

export async function loginRequest(username, password) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export async function registerRequest(name, email, phone, password) {
  return request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, phone, password }),
  })
}

export async function updateProfileRequest(token, name, email, phone) {
  return request('/auth/me', {
    method: 'PUT',
    body: JSON.stringify({ name, email, phone }),
  }, token)
}

export async function updatePreferencesRequest(token, prefs) {
  return request('/auth/me/preferences', {
    method: 'PUT',
    body: JSON.stringify(prefs),
  }, token)
}

export async function fetchPreferences(token) {
  return request('/auth/me/preferences', {}, token)
}

export async function fetchMe(token) {
  return request('/auth/me', {}, token)
}

export async function sendChatQuestion(question, provider, conversationHistory = [], chatId = null, activeDocumentId = null, token = '') {
  return request(
    '/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        question,
        provider,
        conversation_history: conversationHistory,
        chat_id: chatId,
        active_document_id: activeDocumentId,
      }),
    },
    token,
  )
}

export async function preflightChatQuestion(question, provider, conversationHistory = [], chatId = null, token = '') {
  const response = await fetch(`${API_BASE_URL}/chat/preflight`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      question,
      provider,
      conversation_history: conversationHistory,
      chat_id: chatId,
    }),
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `Preflight HTTP ${response.status}`)
  }

  return response.json()
}

export async function sendChatQuestionStream(question, provider, conversationHistory = [], chatId = null, activeDocumentId = null, token = '', signal = null, clarificationContext = null) {
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      question,
      provider,
      conversation_history: conversationHistory,
      chat_id: chatId,
      active_document_id: activeDocumentId,
      clarification_context: clarificationContext,
    }),
    signal,
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `Erro HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  return {
    async *[Symbol.asyncIterator]() {
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const data = JSON.parse(line.slice(6))
            yield data
            if (data?.done) {
              return
            }
          }
        }
        if (buffer.trim().startsWith('data: ')) {
          const data = JSON.parse(buffer.trim().slice(6))
          yield data
          if (data?.done) {
            return
          }
        }
      } catch (err) {
        if (err.name === 'AbortError') return
        throw err
      }
    },
  }
}

export async function triggerIngestion(token) {
  return request('/docs/ingest', { method: 'POST' }, token)
}

export async function fetchDocuments(token) {
  return request('/docs', {}, token)
}

export async function fetchDocumentPreview(documentId, token) {
  return request(`/docs/${documentId}/preview`, {}, token)
}

export async function uploadPdfDocument(file, token) {
  const formData = new FormData()
  formData.append('file', file)
  return request('/docs/upload', { method: 'POST', body: formData }, token)
}

export async function renameDocument(documentId, displayName, token) {
  return request(`/docs/${documentId}/rename`, {
    method: 'PATCH',
    body: JSON.stringify({ display_name: displayName }),
  }, token)
}

export async function activateDocumentInChat(documentId, chatId, token) {
  return request(`/docs/${documentId}/use`, {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId || null }),
  }, token)
}

export async function deleteChat(chatId, token) {
  return request(`/chats/${chatId}`, { method: 'DELETE' }, token)
}

export async function deleteAllChats(token) {
  return request('/chats', { method: 'DELETE' }, token)
}

export async function reprocessDocument(documentId, token) {
  return request(`/docs/${documentId}/reprocess`, { method: 'POST' }, token)
}

export async function deleteDocument(documentId, token) {
  return request(`/docs/${documentId}`, { method: 'DELETE' }, token)
}

export async function fetchChats(token) {
  return request('/chats', {}, token)
}

export async function fetchCatalog(token) {
  return request('/catalog', {}, token)
}

export async function fetchJurisprudence(token, params = {}) {
  const qs = new URLSearchParams()
  if (params.court) qs.set('court', params.court)
  if (params.branch) qs.set('legal_branch', params.branch)
  if (params.search) qs.set('search', params.search)
  if (params.limit) qs.set('limit', params.limit)
  if (params.offset) qs.set('offset', params.offset)
  const query = qs.toString()
  return request(`/jurisprudence${query ? `?${query}` : ''}`, {}, token)
}

export async function fetchEditVersions(chatId, token) {
  return request(`/chats/${chatId}/versions`, {}, token)
}

export async function saveEditVersion(chatId, editIndex, versionIndex, messages, token) {
  return request(`/chats/${chatId}/versions`, {
    method: 'POST',
    body: JSON.stringify({ edit_index: editIndex, version_index: versionIndex, messages }),
  }, token)
}

export async function fetchProDashboard(token) {
  return request('/pro/dashboard', {}, token)
}

export async function fetchProClients(token, search = '') {
  const qs = new URLSearchParams()
  if (search) qs.set('search', search)
  const query = qs.toString()
  return request(`/pro/clients${query ? `?${query}` : ''}`, {}, token)
}

export async function saveProClient(token, payload, clientId = null) {
  return request(`/pro/clients${clientId ? `/${clientId}` : ''}`, {
    method: clientId ? 'PATCH' : 'POST',
    body: JSON.stringify(payload),
  }, token)
}

export async function archiveProClient(token, clientId) {
  return request(`/pro/clients/${clientId}`, { method: 'DELETE' }, token)
}

export async function fetchProCases(token, params = {}) {
  const qs = new URLSearchParams()
  if (params.search) qs.set('search', params.search)
  if (params.status) qs.set('status_filter', params.status)
  const query = qs.toString()
  return request(`/pro/cases${query ? `?${query}` : ''}`, {}, token)
}

export async function fetchProCase(token, caseId) {
  return request(`/pro/cases/${caseId}`, {}, token)
}

export async function saveProCase(token, payload, caseId = null) {
  return request(`/pro/cases${caseId ? `/${caseId}` : ''}`, {
    method: caseId ? 'PATCH' : 'POST',
    body: JSON.stringify(payload),
  }, token)
}

export async function archiveProCase(token, caseId) {
  return request(`/pro/cases/${caseId}`, { method: 'DELETE' }, token)
}


export async function exportProCase(token, caseId) {
  const response = await fetch(`${API_BASE_URL}/pro/cases/${caseId}/export`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  if (!response.ok) {
    let message = 'Erro ao exportar dossiê'
    try {
      const data = await response.json()
      message = data.detail || data.message || message
    } catch (_) {}
    throw new Error(message)
  }
  return response.text()
}

export async function linkProCaseChat(token, caseId, payload = {}) {
  return request(`/pro/cases/${caseId}/chats`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }, token)
}

export async function linkProCaseDocument(token, caseId, payload) {
  return request(`/pro/cases/${caseId}/documents`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }, token)
}

export async function saveProTask(token, caseId, payload, taskId = null) {
  return request(`/pro/cases/${caseId}/tasks${taskId ? `/${taskId}` : ''}`, {
    method: taskId ? 'PATCH' : 'POST',
    body: JSON.stringify(payload),
  }, token)
}

export async function saveProDeadline(token, caseId, payload, deadlineId = null) {
  return request(`/pro/cases/${caseId}/deadlines${deadlineId ? `/${deadlineId}` : ''}`, {
    method: deadlineId ? 'PATCH' : 'POST',
    body: JSON.stringify(payload),
  }, token)
}

export async function createProNote(token, caseId, payload) {
  return request(`/pro/cases/${caseId}/notes`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }, token)
}
