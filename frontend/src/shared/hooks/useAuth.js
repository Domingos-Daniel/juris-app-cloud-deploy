import { useEffect, useState } from 'react'
import { STORAGE_KEYS } from '../constants/app'
import { fetchMe, loginRequest, registerRequest, updateProfileRequest, updatePreferencesRequest, fetchPreferences } from '../services/apiClient'

function readAuthState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.auth)
    if (!raw) return { token: '', user: null }
    return JSON.parse(raw)
  } catch { return { token: '', user: null } }
}

export function useAuth() {
  const [auth, setAuth] = useState(readAuthState)
  const [loading, setLoading] = useState(Boolean(readAuthState().token))

  const persist = (next) => {
    setAuth(next)
    localStorage.setItem(STORAGE_KEYS.auth, JSON.stringify(next))
  }

  useEffect(() => {
    if (!auth.token) return
    let mounted = true
    fetchMe(auth.token)
      .then((user) => { if (mounted) persist({ token: auth.token, user }) })
      .catch(() => { if (mounted) persist({ token: '', user: null }) })
      .finally(() => { if (mounted) setLoading(false) })
    return () => { mounted = false }
  }, [auth.token])

  useEffect(() => {
    if (!auth.token && loading) {
      const id = window.setTimeout(() => setLoading(false), 0)
      return () => window.clearTimeout(id)
    }
  }, [auth.token, loading])

  const login = async (email, password) => {
    setLoading(true)
    try {
      const response = await loginRequest(email, password)
      const fullUser = await fetchMe(response.token).catch(() => response.user)
      persist({ token: response.token, user: fullUser })
      return fullUser
    } finally {
      setLoading(false)
    }
  }

  const register = async (name, email, phone, password) => {
    setLoading(true)
    try {
      const response = await registerRequest(name, email, phone, password)
      const fullUser = await fetchMe(response.token).catch(() => response.user)
      persist({ token: response.token, user: fullUser })
      return fullUser
    } finally {
      setLoading(false)
    }
  }

  const updateProfile = async (token, name, email, phone) => {
    const updated = await updateProfileRequest(token, name, email, phone)
    persist({ token, user: { ...auth.user, ...updated } })
    return updated
  }

  const updatePreferences = async (token, prefs) => {
    const updated = await updatePreferencesRequest(token, prefs)
    persist({ token, user: { ...auth.user, ai_preferences: updated } })
    return updated
  }

  const getPreferences = async (token) => {
    return await fetchPreferences(token)
  }

  const logout = () => { persist({ token: '', user: null }) }

  return {
    token: auth.token, user: auth.user, loading,
    isAuthenticated: Boolean(auth.token && auth.user),
    login, register, logout, updateProfile, updatePreferences, getPreferences,
  }
}
