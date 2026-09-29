"""Production entrypoint: one origin for the React app and the API.

    /api/*     -> the FastAPI app from app.main (same routes as in development)
    /assets/*  -> hashed JS/CSS from `npm run build` (cached for a year)
    anything else -> index.html, so React Router can handle deep links like /sessions/7

Same origin means the httpOnly SameSite=Lax cookie works without CORS.
Run:  cd frontend && npm run build
      cd backend && uvicorn app.server:site --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.main import app as api
from app.main import startup

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # Vite's production build has no inline scripts or styles, so a strict policy works.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
    ),
}


def _dist_dir() -> Path:
    path = Path(get_settings().frontend_dist).resolve()
    if not (path / "index.html").is_file():
        raise RuntimeError(f"Frontend build not found at {path}. Run `npm run build` in frontend/ first.")
    return path


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Mounted sub-apps don't get lifespan events, so run the API's startup here.
    startup()
    yield


def create_site() -> FastAPI:
    dist = _dist_dir()
    index = dist / "index.html"
    site = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @site.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if get_settings().is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        path = request.url.path
        if path.startswith("/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # file names are hashed
        elif not path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"  # always pick up a new deploy's index.html
        return response

    site.mount("/api", api)
    if (dist / "assets").is_dir():
        site.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @site.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        # Never read arbitrary files from disk: everything that isn't an asset gets index.html.
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(index, media_type="text/html")

    return site


site = create_site()
