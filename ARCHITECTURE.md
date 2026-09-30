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
│  Login / Forgot pwd     │                     │  Agents ── code-driven steps (hand-built)     │
│  Profile (upload CV)    │                     │    ├─► RAG (sentence-transformers             │
│  New Job wizard         │                     │    │     + ChromaDB, per user)                │
│  History (3 days)       │                     │    ├─► KeyPool ──► Groq: writes (key A, key B,│
│  Agent reasoning trace  │                     │    │                + fallback models)        │
│  PDF preview            │                     │    └─► TypeSafe Jev (optional): judges        │
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

Two kinds of model, each used for what it's good at:
- **Groq LLMs write**: the parsed job, the tailored resume, the cover letter.
- **TypeSafe Jev judges** (optional, `TYPESAFE_API_KEY`). It never writes text; it answers typed questions
  (`choice`, `score`, yes/no `noul`) about a state in one parallel call (~0.1-0.5s, billed on input only).
  Every Jev step has a Groq fallback: without a key, or when Jev fails (401/422/429/529/network), the step
  uses Groq instead of failing.
- **Code decides** everything that can be computed: retrieval, grounding, keyword coverage, invented
  numbers/skills, years of experience, the score, and when to stop.

| Step | Groq calls | Jev | What code does |
|------|-----------|-----|----------------|
| Job Parser | 1 (JSON mode) | — | shortens long postings, validates the output |
| Fit Scorer | 0 with Jev; else 1 (+1 only for skipped requirements) | 1 call: per requirement a match `choice` + a `noul` per candidate line | retrieves evidence lines, verifies quotes, years check, score, summary/strengths/advice |
| Resume Tailor | 1 draft + at most 2 fixes | per check: a `noul` per changed line ("supported?") + a recruiter screen (6 `noul`s) | keyword guidance with where each keyword is backed, coverage + claim checks, lost-metric check, recruiter screen, keeps a fix only if it's strictly better, adds true-but-missing keywords itself |
| Cover Letter | 1 per round (max 3) | Critic: 1 `score` + 6 `noul` checks per round | hard lint (invented numbers, placeholders, length) vetoes approval |

Measured live on the same real job and resume with Groq only: **~67k → ~15k tokens** for Fit + Tailor + Letter.
E2E timings: fit 1.8s, tailor 2.3s, letter 3.4s. With Jev, Fit and the Critic use no Groq tokens.
`LLM_REASONING_EFFORT=low` (gpt-oss "low", qwen3 "none") roughly halves hidden reasoning tokens (measured
165 → 71 on the same prompt, same answer).

**Why not a ReAct loop everywhere?** The first version ran agent loops for Fit, Tailor and Writer. Live traces
showed ~7 calls per step, each re-sending the whole resume, while the "decisions" (which tool next) were ones
code already knew: the checks are deterministic and the resume fits in the prompt. The hand-written ReAct loop
(`agents/base.py`: tool calls, text-form tool calls, corrective retries, budget trimming) is kept and tested
for steps that genuinely need open-ended tool use.

**Fit scoring is done by code, not by a model.**
1. Code retrieves candidate evidence per requirement: per-user RAG hits, lines mentioning the requirement's
   keywords, and dated role lines for years requirements. Each line gets an id (L1, L2…).
2. Jev (or one Groq call) labels each requirement strong / partial / missing. With Jev, every
   (requirement, line) pair is also a yes/no question in the same call; code picks the most likely line as
   evidence and the line answers can only *lower* a label (strong needs a line ≥0.6, partial ≥0.3). Headings
   and bare company names are never evidence. Evidence is always a verbatim resume line.
3. **Years guard:** for "N+ years of X", code adds up the dated roles whose bullets mention X (overlaps merged);
   below N, "strong" becomes "partial" with a note. Neither LLMs nor Jev are reliable at date arithmetic.
4. Score = Σ weight × credit / Σ weight (must = 2, nice = 1; strong = 1, partial = 0.5, missing = 0).
   ≥75 strong fit, ≥50 moderate, else weak. Summary, strengths and advice are written by code from the
   verified labels; free text citing numbers absent from the resume and job is dropped.

Job text, resume text and tool results are untrusted data in every prompt (prompt-injection defence). Jev
doesn't treat its state as hostile, so it never decides alone: code thresholds and combines its answers.

