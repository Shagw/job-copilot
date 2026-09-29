import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'

const RUN = path.resolve(import.meta.dirname, '../e2e-artifacts/run')
const SHOTS = path.resolve(import.meta.dirname, '../e2e-artifacts/screenshots')
const PASSWORD = 'Secret123'

const RESUME = `Priya Sharma
Backend Engineer | priya@example.com

EXPERIENCE
Zeta Payments - Software Engineer (2021-2024)
- Built REST APIs in Python with FastAPI handling 3M requests per day
- Containerized services with Docker and deployed them on AWS ECS
- Designed PostgreSQL schemas and cut query latency by 35%
- Set up CI/CD pipelines using GitHub Actions
- Mentored 2 interns on testing practices

Freelance - Web Developer (2019-2021)
- Built React dashboards for small businesses

SKILLS
Python, FastAPI, PostgreSQL, Docker, AWS, React, Git`

const JOB = `Senior Backend Engineer - Northwind (Remote, India)
Requirements: 4+ years of Python; experience with FastAPI or Django; Kubernetes in production; AWS;
strong SQL/PostgreSQL; CI/CD. Nice to have: Terraform, React, Kafka.
You will design scalable APIs, own services end to end and mentor junior engineers.`

/** Latest OTP the backend "emailed" (console mode) to this address. */
async function otpFor(email: string, purpose = 'verify_email'): Promise<string> {
  const re = new RegExp(`to=${email.replace(/[.+]/g, '\\$&')} purpose=${purpose} code=(\\d{6})`, 'g')
  for (let i = 0; i < 50; i++) {
    const matches = [...readFileSync(path.join(RUN, 'server.log'), 'utf8').matchAll(re)]
    if (matches.length) return matches.at(-1)![1]
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error(`No OTP for ${email}`)
}

const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true })

