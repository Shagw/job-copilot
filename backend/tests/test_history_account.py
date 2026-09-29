import io
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from docx import Document
from sqlalchemy import select

from app.main import app
from app.models import JobSession, OtpCode
from app.routers.sessions import get_job_fetcher
from app.services.url_fetcher import FetchError, check_url, extract_job_text, fetch_job_text
from tests.conftest import EMAIL, PASSWORD, make_user
from tests.test_agents import JOB_TEXT, PARSED, start_session, use_llm  # noqa: F401

NEW_PASSWORD = "NewSecret456"


def age(db, session_id, **delta):
    row = db.get(JobSession, session_id)
    row.created_at = datetime.now(timezone.utc) - timedelta(**delta)
    db.commit()


# ---------- forgot / reset password ----------

def reset_code(client, outbox, email=EMAIL):
    client.post("/auth/forgot-password", json={"email": email})
    return [c for to, c, purpose in outbox if to == email and purpose == "reset_password"][-1]


def test_forgot_password_is_generic(client, outbox, verified_user):
    known = client.post("/auth/forgot-password", json={"email": EMAIL})
    unknown = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert [p for _, _, p in outbox].count("reset_password") == 1


def test_forgot_password_hides_email_failures(client, outbox, verified_user, monkeypatch):
    from app.routers import auth as auth_router

    def boom(*a):
        raise OSError("smtp down")

    monkeypatch.setattr(auth_router, "send_otp_email", boom)
    assert client.post("/auth/forgot-password", json={"email": EMAIL}).status_code == 200


def test_reset_password_flow(client, outbox, verified_user):
    code = reset_code(client, outbox)
    r = client.post("/auth/reset-password", json={"email": EMAIL, "code": code, "new_password": NEW_PASSWORD})
    assert r.status_code == 200
    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD}).status_code == 200
    # Single use.
    r = client.post("/auth/reset-password", json={"email": EMAIL, "code": code, "new_password": "Another789"})
    assert r.status_code == 400


def test_reset_logs_out_existing_sessions(client, outbox, verified_user):
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    old_cookie = client.cookies.get("access_token")
    code = reset_code(client, outbox)
    client.post("/auth/reset-password", json={"email": EMAIL, "code": code, "new_password": NEW_PASSWORD})
    client.cookies.set("access_token", old_cookie)
    assert client.get("/auth/me").status_code == 401


def test_reset_rejects_wrong_code_weak_password_and_unknown_email(client, outbox, verified_user):
    code = reset_code(client, outbox)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    assert client.post("/auth/reset-password",
                       json={"email": EMAIL, "code": wrong, "new_password": NEW_PASSWORD}).status_code == 400
    assert client.post("/auth/reset-password",
                       json={"email": EMAIL, "code": code, "new_password": "weakpass"}).status_code == 422
    r = client.post("/auth/reset-password",
                    json={"email": "nobody@example.com", "code": code, "new_password": NEW_PASSWORD})
    assert r.status_code == 400 and r.json()["detail"] == "Invalid or expired code"


