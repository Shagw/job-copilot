import { useEffect, useId, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import { errorMessage } from '../lib/format'

const errorIds = new WeakMap<object, number>()
let nextErrorId = 0
function keyFor(error: unknown): string {
  if (typeof error !== 'object' || error === null) return String(error)
  if (!errorIds.has(error)) errorIds.set(error, ++nextErrorId)
  return `e${errorIds.get(error)}`
}

/** Error alert. For "AI is busy" (503 + Retry-After) it counts down so the user knows when to retry. */
export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null
  // A new error remounts the body, which restarts its countdown.
  return <ErrorBody key={keyFor(error)} error={error} />
}

function ErrorBody({ error }: { error: unknown }) {
  const retryAfter = error instanceof ApiError ? error.retryAfter : null
  const [deadline] = useState(() => (retryAfter ? Date.now() + retryAfter * 1000 : 0))
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!deadline) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [deadline])

  const left = deadline ? Math.max(0, Math.ceil((deadline - now) / 1000)) : 0
  return (
    <div className="alert alert-error" role="alert">
      {errorMessage(error)}
      {retryAfter ? <span> {left > 0 ? `You can retry in ${left}s.` : 'You can retry now.'}</span> : null}
    </div>
  )
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="alert alert-info" role="status">
      {children}
    </div>
  )
}

/**
 * Shown while an agent runs: an elapsed timer, plus the live steps the server reports (the latest one is
 * announced to screen readers; earlier ones are shown as done).
 */
export function Working({ label, steps = [] }: { label: string; steps?: string[] }) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000)
    return () => clearInterval(timer)
  }, [])
  return (
    <div className="working-box">
      <div className="working" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span>
          {steps.length ? steps[steps.length - 1] : label} <span className="muted">({seconds}s)</span>
        </span>
      </div>
      {steps.length > 1 && (
        <ol className="live-steps" aria-label="Steps done so far">
          {steps.slice(0, -1).map((s, i) => (
            <li key={i}>
              <span aria-hidden="true">✓</span> {s}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: (id: string, describedBy?: string) => ReactNode
}) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children(id, hintId)}
      {hint && (
        <p className="hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  )
}

/** Edit a list of strings as one item per line. */
export function ListEditor({
  label,
  value,
  onChange,
  rows = 4,
}: {
  label: string
  value: string[]
  onChange: (next: string[]) => void
  rows?: number
}) {
  const [text, setText] = useState(value.join('\n'))
  return (
    <Field label={label} hint="One item per line">
      {(id, hintId) => (
        <textarea
          id={id}
          aria-describedby={hintId}
          rows={rows}
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            onChange(
              e.target.value
                .split('\n')
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }}
        />
      )}
    </Field>
  )
}

