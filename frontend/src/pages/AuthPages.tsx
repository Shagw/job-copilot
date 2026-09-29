import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { ErrorAlert, Field, Notice } from '../components/ui'
import { useAuth } from '../context/auth'

const RESEND_SECONDS = 60
const PASSWORD_HINT = 'At least 8 characters, with a letter and a number.'

function useCountdown(start: number) {
  const [left, setLeft] = useState(start)
  useEffect(() => {
    if (left <= 0) return
    const t = setTimeout(() => setLeft((s) => s - 1), 1000)
    return () => clearTimeout(t)
  }, [left])
  return [left, setLeft] as const
}

function CodeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="6-digit code" hint="Check your inbox (and spam folder). Codes expire after 10 minutes.">
      {(id, hint) => (
        <input
          id={id}
          aria-describedby={hint}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          maxLength={6}
          required
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))}
        />
      )}
    </Field>
  )
}

export function LoginPage() {
  const { setUser } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setUser(await api.login(email, password)) // <GuestOnly> redirects back to where they were going
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        // Account exists but email not verified yet: send a fresh code and go to the verify page.
        await api.resendOtp(email).catch(() => undefined)
        navigate(`/verify?email=${encodeURIComponent(email)}`)
        return
      }
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="auth-card" aria-labelledby="login-title">
      <h1 id="login-title">Log in</h1>
      <form onSubmit={submit}>
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <Field label="Password">
          {(id) => (
            <input id={id} type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          )}
        </Field>
        <ErrorAlert error={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Logging in…' : 'Log in'}
        </button>
      </form>
      <p>
        <Link to="/forgot">Forgot password?</Link> · New here? <Link to="/signup">Create an account</Link>
      </p>
    </section>
  )
}

export function SignupPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (password !== confirm) {
      setError(new Error('Passwords do not match.'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.signup(email, password)
      navigate(`/verify?email=${encodeURIComponent(email)}&sent=1`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="auth-card" aria-labelledby="signup-title">
      <h1 id="signup-title">Create your account</h1>
      <form onSubmit={submit}>
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <Field label="Password" hint={PASSWORD_HINT}>
          {(id, hint) => (
            <input id={id} aria-describedby={hint} type="password" autoComplete="new-password" minLength={8}
              maxLength={72} required value={password} onChange={(e) => setPassword(e.target.value)} />
          )}
        </Field>
        <Field label="Confirm password">
          {(id) => (
            <input id={id} type="password" autoComplete="new-password" required value={confirm}
              onChange={(e) => setConfirm(e.target.value)} />
          )}
        </Field>
        <ErrorAlert error={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
      <p>
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </section>
  )
}

export function VerifyPage() {
  const { setUser } = useAuth()
  const [params] = useSearchParams()
  const [email, setEmail] = useState(params.get('email') ?? '')
  const [code, setCode] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [info, setInfo] = useState<string | null>(params.get('sent') ? 'We sent a 6-digit code to your email.' : null)
  const [busy, setBusy] = useState(false)
  const [left, setLeft] = useCountdown(RESEND_SECONDS)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setUser(await api.verifyOtp(email, code)) // <GuestOnly> sends new users to /profile
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setError(null)
    try {
      const r = await api.resendOtp(email)
      setInfo(r.message)
      setLeft(RESEND_SECONDS)
    } catch (err) {
      setError(err)
    }
  }

  return (
    <section className="auth-card" aria-labelledby="verify-title">
      <h1 id="verify-title">Verify your email</h1>
      {info && <Notice>{info}</Notice>}
      <form onSubmit={submit}>
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <CodeInput value={code} onChange={setCode} />
        <ErrorAlert error={error} />
        <button type="submit" className="primary" disabled={busy || code.length !== 6}>
          {busy ? 'Verifying…' : 'Verify'}
        </button>
        <button type="button" className="secondary" onClick={resend} disabled={left > 0 || !email}>
          {left > 0 ? `Resend code in ${left}s` : 'Resend code'}
        </button>
      </form>
    </section>
  )
}

export function ForgotPasswordPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.forgotPassword(email)
      navigate(`/reset?email=${encodeURIComponent(email)}`)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="auth-card" aria-labelledby="forgot-title">
      <h1 id="forgot-title">Reset your password</h1>
      <p>Enter your email and we'll send you a reset code.</p>
      <form onSubmit={submit}>
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <ErrorAlert error={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Sending…' : 'Send reset code'}
        </button>
      </form>
      <p>
        <Link to="/login">Back to log in</Link>
      </p>
    </section>
  )
}

export function ResetPasswordPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [email, setEmail] = useState(params.get('email') ?? '')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setDone((await api.resetPassword(email, code, password)).message)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <section className="auth-card" aria-labelledby="reset-title">
        <h1 id="reset-title">Password updated</h1>
        <Notice>{done}</Notice>
        <button type="button" className="primary" onClick={() => navigate('/login')}>
          Go to log in
        </button>
      </section>
    )
  }

  return (
    <section className="auth-card" aria-labelledby="reset-title">
      <h1 id="reset-title">Choose a new password</h1>
      <Notice>If an account exists for this email, we've sent a reset code.</Notice>
      <form onSubmit={submit}>
        <Field label="Email">
          {(id) => (
            <input id={id} type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          )}
        </Field>
        <CodeInput value={code} onChange={setCode} />
        <Field label="New password" hint={`${PASSWORD_HINT} This logs you out on every device.`}>
          {(id, hint) => (
            <input id={id} aria-describedby={hint} type="password" autoComplete="new-password" minLength={8}
              maxLength={72} required value={password} onChange={(e) => setPassword(e.target.value)} />
          )}
        </Field>
        <ErrorAlert error={error} />
        <button type="submit" className="primary" disabled={busy || code.length !== 6}>
          {busy ? 'Updating…' : 'Update password'}
        </button>
      </form>
    </section>
  )
}
