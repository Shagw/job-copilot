import type {
  CoverLetterReport,
  CoverLetterTraceRound,
  FitResult,
  ParsedJob,
  TailorReport,
  TraceStep,
} from '../api/types'
import { scoreClass } from '../lib/format'
import { Field, ListEditor } from './ui'

export function ParsedJobEditor({ job, onChange }: { job: ParsedJob; onChange: (job: ParsedJob) => void }) {
  const set = <K extends keyof ParsedJob>(key: K, value: ParsedJob[K]) => onChange({ ...job, [key]: value })
  return (
    <div className="grid-2">
      <Field label="Job title">
        {(id) => <input id={id} value={job.title} onChange={(e) => set('title', e.target.value)} />}
      </Field>
      <Field label="Company">
        {(id) => (
          <input id={id} value={job.company ?? ''} onChange={(e) => set('company', e.target.value || null)} />
        )}
      </Field>
      <Field label="Location">
        {(id) => (
          <input id={id} value={job.location ?? ''} onChange={(e) => set('location', e.target.value || null)} />
        )}
      </Field>
      <Field label="Seniority">
        {(id) => (
          <input id={id} value={job.seniority ?? ''} onChange={(e) => set('seniority', e.target.value || null)} />
        )}
      </Field>
      <div className="span-2">
        <Field label="Summary">
          {(id) => <textarea id={id} rows={2} value={job.summary} onChange={(e) => set('summary', e.target.value)} />}
        </Field>
      </div>
      <ListEditor label="Must-have requirements" value={job.must_have} onChange={(v) => set('must_have', v)} rows={6} />
      <ListEditor label="Nice to have" value={job.nice_to_have} onChange={(v) => set('nice_to_have', v)} rows={6} />
      <div className="span-2">
        <ListEditor label="ATS keywords" value={job.keywords} onChange={(v) => set('keywords', v)} rows={4} />
      </div>
    </div>
  )
}

const MATCH_LABEL = { strong: 'Strong', partial: 'Partial', missing: 'Missing' } as const

