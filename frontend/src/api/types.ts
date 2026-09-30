// Mirrors backend/app/schemas.py and the agent result models.

export interface User {
  id: number
  email: string
  is_verified: boolean
  created_at: string
  is_admin: boolean
}

export interface Message {
  message: string
}

export interface Resume {
  id: number
  filename: string
  raw_text: string
  uploaded_at: string
  chunks: number | null
}

export interface ParsedJob {
  title: string
  company: string | null
  location: string | null
  seniority: string | null
  summary: string
  must_have: string[]
  nice_to_have: string[]
  responsibilities: string[]
  keywords: string[]
}

export type Match = 'strong' | 'partial' | 'missing'

export interface RequirementFit {
  id: string
  requirement: string
  importance: 'must' | 'nice'
  match: Match
  evidence: string | null
  note: string | null
}

export interface FitResult {
  score: number
  verdict: string
  summary: string
  requirements: RequirementFit[]
  strengths: string[]
  gaps: string[]
  advice: string[]
}

export interface Coverage {
  total: number
  covered: string[]
  missing_supported: string[]
  missing_unsupported: string[]
  percent: number
  supported_percent: number
}

export interface ClaimIssue {
  type: string
  detail: string
  line: string
}

export interface TailorReport {
  coverage_before: Coverage
  coverage_after: Coverage
  claims: { ok: boolean; issues: ClaimIssue[] }
  changes: string[]
  target_met: boolean
  /** Set when the resume was too long and the AI worked from a shortened version. */
  note?: string | null
  /** 0 = first tailoring; 1, 2, ... = re-tailored with the user's change requests. */
  revision?: number
  /** Every note given so far (trusted facts for later revisions). */
  notes?: string[]
  /** Keywords covered now but not before, and where they are in the tailored resume. */
  keywords_added?: { keyword: string; where: string }[]
  /** Quantified results from the resume that the tailored version lost. */
  metrics_dropped?: string[]
  /** 15-second recruiter screen: code checks, plus Jev judgments when enabled. */
  recruiter?: RecruiterCheck[]
  /** Fit re-scored on this version (same job, same scorer); null if there was no earlier fit. */
  fit_after?: FitChange | null
}

export interface FitChange {
  before: number
  after: number
  verdict: string
  /** e.g. "Kubernetes: missing → partial" */
  improved: string[]
  worse: string[]
}

export interface ResumeVersion {
  id: number
  /** 1 = oldest */
  number: number
  source: 'tailor' | 'revise' | 'edit' | 'restore' | 'earlier'
  note: string | null
  text: string
  created_at: string
  coverage: number | null
  fit_score: number | null
  current: boolean
}

export interface RecruiterCheck {
  id: string
  label: string
  ok: boolean
  detail?: string | null
  by: 'code' | 'jev'
}

export interface CoverLetterRound {
  round: number
  score: number
  approved: boolean
  issues: string[]
}

export interface CoverLetterReport {
  approved: boolean
  score: number
  rounds: number
  remaining_issues: string[]
  suggestions: string[]
  history: CoverLetterRound[]
  note?: string | null
}

/** One step of an agent's reasoning trace (see backend app/agents/base.py). */
export interface TraceStep {
  step: number
  type: 'tool' | 'final'
  tool?: string
  args?: Record<string, unknown>
  result?: string
  model: string
  thought?: string | null
  tokens?: number | null
  forced?: boolean
  retry?: boolean
}

export interface CoverLetterTraceRound {
  round: number
  writer: TraceStep[]
  critic: {
    score?: number
    approved?: boolean
    issues?: string[]
    suggestions?: string[]
    hints?: string[]
    error?: string
  }
}

export interface AgentTrace {
  fit?: TraceStep[]
  tailor?: TraceStep[]
  cover_letter?: CoverLetterTraceRound[]
}

export type Step = 'created' | 'parsed' | 'scored' | 'tailored' | 'done'
export const STATUSES = ['draft', 'applied', 'interview', 'rejected', 'offer'] as const
export type Status = (typeof STATUSES)[number]

export interface JobSession {
  id: number
  job_url: string | null
  job_text: string
  parsed_job: ParsedJob | null
  fit_result: FitResult | null
  tailored_resume: string | null
  tailor_report: TailorReport | null
  cover_letter: string | null
  cover_letter_report: CoverLetterReport | null
  agent_trace: AgentTrace | null
  current_step: Step
  status: Status
  created_at: string
}

export interface SessionSummary {
  id: number
  title: string | null
  company: string | null
  job_url: string | null
  fit_score: number | null
  current_step: Step
  status: Status
  created_at: string
  visible_until: string
}

export interface LlmSlot {
  slot: number
  key: string
  model: string
  state: 'active' | 'cooling' | 'disabled'
  cooldown_remaining_seconds: number
  last_error: string | null
}
