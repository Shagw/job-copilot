import type {
  JobSession,
  LlmSlot,
  Message,
  ParsedJob,
  Resume,
  SessionSummary,
  Status,
  User,
} from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export class ApiError extends Error {
  readonly status: number
  readonly retryAfter: number | null

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

/** Turn FastAPI error bodies ({detail: string} or {detail: [{msg}]}) into one readable message. */
function detailMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
        .filter(Boolean)
        .map((m) => m.replace(/^Value error, /, ''))
        .join('. ')
    }
  }
  if (status === 0) return 'Cannot reach the server. Is the backend running?'
  return `Something went wrong (${status}).`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(BASE + path, {
      credentials: 'include', // send the httpOnly auth cookie
      ...init,
      headers:
        init.body instanceof FormData
          ? init.headers
          : { 'Content-Type': 'application/json', ...init.headers },
    })
  } catch {
    throw new ApiError(0, detailMessage(null, 0))
  }

  const text = await response.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  if (!response.ok) {
    const header = response.headers.get('Retry-After')
    const retry = header ? Number(header) : null
    throw new ApiError(response.status, detailMessage(body, response.status), Number.isFinite(retry) ? retry : null)
  }
  return body as T
}

const post = <T>(path: string, data?: unknown) =>
  request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) })

export const api = {
  // ---- auth ----
  me: () => request<User>('/auth/me'),
  signup: (email: string, password: string) => post<Message>('/auth/signup', { email, password }),
  verifyOtp: (email: string, code: string) => post<User>('/auth/verify-otp', { email, code }),
  resendOtp: (email: string) => post<Message>('/auth/resend-otp', { email }),
  login: (email: string, password: string) => post<User>('/auth/login', { email, password }),
  logout: () => post<Message>('/auth/logout'),
  forgotPassword: (email: string) => post<Message>('/auth/forgot-password', { email }),
  resetPassword: (email: string, code: string, new_password: string) =>
    post<Message>('/auth/reset-password', { email, code, new_password }),

  // ---- resume ----
  getResume: () => request<Resume>('/resume'),
  uploadResume: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Resume>('/resume', { method: 'POST', body: form })
  },

  // ---- sessions ----
  listSessions: () => request<SessionSummary[]>('/sessions'),
  getSession: (id: number) => request<JobSession>(`/sessions/${id}`),
  createSession: (input: { job_text?: string; job_url?: string }) => post<JobSession>('/sessions', input),
  runFit: (id: number, parsed_job?: ParsedJob) =>
    post<JobSession>(`/sessions/${id}/fit`, parsed_job ? { parsed_job } : {}),
  runTailor: (id: number, instructions?: string) =>
    post<JobSession>(`/sessions/${id}/tailor`, instructions ? { instructions } : {}),
  runCoverLetter: (id: number, tailored_resume?: string, instructions?: string) =>
    post<JobSession>(`/sessions/${id}/cover-letter`, {
      ...(tailored_resume ? { tailored_resume } : {}),
      ...(instructions ? { instructions } : {}),
    }),
  updateSession: (
    id: number,
    changes: Partial<{ parsed_job: ParsedJob; tailored_resume: string; cover_letter: string; status: Status }>,
  ) => request<JobSession>(`/sessions/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  exportUrl: (id: number, kind: 'resume' | 'cover-letter') => `${BASE}/sessions/${id}/export/${kind}`,

  // ---- admin ----
  llmStatus: () => request<LlmSlot[]>('/admin/llm-status'),
}
