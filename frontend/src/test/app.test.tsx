import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import type { FitResult } from '../api/types'
import { ErrorAlert } from '../components/ui'
import { timeLeft } from '../lib/format'
import { PARSED, USER, mockFetch, renderApp, session } from './helpers'
import type { FetchMock } from './helpers'

let f: FetchMock
beforeEach(() => {
  f = mockFetch()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const loggedOut = () => f.on('GET', '/auth/me', { status: 401, body: { detail: 'Not authenticated' } })
const loggedIn = (user = USER) => f.on('GET', '/auth/me', { body: user })

const FIT: FitResult = {
  score: 60,
  verdict: 'Moderate fit',
  summary: 'Good Python, no Kubernetes.',
  requirements: [
    { id: 'M1', requirement: 'Python', importance: 'must', match: 'strong', evidence: 'Built APIs in Python', note: null },
    { id: 'M2', requirement: 'Kubernetes', importance: 'must', match: 'missing', evidence: null, note: 'Not found' },
  ],
  strengths: ['Python APIs'],
  gaps: ['Kubernetes'],
  advice: ['Lead with API work'],
}

// ---------- API client ----------

describe('api client', () => {
  it('sends cookies and JSON', async () => {
    f.on('POST', '/auth/login', { body: USER })
    await api.login('a@b.com', 'Secret123')
    expect(f.calls[0].init.credentials).toBe('include')
    expect(f.calls[0].body).toEqual({ email: 'a@b.com', password: 'Secret123' })
  })

  it('turns FastAPI validation errors into a readable message', async () => {
    f.on('POST', '/auth/signup', {
      status: 422,
      body: { detail: [{ msg: 'Value error, Password must contain at least one letter and one digit' }] },
    })
    await expect(api.signup('a@b.com', 'aaaaaaaa')).rejects.toThrow('Password must contain at least one letter and one digit')
  })

  it('exposes Retry-After for busy AI', async () => {
    f.on('POST', '/sessions', {
      status: 503,
      body: { detail: 'AI is busy, try again in 12 seconds' },
      headers: { 'Retry-After': '12' },
    })
    const err = await api.createSession({ job_text: 'x' }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(503)
    expect(err.retryAfter).toBe(12)
  })

  it('reports an unreachable backend', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    await expect(api.me()).rejects.toThrow('Cannot reach the server')
  })

  it('does not set JSON content-type for file uploads', async () => {
    f.on('POST', '/resume', { status: 201, body: {} })
    await api.uploadResume(new File(['hello'], 'cv.txt'))
    expect(f.calls[0].init.headers).toBeUndefined()
    expect(f.calls[0].init.body).toBeInstanceOf(FormData)
  })
})

// ---------- auth flows ----------

describe('auth', () => {
  it('redirects logged-out users to login', async () => {
    loggedOut()
    renderApp('/')
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeInTheDocument()
  })

  it('logs in and shows history', async () => {
    loggedOut()
    f.on('POST', '/auth/login', { body: USER })
    f.on('GET', '/sessions', { body: [] })
    renderApp('/login')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'Secret123')
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('heading', { name: 'Your recent jobs' })).toBeInTheDocument()
    expect(await screen.findByText(/Nothing in the last 3 days/)).toBeInTheDocument()
  })

  it('shows the login error', async () => {
    loggedOut()
    f.on('POST', '/auth/login', { status: 401, body: { detail: 'Invalid email or password' } })
    renderApp('/login')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
  })

  it('sends unverified users to the OTP page with a fresh code', async () => {
    loggedOut()
    f.on('POST', '/auth/login', { status: 403, body: { detail: 'Email not verified' } })
    f.on('POST', '/auth/resend-otp', { body: { message: 'sent' } })
    renderApp('/login')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'Secret123')
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeInTheDocument()
    expect(f.calls.some((c) => c.path === '/auth/resend-otp')).toBe(true)
    expect(screen.getByLabelText('Email')).toHaveValue('alice@example.com')
  })

  it('signup checks passwords match before calling the API', async () => {
    loggedOut()
    renderApp('/signup')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'Secret123')
    await user.type(screen.getByLabelText('Confirm password'), 'Secret124')
    await user.click(screen.getByRole('button', { name: 'Create account' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Passwords do not match')
    expect(f.calls.some((c) => c.path === '/auth/signup')).toBe(false)
  })

  it('verifies the OTP and lands on the resume page', async () => {
    loggedOut()
    f.on('POST', '/auth/verify-otp', { body: USER })
    f.on('GET', '/resume', { status: 404, body: { detail: 'No resume uploaded yet' } })
    renderApp('/verify?email=alice%40example.com&sent=1')
    const user = userEvent.setup()
    const code = await screen.findByLabelText('6-digit code')
    await user.type(code, '12a34 56') // non-digits are stripped
    expect(code).toHaveValue('123456')
    expect(screen.getByRole('button', { name: /Resend code in/ })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByRole('heading', { name: 'Your master resume' })).toBeInTheDocument()
    expect(f.calls.find((c) => c.path === '/auth/verify-otp')?.body).toEqual({ email: 'alice@example.com', code: '123456' })
  })

  it('returns to the page the user originally wanted after login', async () => {
    loggedOut()
    f.on('POST', '/auth/login', { body: USER })
    f.on('GET', '/sessions/7', { body: session() })
    renderApp('/sessions/7')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Email'), 'alice@example.com')
    await user.type(screen.getByLabelText('Password'), 'Secret123')
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByLabelText('Job title')).toHaveValue('Senior Backend Engineer')
  })

  it('shows the admin link only to admins', async () => {
    loggedIn({ ...USER, is_admin: true })
    f.on('GET', '/sessions', { body: [] })
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'AI status' })).toBeInTheDocument()
  })
})

