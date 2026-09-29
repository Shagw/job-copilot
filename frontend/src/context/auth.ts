import { createContext, useContext } from 'react'
import type { User } from '../api/types'

export interface AuthState {
  user: User | null
  loading: boolean
  /** Set the user after login / OTP verification (the backend already set the cookie). */
  setUser: (user: User | null) => void
  refresh: () => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
