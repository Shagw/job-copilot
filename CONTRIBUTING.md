# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Setup

Follow the [Quick start](README.md#quick-start). You need a free Groq key to use the app, but **not** to run the
tests: the backend and frontend test suites run offline with a fake LLM, a fake Jev and a fake embedder.

## Before opening a pull request

```bash
cd backend && .venv/bin/python -m pytest -q          # backend tests (offline)
cd frontend && npm run typecheck && npm run lint && npm test
cd frontend && npm run e2e                           # optional: full browser journey, uses your real Groq keys
```

CI runs the first two on every pull request.

## Ground rules

- **Human in the loop.** The app never submits applications, and never scrapes LinkedIn/Indeed or other sites
  whose terms forbid it. PRs that add auto-apply or scraping won't be merged.
- **Don't invent facts about the candidate.** Anything the tailor or writer adds must be backed by the resume or
  the candidate's own notes, and checked in code (`agents/ats.py`) or by Jev. Add a test when you change a check.
- **Every Jev step needs a Groq fallback**, and every LLM step needs a clear error when all keys are busy.
- **Users are isolated.** Queries and RAG searches are always scoped to the current user; add a test for any new
  endpoint that reads user data.
- Match the existing style: plain FastAPI + SQLAlchemy, no agent frameworks, React function components, no new
  UI library. Pin exact dependency versions.
- Never commit `.env`, real keys, resumes or the `backend/storage/` folder.

## Pull requests

Keep them focused. Describe what changed, how you tested it, and include before/after token counts or timings if
you touched an agent. Design notes for larger changes go in [ARCHITECTURE.md](ARCHITECTURE.md).

By contributing you agree your work is released under the [MIT License](LICENSE).