**Resume Tailor.** `keyword_coverage` and `verify_claims` are plain Python (`agents/ats.py`):
- Coverage splits job keywords into *covered*, *missing but supported* (in the original → add) and
  *missing, unsupported* (→ never add). Handles `CI/CD`, `C++`, `Node.js`, plurals and aliases (`k8s`).
- The claim check flags numbers (digits and number words) and job skills the original resume lacks.
- Jev adds a semantic check regexes can't do: invented responsibilities ("owned incident response").
- **Writing rules** in the prompt: action verb + what + technology + impact bullets, the job's exact terms where
  true, a specific summary (target role, top matching skills, one result), skills grouped by category with the
  most relevant first, same depth and length.
- **Quantified results are protected** (`ats.metrics_in` / `dropped_metrics`): every impact number in the
  source (40%, $2M, 1M+, 10,000; not years or phone numbers) must survive. Lost ones go into the fix round and
  the report.
- **Recruiter screen** (`agents/recruiter.py`, no Groq tokens). Code: top job skills in the first third,
  measurable impact (no fewer quantified bullets than the original), length, stuffing (whole-word counts vs. the
  original). Jev (optional, one call): relevant within 15 seconds, specific summary, natural keywords, clear
  current role, concise bullets, clean formatting. Specific questions on purpose: a vague "would it be
  rejected?" scored ~0.8 for almost any resume. Failed checks feed the fix round and the report.
- **Keyword placement** (`ats.keyword_places`): the prompt says where each supported keyword is backed (Skills,
  Summary, "Experience: <role>"); the report lists keywords added and the section they landed in.
- Up to 2 fix rounds; a fix is kept only if it's strictly better (weighted: unsupported claim 3, lost metric 2,
  missing keyword or failed recruiter check 1). Keywords the candidate has that the model still leaves out are added to the skills section by code.
- **The candidate's notes are facts.** Skills they state ("I used MongoDB in side projects") move from
  "never add" to "may add", and every check (regex and Jev) accepts them. Jev gets the resume, notes and
  current version as separate state fields.
- **Re-tailor, repeatedly.** `POST /sessions/{id}/tailor` with `current_resume` revises the current (possibly
  hand-edited) version with the user's requests (format, a section, the summary, keywords) instead of starting
  over. Notes from every round are kept in the report and stay trusted; the current version counts as a source,
  so the checks judge only what the revision adds.
- The report (coverage before/after, remaining issues, changes, keywords added and where, lost metrics, recruiter
  checks, revision, notes) is recomputed on the final text.

**Writer ⇄ Critic.** Each round: one Writer call → code lint (hard: invented numbers, placeholders, length;
soft: clichés, unbacked skills) → Critic. The Jev Critic's quality `score` maps to 2-10 and its yes/no checks
(specific evidence, addresses requirements, names the role, grounded, implies missing skills, clichés) map to
fixed feedback for the next round; without Jev a Groq hiring-manager review does the same job. Approved only if
the Critic approves *and* there are no hard issues; max 3 rounds; the last draft is always returned.

**Robustness.**
- If a final answer is empty, in the wrong format or contains tool-call markup, the agent gets up to 2
  corrective retries with tools off. If it still fails, the step errors and nothing is saved.
- Some models (qwen3) sometimes *write* tool calls as text (`<tool_call><function=…>` or Hermes JSON) instead
  of using the API. These are parsed and executed like real calls and marked `text_call` in the trace.
  Tool-call text is never accepted as a resume or letter.
- Old tool-call arguments (full drafts) are trimmed from the agent history to save tokens.
- `search_my_experience` returns each resume excerpt once per agent run; repeats say "same excerpt as shown
  earlier". Before this, repeated hits pushed the Fit Scorer past 8K tokens on a normal two-page resume.
- Before each call, `max_tokens` is clamped so prompt + output fits `LLM_MAX_REQUEST_TOKENS`. Groq's free tier
  counts both against its 8K tokens/minute limit. A 413 shrinks `max_tokens` by the overflow and retries.
- If the history still doesn't fit, the oldest tool results are trimmed (the newest are kept whole).

**Long inputs are fitted automatically** (`agents/shorten.py`), never rejected as "too long":
1. Clean whitespace and drop duplicate lines (common on scraped pages).
2. Drop boilerplate: EEO/legal text, cookie and privacy banners, "share this job", footers.
3. Keep the most useful lines in their original order: first lines (name, title), headings, dated role lines,
   requirement-style lines and lines mentioning job keywords.

