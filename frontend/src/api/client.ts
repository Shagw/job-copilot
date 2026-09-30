import type {
  JobSession,
  LlmSlot,
  Message,
  ParsedJob,
  Resume,
  ResumeVersion,
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

  return readJson<T>(response)
}

/** Parse a JSON response; non-2xx becomes an ApiError with the server's message. */
async function readJson<T>(response: Response): Promise<T> {
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

/** Called with each live step of an agent run ("Writing the tailored draft", "Fix round 1: ..."). */
export type OnProgress = (message: string) => void

/** Parse one server-sent event block ("event: x\ndata: {...}"); comment lines (": keep-alive") are skipped. */
function parseEvent(block: string): { event: string; data: unknown } | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  try {
    return { event, data: JSON.parse(data.join('\n')) }
  } catch {
    return null
  }
}

/**
 * POST an agent run and follow its live progress. The server streams `progress` events, then one `done`
 * (the saved session) or `error`. Errors raised before the run starts come back as normal JSON responses.
 */
async function postStream<T>(path: string, data: unknown, onProgress?: OnProgress): Promise<T> {
  let response: Response
  try {
    response = await fetch(BASE + path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(data),
    })
  } catch {
    throw new ApiError(0, detailMessage(null, 0))
  }
  const type = response.headers.get('Content-Type') ?? ''
  if (!type.includes('text/event-stream') || !response.body) {
    // Not streamed (an error before the run, or a server without streaming): same handling as JSON.
    return readJson<T>(response)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    let chunk: ReadableStreamReadResult<Uint8Array>
    try {
      chunk = await reader.read()
    } catch {
      throw new ApiError(0, 'The connection was lost. Reload the page: the result is saved when the run finishes.')
    }
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true }).replace(/\r\n/g, '\n')
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const parsed = parseEvent(buffer.slice(0, end))
      buffer = buffer.slice(end + 2)
      if (!parsed) continue
      if (parsed.event === 'progress') {
        const message = (parsed.data as { message?: unknown }).message
        if (typeof message === 'string') onProgress?.(message)
      } else if (parsed.event === 'done') {
        void reader.cancel().catch(() => undefined)
        return parsed.data as T
      } else if (parsed.event === 'error') {
        const err = parsed.data as { status?: number; retry_after?: number }
        void reader.cancel().catch(() => undefined)
        throw new ApiError(err.status ?? 500, detailMessage(parsed.data, err.status ?? 500), err.retry_after ?? null)
      }
    }
  }
  throw new ApiError(0, 'The connection was lost. Reload the page: the result is saved when the run finishes.')
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
  runFit: (id: number, parsed_job?: ParsedJob, onProgress?: OnProgress) =>
    postStream<JobSession>(`/sessions/${id}/fit`, parsed_job ? { parsed_job } : {}, onProgress),
  /** Tailor from the original resume, or (with `current_resume`) revise the current tailored version. */
  runTailor: (id: number, instructions?: string, current_resume?: string, onProgress?: OnProgress) =>
    postStream<JobSession>(
      `/sessions/${id}/tailor`,
      { ...(instructions ? { instructions } : {}), ...(current_resume ? { current_resume } : {}) },
      onProgress,
    ),
  runCoverLetter: (id: number, tailored_resume?: string, instructions?: string, onProgress?: OnProgress) =>
    postStream<JobSession>(
      `/sessions/${id}/cover-letter`,
      { ...(tailored_resume ? { tailored_resume } : {}), ...(instructions ? { instructions } : {}) },
      onProgress,
    ),
  listResumeVersions: (id: number) => request<ResumeVersion[]>(`/sessions/${id}/resume-versions`),
  restoreResumeVersion: (id: number, versionId: number) =>
    post<JobSession>(`/sessions/${id}/resume-versions/${versionId}/restore`),
  updateSession: (
    id: number,
    changes: Partial<{ parsed_job: ParsedJob; tailored_resume: string; cover_letter: string; status: Status }>,
  ) => request<JobSession>(`/sessions/${id}`, { method: 'PATCH', body: JSON.stringify(changes) }),
  exportUrl: (id: number, kind: 'resume' | 'cover-letter', format: 'docx' | 'pdf' = 'docx') =>
    `${BASE}/sessions/${id}/export/${kind}` + (format === 'pdf' ? '?format=pdf' : ''),
  /** Inline PDF for the in-app preview. `version` busts the browser cache after a save. */
  previewUrl: (id: number, kind: 'resume' | 'cover-letter', version: number) =>
    `${BASE}/sessions/${id}/export/${kind}?format=pdf&inline=true&v=${version}`,

  // ---- admin ----
  llmStatus: () => request<LlmSlot[]>('/admin/llm-status'),
}