// ---------- history ----------

describe('history', () => {
  it('lists recent jobs with score, step, status and time left', async () => {
    loggedIn()
    const created = new Date(Date.now() - 3_600_000).toISOString()
    const until = new Date(Date.now() + 2 * 86_400_000 + 3 * 3_600_000).toISOString()
    f.on('GET', '/sessions', {
      body: [{ id: 7, title: 'Backend Engineer', company: 'Initech', job_url: null, fit_score: 82,
        current_step: 'tailored', status: 'applied', created_at: created, visible_until: until }],
    })
    renderApp('/')
    const link = await screen.findByRole('link', { name: /Backend Engineer/ })
    expect(link).toHaveAttribute('href', '/sessions/7')
    const item = link.closest('li')!
    expect(within(item).getByText('Fit 82')).toHaveClass('good')
    expect(within(item).getByText('Resume tailored')).toBeInTheDocument()
    expect(within(item).getByText('applied')).toBeInTheDocument()
    expect(within(item).getByText(/2d \d+h left/)).toBeInTheDocument()
  })
})

// ---------- session wizard (human in the loop) ----------

describe('session wizard', () => {
  it('lets the user edit the parsed job, then runs the fit agent with the edits', async () => {
    loggedIn()
    f.on('GET', '/sessions/7', { body: session() })
    f.on('POST', '/sessions/7/fit', (init) => {
      const edited = JSON.parse(String(init.body)).parsed_job
      return { body: session({ parsed_job: edited, fit_result: FIT, current_step: 'scored',
        agent_trace: { fit: [
          { step: 1, type: 'tool', tool: 'search_my_experience', args: { query: 'Kubernetes' },
            result: '{"results": []}', model: 'openai/gpt-oss-120b', thought: 'Check K8s', tokens: 900 },
          { step: 2, type: 'final', model: 'openai/gpt-oss-120b', tokens: 1200 },
        ] } }) }
    })
    renderApp('/sessions/7')
    const user = userEvent.setup()

    const title = await screen.findByLabelText('Job title')
    expect(screen.getByRole('tab', { name: '2. Fit' })).toBeDisabled() // can't skip ahead
    await user.clear(title)
    await user.type(title, 'Staff Engineer')
    const musts = screen.getByLabelText('Must-have requirements')
    await user.clear(musts)
    await user.type(musts, 'Python{enter}Go')
    await user.click(screen.getByRole('button', { name: 'Approve & score my fit' }))

    // Result of the agent is shown for review on the next tab.
    expect(await screen.findByLabelText('Fit score 60 out of 100')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '2. Fit' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Built APIs in Python')).toBeInTheDocument()
    const sent = f.calls.find((c) => c.path === '/sessions/7/fit')!.body as { parsed_job: typeof PARSED }
    expect(sent.parsed_job.title).toBe('Staff Engineer')
    expect(sent.parsed_job.must_have).toEqual(['Python', 'Go'])

    // Agent reasoning trace is available.
    await user.click(screen.getByText(/How the agent got here \(2 steps\)/))
    expect(screen.getByText('search_my_experience “Kubernetes”')).toBeInTheDocument()
    expect(screen.getByText(/Check K8s/)).toBeInTheDocument()
  })

  it('sends the edited tailored resume and notes to the cover letter agent', async () => {
    loggedIn()
    const tailored = 'Alice Example\n\nEXPERIENCE\n- Built APIs in Python with FastAPI at Acme for three years'
    f.on('GET', '/sessions/7', { body: session({ fit_result: FIT, tailored_resume: tailored, current_step: 'tailored',
      tailor_report: {
        coverage_before: { total: 3, covered: ['Python'], missing_supported: [], missing_unsupported: ['Kubernetes'], percent: 33, supported_percent: 50 },
        coverage_after: { total: 3, covered: ['Python', 'React'], missing_supported: [], missing_unsupported: ['Kubernetes'], percent: 67, supported_percent: 100 },
        claims: { ok: false, issues: [{ type: 'unsupported_number', detail: "'9' does not appear in the original resume", line: '- 9M users' }] },
        changes: ['Added summary'], target_met: false } }) })
    f.on('POST', '/sessions/7/cover-letter', {
      body: session({ fit_result: FIT, tailored_resume: tailored + ' edited', cover_letter: 'Dear Initech team, ...',
        current_step: 'done',
        cover_letter_report: { approved: true, score: 9, rounds: 2, remaining_issues: [], suggestions: [],
          history: [{ round: 1, score: 6, approved: false, issues: ['generic'] }, { round: 2, score: 9, approved: true, issues: [] }] } }),
    })
    renderApp('/sessions/7')
    const user = userEvent.setup()

    expect(await screen.findByText(/Check these lines before using the resume/)).toBeInTheDocument()
    expect(screen.getByText('Kubernetes')).toHaveClass('chip-bad') // never added: no evidence
    await user.click(screen.getByRole('button', { name: 'Edit text' }))
    await user.type(screen.getByLabelText('Resume text'), ' edited')
    await user.type(screen.getByLabelText('Notes for the cover letter (optional)'), 'Love your open source work')
    await user.click(screen.getByRole('button', { name: 'Approve & write cover letter' }))

    expect(await screen.findByLabelText('Cover letter')).toHaveValue('Dear Initech team, ...')
    expect(screen.getByText(/score 9\/10 after 2 rounds \(6 → 9\)/)).toBeInTheDocument()
    expect(f.calls.find((c) => c.path === '/sessions/7/cover-letter')!.body).toEqual({
      tailored_resume: tailored + ' edited',
      instructions: 'Love your open source work',
    })
  })

  it('shows keywords added with their place, lost results and the recruiter check', async () => {
    loggedIn()
    const tailored = 'Alice Example\n\nEXPERIENCE\n- Built APIs in Python with FastAPI at Acme for three years'
    f.on('GET', '/sessions/7', { body: session({ fit_result: FIT, tailored_resume: tailored, current_step: 'tailored',
      tailor_report: {
        coverage_before: { total: 2, covered: [], missing_supported: [], missing_unsupported: [], percent: 0, supported_percent: 0 },
        coverage_after: { total: 2, covered: ['React'], missing_supported: [], missing_unsupported: [], percent: 50, supported_percent: 100 },
        claims: { ok: true, issues: [] }, changes: [], target_met: true,
        keywords_added: [{ keyword: 'React', where: 'Experience: Acme Corp (2021-2024)' }],
        metrics_dropped: ['40%'],
        recruiter: [
          { id: 'measurable_impact', label: 'Shows measurable impact', ok: true, by: 'code' },
          { id: 'relevant_fast', label: 'Relevant within 15 seconds', ok: false, detail: 'Name the target role', by: 'jev' },
        ] } }) })
    renderApp('/sessions/7')

    expect(await screen.findByText('in Experience: Acme Corp (2021-2024)')).toBeInTheDocument()
    expect(screen.getByText(/These results from your resume are missing/).closest('[role="alert"]')).toHaveTextContent('40%')
    expect(screen.getByText(/1\/2 passed/)).toBeInTheDocument()
    expect(screen.getByText('Needs work:').parentElement).toHaveTextContent('Relevant within 15 seconds — Name the target role')
  })

  it('re-tailors the current text with requested changes, again and again', async () => {
    loggedIn()
    const v0 = 'Alice Example\n\nEXPERIENCE\n- Built APIs in Python with FastAPI at Acme for three years'
    const v1 = v0 + '\n\nSKILLS\n- MongoDB'
    const v2 = v1.replace('SKILLS', 'TECHNICAL SKILLS')
    f.on('GET', '/sessions/7', { body: session({ fit_result: FIT, tailored_resume: v0, current_step: 'tailored' }) })
    let round = 0
    f.on('POST', '/sessions/7/tailor', () => {
      round += 1
      return { body: session({ fit_result: FIT, tailored_resume: round === 1 ? v1 : v2, current_step: 'tailored',
        tailor_report: { coverage_before: { total: 1, covered: [], missing_supported: [], missing_unsupported: [], percent: 0, supported_percent: 0 },
          coverage_after: { total: 1, covered: ['MongoDB'], missing_supported: [], missing_unsupported: [], percent: 100, supported_percent: 100 },
          claims: { ok: true, issues: [] }, changes: [], target_met: true, revision: round, notes: [] } }) }
    })
    renderApp('/sessions/7')
    const user = userEvent.setup()

    const button = await screen.findByRole('button', { name: 'Re-tailor with these changes' })
    expect(button).toBeDisabled() // nothing requested yet
    await user.click(screen.getByRole('button', { name: 'Edit text' }))
    await user.type(screen.getByLabelText('Resume text'), ' (edited)')
    await user.type(screen.getByLabelText('What should change?'), 'Add MongoDB, I used it in side projects')
    await user.click(button)

    expect(await screen.findByText('(revised 1×)')).toBeInTheDocument()
    expect(screen.getByLabelText('What should change?')).toHaveValue('') // ready for the next request
    const tailorCalls = () => f.calls.filter((c) => c.path === '/sessions/7/tailor')
    expect(tailorCalls()[0].body).toEqual({ instructions: 'Add MongoDB, I used it in side projects', current_resume: v0 + ' (edited)' })

    await user.type(screen.getByLabelText('What should change?'), 'Rename the skills section')
    await user.click(screen.getByRole('button', { name: 'Re-tailor with these changes' }))
    expect(await screen.findByText('(revised 2×)')).toBeInTheDocument()
    expect(tailorCalls()[1].body).toEqual({ instructions: 'Rename the skills section', current_resume: v1 }) // the new version
  })

  it('previews the tailored resume as a PDF and saves edits before re-rendering it', async () => {
    loggedIn()
    const tailored = 'Alice Example\n\nEXPERIENCE\n- Built APIs in Python with FastAPI at Acme for three years'
    const s = session({ fit_result: FIT, tailored_resume: tailored, current_step: 'tailored' })
    f.on('GET', '/sessions/7', { body: s })
    f.on('PATCH', '/sessions/7', (init) => ({ body: { ...s, ...JSON.parse(String(init.body)) } }))
    renderApp('/sessions/7')
    const user = userEvent.setup()

    const frame = await screen.findByTitle('Tailored resume (PDF preview)')
    expect(frame).toHaveAttribute('src', '/api/sessions/7/export/resume?format=pdf&inline=true&v=0#view=FitH&navpanes=0')
    expect(screen.getByRole('button', { name: 'Preview (PDF)' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('link', { name: 'Download PDF' })).toHaveAttribute('href', '/api/sessions/7/export/resume?format=pdf')

    await user.click(screen.getByRole('button', { name: 'Edit text' }))
    expect(screen.queryByTitle('Tailored resume (PDF preview)')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Resume text'), ' and Go')
    await user.click(screen.getByRole('button', { name: 'Save & preview' }))

    expect(await screen.findByTitle('Tailored resume (PDF preview)')).toHaveAttribute(
      'src', '/api/sessions/7/export/resume?format=pdf&inline=true&v=1#view=FitH&navpanes=0') // cache-busted after save
    expect(f.calls.find((c) => c.method === 'PATCH')!.body).toEqual({ tailored_resume: tailored + ' and Go' })
  })

  it('saves letter edits and status with PATCH', async () => {
    loggedIn()
    const s = session({ fit_result: FIT, tailored_resume: 'x'.repeat(60), cover_letter: 'Dear team, ' + 'y'.repeat(60),
      current_step: 'done' })
    f.on('GET', '/sessions/7', { body: s })
    f.on('PATCH', '/sessions/7', (init) => ({ body: { ...s, ...JSON.parse(String(init.body)) } }))
    renderApp('/sessions/7')
    const user = userEvent.setup()
    const save = await screen.findByRole('button', { name: 'Save' })
    expect(save).toBeDisabled() // nothing changed yet
    await user.selectOptions(screen.getByLabelText('Application status'), 'applied')
    await user.click(save)
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(f.calls.find((c) => c.method === 'PATCH')!.body).toMatchObject({ status: 'applied' })
    expect(screen.getByRole('link', { name: 'Download letter (DOCX)' })).toHaveAttribute('href', '/api/sessions/7/export/cover-letter')
  })

  it('explains archived or missing sessions', async () => {
    loggedIn()
    f.on('GET', '/sessions/99', { status: 404, body: { detail: 'Session not found' } })
    renderApp('/sessions/99')
    expect(await screen.findByText(/older than 3 days and archived/)).toBeInTheDocument()
  })
})

// ---------- new job ----------

describe('new job', () => {
  it('falls back to paste mode when a link cannot be fetched', async () => {
    loggedIn()
    f.on('GET', '/resume', { body: { id: 1 } })
    f.on('POST', '/sessions', { status: 422, body: { detail: "www.linkedin.com doesn't allow automated access. Please copy and paste the job description." } })
    renderApp('/new')
    const user = userEvent.setup()
    await user.click(await screen.findByLabelText('Use a link'))
    await user.type(screen.getByLabelText('Job link'), 'https://www.linkedin.com/jobs/view/1')
    await user.click(screen.getByRole('button', { name: 'Analyse job' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('linkedin.com')
    expect(screen.getByLabelText('Job description')).toBeInTheDocument()
  })

  it('warns when there is no resume yet', async () => {
    loggedIn()
    f.on('GET', '/resume', { status: 404, body: { detail: 'No resume uploaded yet' } })
    renderApp('/new')
    expect(await screen.findByRole('link', { name: /Upload one first/ })).toHaveAttribute('href', '/profile')
  })
})

// ---------- small pieces ----------

describe('ErrorAlert', () => {
  it('counts down Retry-After', () => {
    vi.useFakeTimers()
    render(<ErrorAlert error={new ApiError(503, 'AI is busy, try again in 3 seconds', 3)} />)
    expect(screen.getByRole('alert')).toHaveTextContent('You can retry in 3s.')
    act(() => vi.advanceTimersByTime(3100))
    expect(screen.getByRole('alert')).toHaveTextContent('You can retry now.')
  })

  it('renders nothing without an error', () => {
    const { container } = render(<ErrorAlert error={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('timeLeft', () => {
  const now = Date.parse('2026-09-29T10:00:00Z')
  it.each([
    ['2026-10-01T13:00:00Z', '2d 3h left'],
    ['2026-09-29T15:30:00Z', '5h left'],
    ['2026-09-29T10:20:00Z', '20m left'],
    ['2026-09-29T09:00:00Z', 'archiving soon'],
  ])('%s -> %s', (iso, expected) => {
    expect(timeLeft(iso, now)).toBe(expected)
  })
})

describe('resume upload', () => {
  it('uploads a file and shows the parsed text', async () => {
    loggedIn()
    f.on('GET', '/resume', { status: 404, body: { detail: 'No resume uploaded yet' } })
    f.on('POST', '/resume', { status: 201, body: { id: 3, filename: 'cv.pdf', raw_text: 'Alice Example — Backend Engineer',
      uploaded_at: '2026-09-29T10:00:00+00:00', chunks: 2 } })
    renderApp('/profile')
    const user = userEvent.setup()
    await user.upload(await screen.findByLabelText('Resume file'), new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: 'Upload resume' }))
    await waitFor(() => expect(screen.getByText('Alice Example — Backend Engineer')).toBeInTheDocument())
    expect(screen.getByText(/Resume saved/)).toBeInTheDocument()
  })
})
