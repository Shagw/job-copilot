import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { JobSession, ParsedJob, Status } from '../api/types'
import { STATUSES } from '../api/types'
import {
  AgentTraceView,
  CoverLetterReportView,
  FitCard,
  ParsedJobEditor,
  TailorReportView,
} from '../components/Reports'
import { PdfPreview } from '../components/PdfPreview'
import { ErrorAlert, Field, Notice, Working } from '../components/ui'
import { formatDate } from '../lib/format'

type Tab = 'job' | 'fit' | 'resume' | 'letter'
const TABS: { id: Tab; label: string }[] = [
  { id: 'job', label: '1. Job' },
  { id: 'fit', label: '2. Fit' },
  { id: 'resume', label: '3. Resume' },
  { id: 'letter', label: '4. Cover letter' },
]
const TAB_FOR_STEP: Record<JobSession['current_step'], Tab> = {
  created: 'job',
  parsed: 'job',
  scored: 'fit',
  tailored: 'resume',
  done: 'letter',
}

function available(s: JobSession, tab: Tab): boolean {
  if (tab === 'job') return true
  if (tab === 'fit') return !!s.fit_result
  if (tab === 'resume') return !!s.tailored_resume
  return !!s.cover_letter
}

/**
 * The human-in-the-loop wizard. Each tab shows one agent's output for review/editing,
 * and the user explicitly approves before the next agent runs.
 */
export function SessionPage() {
  const id = Number(useParams().id)
  const [session, setSession] = useState<JobSession | null>(null)
  const [tab, setTab] = useState<Tab>('job')
  const [loadError, setLoadError] = useState<unknown>(null)

  useEffect(() => {
    api
      .getSession(id)
      .then((s) => {
        setSession(s)
        setTab(TAB_FOR_STEP[s.current_step])
      })
      .catch(setLoadError)
  }, [id])

  if (loadError) {
    const gone = loadError instanceof ApiError && loadError.status === 404
    return gone ? <Notice>This job isn't available. It may be older than 3 days and archived.</Notice> : <ErrorAlert error={loadError} />
  }
  if (!session) return <Working label="Loading" />

  const update = (s: JobSession, next?: Tab) => {
    setSession(s)
    if (next) setTab(next)
  }
  const job = session.parsed_job

  return (
    <section aria-labelledby="session-title">
      <h1 id="session-title">
        {job?.title || 'Job'} {job?.company && <span className="muted">at {job.company}</span>}
      </h1>
      <p className="muted small">
        Started {formatDate(session.created_at)}
        {session.job_url && (
          <>
            {' · '}
            <a href={session.job_url} target="_blank" rel="noreferrer noopener">
              original posting
            </a>
          </>
        )}
      </p>

      <div className="stepper" role="tablist" aria-label="Application steps">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            disabled={!available(session, t.id)}
            className={tab === t.id ? 'active' : available(session, t.id) ? 'done' : ''}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="card" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'job' && <JobStep session={session} onDone={(s) => update(s, 'fit')} />}
        {tab === 'fit' && <FitStep session={session} onDone={(s) => update(s, 'resume')} />}
        {tab === 'resume' && <ResumeStep session={session} onDone={(s) => update(s, 'letter')} onSaved={(s) => update(s)} />}
        {tab === 'letter' && <LetterStep session={session} onSaved={(s) => update(s)} />}
      </div>
    </section>
  )
}

interface StepProps {
  session: JobSession
  onDone: (s: JobSession) => void
}