/** Fail the test on JS errors, CSP violations or unexpected failed requests. */
function watchErrors(page: Page): string[] {
  const errors: string[] = []
  // Expected "errors": not logged in yet, and a brand-new user has no resume yet.
  const expected = [/\/api\/auth\/me$/, /\/api\/resume$/]
  page.on('response', (r) => {
    if (r.status() >= 400 && !expected.some((re) => re.test(new URL(r.url()).pathname)))
      errors.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`)
  })
  page.on('console', (m) => {
    if (m.type() === 'error' && !m.text().startsWith('Failed to load resource')) errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(e.message))
  return errors
}

test('full journey: signup → resume → job → fit → tailor → cover letter → history', async ({ page }) => {
  const errors = watchErrors(page)
  const email = `priya+${Date.now()}@example.com`
  const timings: Record<string, number> = {}
  const timed = async (name: string, fn: () => Promise<unknown>) => {
    const t0 = Date.now()
    await fn()
    timings[name] = Math.round((Date.now() - t0) / 100) / 10
  }

  // --- signup + OTP ---
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
  await shot(page, '01-login')
  await page.getByRole('link', { name: 'Create an account' }).click()
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByLabel('Confirm password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Verify your email' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Resend code in \d+s/ })).toBeDisabled()
  await page.getByLabel('6-digit code').fill(await otpFor(email))
  await shot(page, '02-verify')
  await page.getByRole('button', { name: 'Verify' }).click()

  // --- resume upload ---
  await expect(page.getByRole('heading', { name: 'Your master resume' })).toBeVisible()
  await page.getByLabel('Resume file').setInputFiles({ name: 'priya.txt', mimeType: 'text/plain', buffer: Buffer.from(RESUME) })
  await timed('upload', async () => {
    await page.getByRole('button', { name: 'Upload resume' }).click()
    await expect(page.getByText('Resume saved.')).toBeVisible({ timeout: 60_000 })
  })
  await expect(page.locator('pre.doc')).toContainText('Zeta Payments')
  await shot(page, '03-resume')

  // --- new job (paste) ---
  await page.getByRole('link', { name: 'Start a new job' }).click()
  await expect(page.getByRole('heading', { name: 'New job' })).toBeVisible()
  await page.getByLabel('Job description').fill(JOB)
  await timed('parse', async () => {
    await page.getByRole('button', { name: 'Analyse job' }).click()
    await expect(page.getByLabel('Job title')).toHaveValue(/Backend Engineer/, { timeout: 60_000 })
  })
  await expect(page.getByLabel('Company')).toHaveValue('Northwind')
  await expect(page.getByRole('tab', { name: '2. Fit' })).toBeDisabled()
  await shot(page, '04-job-review')

  // --- fit (human edits the requirements first) ---
  const musts = page.getByLabel('Must-have requirements')
  await musts.fill((await musts.inputValue()) + '\nGit')
  await timed('fit', async () => {
    await page.getByRole('button', { name: /Approve & score my fit/ }).click()
    await expect(page.getByLabel(/Fit score \d+ out of 100/)).toBeVisible({ timeout: 120_000 })
  })
  await expect(page.getByRole('cell', { name: 'Git', exact: true }).first()).toBeVisible() // the edit was used
  await page.getByText(/How the agent got here/).click()
  await expect(page.locator('.trace li').first()).toContainText('search_my_experience')
  await shot(page, '05-fit')

  // --- tailor ---
  await page.getByLabel('Notes for the resume tailor (optional)').fill('Emphasise API design and mentoring')
  await timed('tailor', async () => {
    await page.getByRole('button', { name: 'Tailor my resume' }).click()
    await expect(page.getByLabel('Tailored resume', { exact: true })).toBeVisible({ timeout: 180_000 })
  })
  const tailored = await page.getByLabel('Tailored resume', { exact: true }).inputValue()
  expect(tailored).toContain('Zeta Payments')
  expect(tailored).not.toMatch(/Kubernetes|Terraform|Kafka/) // never claims skills the resume lacks
  await expect(page.getByText(/Keyword coverage:/)).toBeVisible()
  await shot(page, '06-tailored')

  // --- human edit, then cover letter ---
  await page.getByLabel('Tailored resume', { exact: true }).fill(tailored + '\n\nINTERESTS\nOpen-source contributor')
  await page.getByLabel('Notes for the cover letter (optional)').fill('I like that Northwind is remote-first')
  await timed('cover_letter', async () => {
    await page.getByRole('button', { name: 'Approve & write cover letter' }).click()
    await expect(page.getByLabel('Cover letter', { exact: true })).toBeVisible({ timeout: 240_000 })
  })
  const letter = await page.getByLabel('Cover letter', { exact: true }).inputValue()
  expect(letter).toMatch(/^Dear /)
  expect(letter).toContain('Priya')
  expect(letter.split(/\s+/).length).toBeGreaterThan(150)
  await expect(page.getByText(/score \d+\/10 after \d+ rounds?/)).toBeVisible()
  await shot(page, '07-cover-letter')

  // --- save status, download ---
  await page.getByLabel('Application status').selectOption('applied')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved.')).toBeVisible()
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Download resume (DOCX)' }).click(),
  ])
  expect(download.suggestedFilename()).toMatch(/^resume-Northwind-.*\.docx$/)
  const docx = await download.path()
  expect(readFileSync(docx).subarray(0, 2).toString()).toBe('PK') // a real .docx (zip) file

  // --- history ---
  await page.getByRole('link', { name: 'History' }).click()
  await expect(page.getByRole('heading', { name: 'Your recent jobs' })).toBeVisible()
  const item = page.locator('.history-item').first()
  await expect(item).toContainText('Northwind')
  await expect(item).toContainText(/Fit \d+/)
  await expect(item).toContainText('applied')
  await expect(item).toContainText(/2d 2\dh left/)
  await shot(page, '08-history')

  // --- reload keeps the session (cookie), logout ends it ---
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Your recent jobs' })).toBeVisible()
  await page.getByRole('button', { name: 'Log out' }).click()
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
  await page.goto('/sessions/1')
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()

  console.log('Timings (s):', JSON.stringify(timings))
  expect(errors).toEqual([])
})

test('auth cookie is httpOnly and the page is protected by strict headers', async ({ page, context }) => {
  const email = `cookie+${Date.now()}@example.com`
  const response = await page.goto('/signup')
  const headers = response!.headers()
  expect(headers['content-security-policy']).toContain("default-src 'self'")
  expect(headers['x-frame-options']).toBe('DENY')

  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByLabel('Confirm password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('6-digit code').fill(await otpFor(email))
  await page.getByRole('button', { name: 'Verify' }).click()
  await expect(page.getByRole('heading', { name: 'Your master resume' })).toBeVisible()

  const cookie = (await context.cookies()).find((c) => c.name === 'access_token')!
  expect(cookie.httpOnly).toBe(true)
  expect(cookie.sameSite).toBe('Lax')
  expect(await page.evaluate(() => document.cookie)).not.toContain('access_token') // JS can't read it
})

test('forgot password resets and old password stops working', async ({ page }) => {
  const email = `reset+${Date.now()}@example.com`
  await page.goto('/signup')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD)
  await page.getByLabel('Confirm password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('6-digit code').fill(await otpFor(email))
  await page.getByRole('button', { name: 'Verify' }).click()
  await expect(page.getByRole('heading', { name: 'Your master resume' })).toBeVisible()
  await page.getByRole('button', { name: 'Log out' }).click()

  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
  await page.getByRole('link', { name: 'Forgot password?' }).click()
  await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible()
  await page.getByLabel('Email').fill(email)
  await page.getByRole('button', { name: 'Send reset code' }).click()
  await expect(page.getByRole('heading', { name: 'Choose a new password' })).toBeVisible()
  await page.getByLabel('6-digit code').fill(await otpFor(email, 'reset_password'))
  await page.getByLabel('New password', { exact: true }).fill('NewSecret456')
  await page.getByRole('button', { name: 'Update password' }).click()
  await expect(page.getByRole('heading', { name: 'Password updated' })).toBeVisible()
  await page.getByRole('button', { name: 'Go to log in' }).click()
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()

  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByRole('alert')).toHaveText('Invalid email or password')
  await page.getByLabel('Password').fill('NewSecret456')
  await page.getByRole('button', { name: 'Log in' }).click()
  await expect(page.getByRole('heading', { name: 'Your recent jobs' })).toBeVisible()
})

test('mobile layout', async ({ browser }) => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
  await shot(page, '09-mobile-login')
  await page.close()
})
