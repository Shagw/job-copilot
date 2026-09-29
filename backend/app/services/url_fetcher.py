"""Fetch a job posting from a public URL, safely.

SSRF protection (the server must never be tricked into calling internal machines):
- http/https only, ports 80/443 only, no credentials in the URL.
- The hostname is resolved once; if ANY address is private, loopback, link-local
  (e.g. cloud metadata 169.254.169.254), multicast or reserved, the URL is rejected.
- We then connect to that *already-checked IP* (Host header + TLS SNI keep the real
  hostname), so a second DNS lookup can't swap in an internal address (DNS rebinding).
- Redirects are followed manually (max 3) and every hop is checked again.
- 10s timeout, 2 MB size cap, HTML/plain text only.

Sites that forbid automated access (LinkedIn, Indeed, ...) are not fetched at all;
the user is asked to paste the description instead.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3
MAX_TEXT_CHARS = 100_000  # the parser shortens long pages automatically
ALLOWED_PORTS = {80, 443}
USER_AGENT = "JobCopilot/1.0 (personal job-application assistant; fetches single pages on user request)"

# Their terms prohibit automated access; respect that.
BLOCKED_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "naukri.com", "monster.com",
    "ziprecruiter.com", "wellfound.com", "angel.co",
}


class FetchError(Exception):
    """User-facing reason the page couldn't be fetched. The UI falls back to 'paste the text'."""


Resolver = Callable[[str, int], list[str]]


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _blocked(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def check_url(url: str, resolver: Resolver = _default_resolver) -> tuple[httpx.URL, str]:
    """Validate a URL. Returns (url, safe_ip_to_connect_to) or raises FetchError."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        raise FetchError("That doesn't look like a valid link.")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http(s) links are supported.")
    if parts.username or parts.password:
        raise FetchError("Links with embedded credentials are not allowed.")
    port = port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError("Only standard web ports (80/443) are allowed.")

    host = parts.hostname.lower().rstrip(".")
    if _blocked(host):
        raise FetchError(f"{host} doesn't allow automated access. Please copy and paste the job description.")

    try:
        ips = [host] if _looks_like_ip(host) else resolver(host, port)
    except (socket.gaierror, UnicodeError, OSError):
        raise FetchError("Couldn't find that website. Check the link.")
    if not ips or not all(_is_public(ip) for ip in ips):
        raise FetchError("That address isn't a public website.")
    return httpx.URL(url.strip()), ips[0]


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


# ---------- text extraction ----------

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form", "iframe", "template"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        self.json_ld: list[str] = []
        self._in_json_ld = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._in_json_ld = True
            self.json_ld.append("")
            return
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li" and not self.skip_depth:
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            return
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_json_ld:
            self.json_ld[-1] += data
        elif self._in_title:
            self.title += data
        elif not self.skip_depth:
            self.parts.append(data)


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text[:MAX_TEXT_CHARS]


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return _clean("".join(p.parts))


def _job_posting_from_json_ld(blocks: list[str]) -> str | None:
    """Most ATS pages (Greenhouse, Lever, Workday, ...) embed a schema.org JobPosting."""
    def walk(node):
        if isinstance(node, list):
            for n in node:
                yield from walk(n)
        elif isinstance(node, dict):
            if "@graph" in node:
                yield from walk(node["@graph"])
            types = node.get("@type")
            if types == "JobPosting" or (isinstance(types, list) and "JobPosting" in types):
                yield node

    for raw in blocks:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for job in walk(data):
            desc = _html_to_text(str(job.get("description") or ""))
            if len(desc) < 100:
                continue
            org = job.get("hiringOrganization")
            company = org.get("name") if isinstance(org, dict) else None
            header = " - ".join(x for x in [job.get("title"), company] if x)
            return _clean(f"{header}\n\n{desc}" if header else desc)
    return None


def extract_job_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    structured = _job_posting_from_json_ld(p.json_ld)
    if structured:
        return structured
    body = _clean("".join(p.parts))
    title = " ".join(p.title.split())
    return _clean(f"{title}\n\n{body}") if title and title not in body[:200] else body


# ---------- fetching ----------

def fetch_job_text(
    url: str,
    resolver: Resolver = _default_resolver,
    transport: httpx.BaseTransport | None = None,
) -> str:
    current = url
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False, transport=transport,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            target, ip = check_url(current, resolver)
            pinned = target.copy_with(host=ip)  # connect to the IP we just validated
            request = client.build_request(
                "GET", pinned, headers={"Host": target.netloc.decode()},
                extensions={"sni_hostname": target.host},
            )
            try:
                response = client.send(request, stream=True)
            except httpx.TimeoutException:
                raise FetchError("The website took too long to respond.")
            except httpx.HTTPError:
                raise FetchError("Couldn't connect to that website.")
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("The website sent an invalid redirect.")
                    current = urljoin(str(target), location)
                    continue
                if response.status_code in (401, 403, 429):
                    raise FetchError("That website blocked the request. Please paste the job description.")
                if response.status_code >= 400:
                    raise FetchError(f"The website returned an error ({response.status_code}).")
                ctype = response.headers.get("content-type", "").lower()
                if not ctype.startswith(("text/html", "text/plain", "application/xhtml")):
                    raise FetchError("That link isn't a web page (PDFs and files aren't supported yet).")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError("That page is too large.")
                encoding = response.encoding or "utf-8"
            finally:
                response.close()

            html = bytes(body).decode(encoding, errors="replace")
            text = extract_job_text(html) if "html" in ctype else _clean(html)
            if len(text) < 200:
                raise FetchError("Couldn't find a job description on that page. It may need JavaScript. "
                                 "Please paste the description instead.")
            return text
    raise FetchError("Too many redirects.")