Each agent computes its own character budget from `LLM_MAX_REQUEST_TOKENS`. The Tailor allows for about 2.75
copies of the resume per request (prompt, draft in a tool call, output). When the resume was shortened, the
report says so. Grounding, claim and coverage checks always use the full original text.

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
- **5xx / network** → 5s cooldown, try the next slot.
- **400 `json_validate_failed` / `tool_use_failed`** (the model produced bad JSON or an unwanted tool call) → 1s
  cooldown, try the next slot. Any other **400** → raised immediately (request bug).
- Pool is a process-wide singleton guarded by a lock, so **no request can use a cooling slot**.
- All slots cooling → wait up to `LLM_MAX_WAIT_SECONDS` (20s); otherwise HTTP 503 "try again in X seconds".
  The budget is time-based rather than a retry count, because per-minute limits produce bursts of short 429s.
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

## 6b. Documents (DOCX + PDF)

- Both are built from the saved plain text with the same rules: ALL-CAPS lines are headings and `- ` lines are
  bullets. The PDF (`services/pdf_export.py`, fpdf2) also treats the first line as the name, lines before the
  first heading as contact details, and lines with a year as bold role/date lines.
- The PDF uses real, selectable text so ATS parsers can read it. The built-in fonts are Latin-1 only, so
  typography is mapped (dashes → `-`, curly quotes → straight, → → `->`, ₹ → `Rs.`), Unicode spaces become
  spaces, invisible characters are dropped, and bullets are drawn as shapes. Non-Latin scripts show as `?`.
- `inline=true` is for the in-app preview. It returns `Content-Disposition: inline` with
  `X-Frame-Options: SAMEORIGIN` and `frame-ancestors 'self'`, so only our own page can embed it; downloads keep
  the site default of never being framed. All exports send `Cache-Control: private, no-store`.

## 7. API

| Area | Endpoints |
|------|-----------|
| Auth | `POST /auth/signup`, `/auth/verify-otp`, `/auth/resend-otp`, `/auth/login`, `/auth/logout`, `/auth/forgot-password`, `/auth/reset-password`, `GET /auth/me` |
| Resume | `POST /resume`, `GET /resume` |
| Sessions | `POST /sessions` (text or URL → parse), `POST /sessions/{id}/fit`, `/tailor` (`instructions`; plus `current_resume` to revise the current version), `/cover-letter`, `PATCH /sessions/{id}` (edits + status), `GET /sessions` (history, last 3 days), `GET /sessions/{id}`, `GET /sessions/{id}/export/{resume\|cover-letter}?format=docx\|pdf[&inline=true]` |
| Ops | `GET /admin/llm-status` (only emails in `ADMIN_EMAILS`) |

## 8. Folder structure

```
job-copilot/
├── README.md  ARCHITECTURE.md  CONTRIBUTING.md  SECURITY.md  LICENSE (MIT)
├── docs/          USER_GUIDE.md (using the app), FORKING.md (run your own copy)
├── .github/       workflows/ci.yml (offline tests on every push/PR), issue + PR templates
├── backend/
│   ├── app/
│   │   ├── main.py  config.py  database.py  models.py  schemas.py
│   │   ├── auth/        security.py (bcrypt, JWT), otp.py, deps.py, rate_limit.py
│   │   ├── routers/     auth.py, resume.py, sessions.py, admin.py
│   │   ├── agents/      base.py (ReAct loop), tools.py, ats.py (coverage + claim checks), job_parser.py,
│   │   │                fit_scorer.py, resume_tailor.py, recruiter.py (recruiter screen), cover_writer.py,
│   │   │                cover_critic.py
│   │   ├── llm/         key_pool.py, groq_client.py, jev_client.py (TypeSafe Jev, optional)
│   │   ├── rag/         embeddings.py, store.py (chunking + per-user ChromaDB index)
│   │   ├── agents/…     + shorten.py (fit long inputs to the token budget)
│   │   ├── server.py    production site: built frontend at /, API at /api, security headers
│   │   └── services/    email.py, url_fetcher.py, file_parser.py, archive.py, docx_export.py, pdf_export.py
│   ├── tests/
│   ├── requirements.txt   .env.example
└── frontend/
    ├── vite.config.ts        # dev proxy: /api/* → FastAPI (same origin, so the httpOnly cookie just works)
    └── src/
        ├── api/              client.ts (typed fetch, ApiError with retryAfter), types.ts
        ├── context/          AuthProvider (asks /auth/me on load; the cookie itself is unreadable by JS)
        ├── components/       Layout + route guards, ui (ErrorAlert countdown, Working timer), Reports, AgentTrace
        ├── pages/            Login/Signup/Verify/Forgot/Reset, History, New job, Resume, Session wizard, AI status
        ├── components/…      + PdfPreview (browser PDF viewer in an iframe)
        └── test/             Vitest + Testing Library (fetch mocked)
    e2e/                      Playwright: serve.sh (prod build + throwaway DB), journey.spec.ts
```

