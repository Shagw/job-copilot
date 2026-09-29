import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import App from '../App'
import type { JobSession, ParsedJob, User } from '../api/types'
import { AuthProvider } from '../context/AuthContext'

type Reply = { status?: number; body?: unknown; headers?: Record<string, string> }
type Handler = Reply | ((init: RequestInit, url: string) => Reply)

export interface FetchMock {
  calls: { method: string; path: string; body: unknown; init: RequestInit }[]
  on: (method: string, path: string, handler: Handler) => void
}

/** Replace global fetch with a tiny router: on('POST', '/auth/login', {body: ...}). */
export function mockFetch(): FetchMock {
  const routes = new Map<string, Handler>()
  const calls: FetchMock['calls'] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const method = (init.method ?? 'GET').toUpperCase()
      const path = input.replace(/^\/api/, '')
      let body: unknown = init.body
      if (typeof body === 'string') body = JSON.parse(body)
      calls.push({ method, path, body, init })
      const handler = routes.get(`${method} ${path}`)
      const reply: Reply = !handler
        ? { status: 404, body: { detail: `no mock for ${method} ${path}` } }
        : typeof handler === 'function'
          ? handler(init, path)
          : handler
      return new Response(reply.body === undefined ? '' : JSON.stringify(reply.body), {
        status: reply.status ?? 200,
        headers: { 'Content-Type': 'application/json', ...reply.headers },
      })
    }),
  )
  return { calls, on: (method, path, handler) => routes.set(`${method} ${path}`, handler) }
}

export function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  )
}

export const USER: User = {
  id: 1,
  email: 'alice@example.com',
  is_verified: true,
  created_at: '2026-09-29T10:00:00+00:00',
  is_admin: false,
}

export const PARSED: ParsedJob = {
  title: 'Senior Backend Engineer',
  company: 'Initech',
  location: 'Remote',
  seniority: 'Senior',
  summary: 'Build APIs.',
  must_have: ['Python', 'Kubernetes'],
  nice_to_have: ['React'],
  responsibilities: [],
  keywords: ['Python', 'Kubernetes', 'React'],
}

export function session(overrides: Partial<JobSession> = {}): JobSession {
  return {
    id: 7,
    job_url: null,
    job_text: 'Senior Backend Engineer at Initech. Python, Kubernetes.',
    parsed_job: PARSED,
    fit_result: null,
    tailored_resume: null,
    tailor_report: null,
    cover_letter: null,
    cover_letter_report: null,
    agent_trace: null,
    current_step: 'parsed',
    status: 'draft',
    created_at: '2026-09-29T10:00:00+00:00',
    ...overrides,
  }
}
