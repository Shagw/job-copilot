# Job Application Copilot — Architecture

A multi-user, **human-in-the-loop** AI copilot for job applications. Users sign
up, verify their email with an OTP, upload a master resume, and for each job
posting AI agents parse the job, score fit, tailor the resume, and write a
cover letter. The user reviews and approves every step. Nothing is
auto-submitted.

---

## 1. System overview

```
┌─────────────────────────┐                     ┌──────────────────────────────────────────────┐
│  FRONTEND               │   JSON over HTTPS   │  BACKEND (FastAPI, single process)           │
│  React + Vite + TS      │ ◄─────────────────► │                                              │
│                         │  JWT httpOnly cookie│  Auth ── bcrypt, JWT, OTP ──► Gmail SMTP      │
│  Signup / Verify OTP    │                     │                                              │
│  Login / Forgot pwd     │                     │  Agents ── ReAct loop (hand-built)            │
│  Profile (upload CV)    │                     │    ├─► Tools ──► RAG (sentence-transformers   │
│  New Job wizard         │                     │    │              + ChromaDB, per user)       │
│  History (3 days)       │                     │    └─► KeyPool ──► Groq (key A, key B,        │
│  Agent reasoning trace  │                     │                     + fallback models)        │
└─────────────────────────┘                     │  SQLite (SQLAlchemy)                          │
                                                └──────────────────────────────────────────────┘
```

Per-job flow (human approves between every step):

```
Paste link/text → [Job Parser] → review → [Fit Scorer agent] → review
               → [Resume Tailor agent] → review/edit → [Writer ⇄ Critic] → review/edit → saved to history
```

## 2. Authentication

Password login + email OTP verification.

| Flow | Steps |
|------|-------|
| Signup | email + password → bcrypt hash → user saved with `is_verified=false` → 6-digit OTP emailed |
| Verify | user enters OTP → `is_verified=true` → JWT cookie set (logged in) |
| Login | email + password → bcrypt check → must be verified → JWT cookie |
| Logout | cookie cleared |
| Forgot password | request OTP (generic response, never reveals whether email exists) → OTP + new password |

- Password reset bumps `users.token_version`; every JWT carries the version it was issued with, so all
  existing sessions (every device) are logged out. Reset also marks the email verified (inbox proven).
- Production refuses to start with `EMAIL_MODE=console` (it would log OTP codes).

- JWT (HS256, 1-day expiry) stored in an **httpOnly, SameSite=Lax** cookie; `Secure` in production.
- OTP: generated with `secrets`, stored **hashed**, 10-minute expiry, single use,
  locked after 5 wrong attempts, 60s resend cooldown, max 5 sends/hour per user.
- Login and OTP endpoints are rate-limited per IP (in-memory limiter).
- Email: `EMAIL_MODE=console` prints OTPs to the backend log (dev);
  `EMAIL_MODE=smtp` sends via Gmail SMTP (`smtp.gmail.com:587`, STARTTLS, App Password).

## 3. AI agents

| Step | Type | Tools |
|------|------|-------|
| Job Parser | Single LLM call, JSON output (an agent would be over-engineering) | — |
| Fit Scorer | Agent: gathers evidence per requirement before scoring | `search_my_experience` |
| Resume Tailor | ReAct agent: loops until ≥80% must-have keyword coverage and all claims grounded | `search_my_experience`, `check_ats_coverage`, `verify_claims` |
| Cover Letter | Writer ⇄ Critic, max 3 rounds | `search_my_experience` |

- Hand-written agent loop using Groq tool calling (no LangChain/CrewAI).
- Every agent has a max-step limit; the reasoning trace is stored and shown in the UI.
- Agents never invent experience: tools only return the user's own resume chunks.

**Fit scoring is done by code, not by the LLM.** The agent only classifies each
requirement (strong / partial / missing) with a quoted evidence snippet. Then:
1. Each evidence quote is checked against the resume text; unverified quotes are
   dropped and that requirement becomes "missing".
2. Requirements the model skipped are filled in as "missing".
3. Score = Σ weight × credit / Σ weight, with must = 2, nice = 1 and
   strong = 1, partial = 0.5, missing = 0. ≥75 strong fit, ≥50 moderate, else weak.

Job text and tool results are treated as untrusted data in every prompt (prompt-injection defence).