export function FitCard({ fit }: { fit: FitResult }) {
  return (
    <div>
      <div className="score-row">
        <div className={`score ${scoreClass(fit.score)}`} aria-label={`Fit score ${fit.score} out of 100`}>
          {fit.score}
        </div>
        <div>
          <strong>{fit.verdict}</strong>
          <p>{fit.summary}</p>
        </div>
      </div>
      <table className="table">
        <caption className="sr-only">Requirements and evidence</caption>
        <thead>
          <tr>
            <th scope="col">Requirement</th>
            <th scope="col">Match</th>
            <th scope="col">Evidence from your resume</th>
          </tr>
        </thead>
        <tbody>
          {fit.requirements.map((r) => (
            <tr key={r.id}>
              <td>
                {r.requirement} {r.importance === 'nice' && <span className="muted small">(nice to have)</span>}
              </td>
              <td>
                <span className={`badge match-${r.match}`}>{MATCH_LABEL[r.match]}</span>
              </td>
              <td>
                {r.evidence ? <q>{r.evidence}</q> : <span className="muted">{r.note ?? 'No evidence found'}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="grid-2">
        <BulletList title="Strengths" items={fit.strengths} />
        <BulletList title="Advice for this application" items={fit.advice} />
      </div>
    </div>
  )
}

function BulletList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div>
      <h3>{title}</h3>
      <ul>
        {items.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    </div>
  )
}

function Chips({ items, kind }: { items: string[]; kind: string }) {
  if (!items.length) return <span className="muted">none</span>
  return (
    <span className="chips">
      {items.map((x) => (
        <span key={x} className={`chip chip-${kind}`}>
          {x}
        </span>
      ))}
    </span>
  )
}

export function TailorReportView({ report }: { report: TailorReport }) {
  const before = report.coverage_before
  const after = report.coverage_after
  return (
    <div className="report">
      <p>
        <strong>Keyword coverage:</strong> {before.percent}% → {after.percent}% of all job keywords ·{' '}
        <strong>{after.supported_percent}%</strong> of the ones your resume supports
        {report.target_met ? (
          <span className="badge good"> checks passed</span>
        ) : (
          <span className="badge ok"> please review</span>
        )}
      </p>
      <p>
        Covered: <Chips items={after.covered} kind="good" />
      </p>
      {after.missing_supported.length > 0 && (
        <p>
          You have these but the draft doesn't show them: <Chips items={after.missing_supported} kind="ok" />
        </p>
      )}
      <p>
        Not added (no evidence in your resume): <Chips items={after.missing_unsupported} kind="bad" />
      </p>
      {!report.claims.ok && (
        <div className="alert alert-warn" role="alert">
          <strong>Check these lines before using the resume:</strong>
          <ul>
            {report.claims.issues.map((i, n) => (
              <li key={n}>
                {i.detail}: <q>{i.line}</q>
              </li>
            ))}
          </ul>
        </div>
      )}
      {report.changes.length > 0 && <BulletList title="What the agent changed" items={report.changes} />}
      {report.note && <p className="hint">ℹ️ {report.note}</p>}
    </div>
  )
}

export function CoverLetterReportView({ report }: { report: CoverLetterReport }) {
  return (
    <div className="report">
      <p>
        {report.approved ? (
          <span className="badge good">Approved by the critic</span>
        ) : (
          <span className="badge ok">Not approved yet</span>
        )}{' '}
        score {report.score}/10 after {report.rounds} round{report.rounds === 1 ? '' : 's'} (
        {report.history.map((h) => h.score).join(' → ')})
      </p>
      {report.note && <p className="hint">ℹ️ {report.note}</p>}
      {!report.approved && (
        <div className="alert alert-warn" role="alert">
          <strong>The critic still flagged:</strong>
          <ul>
            {[...report.remaining_issues, ...report.suggestions].map((x, n) => (
              <li key={n}>{x}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function stepLine(t: TraceStep): string {
  if (t.type === 'final') return t.forced ? 'Step limit reached → final answer' : t.retry ? 'Fixed answer format' : 'Final answer'
  const args = t.args ?? {}
  const query = typeof args.query === 'string' ? `“${args.query}”` : ''
  return `${t.tool}${query ? ` ${query}` : ''}`
}

/** The final step's "thought" is often the raw JSON/tagged answer itself; that's shown above, not here. */
function showThought(t: TraceStep): boolean {
  return !!t.thought && !(t.type === 'final' && /^\s*[{<[]/.test(t.thought))
}

function TraceList({ steps }: { steps: TraceStep[] }) {
  return (
    <ol className="trace">
      {steps.map((t, i) => (
        <li key={i}>
          <div>
            <strong>{stepLine(t)}</strong>{' '}
            <span className="muted small">
              {t.model}
              {t.tokens ? ` · ${t.tokens} tokens` : ''}
            </span>
          </div>
          {showThought(t) && <div className="thought">💭 {t.thought}</div>}
          {t.result && <code className="observation">{t.result}</code>}
        </li>
      ))}
    </ol>
  )
}

/** "How the agent got here": the ReAct steps (tool calls, results, reasoning) behind a result. */
export function AgentTraceView({ steps, rounds }: { steps?: TraceStep[]; rounds?: CoverLetterTraceRound[] }) {
  if (!steps?.length && !rounds?.length) return null
  const count = steps ? steps.length : rounds!.length
  return (
    <details className="trace-box">
      <summary>
        How the agent got here ({count} {steps ? 'steps' : 'rounds'})
      </summary>
      {steps && <TraceList steps={steps} />}
      {rounds?.map((r) => (
        <div key={r.round} className="round">
          <h4>Round {r.round}: writer</h4>
          <TraceList steps={r.writer} />
          <h4>
            Round {r.round}: critic{' '}
            {r.critic.score !== undefined && (
              <span className="muted small">
                score {r.critic.score}/10 · {r.critic.approved ? 'approved' : 'sent back'}
              </span>
            )}
          </h4>
          {r.critic.error && <p className="muted">{r.critic.error}</p>}
          {!!r.critic.issues?.length && (
            <ul>
              {r.critic.issues.map((x, n) => (
                <li key={n}>{x}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </details>
  )
}