### Frontend flow

- **Session wizard** (`/sessions/:id`): tabs 1 Job → 2 Fit → 3 Resume → 4 Cover letter. A tab unlocks only
  after its agent has run. Each tab shows the agent output in editable fields plus its report and a
  collapsible "How the agent got here" trace; the user's edits are sent with the approval that starts
  the next agent.
- The Resume tab is a two-column workspace on wide screens: the document (PDF preview by default, "Edit text"
  toggle) and "Ask for changes" on the left, a sticky sidebar with the checks (coverage meter, keywords, recruiter
  checklist, changes) on the right, and a "Next: cover letter" section below.
  "Save & preview" PATCHes the text, then reloads the PDF (a cache-busting `v` parameter).
- **Ask for changes** on the Resume tab sends the current text (including unsaved edits) plus the request to
  `/tailor` as `current_resume`; the result replaces the text, the preview reloads and the box clears for the
  next request. The heading shows the revision count. It can be repeated any number of times.
- Long agent runs show an elapsed-seconds timer. A 503 "AI is busy" shows a live retry countdown.
- A job link the backend can't fetch (LinkedIn, JS-only pages) switches the form to paste mode.
- Visuals: a stepper with numbered circles, card shadows, and dark mode via `prefers-color-scheme`. No inline
  styles (the coverage meter is a native `<progress>`), so the strict CSP holds.
- Accessibility: labelled inputs with hints, `role="alert"`/`status` live regions, tab semantics,
  skip link, visible focus, reduced-motion support.

## 9. Tech stack

| Part | Choice |
|------|--------|
| Frontend | React, Vite, TypeScript, React Router |
| Backend | FastAPI, Pydantic, SQLAlchemy 2, SQLite |
| Auth | `bcrypt`, `PyJWT`, httpOnly cookie |
| Email | `smtplib` (Gmail SMTP) / console mode |
| LLM | Groq SDK (writing); TypeSafe Jev over HTTP with httpx (judgments, optional) |
| RAG | sentence-transformers (`all-MiniLM-L6-v2`), ChromaDB |
| Files | pypdf (read), python-docx (DOCX), fpdf2 (PDF) |
| Tests | pytest + FastAPI TestClient (fake LLM/embedder), Vitest + Testing Library, Playwright (full Chromium) |

## 9b. Production serving

`uvicorn app.server:site` serves the built React app and the API on one origin: `/api` → the API app,
`/assets` → hashed files cached for a year, and everything else → `index.html` for client routing. Same origin
means the httpOnly cookie needs no CORS. Headers: a strict CSP (no inline scripts or styles,
`frame-ancestors 'none'`, `object-src 'none'`), `X-Frame-Options: DENY`, `nosniff`, a referrer policy, a
permissions policy and HSTS in production. Startup refuses a `JWT_SECRET` under 32 characters and
`EMAIL_MODE=console` in production. Run one process (cooldowns and rate limits are in memory).

## 9c. Testing

- **Backend (262):** offline, with a scripted `FakeLLM`, a `FakeJev` and a fake embedder. Covers the auth flows,
  OTP limits, RAG isolation between users, KeyPool failover, agent loops (text tool calls, retries, step limits),
  scoring and grounding, Jev client contract and fallbacks, ATS/claim checks, notes as facts, repeated
  re-tailoring, shortening, URL fetcher SSRF, archive and exports.
- **Frontend (30):** component and flow tests with mocked fetch.
- **E2E (4):** real Chromium against the production build and real Groq. Covers the full journey, cookie and
  security headers, password reset and mobile layout. Fails on any JS error, CSP violation or unexpected failed
  request. It uses full Chromium because the default headless shell has no PDF viewer.
- **CI** (GitHub Actions) runs the backend tests and the frontend typecheck, lint, tests and build on every
  push and pull request, with no secrets. E2E needs real Groq keys, so it runs locally only.

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

All seven days are done. Post-launch fixes from real use: automatic input fitting, PDF preview/export,
text-form tool calls, Unicode handling in PDFs.