**Resume Tailor.** `check_ats_coverage` and `verify_claims` are plain Python (`agents/ats.py`), not LLM calls:
- Coverage splits job keywords into *covered*, *missing but supported* (in the original resume → add them)
  and *missing, unsupported* (→ never add). Matching handles `CI/CD`, `C++`, `Node.js`, plurals and aliases (`k8s`).
- Claim check flags numbers and job skills in the draft that the original resume doesn't contain.
- After the agent finishes, code re-runs both checks and stores a report (coverage before/after, remaining
  issues, list of changes) for the user to review. Nothing is auto-approved.

**Writer ⇄ Critic.** Each round: Writer agent drafts → code lint (hard: invented numbers, placeholders,
length; soft: clichés, skills the resume lacks) → LLM Critic scores 1-10. Approved only if the Critic approves
*and* there are no hard issues. Otherwise the feedback goes back to the Writer, max 3 rounds. The last draft is
always returned with its status.

**Robustness.**
- If a final answer is empty or in the wrong format, the agent gets one corrective nudge.
- Old tool-call arguments (full drafts) are trimmed from the agent history to save tokens.
- Before each call, `max_tokens` is clamped so prompt + output fits `LLM_MAX_REQUEST_TOKENS`. Groq's free tier
  counts both against its 8K tokens/minute limit. A 413 shrinks `max_tokens` by the overflow and retries.

## 4. KeyPool (Groq failover + cooldown)

Ordered **slots** = (API key, model), best model on every key first:

```
1. key A + openai/gpt-oss-120b
2. key B + openai/gpt-oss-120b
3. key A + openai/gpt-oss-20b        (fallback model)
4. key B + openai/gpt-oss-20b
5. key A + qwen/qwen3.8-27b          (second fallback)
6. key B + qwen/qwen3.8-27b
```

Models are set in `GROQ_MODELS`; the Llama 3.x models originally planned are no longer offered by Groq.

- Pick the first slot that is not cooling down.
- **429** → slot cools down for Groq's `retry-after` (or the "try again in…" hint, default 60s) → retry the same request on the next free slot.
- **401** → every slot using that key is disabled. **404** (model removed) → that model is disabled on every key.
- **5xx / network** → 5s cooldown, try the next slot. **400** → raised immediately (request bug).
- Pool is a process-wide singleton guarded by a lock, so **no request can use a cooling slot**.
- All slots cooling → wait up to 10s; otherwise HTTP 503 "try again in X seconds".
- Keys are never logged; `/admin/llm-status` shows only the last 4 characters.
- Cooldown state is in memory → run a single backend process. Multi-worker scaling would move it to Redis.
- Note: Groq limits are per organization, so key A and key B must come from different accounts to add capacity.

## 5. Job links

- Backend fetches public career pages (Greenhouse, Lever, company sites) when `POST /sessions` gets a
  `job_url` and no `job_text`. Pasted text always wins.
- Extraction prefers the page's schema.org `JobPosting` JSON-LD (used by most ATS platforms), else visible text.
- Blocked sites (LinkedIn, Indeed, Glassdoor, Naukri, …) are never fetched → 422 asking the user to paste.
- SSRF protection: http(s) only, ports 80/443, no credentials in URLs; the hostname is resolved once and
  rejected if **any** address is private/loopback/link-local/reserved; the request is then sent to that
  validated IP (Host header + TLS SNI keep the real name, so certificates are still verified), which blocks
  DNS rebinding. Redirects (max 3) are re-checked hop by hop. 10s timeout, 2 MB cap, HTML/text only.

## 6. Data model

```
users         id, email (unique), password_hash, is_verified, token_version, created_at
otp_codes     id, user_id, code_hash, purpose (verify_email | reset_password),
              expires_at, attempts, used_at, created_at
resumes       id, user_id, filename, raw_text, uploaded_at
              (chunks embedded in ChromaDB, tagged with user_id)
job_sessions  id, user_id, job_url, job_text, parsed_job (JSON), fit_result (JSON),
              tailored_resume, tailor_report (JSON), cover_letter, cover_letter_report (JSON),
              agent_trace (JSON), current_step, status, created_at, archived_at
```