def test_verification_and_reset_codes_are_not_interchangeable(client, outbox):
    client.post("/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    verify_code = outbox[-1][1]
    r = client.post("/auth/reset-password", json={"email": EMAIL, "code": verify_code, "new_password": NEW_PASSWORD})
    assert r.status_code == 400


def test_reset_verifies_unverified_account(client, outbox, db):
    client.post("/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    # Let the verify code's cooldown not matter: reset has its own purpose/cooldown.
    code = reset_code(client, outbox)
    client.post("/auth/reset-password", json={"email": EMAIL, "code": code, "new_password": NEW_PASSWORD})
    assert client.post("/auth/login", json={"email": EMAIL, "password": NEW_PASSWORD}).status_code == 200


# ---------- history + archive ----------

def test_history_lists_recent_sessions_newest_first(logged_in, use_llm, db):  # noqa: F811
    a = start_session(logged_in, use_llm)
    b = start_session(logged_in, use_llm)
    age(db, a["id"], hours=5)
    items = logged_in.get("/sessions").json()
    assert [i["id"] for i in items] == [b["id"], a["id"]]
    first = items[0]
    assert first["title"] == "Senior Backend Engineer" and first["company"] == "Initech"
    assert first["fit_score"] is None and first["current_step"] == "parsed" and first["status"] == "draft"
    created = datetime.fromisoformat(first["created_at"])
    assert datetime.fromisoformat(first["visible_until"]) - created == timedelta(days=3)


def test_history_hides_and_archives_old_sessions_without_deleting(logged_in, use_llm, db):  # noqa: F811
    old = start_session(logged_in, use_llm)
    new = start_session(logged_in, use_llm)
    age(db, old["id"], days=3, minutes=1)
    assert [i["id"] for i in logged_in.get("/sessions").json()] == [new["id"]]
    db.expire_all()
    row = db.get(JobSession, old["id"])
    assert row is not None and row.archived_at is not None  # archived, not deleted
    assert db.get(JobSession, new["id"]).archived_at is None


def test_login_runs_archive_sweep(client, outbox, use_llm, db):  # noqa: F811
    make_user(client, outbox, stay_logged_in=True)
    s = start_session(client, use_llm)
    age(db, s["id"], days=4)
    client.post("/auth/logout")
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    db.expire_all()
    assert db.get(JobSession, s["id"]).archived_at is not None


def test_history_is_per_user(client, outbox, use_llm):  # noqa: F811
    make_user(client, outbox, stay_logged_in=True)
    start_session(client, use_llm)
    client.post("/auth/logout")
    make_user(client, outbox, email="bob@example.com", stay_logged_in=True)
    assert client.get("/sessions").json() == []


def test_history_requires_login(client):
    assert client.get("/sessions").status_code == 401


def test_there_is_no_delete_endpoint(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    assert logged_in.delete(f"/sessions/{s['id']}").status_code == 405


# ---------- PATCH ----------

def test_patch_saves_edits_and_status(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    edited_job = dict(PARSED, title="Staff Backend Engineer")
    r = logged_in.patch(f"/sessions/{s['id']}", json={
        "parsed_job": edited_job,
        "tailored_resume": "My edited resume " * 5,
        "cover_letter": "My edited letter " * 5,
        "status": "applied",
    })
    assert r.status_code == 200
    body = logged_in.get(f"/sessions/{s['id']}").json()
    assert body["parsed_job"]["title"] == "Staff Backend Engineer" and body["status"] == "applied"
    assert body["tailored_resume"].startswith("My edited resume") and body["cover_letter"].startswith("My edited")


def test_patch_is_partial(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    logged_in.patch(f"/sessions/{s['id']}", json={"cover_letter": "Letter text " * 6})
    logged_in.patch(f"/sessions/{s['id']}", json={"status": "interview"})
    body = logged_in.get(f"/sessions/{s['id']}").json()
    assert body["cover_letter"].startswith("Letter text") and body["status"] == "interview"
    assert body["parsed_job"]["title"] == "Senior Backend Engineer"


def test_patch_validation(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    assert logged_in.patch(f"/sessions/{s['id']}", json={"status": "hired!!"}).status_code == 422
    assert logged_in.patch(f"/sessions/{s['id']}", json={"cover_letter": "short"}).status_code == 422


def test_patch_other_users_or_archived_session_is_404(client, outbox, use_llm, db):  # noqa: F811
    make_user(client, outbox, stay_logged_in=True)
    s = start_session(client, use_llm)
    age(db, s["id"], days=5)
    assert client.patch(f"/sessions/{s['id']}", json={"status": "applied"}).status_code == 404
    fresh = start_session(client, use_llm)
    client.post("/auth/logout")
    make_user(client, outbox, email="bob@example.com", stay_logged_in=True)
    assert client.patch(f"/sessions/{fresh['id']}", json={"status": "applied"}).status_code == 404


# ---------- DOCX export ----------

def test_export_resume_docx(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    resume = "Alice Example\n\nEXPERIENCE\n- Built REST APIs in Python\n- Deployed on AWS"
    logged_in.patch(f"/sessions/{s['id']}", json={"tailored_resume": resume})
    r = logged_in.get(f"/sessions/{s['id']}/export/resume")
    assert r.status_code == 200
    assert 'filename="resume-Initech-Senior-Backend-Engineer.docx"' in r.headers["content-disposition"]
    doc = Document(io.BytesIO(r.content))
    styles = {p.text: p.style.name for p in doc.paragraphs}
    assert styles["EXPERIENCE"].startswith("Heading") and styles["Built REST APIs in Python"] == "List Bullet"


def test_export_missing_or_invalid_kind(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    assert logged_in.get(f"/sessions/{s['id']}/export/cover-letter").status_code == 404
    assert logged_in.get(f"/sessions/{s['id']}/export/passwords").status_code == 422


# ---------- URL fetcher: SSRF protection ----------

def resolver_for(mapping):
    def resolve(host, port):
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host]
    return resolve


PUBLIC = resolver_for({"jobs.example.com": ["93.184.216.34"], "evil.example.com": ["10.0.0.5"],
                       "mixed.example.com": ["93.184.216.34", "127.0.0.1"],
                       "meta.example.com": ["169.254.169.254"], "v6.example.com": ["::ffff:127.0.0.1"]})


@pytest.mark.parametrize(
    "url,reason",
    [
        ("ftp://jobs.example.com/x", "http(s)"),
        ("file:///etc/passwd", "http(s)"),
        ("http://jobs.example.com:8080/", "ports"),
        ("http://user:pw@jobs.example.com/", "credentials"),
        ("http://127.0.0.1/", "public"),
        ("http://[::1]/", "public"),
        ("http://evil.example.com/", "public"),
        ("http://mixed.example.com/", "public"),  # one bad address is enough to reject
        ("http://meta.example.com/", "public"),   # cloud metadata endpoint
        ("http://v6.example.com/", "public"),     # IPv4-mapped IPv6 loopback
        ("http://unknown.example.com/", "find"),
        ("https://www.linkedin.com/jobs/view/123", "paste"),
        ("https://in.indeed.com/viewjob?jk=1", "paste"),
    ],
)
def test_check_url_rejects(url, reason):
    with pytest.raises(FetchError) as e:
        check_url(url, PUBLIC)
    assert reason in str(e.value)


def test_check_url_accepts_public_site():
    url, ip = check_url("https://jobs.example.com/role/1", PUBLIC)
    assert ip == "93.184.216.34" and url.host == "jobs.example.com"


JOB_HTML = """<html><head><title>Careers</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Backend Engineer","hiringOrganization":{"@type":"Organization","name":"Initech"},
"description":"<p>We need <b>Python</b> and FastAPI.</p><ul><li>5+ years Python</li><li>AWS</li></ul>""" + \
    "<p>" + "Build and run APIs for millions of users. " * 10 + "</p>" + """"}</script>
<script>var tracking = "ignore me";</script></head>
<body><nav>Home | Jobs</nav><h1>Backend Engineer</h1><p>Some body text</p></body></html>"""


def test_extract_prefers_json_ld_job_posting():
    text = extract_job_text(JOB_HTML)
    assert text.startswith("Backend Engineer - Initech")
    assert "- 5+ years Python" in text and "ignore me" not in text and "Home | Jobs" not in text


def test_extract_falls_back_to_visible_text():
    html = "<html><head><style>.x{}</style></head><body><nav>menu</nav><main><h1>Data Engineer</h1>" \
           "<ul><li>SQL</li><li>Spark</li></ul></main><script>evil()</script></body></html>"
    text = extract_job_text(html)
    assert "Data Engineer" in text and "- SQL" in text and "evil" not in text and "menu" not in text


def mock_transport(routes, seen):
    def handler(request: httpx.Request):
        seen.append(request)
        key = (request.headers["host"], request.url.path)
        status, headers, body = routes[key]
        return httpx.Response(status, headers=headers, content=body)
    return httpx.MockTransport(handler)


def test_fetch_connects_to_validated_ip_with_original_host():
    seen = []
    routes = {("jobs.example.com", "/r/1"): (200, {"content-type": "text/html"}, JOB_HTML.encode())}
    text = fetch_job_text("https://jobs.example.com/r/1", PUBLIC, mock_transport(routes, seen))
    assert "Backend Engineer - Initech" in text
    assert seen[0].url.host == "93.184.216.34"  # pinned: no second DNS lookup
    assert seen[0].headers["host"] == "jobs.example.com"
    assert seen[0].extensions["sni_hostname"] == "jobs.example.com"


def test_redirect_to_internal_address_is_blocked():
    seen = []
    routes = {("jobs.example.com", "/r/1"): (302, {"location": "http://evil.example.com/admin"}, b"")}
    with pytest.raises(FetchError) as e:
        fetch_job_text("https://jobs.example.com/r/1", PUBLIC, mock_transport(routes, seen))
    assert "public" in str(e.value) and len(seen) == 1  # never contacted the internal host


def test_safe_redirect_is_followed():
    seen = []
    routes = {
        ("jobs.example.com", "/old"): (301, {"location": "/new"}, b""),
        ("jobs.example.com", "/new"): (200, {"content-type": "text/html"}, JOB_HTML.encode()),
    }
    assert "Initech" in fetch_job_text("https://jobs.example.com/old", PUBLIC, mock_transport(routes, seen))


def test_too_many_redirects():
    routes = {("jobs.example.com", "/loop"): (302, {"location": "/loop"}, b"")}
    with pytest.raises(FetchError, match="redirects"):
        fetch_job_text("https://jobs.example.com/loop", PUBLIC, mock_transport(routes, []))


@pytest.mark.parametrize(
    "status,headers,body,message",
    [
        (403, {"content-type": "text/html"}, b"no", "blocked"),
        (404, {"content-type": "text/html"}, b"no", "404"),
        (200, {"content-type": "application/pdf"}, b"%PDF", "isn't a web page"),
        (200, {"content-type": "text/html"}, b"<html><body>Loading...</body></html>", "JavaScript"),
        (200, {"content-type": "text/html"}, b"a" * (2 * 1024 * 1024 + 10), "too large"),
    ],
)
def test_fetch_failures(status, headers, body, message):
    routes = {("jobs.example.com", "/x"): (status, headers, body)}
    with pytest.raises(FetchError, match=message):
        fetch_job_text("https://jobs.example.com/x", PUBLIC, mock_transport(routes, []))


# ---------- create session from a URL ----------

@pytest.fixture
def fetcher():
    calls = []

    def install(result):
        def fake(url):
            calls.append(url)
            if isinstance(result, Exception):
                raise result
            return result
        app.dependency_overrides[get_job_fetcher] = lambda: fake
        return calls

    yield install
    app.dependency_overrides.pop(get_job_fetcher, None)


def test_create_session_from_url(logged_in, use_llm, fetcher):  # noqa: F811
    calls = fetcher(JOB_TEXT)
    llm = use_llm(json.dumps(PARSED))
    r = logged_in.post("/sessions", json={"job_url": "https://jobs.example.com/r/1"})
    assert r.status_code == 201 and r.json()["job_text"] == JOB_TEXT.strip()
    assert calls == ["https://jobs.example.com/r/1"]
    assert JOB_TEXT.strip()[:40] in llm.calls[0][0][1]["content"]


def test_pasted_text_wins_over_url(logged_in, use_llm, fetcher):  # noqa: F811
    calls = fetcher("should not be used")
    use_llm(json.dumps(PARSED))
    logged_in.post("/sessions", json={"job_url": "https://jobs.example.com/r/1", "job_text": JOB_TEXT})
    assert calls == []


def test_fetch_failure_asks_user_to_paste(logged_in, use_llm, fetcher, db):  # noqa: F811
    fetcher(FetchError("www.linkedin.com doesn't allow automated access. Please copy and paste the job description."))
    use_llm()
    r = logged_in.post("/sessions", json={"job_url": "https://www.linkedin.com/jobs/view/1"})
    assert r.status_code == 422 and "paste" in r.json()["detail"]
    assert db.scalar(select(JobSession)) is None


def test_create_needs_text_or_url(logged_in, use_llm):  # noqa: F811
    use_llm()
    assert logged_in.post("/sessions", json={}).status_code == 422


# ---------- config guards ----------

def test_production_refuses_console_email():
    from app.config import Settings

    with pytest.raises(ValueError, match="EMAIL_MODE"):
        Settings(environment="production", jwt_secret="x" * 40, email_mode="console")
    Settings(environment="production", jwt_secret="x" * 40, email_mode="smtp")


def test_otp_rows_are_kept_after_use(client, outbox, verified_user, db):
    code = reset_code(client, outbox)
    client.post("/auth/reset-password", json={"email": EMAIL, "code": code, "new_password": NEW_PASSWORD})
    assert all(o.used_at is not None for o in db.scalars(select(OtpCode)))


def test_datetimes_are_serialized_as_utc(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    assert s["created_at"].endswith("+00:00") or s["created_at"].endswith("Z")
    item = logged_in.get("/sessions").json()[0]
    assert item["created_at"].endswith(("+00:00", "Z")) and item["visible_until"].endswith(("+00:00", "Z"))
    assert logged_in.get("/auth/me").json()["created_at"].endswith(("+00:00", "Z"))


def test_me_reports_admin_flag(client, outbox):
    make_user(client, outbox, stay_logged_in=True)
    assert client.get("/auth/me").json()["is_admin"] is False
    client.post("/auth/logout")
    make_user(client, outbox, email="admin@example.com", stay_logged_in=True)
    assert client.get("/auth/me").json()["is_admin"] is True


# ---------- PDF export / preview ----------

PDF_RESUME = ("Alice Example\nBackend Engineer | alice@example.com\n\nEXPERIENCE\n"
              "Initech – Software Engineer (2021–2024)\n- Built REST APIs in Python → 3M requests/day\n"
              "• Deployed on AWS — with Docker\n\nSKILLS\nPython, FastAPI, ₹ budgets, café ✓")


def _pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def test_export_resume_pdf_download(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    logged_in.patch(f"/sessions/{s['id']}", json={"tailored_resume": PDF_RESUME})
    r = logged_in.get(f"/sessions/{s['id']}/export/resume?format=pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert r.headers["content-disposition"] == 'attachment; filename="resume-Initech-Senior-Backend-Engineer.pdf"'
    assert r.headers["cache-control"] == "private, no-store"
    text = _pdf_text(r.content)  # real, selectable text (ATS-readable), Unicode mapped instead of crashing
    for expected in ["Alice Example", "EXPERIENCE", "Initech - Software Engineer (2021-2024)",
                     "Built REST APIs in Python -> 3M requests/day", "Deployed on AWS - with Docker", "Rs. budgets",
                     "café"]:
        assert expected in text, expected
    assert "x-frame-options" not in r.headers  # downloads keep the site default (never framed)


def test_pdf_preview_is_inline_and_frameable_only_by_us(logged_in, use_llm):  # noqa: F811
    s = start_session(logged_in, use_llm)
    logged_in.patch(f"/sessions/{s['id']}", json={"tailored_resume": PDF_RESUME})
    r = logged_in.get(f"/sessions/{s['id']}/export/resume?format=pdf&inline=true")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline;")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"  # no object-src: it would block the viewer


def test_cover_letter_pdf_and_owner_only(client, logged_in, use_llm, outbox):  # noqa: F811
    s = start_session(logged_in, use_llm)
    letter = "Dear Hiring Manager,\n\nI build APIs.\nThey scale.\n\nSincerely,\nAlice Example"
    logged_in.patch(f"/sessions/{s['id']}", json={"cover_letter": letter * 3})
    r = logged_in.get(f"/sessions/{s['id']}/export/cover-letter?format=pdf")
    assert r.status_code == 200 and "Sincerely," in _pdf_text(r.content)
    assert logged_in.get(f"/sessions/{s['id']}/export/resume?format=exe").status_code == 422
    client.post("/auth/logout")
    make_user(client, outbox, email="bob@example.com", stay_logged_in=True)
    assert client.get(f"/sessions/{s['id']}/export/cover-letter?format=pdf").status_code == 404


def test_long_resume_pdf_paginates():
    from pypdf import PdfReader
    from app.services.pdf_export import resume_to_pdf
    pdf = resume_to_pdf("Alice\n\nEXPERIENCE\n" + "\n".join(f"- Achievement number {i} " * 3 for i in range(120)))
    assert len(PdfReader(io.BytesIO(pdf)).pages) >= 3


def test_pdf_text_mapping_handles_unicode_spaces_and_dashes():
    """Seen live: 'Nov 2022' with a narrow no-break space rendered as 'Nov?2022'."""
    from app.services.pdf_export import to_latin1

    for space in ["\u202f", "\u2007", "\u2002", "\u2003", "\u2008", "\u205f", "\u3000", "\u200a", "\u00a0"]:
        assert to_latin1(f"Nov{space}2022") == "Nov 2022", hex(ord(space))
    for invisible in ["\u200b", "\u2060", "\u00ad", "\ufeff", "\u200d"]:
        assert to_latin1(f"Py{invisible}thon") == "Python", hex(ord(invisible))
    for dash in ["\u2010", "\u2011", "\u2012", "\u2013", "\u2015", "\u2e3a", "\ufe58"]:
        assert to_latin1(f"2022{dash}2026") in ("2022-2026", "2022 - 2026"), hex(ord(dash))
    assert to_latin1("oﬃce café ½") == "office café ½"  # ligature decomposed, Latin-1 kept
    assert to_latin1("Hindi: नमस्ते") .count("?") > 0  # no plain equivalent: still visible as '?'