/** Run an agent call with busy/error state. */
function useAgentRun() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function run(fn: () => Promise<JobSession>, done: (s: JobSession) => void) {
    setBusy(true)
    setError(null)
    try {
      done(await fn())
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

function JobStep({ session, onDone }: StepProps) {
  const [job, setJob] = useState<ParsedJob>(session.parsed_job!)
  const { busy, error, run } = useAgentRun()
  return (
    <>
      <h2>Review what the AI understood</h2>
      <p className="muted">Fix anything it got wrong. The fit score uses exactly these requirements.</p>
      <ParsedJobEditor job={job} onChange={setJob} />
      <details>
        <summary>Original job text</summary>
        <pre className="doc">{session.job_text}</pre>
      </details>
      <ErrorAlert error={error} />
      <div className="actions">
        <button type="button" className="primary" disabled={busy}
          onClick={() => run(() => api.runFit(session.id, job), onDone)}>
          {session.fit_result ? 'Approve & re-score fit' : 'Approve & score my fit'}
        </button>
      </div>
      {busy && <Working label="Fit Scorer agent is searching your resume for evidence" />}
    </>
  )
}

function FitStep({ session, onDone }: StepProps) {
  const [notes, setNotes] = useState('')
  const { busy, error, run } = useAgentRun()
  return (
    <>
      <h2>How well you fit</h2>
      <FitCard fit={session.fit_result!} />
      <AgentTraceView steps={session.agent_trace?.fit} />
      <Field label="Notes for the resume tailor (optional)" hint='For example: "Emphasise leadership" or "I have used MongoDB in side projects; add it". Skills you state here count as true and may be added.'>
        {(id, hint) => (
          <textarea id={id} aria-describedby={hint} rows={2} maxLength={1000} value={notes}
            onChange={(e) => setNotes(e.target.value)} />
        )}
      </Field>
      <ErrorAlert error={error} />
      <div className="actions">
        <button type="button" className="primary" disabled={busy}
          onClick={() => run(() => api.runTailor(session.id, notes.trim() || undefined), onDone)}>
          {session.tailored_resume ? 'Re-tailor my resume' : 'Tailor my resume'}
        </button>
      </div>
      {busy && <Working label="Resume Tailor agent is drafting, checking keywords and verifying claims" />}
    </>
  )
}

function ResumeStep({ session, onDone, onSaved }: StepProps & { onSaved: (s: JobSession) => void }) {
  const [text, setText] = useState(session.tailored_resume!)
  const [notes, setNotes] = useState('')
  const [mode, setMode] = useState<'preview' | 'edit'>('preview')
  const [version, setVersion] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<unknown>(null)
  const { busy, error, run } = useAgentRun()
  const [changes, setChanges] = useState('')
  const retailor = useAgentRun()
  const dirty = text !== session.tailored_resume
  const revision = session.tailor_report?.revision ?? 0

  /** Revise the current text (including unsaved edits) with the user's requests; repeatable. */
  function reTailor() {
    retailor.run(() => api.runTailor(session.id, changes.trim(), text), (s) => {
      onSaved(s)
      setText(s.tailored_resume!)
      setChanges('')
      setVersion((v) => v + 1)
      setMode('preview')
    })
  }

  /** The PDF is rendered from the saved text, so save edits before showing the preview. */
  async function showPreview() {
    setSaveError(null)
    if (dirty) {
      setSaving(true)
      try {
        onSaved(await api.updateSession(session.id, { tailored_resume: text }))
        setVersion((v) => v + 1)
      } catch (e) {
        setSaveError(e)
        return
      } finally {
        setSaving(false)
      }
    }
    setMode('preview')
  }
  return (
    <>
      <h2>Your tailored resume</h2>
      {session.tailor_report && <TailorReportView report={session.tailor_report} />}
      <AgentTraceView steps={session.agent_trace?.tailor} />
      <div className="doc-toolbar">
        <h3 id="resume-view-label">Tailored resume</h3>
        <div className="segmented" role="group" aria-labelledby="resume-view-label">
          <button type="button" aria-pressed={mode === 'preview'} disabled={saving || text.trim().length < 50}
            onClick={showPreview}>
            {saving ? 'Saving…' : dirty && mode === 'edit' ? 'Save & preview' : 'Preview (PDF)'}
          </button>
          <button type="button" aria-pressed={mode === 'edit'} onClick={() => setMode('edit')}>
            Edit text
          </button>
        </div>
      </div>
      <ErrorAlert error={saveError} />
      {mode === 'preview' ? (
        <PdfPreview src={api.previewUrl(session.id, 'resume', version)} title="Tailored resume (PDF preview)" />
      ) : (
        <Field label="Resume text" hint="Keep headings in CAPITALS and start bullets with “- ” for the best PDF layout. Your version is used for the cover letter.">
          {(id, hint) => (
            <textarea id={id} aria-describedby={hint} className="doc-editor" rows={22} value={text}
              onChange={(e) => setText(e.target.value)} />
          )}
        </Field>
      )}
      <section className="retailor" aria-labelledby="retailor-title">
        <h3 id="retailor-title">
          Ask for changes {revision > 0 && <span className="muted small">(revised {revision}×)</span>}
        </h3>
        <Field label="What should change?"
          hint='For example: "Fix the formatting", "Add MongoDB, I used it in side projects", "Improve the summary", "Rewrite the projects section". Works on the current text, including your edits. Skills you state count as true.'>
          {(id, hint) => (
            <textarea id={id} aria-describedby={hint} rows={3} maxLength={1000} value={changes}
              onChange={(e) => setChanges(e.target.value)} />
          )}
        </Field>
        <ErrorAlert error={retailor.error} />
        <div className="actions">
          <button type="button" className="secondary"
            disabled={retailor.busy || busy || saving || !changes.trim() || text.trim().length < 50}
            onClick={reTailor}>
            Re-tailor with these changes
          </button>
        </div>
        {retailor.busy && <Working label="Resume Tailor is applying your changes and re-checking the resume" />}
      </section>
      <Field label="Notes for the cover letter (optional)" hint="Why this company, tone, anything personal to mention.">
        {(id, hint) => (
          <textarea id={id} aria-describedby={hint} rows={2} maxLength={1000} value={notes}
            onChange={(e) => setNotes(e.target.value)} />
        )}
      </Field>
      <ErrorAlert error={error} />
      <div className="actions">
        <a className="button secondary" href={api.exportUrl(session.id, 'resume', 'pdf')}>
          Download PDF
        </a>
        <a className="button secondary" href={api.exportUrl(session.id, 'resume')}>
          Download DOCX
        </a>
        <button type="button" className="primary" disabled={busy || retailor.busy || saving || text.trim().length < 50}
          onClick={() => run(() => api.runCoverLetter(session.id, text, notes.trim() || undefined), onDone)}>
          Approve & write cover letter
        </button>
      </div>
      <p className="muted small">Downloads use the last saved version. Approving also saves your edits.</p>
      {busy && <Working label="Writer and Critic agents are drafting and reviewing your letter" />}
    </>
  )
}

function LetterStep({ session, onSaved }: { session: JobSession; onSaved: (s: JobSession) => void }) {
  const [letter, setLetter] = useState(session.cover_letter!)
  const [status, setStatus] = useState<Status>(session.status)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const dirty = letter !== session.cover_letter || status !== session.status

  async function save() {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      onSaved(await api.updateSession(session.id, { cover_letter: letter, status }))
      setSaved(true)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h2>Your cover letter</h2>
      {session.cover_letter_report && <CoverLetterReportView report={session.cover_letter_report} />}
      <AgentTraceView rounds={session.agent_trace?.cover_letter} />
      <Field label="Cover letter" hint="Edit freely, then save.">
        {(id, hint) => (
          <textarea id={id} aria-describedby={hint} className="doc-editor" rows={18} value={letter}
            onChange={(e) => {
              setLetter(e.target.value)
              setSaved(false)
            }} />
        )}
      </Field>
      <Field label="Application status" hint="Track where this application is. You submit it yourself.">
        {(id, hint) => (
          <select id={id} aria-describedby={hint} value={status} onChange={(e) => {
            setStatus(e.target.value as Status)
            setSaved(false)
          }}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        )}
      </Field>
      <ErrorAlert error={error} />
      {saved && <Notice>Saved.</Notice>}
      <div className="actions">
        <button type="button" className="primary" disabled={busy || !dirty || letter.trim().length < 50} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <a className="button secondary" href={api.exportUrl(session.id, 'cover-letter', 'pdf')}>
          Download letter (PDF)
        </a>
        <a className="button secondary" href={api.exportUrl(session.id, 'cover-letter')}>
          Download letter (DOCX)
        </a>
        <a className="button secondary" href={api.exportUrl(session.id, 'resume', 'pdf')}>
          Download resume (PDF)
        </a>
        <a className="button secondary" href={api.exportUrl(session.id, 'resume')}>
          Download resume (DOCX)
        </a>
        <button type="button" className="secondary" onClick={() => navigator.clipboard?.writeText(letter)}>
          Copy letter
        </button>
      </div>
    </>
  )
}
