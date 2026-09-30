import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import type { ResumeVersion, TailorReport } from '../api/types'
import { Working } from '../components/ui'
import { diffLines } from '../lib/diff'
import { USER, mockFetch, renderApp, session } from './helpers'
import type { FetchMock } from './helpers'

let f: FetchMock
beforeEach(() => {
  f = mockFetch()
  f.on('GET', '/auth/me', { body: USER })
})
afterEach(() => {
  vi.unstubAllGlobals()
})

/** A text/event-stream response delivered in the given chunks (to test events split across reads). */
function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

const ev = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

describe('streaming agent runs', () => {
  it('reports each progress step, then returns the saved session', async () => {
    const done = session({ current_step: 'scored' })
    const text = ': started\n\n' + ev('progress', { message: 'Finding evidence' }) + ev('progress', { message: 'Scoring' }) + ev('done', done)
    const fetch = vi.fn(async () => sseResponse([text.slice(0, 30), text.slice(30, 75), text.slice(75)]))
    vi.stubGlobal('fetch', fetch)
    const steps: string[] = []

    const result = await api.runFit(7, undefined, (m) => steps.push(m))
    expect(result.current_step).toBe('scored')
    expect(steps).toEqual(['Finding evidence', 'Scoring'])
    const init = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect((init.headers as Record<string, string>).Accept).toBe('text/event-stream')
  })

  it('turns an error event into an ApiError with Retry-After', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      sseResponse([ev('progress', { message: 'Writing' }), ev('error', { status: 503, detail: 'AI is busy, try again in 9 seconds', retry_after: 9 })])))
    const err = await api.runTailor(7).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect([err.status, err.message, err.retryAfter]).toEqual([503, 'AI is busy, try again in 9 seconds', 9])
  })

  it('says the connection was lost if the stream ends without a result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([ev('progress', { message: 'Writing' })])))
    await expect(api.runCoverLetter(7)).rejects.toThrow('The connection was lost')
  })

  it('still handles plain JSON errors from before the run starts', async () => {
    f.on('POST', '/sessions/7/tailor', { status: 400, body: { detail: 'Upload a resume first' } })
    await expect(api.runTailor(7)).rejects.toThrow('Upload a resume first')
  })

  it('shows the latest step live and earlier ones as done', () => {
    render(<Working label="Tailoring" steps={['Writing the tailored draft', 'Checking keywords', 'Fix round 1: fixing 2 problems']} />)
    expect(screen.getByRole('status')).toHaveTextContent('Fix round 1: fixing 2 problems')
    const done = within(screen.getByRole('list', { name: 'Steps done so far' })).getAllByRole('listitem')
    expect(done.map((li) => li.textContent?.trim())).toEqual(['✓ Writing the tailored draft', '✓ Checking keywords'])
  })
})

describe('diffLines', () => {
  it('marks added, removed and unchanged lines', () => {
    expect(diffLines('a\nb\nc', 'a\nc\nd')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'removed', text: 'b' },
      { kind: 'same', text: 'c' },
      { kind: 'added', text: 'd' },
    ])
  })
})

// ---------- Resume step: fit on this version, versions, compare, restore ----------

const V1 = 'Alice Example\n\nSUMMARY\nBackend engineer.\n\nEXPERIENCE\n- Built APIs in Python at Acme for three years'
const V2 = V1.replace('Backend engineer.', 'Backend engineer building Python APIs on AWS.')

const REPORT: TailorReport = {
  coverage_before: { total: 2, covered: [], missing_supported: [], missing_unsupported: [], percent: 0, supported_percent: 0 },
  coverage_after: { total: 2, covered: ['Python', 'AWS'], missing_supported: [], missing_unsupported: [], percent: 100, supported_percent: 100 },
  claims: { ok: true, issues: [] },
  changes: [],
  target_met: true,
  fit_after: { before: 62, after: 78, verdict: 'Good fit', improved: ['AWS: missing → strong'], worse: [] },
}

const VERSIONS: ResumeVersion[] = [
  { id: 12, number: 2, source: 'revise', note: 'Mention AWS in the summary', text: V2, created_at: '2026-09-30T10:05:00+00:00', coverage: 100, fit_score: 78, current: true },
  { id: 11, number: 1, source: 'tailor', note: null, text: V1, created_at: '2026-09-30T10:00:00+00:00', coverage: 50, fit_score: 70, current: false },
]

describe('resume versions', () => {
  it('shows the fit re-scored on the tailored version', async () => {
    f.on('GET', '/sessions/7', { body: session({ tailored_resume: V2, tailor_report: REPORT, current_step: 'tailored' }) })
    renderApp('/sessions/7')
    const block = (await screen.findByText('Fit on this version')).closest('.report-block') as HTMLElement
    expect(block).toHaveTextContent('62 → 78 (+16, Good fit)')
    expect(block).toHaveTextContent('AWS: missing → strong')
  })

  it('lists versions, compares one with the current text and restores it', async () => {
    f.on('GET', '/sessions/7', { body: session({ tailored_resume: V2, tailor_report: REPORT, current_step: 'tailored' }) })
    f.on('GET', '/sessions/7/resume-versions', { body: VERSIONS })
    f.on('POST', '/sessions/7/resume-versions/11/restore', {
      body: session({ tailored_resume: V1, tailor_report: { ...REPORT, fit_after: null }, current_step: 'tailored' }) })
    renderApp('/sessions/7')
    const user = userEvent.setup()

    await user.click(await screen.findByText('Versions'))
    const list = await screen.findByRole('list', { name: 'Resume versions, newest first' })
    const [v2, v1] = within(list).getAllByRole('listitem')
    expect(v2).toHaveTextContent('v2 Re-tailored')
    expect(v2).toHaveTextContent('current')
    expect(v2).toHaveTextContent('“Mention AWS in the summary”')
    expect(v1).toHaveTextContent('Keyword coverage 50% · Fit 70')
    expect(within(v2).getByRole('button', { name: 'Same as current' })).toBeDisabled()

    await user.click(within(v1).getByRole('button', { name: 'Compare with current' }))
    const diff = screen.getByText(/Changes from v1 to the current text/).closest('figure') as HTMLElement
    expect(diff).toHaveTextContent('+1 −1 lines')
    expect(diff.querySelector('del')).toHaveTextContent('Backend engineer.')
    expect(diff.querySelector('ins')).toHaveTextContent('Backend engineer building Python APIs on AWS.')

    await user.click(within(v1).getByRole('button', { name: 'Restore v1' }))
    await waitFor(() => expect(f.calls.some((c) => c.method === 'POST' && c.path === '/sessions/7/resume-versions/11/restore')).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Edit text' }))
    expect(screen.getByLabelText('Resume text')).toHaveValue(V1)
  })

  it('does not restore over unsaved edits', async () => {
    f.on('GET', '/sessions/7', { body: session({ tailored_resume: V2, tailor_report: REPORT, current_step: 'tailored' }) })
    f.on('GET', '/sessions/7/resume-versions', { body: VERSIONS })
    renderApp('/sessions/7')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit text' }))
    await user.type(screen.getByLabelText('Resume text'), ' more')
    await user.click(screen.getByText('Versions'))
    expect(await screen.findByRole('button', { name: 'Restore v1' })).toBeDisabled()
    expect(screen.getByText('Save or discard your edits before restoring a version.')).toBeInTheDocument()
  })
})