- Every query is scoped by `user_id`.
- History returns only rows with `created_at` within the last 3 days.
- An archive sweep (at startup for everyone, at login and on history reads per user) sets `archived_at` on older rows.
- **Nothing is ever deleted**; there is no delete endpoint.

## 7. API

| Area | Endpoints |
|------|-----------|
| Auth | `POST /auth/signup`, `/auth/verify-otp`, `/auth/resend-otp`, `/auth/login`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password`, `GET /auth/me` |
| Resume | `POST /resume`, `GET /resume` |
| Sessions | `POST /sessions` (text or URL → parse), `POST /sessions/{id}/fit`, `/tailor`, `/cover-letter`, `PATCH /sessions/{id}` (edits + status), `GET /sessions` (history, last 3 days), `GET /sessions/{id}`, `GET /sessions/{id}/export/{resume\|cover-letter}` (DOCX) |
| Ops | `GET /admin/llm-status` (only emails in `ADMIN_EMAILS`) |

## 8. Folder structure

```
job-copilot/
├── ARCHITECTURE.md   README.md
├── backend/
│   ├── app/
│   │   ├── main.py  config.py  database.py  models.py  schemas.py
│   │   ├── auth/        security.py (bcrypt, JWT), otp.py, deps.py, rate_limit.py
│   │   ├── routers/     auth.py, resume.py, sessions.py, admin.py
│   │   ├── agents/      base.py (ReAct loop), tools.py, ats.py (coverage + claim checks), job_parser.py,
│   │   │                fit_scorer.py, resume_tailor.py, cover_writer.py, cover_critic.py
│   │   ├── llm/         key_pool.py, groq_client.py
│   │   ├── rag/         embeddings.py, store.py (chunking + per-user ChromaDB index)
│   │   └── services/    email.py, url_fetcher.py, file_parser.py, archive.py, docx_export.py
│   ├── tests/
│   ├── requirements.txt   .env.example
└── frontend/
    ├── vite.config.ts        # dev proxy: /api/* → FastAPI (same origin, so the httpOnly cookie just works)
    └── src/
        ├── api/              client.ts (typed fetch, ApiError with retryAfter), types.ts
        ├── context/          AuthProvider (asks /auth/me on load; the cookie itself is unreadable by JS)
        ├── components/       Layout + route guards, ui (ErrorAlert countdown, Working timer), Reports, AgentTrace
        ├── pages/            Login/Signup/Verify/Forgot/Reset, History, New job, Resume, Session wizard, AI status
        └── test/             Vitest + Testing Library (fetch mocked)
```

### Frontend flow

- **Session wizard** (`/sessions/:id`): tabs 1 Job → 2 Fit → 3 Resume → 4 Cover letter. A tab unlocks only
  after its agent has run. Each tab shows the agent output in editable fields plus its report and a
  collapsible "How the agent got here" trace; the user's edits are sent with the approval that starts
  the next agent.
- Long agent runs show an elapsed-seconds timer. A 503 "AI is busy" shows a live retry countdown.
- A job link the backend can't fetch (LinkedIn, JS-only pages) switches the form to paste mode.
- Accessibility: labelled inputs with hints, `role="alert"`/`status` live regions, tab semantics,
  skip link, visible focus, reduced-motion support.

## 9. Tech stack

| Part | Choice |
|------|--------|
| Frontend | React, Vite, TypeScript, React Router |
| Backend | FastAPI, Pydantic, SQLAlchemy 2, SQLite |
| Auth | `bcrypt`, `PyJWT`, httpOnly cookie |
| Email | `smtplib` (Gmail SMTP) / console mode |
| LLM | Groq SDK |
| RAG | sentence-transformers (`all-MiniLM-L6-v2`), ChromaDB |
| Files | pypdf, python-docx |
| Tests | pytest, FastAPI TestClient |

## 10. Week plan

| Day | Work |
|-----|------|
| 1 | Backend scaffold, DB models, signup/verify/login/logout, OTP (console), tests |
| 2 | Resume upload + RAG, KeyPool + Groq client, tests |
| 3 | Agent loop, tools, Job Parser, Fit Scorer |
| 4 | Resume Tailor agent, Writer + Critic |
| 5 | History + archive, URL fetcher, Gmail SMTP, forgot password |
| 6 | React frontend |
| 7 | E2E tests, polish, README with learning notes, demo |
