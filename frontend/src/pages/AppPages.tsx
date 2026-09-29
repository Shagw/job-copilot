import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { LlmSlot, Resume, SessionSummary, Step } from '../api/types'
import { ErrorAlert, Field, Notice, Working } from '../components/ui'
import { formatDate, scoreClass, timeLeft } from '../lib/format'

const ACCEPT = '.pdf,.docx,.txt'
const MAX_MB = 5

export function ProfilePage() {
  const [resume, setResume] = useState<Resume | null>(null)
  const [loading, setLoading] = useState(true)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api
      .getResume()
      .then(setResume)
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 404)) setError(e)
      })
      .finally(() => setLoading(false))
  }, [])

  async function upload(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(new Error(`File is too large (max ${MAX_MB} MB).`))
      return
    }
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      setResume(await api.uploadResume(file))
      setSaved(true)
      setFile(null)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="profile-title">
      <h1 id="profile-title">Your master resume</h1>
      <p className="muted">
        The agents only ever use facts from this resume. Uploading a new one replaces it for future jobs.
      </p>
      <form onSubmit={upload} className="card">
        <Field label="Resume file" hint={`PDF, DOCX or TXT, up to ${MAX_MB} MB. Scanned images can't be read.`}>
          {(id, hint) => (
            <input id={id} aria-describedby={hint} type="file" accept={ACCEPT}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          )}
        </Field>
        <ErrorAlert error={error} />
        {saved && (
          <Notice>
            Resume saved. <Link to="/new">Start a new job →</Link>
          </Notice>
        )}
        <button type="submit" className="primary" disabled={!file || busy}>
          {busy ? 'Uploading…' : resume ? 'Replace resume' : 'Upload resume'}
        </button>
        {busy && <Working label="Reading and indexing your resume" />}
      </form>

      {loading ? (
        <Working label="Loading" />
      ) : resume ? (
        <div className="card">
          <h2>
            {resume.filename} <span className="muted small">uploaded {formatDate(resume.uploaded_at)}</span>
          </h2>
          <pre className="doc">{resume.raw_text}</pre>
        </div>
      ) : (
        <Notice>No resume yet. Upload one to get started.</Notice>
      )}
    </section>
  )
}

export function NewJobPage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'text' | 'url'>('text')
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [hasResume, setHasResume] = useState<boolean | null>(null)

  useEffect(() => {
    api
      .getResume()
      .then(() => setHasResume(true))
      .catch(() => setHasResume(false))
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const s = await api.createSession(mode === 'text' ? { job_text: text } : { job_url: url })
      navigate(`/sessions/${s.id}`)
    } catch (err) {
      setError(err)
      // A link we can't fetch (LinkedIn, JS-only pages): switch to paste mode, keep the URL.
      if (mode === 'url' && err instanceof ApiError && err.status === 422) setMode('text')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="new-title">
      <h1 id="new-title">New job</h1>
      {hasResume === false && (
        <Notice>
          You'll need a resume for the fit score and tailoring. <Link to="/profile">Upload one first →</Link>
        </Notice>
      )}
      <form onSubmit={submit} className="card">
        <div className="tabs" role="radiogroup" aria-label="How to add the job">
          <label>
            <input type="radio" name="mode" checked={mode === 'text'} onChange={() => setMode('text')} /> Paste
            description
          </label>
          <label>
            <input type="radio" name="mode" checked={mode === 'url'} onChange={() => setMode('url')} /> Use a link
          </label>
        </div>
        {mode === 'text' ? (
          <Field label="Job description" hint="Paste the full posting (at least 50 characters).">
            {(id, hint) => (
              <textarea id={id} aria-describedby={hint} rows={14} minLength={50} maxLength={100000} required
                value={text} onChange={(e) => setText(e.target.value)} />
            )}
          </Field>
        ) : (
          <Field
            label="Job link"
            hint="Public career pages (Greenhouse, Lever, company sites). LinkedIn and Indeed block this, so paste those instead."
          >
            {(id, hint) => (
              <input id={id} aria-describedby={hint} type="url" required placeholder="https://…" value={url}
                onChange={(e) => setUrl(e.target.value)} />
            )}
          </Field>
        )}
        <ErrorAlert error={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Reading the job…' : 'Analyse job'}
        </button>
        {busy && <Working label={mode === 'url' ? 'Fetching and reading the posting' : 'Reading the posting'} />}
      </form>
    </section>
  )
}

const STEP_LABEL: Record<Step, string> = {
  created: 'Started',
  parsed: 'Job parsed',
  scored: 'Fit scored',
  tailored: 'Resume tailored',
  done: 'Cover letter done',
}

export function HistoryPage() {
  const [items, setItems] = useState<SessionSummary[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(() => {
    api.listSessions().then(setItems).catch(setError)
  }, [])
  useEffect(load, [load])

  return (
    <section aria-labelledby="history-title">
      <div className="row-between">
        <h1 id="history-title">Your recent jobs</h1>
        <Link to="/new" className="button primary">
          + New job
        </Link>
      </div>
      <p className="muted">Jobs stay here for 3 days, then they're archived.</p>
      <ErrorAlert error={error} />
      {items === null && !error && <Working label="Loading" />}
      {items?.length === 0 && <Notice>Nothing in the last 3 days. Start with a new job.</Notice>}
      <ul className="history">
        {items?.map((s) => (
          <li key={s.id} className="card history-item">
            <Link to={`/sessions/${s.id}`} className="history-link">
              <span className="history-title">{s.title || 'Untitled job'}</span>
              <span className="muted">{s.company ?? 'Unknown company'}</span>
            </Link>
            <div className="history-meta">
              {s.fit_score !== null && <span className={`badge ${scoreClass(s.fit_score)}`}>Fit {s.fit_score}</span>}
              <span className="badge">{STEP_LABEL[s.current_step]}</span>
              <span className={`badge status-${s.status}`}>{s.status}</span>
              <span className="muted small">
                {formatDate(s.created_at)} · {timeLeft(s.visible_until)}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}


export function AdminPage() {
  const [slots, setSlots] = useState<LlmSlot[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(() => {
    api.llmStatus().then(setSlots).catch(setError)
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  return (
    <section aria-labelledby="admin-title">
      <h1 id="admin-title">AI status</h1>
      <p className="muted">Groq key and model slots, tried top to bottom. Refreshes every 5 seconds.</p>
      <ErrorAlert error={error} />
      {slots && (
        <table className="table">
          <caption className="sr-only">LLM slots</caption>
          <thead>
            <tr>
              <th scope="col">Slot</th>
              <th scope="col">Key</th>
              <th scope="col">Model</th>
              <th scope="col">State</th>
              <th scope="col">Cooldown</th>
              <th scope="col">Last error</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((s) => (
              <tr key={s.slot}>
                <td>{s.slot}</td>
                <td>
                  <code>{s.key}</code>
                </td>
                <td>{s.model}</td>
                <td>
                  <span className={`badge state-${s.state}`}>{s.state}</span>
                </td>
                <td>{s.cooldown_remaining_seconds > 0 ? `${Math.ceil(s.cooldown_remaining_seconds)}s` : '—'}</td>
                <td>{s.last_error ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
