#!/bin/sh
# Starts the app exactly as in production (one origin: built React app + /api) with throwaway
# storage, for the Playwright end-to-end test. Uses the real Groq keys from backend/.env.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
RUN="$HERE/../e2e-artifacts/run"
rm -rf "$RUN" && mkdir -p "$RUN"

cd "$HERE/.." && npm run -s build > "$RUN/build.log" 2>&1

cd "$HERE/../../backend"
export DATABASE_URL="sqlite:///$RUN/e2e.db"
export CHROMA_PATH="$RUN/chroma"
export EMAIL_MODE=console          # the test reads OTP codes from the server log
export ADMIN_EMAILS=e2e-admin@example.com
exec .venv/bin/uvicorn app.server:site --port "${E2E_PORT:-8790}" > "$RUN/server.log" 2>&1
