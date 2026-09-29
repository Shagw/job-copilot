import importlib

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import EMAIL, PASSWORD


@pytest.fixture
def site(tmp_path, outbox):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Job Copilot</title><div id=root></div>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('app')")
    (tmp_path.parent / "secret.txt").write_text("do not serve")
    settings = get_settings()
    old = settings.frontend_dist
    settings.frontend_dist = str(tmp_path)
    try:
        import app.server as server

        server = importlib.reload(server)
        with TestClient(server.site) as c:
            yield c
    finally:
        settings.frontend_dist = old


def test_spa_routes_return_index(site):
    for path in ["/", "/login", "/sessions/7", "/profile"]:
        r = site.get(path)
        assert r.status_code == 200 and "Job Copilot" in r.text and r.headers["cache-control"] == "no-cache"


def test_assets_are_cached_long(site):
    r = site.get("/assets/index-abc123.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]


def test_security_headers(site):
    h = site.get("/").headers
    assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"


def test_api_is_served_under_api_on_the_same_origin(site, outbox):
    assert site.get("/api/health").json() == {"status": "ok"}
    site.post("/api/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    r = site.post("/api/auth/verify-otp", json={"email": EMAIL, "code": outbox[-1][1]})
    assert r.status_code == 200
    assert site.get("/api/auth/me").json()["email"] == EMAIL  # cookie works through the mount
    assert site.get("/api/sessions").json() == []


def test_unknown_api_path_is_json_404_not_index(site):
    r = site.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt"])
def test_no_path_traversal(site, path):
    r = site.get(path)
    assert "do not serve" not in r.text


def test_missing_build_gives_clear_error(tmp_path):
    settings = get_settings()
    old = settings.frontend_dist
    settings.frontend_dist = str(tmp_path / "missing")
    try:
        import app.server as server

        with pytest.raises(RuntimeError, match="npm run build"):
            importlib.reload(server)
    finally:
        settings.frontend_dist = old
