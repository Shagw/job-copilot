import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { JobSession, ResumeVersion } from '../api/types'
import { diffLines } from '../lib/diff'
import { formatDate } from '../lib/format'
import { ErrorAlert } from './ui'

const SOURCE: Record<ResumeVersion['source'], string> = {
  tailor: 'Tailored',
  revise: 'Re-tailored',
  edit: 'Your edit',
  restore: 'Restored',
  earlier: 'Earlier version',
}

/** Every version of the tailored resume: compare any of them with the current text, or restore one. */
export function VersionsPanel({
  session,
  current,
  dirty,
  onRestored,
}: {
  session: JobSession
  /** The text in the editor now (may include unsaved edits). */
  current: string
  dirty: boolean
  onRestored: (s: JobSession) => void
}) {
  const [open, setOpen] = useState(false)
  const [versions, setVersions] = useState<ResumeVersion[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [comparing, setComparing] = useState<number | null>(null)
  const [restoring, setRestoring] = useState(false)

  // Reload when opened and whenever the saved text changes (tailor, re-tailor, save, restore).
  useEffect(() => {
    if (!open) return
    let cancelled = false
    api
      .listResumeVersions(session.id)
      .then((v) => !cancelled && setVersions(v))
      .catch((e) => !cancelled && setError(e))
    return () => {
      cancelled = true
    }
  }, [open, session.id, session.tailored_resume])

  async function restore(v: ResumeVersion) {
    setRestoring(true)
    setError(null)
    try {
      onRestored(await api.restoreResumeVersion(session.id, v.id))
      setComparing(null)
    } catch (e) {
      setError(e)
    } finally {
      setRestoring(false)
    }
  }

  const selected = versions?.find((v) => v.id === comparing)
  return (
    <details className="versions" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Versions{versions ? ` (${versions.length})` : ''}</summary>
      <ErrorAlert error={error} />
      {open && !versions && !error && <p className="muted small">Loading…</p>}
      {versions && (
        <ol className="version-list" aria-label="Resume versions, newest first">
          {versions.map((v) => (
            <li key={v.id} className={v.current ? 'version current' : 'version'}>
              <div className="version-head">
                <strong>v{v.number}</strong> {SOURCE[v.source] ?? v.source}
                {v.current && <span className="badge good">current</span>}
                <span className="muted small">{formatDate(v.created_at)}</span>
              </div>
              {v.note && <p className="small version-note">“{v.note}”</p>}
              <p className="muted small">
                {v.coverage !== null && <>Keyword coverage {v.coverage}%</>}
                {v.coverage !== null && v.fit_score !== null && ' · '}
                {v.fit_score !== null && <>Fit {v.fit_score}</>}
              </p>
              <div className="actions">
                <button type="button" className="secondary small-button" aria-pressed={comparing === v.id}
                  disabled={v.text === current}
                  onClick={() => setComparing(comparing === v.id ? null : v.id)}>
                  {v.text === current ? 'Same as current' : comparing === v.id ? 'Hide changes' : 'Compare with current'}
                </button>
                {!v.current && (
                  <button type="button" className="secondary small-button" disabled={restoring || dirty}
                    onClick={() => restore(v)}>
                    Restore v{v.number}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {versions && dirty && <p className="hint">Save or discard your edits before restoring a version.</p>}
      {selected && <DiffView before={selected.text} after={current} label={`Changes from v${selected.number} to the current text`} />}
    </details>
  )
}

function DiffView({ before, after, label }: { before: string; after: string; label: string }) {
  const lines = diffLines(before, after)
  const added = lines.filter((l) => l.kind === 'added').length
  const removed = lines.filter((l) => l.kind === 'removed').length
  return (
    <figure className="diff">
      <figcaption>
        {label}: <span className="diff-added-count">+{added}</span> <span className="diff-removed-count">−{removed}</span> lines
      </figcaption>
      <pre>
        {lines.map((l, i) =>
          l.kind === 'same' ? (
            <span key={i} className="diff-same">  {l.text}{'\n'}</span>
          ) : l.kind === 'added' ? (
            <ins key={i}>+ {l.text}{'\n'}</ins>
          ) : (
            <del key={i}>− {l.text}{'\n'}</del>
          ),
        )}
      </pre>
    </figure>
  )
}
