import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, api } from '../api/client'
import type { User } from '../api/types'
import { AuthContext } from './auth'

async function loadUser(): Promise<User | null> {
  try {
    return await api.me()
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401) console.error(e)
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setUser(await loadUser())
    setLoading(false)
  }, [])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      setUser(null)
    }
  }, [])

  // Who am I? The cookie is httpOnly, so asking the backend is the only way to know.
  useEffect(() => {
    let cancelled = false
    loadUser().then((u) => {
      if (cancelled) return
      setUser(u)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const value = useMemo(() => ({ user, loading, setUser, refresh, logout }), [user, loading, refresh, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
