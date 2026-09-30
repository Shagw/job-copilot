# Fork guide

A step-by-step guide to running your own copy, from forking to a public URL. It takes about 30 minutes, and
every service used has a free tier.

## 1. Fork and clone

Click **Fork** on GitHub, then:

```bash
git clone https://github.com/<you>/job-copilot.git
cd job-copilot
```

You need Python 3.10+ and Node 20+ (developed on Python 3.13 and Node 24).

## 2. Get your keys

| Key | Required? | How |
|-----|-----------|-----|
| **Groq API key** | Yes | Sign in at [console.groq.com](https://console.groq.com) → API Keys → Create. Limits are per *account*, so a key from a second account doubles capacity. |
| **Gmail App Password** | For real email (production) | Google Account → Security → turn on 2-Step Verification → **App passwords** → create one. Or use any SMTP provider. |
| **TypeSafe Jev key** | Optional | [console.typesafe.ai](https://console.typesafe.ai) → Settings → Keys. Makes Fit scoring and the letter critic faster and saves Groq tokens. |

## 3. Configure

```bash
cd backend
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET
```

Edit `backend/.env`:

```ini
JWT_SECRET=<the value you just generated>
GROQ_API_KEYS=<key from account A>,<key from account B>
EMAIL_MODE=console          # OTP codes appear in the backend terminal; switch to smtp later
ADMIN_EMAILS=<your email>   # can open the AI status page at /admin
TYPESAFE_API_KEY=           # optional
```

`.env` is in `.gitignore`. Never commit it. Every setting is described in `.env.example` and in the
[README configuration table](../README.md#configuration-backendenv).

## 4. Run locally

```bash
# terminal 1
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload        # API docs: http://localhost:8000/docs

# terminal 2
cd frontend
npm install
npm run dev                                    # app: http://localhost:5173
```

Sign up, copy the 6-digit code from terminal 1 (`[DEV EMAIL] ... code=123456`), upload a resume and paste a job.
The first upload downloads the ~90 MB embedding model once.

Check that everything works:

```bash
cd backend && .venv/bin/python -m pytest -q                     # offline, no keys needed
cd frontend && npm run typecheck && npm run lint && npm test
cd frontend && npm run e2e                                      # optional: real browser + your Groq keys
```

## 5. Deploy for other users

The app runs as **one process** that serves both the React app and the API from the same origin.

```bash
cd frontend && npm ci && npm run build
cd ../backend && ENVIRONMENT=production .venv/bin/uvicorn app.server:site --host 0.0.0.0 --port 8000
```

Production settings in `backend/.env`:

```ini
ENVIRONMENT=production
EMAIL_MODE=smtp
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<gmail app password>
EMAIL_FROM=you@gmail.com
FRONTEND_ORIGIN=https://your-domain.example
```

The server refuses to start in production if `JWT_SECRET` is shorter than 32 characters or email is set to
`console`.

Where to host it: any host that runs a long-lived Python process with a persistent disk. For example a small VM
(with Caddy or nginx for HTTPS), or Railway, Render or Fly.io with a volume mounted at `backend/storage/`
(SQLite database, uploads and the vector index). Requirements:

- **HTTPS**: the session cookie is `Secure` in production.
- **Exactly one instance**: key cooldowns and rate limits are kept in memory.
- **Backups of `backend/storage/`**: all user data lives there.

## 6. Multiple users

Multiple users work out of the box:

- Anyone with an email address can sign up. Accounts are verified with an email code, and passwords are hashed
  with bcrypt.
- Each user sees only their own resume, jobs, history and search index. Other users' ids return 404.
- History is shown for 3 days (`HISTORY_VISIBLE_DAYS`), then archived, never deleted.
- Each user can run up to 30 AI steps per 10 minutes, which protects your keys.
- Everyone shares **your** API keys. On the free Groq tier that's a handful of people at once. For more, add
  keys from more Groq accounts, use a paid tier, or enable Jev.
- Admins (`ADMIN_EMAILS`) can see which keys and models are cooling down at `/admin`.

Resumes are personal data. Read [SECURITY.md](../SECURITY.md), tell your users that resume text is sent to Groq
(and TypeSafe, if enabled), and decide how long you keep archived data.

## 7. Customize

| What | Where |
|------|-------|
| Models and their fallback order | `GROQ_MODELS` in `.env` |
| Prompts and agent behavior | `backend/app/agents/` (`job_parser.py`, `fit_scorer.py`, `resume_tailor.py`, `cover_writer.py`, `cover_critic.py`) |
| Fake-skill and invented-number checks | `backend/app/agents/ats.py` |
| Fit score weights and thresholds | `backend/app/agents/fit_scorer.py` |
| PDF layout | `backend/app/services/pdf_export.py` |
| UI text and styling | `frontend/src/pages/`, `frontend/src/components/`, `frontend/src/index.css` |
| History window, upload size, OTP limits | the "Optional tuning" block in `.env.example` |

[ARCHITECTURE.md](../ARCHITECTURE.md) explains how the pieces fit together before you change them.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| No OTP email arrives | With `EMAIL_MODE=console` the code is printed in the backend terminal. With SMTP, check the App Password (not your normal password) and `EMAIL_FROM`. |
| "AI is busy, try again in N seconds" | All Groq keys/models are rate-limited. Wait, add a key from another Groq account, or enable Jev. |
| Server won't start in production | `JWT_SECRET` is under 32 characters or `EMAIL_MODE` isn't `smtp`. |
| A job link can't be fetched | The page renders with JavaScript or blocks bots. Paste the job text instead. |
| First resume upload is slow | The embedding model is downloading once (~90 MB). |
| Logged out immediately after login in production | You're on plain HTTP; the cookie is `Secure`. Use HTTPS. |
| Jev steps fall back to Groq | `TYPESAFE_API_KEY` is missing or invalid (the backend log says why). The app still works. |

Still stuck? Open an issue using the bug template. Remove keys, emails and resume text from any logs you paste.
