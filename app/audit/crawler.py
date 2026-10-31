"""사이트 크롤러: robots.txt / sitemap / llms.txt 수집 + 같은 도메인 BFS 크롤링.

- robots.txt 를 준수합니다 (우리 봇 토큰: config.USER_AGENT_TOKEN).
- 요청 사이 대기(config.CRAWL_DELAY)로 대상 서버 부하를 줄입니다.
- 내부망/로컬 주소는 기본 차단합니다(SSRF 방지, 리다이렉트 경로도 매번 검사).
- JS 렌더링은 하지 않습니다(정적 HTML 기준). JS 의존 사이트는 진단에서 경고로 표시합니다.
"""
from __future__ import annotations

import html as htmllib
import ipaddress
import json
import re
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from bs4.element import Comment, Doctype

from .. import config

SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".css", ".js", ".json", ".xml", ".txt", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".hwp", ".woff", ".woff2", ".ttf", ".eot", ".gz",
}
TEXT_SKIP_PARENTS = {"script", "style", "noscript", "template", "head", "title"}


class FetchBlocked(Exception):
    """보안 정책상 요청할 수 없는 주소."""


@dataclass
class Fetched:
    url: str
    final_url: str
    status: int
    headers: dict
    body: bytes
    elapsed_ms: int
    redirects: int


@dataclass
class Page:
    url: str
    final_url: str
    status: int
    elapsed_ms: int = 0
    headers: dict = field(default_factory=dict)
    content_type: str = ""
    size: int = 0
    depth: int = 0
    redirects: int = 0
    error: str = ""
    is_html: bool = False
    soup: BeautifulSoup | None = None
    text: str = ""
    script_count: int = 0
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    jsonld_objects: list[dict] = field(default_factory=list)
    jsonld_types: set[str] = field(default_factory=set)
    jsonld_errors: int = 0
    title: str = ""
    insecure_resources: list[str] = field(default_factory=list)   # https 페이지의 http:// 리소스
    feeds: list[str] = field(default_factory=list)                # RSS/Atom 링크


@dataclass
class CrawlResult:
    start_url: str
    home_url: str
    host: str
    final_scheme: str = "https"
    pages: list[Page] = field(default_factory=list)
    robots_status: int | None = None
    robots_error: str = ""
    robots: RobotFileParser | None = None
    robots_sitemaps: list[str] = field(default_factory=list)
    sitemap_urls: list[str] = field(default_factory=list)
    sitemap_found: bool = False
    llms_txt_status: int | None = None
    http_redirects_to_https: bool | None = None   # http:// 접속 시 https로 이동하는지 (None=확인 불가)
    blocked_by_robots: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def can_fetch(self, agent: str, url: str | None = None) -> bool:
        if self.robots is None:
            return True
        return self.robots.can_fetch(agent, url or self.home_url)


# ---------- URL / 보안 ----------

def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def host_key(host: str | None) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _check_public_host(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise FetchBlocked(f"지원하지 않는 프로토콜: {p.scheme}")
    if not p.hostname:
        raise FetchBlocked("호스트가 없는 주소")
    if config.ALLOW_PRIVATE_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise FetchBlocked(f"도메인 조회 실패: {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise FetchBlocked(f"내부망/예약 주소는 크롤링할 수 없습니다: {p.hostname}")


def _page_key(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return f"{p.scheme}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


# ---------- HTTP ----------

def _fetch(session: requests.Session, url: str, max_redirects: int = 5) -> Fetched:
    """리다이렉트를 직접 따라가며 매 단계 호스트를 검사합니다."""
    start = time.monotonic()
    current = url
    redirects = 0
    while True:
        _check_public_host(current)
        r = session.get(current, timeout=config.REQUEST_TIMEOUT, allow_redirects=False, stream=True)
        try:
            if r.is_redirect or r.is_permanent_redirect:
                loc = r.headers.get("Location")
                if not loc or redirects >= max_redirects:
                    body = b""
                    break
                current = urljoin(current, loc)
                redirects += 1
                continue
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= config.MAX_BODY_BYTES:
                    break
            body = b"".join(chunks)
            break
        finally:
            r.close()
    elapsed = int((time.monotonic() - start) * 1000)
    return Fetched(url, current, r.status_code, {k.lower(): v for k, v in r.headers.items()},
                   body, elapsed, redirects)


def _decode(body: bytes) -> str:
    for enc in ("utf-8", "euc-kr"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="ignore")


# ---------- 페이지 분석 ----------

def analyze_page(page: Page, body: bytes) -> None:
    soup = BeautifulSoup(body, "lxml")
    page.soup = soup
    page.script_count = len(soup.find_all("script"))

    parts = []
    for s in soup.find_all(string=True):
        if isinstance(s, (Comment, Doctype)):
            continue
        if s.parent is not None and s.parent.name in TEXT_SKIP_PARENTS:
            continue
        t = s.strip()
        if t:
            parts.append(t)
    page.text = " ".join(parts)
    t = soup.find("title")
    page.title = t.get_text(strip=True) if t else ""

    if urlparse(page.final_url).scheme == "https":
        for tag, attr in (("img", "src"), ("script", "src"), ("link", "href"), ("iframe", "src"),
                          ("source", "src"), ("video", "src"), ("audio", "src")):
            for el in soup.find_all(tag, attrs={attr: True}):
                if tag == "link" and "stylesheet" not in [x.lower() for x in (el.get("rel") or [])]:
                    continue
                if el[attr].strip().lower().startswith("http://"):
                    page.insecure_resources.append(el[attr].strip())

    for link in soup.find_all("link", attrs={"type": re.compile(r"(rss|atom)\+xml", re.I)}):
        if link.get("href"):
            page.feeds.append(urljoin(page.final_url, link["href"].strip()))

    site_host = host_key(urlparse(page.final_url).hostname)
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
            continue
        absolute, _ = urldefrag(urljoin(page.final_url, href))
        if urlparse(absolute).scheme not in ("http", "https") or absolute in seen:
            continue
        seen.add(absolute)
        if host_key(urlparse(absolute).hostname) == site_host:
            page.internal_links.append(absolute)
        else:
            page.external_links.append(absolute)

    for tag in soup.find_all("script", attrs={"type": re.compile(r"^application/ld\+json$", re.I)}):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            page.jsonld_errors += 1
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if not isinstance(obj, dict):
                continue
            if "@graph" in obj and isinstance(obj["@graph"], list):
                stack.extend(obj["@graph"])
            page.jsonld_objects.append(obj)
            t = obj.get("@type")
            for name in (t if isinstance(t, list) else [t]):
                if isinstance(name, str):
                    page.jsonld_types.add(name)


def _make_page(url: str, f: Fetched, depth: int) -> Page:
    ctype = f.headers.get("content-type", "").lower()
    page = Page(url=url, final_url=f.final_url, status=f.status, elapsed_ms=f.elapsed_ms,
                headers=f.headers, content_type=ctype, size=len(f.body), depth=depth,
                redirects=f.redirects)
    page.is_html = f.status < 400 and ("html" in ctype or (not ctype and f.body[:1] == b"<"))
    if page.is_html:
        analyze_page(page, f.body)
    return page


# ---------- robots / sitemap / llms ----------

def _load_robots(session: requests.Session, res: CrawlResult) -> None:
    base = f"{urlparse(res.home_url).scheme}://{urlparse(res.home_url).netloc}"
    rp = RobotFileParser()
    try:
        f = _fetch(session, base + "/robots.txt")
        res.robots_status = f.status
        if f.status == 200:
            rp.parse(_decode(f.body).splitlines())
        else:
            rp.parse([])  # 없거나 오류면 전체 허용으로 해석
            if f.status >= 500:
                res.notes.append(f"robots.txt 서버 오류({f.status}): 전체 허용으로 간주")
    except FetchBlocked:
        raise
    except requests.RequestException as e:
        res.robots_error = str(e)[:200]
        rp.parse([])
    res.robots = rp
    res.robots_sitemaps = list(rp.site_maps() or [])


def _extract_locs(text: str) -> list[str]:
    return [htmllib.unescape(m).strip() for m in re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.S | re.I)]


def _load_sitemaps(session: requests.Session, res: CrawlResult) -> None:
    base = f"{urlparse(res.home_url).scheme}://{urlparse(res.home_url).netloc}"
    candidates = res.robots_sitemaps or [base + "/sitemap.xml", base + "/sitemap_index.xml"]
    urls: list[str] = []
    fetched_maps = 0

    def read(sm_url: str):
        nonlocal fetched_maps
        if fetched_maps >= 8:
            return None
        fetched_maps += 1
        try:
            f = _fetch(session, sm_url)
        except (FetchBlocked, requests.RequestException):
            return None
        if f.status != 200:
            return None
        return _decode(f.body)

    for sm in candidates:
        text = read(sm)
        if text is None or "<loc" not in text.lower():
            continue
        res.sitemap_found = True
        locs = _extract_locs(text)
        if "<sitemapindex" in text.lower():
            for child in locs[:6]:
                ctext = read(child)
                if ctext:
                    urls.extend(_extract_locs(ctext))
        else:
            urls.extend(locs)
        if urls:
            break
    res.sitemap_urls = list(dict.fromkeys(urls))[:5000]


def _check_http_redirect(session: requests.Session, res: CrawlResult) -> None:
    if res.final_scheme != "https":
        res.http_redirects_to_https = False
        return
    p = urlparse(res.home_url)
    try:
        f = _fetch(session, f"http://{p.netloc}{p.path or '/'}")
        res.http_redirects_to_https = urlparse(f.final_url).scheme == "https"
    except (FetchBlocked, requests.RequestException):
        res.http_redirects_to_https = None


def _load_llms(session: requests.Session, res: CrawlResult) -> None:
    base = f"{urlparse(res.home_url).scheme}://{urlparse(res.home_url).netloc}"
    try:
        f = _fetch(session, base + "/llms.txt")
        res.llms_txt_status = f.status
    except (FetchBlocked, requests.RequestException):
        res.llms_txt_status = None


# ---------- 메인 ----------

def crawl(start_url: str, max_pages: int = 30, delay: float | None = None) -> CrawlResult:
    delay = config.CRAWL_DELAY if delay is None else delay
    max_pages = max(1, min(max_pages, config.MAX_PAGES_LIMIT))
    start_url = normalize_url(start_url)
    _check_public_host(start_url)

    session = requests.Session()
    session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})

    parsed = urlparse(start_url)
    res = CrawlResult(start_url=start_url, home_url=start_url, host=parsed.hostname or "")

    # 홈 먼저 요청해서 리다이렉트(www, https 등) 반영
    try:
        first = _fetch(session, start_url)
    except requests.RequestException as e:
        # https 연결이 안 되는 사이트(인증서 없음 등)는 http로 한 번 더 시도
        if parsed.scheme != "https":
            raise RuntimeError(f"사이트에 연결할 수 없습니다: {type(e).__name__}") from e
        fallback = "http://" + start_url[len("https://"):]
        try:
            first = _fetch(session, fallback)
        except requests.RequestException as e2:
            raise RuntimeError(f"사이트에 연결할 수 없습니다(https·http 모두 실패): {type(e2).__name__}") from e2
        res.notes.append("https 연결에 실패해 http로 진단했습니다. SSL 인증서 적용 여부를 확인하세요.")
    final = urlparse(first.final_url)
    res.home_url = first.final_url
    res.host = final.hostname or res.host
    res.final_scheme = final.scheme
    home_page = _make_page(start_url, first, 0)

    _load_robots(session, res)
    _load_sitemaps(session, res)
    _load_llms(session, res)
    _check_http_redirect(session, res)

    site_key = host_key(res.host)
    seen = {_page_key(first.final_url), _page_key(start_url)}
    res.pages.append(home_page)
    queue: deque[tuple[str, int]] = deque()

    def enqueue(links: list[str], depth: int):
        for link in links:
            if len(link) > 300:
                continue
            k = _page_key(link)
            if k in seen:
                continue
            path = urlparse(link).path.lower()
            if any(path.endswith(ext) for ext in SKIP_EXT):
                continue
            seen.add(k)
            queue.append((link, depth))

    if home_page.is_html:
        enqueue(home_page.internal_links, 1)

    def visit(url: str, depth: int) -> None:
        if not res.can_fetch(config.USER_AGENT_TOKEN, url):
            res.blocked_by_robots.append(url)
            return
        time.sleep(delay)
        try:
            f = _fetch(session, url)
        except FetchBlocked as e:
            res.notes.append(f"차단된 요청 건너뜀: {url} ({e})")
            return
        except requests.RequestException as e:
            res.pages.append(Page(url=url, final_url=url, status=0, depth=depth, error=str(e)[:200]))
            return
        page = _make_page(url, f, depth)
        res.pages.append(page)
        if page.is_html and 0 < depth < config.MAX_CRAWL_DEPTH:
            enqueue([l for l in page.internal_links if host_key(urlparse(l).hostname) == site_key], depth + 1)

    while queue and len(res.pages) < max_pages:
        url, depth = queue.popleft()
        visit(url, depth)

    # 링크로 못 찾은 페이지는 사이트맵에서 보충
    if len(res.pages) < max_pages and res.sitemap_urls:
        for u in res.sitemap_urls:
            if len(res.pages) >= max_pages:
                break
            if host_key(urlparse(u).hostname) != site_key:
                continue
            k = _page_key(u)
            if k in seen:
                continue
            seen.add(k)
            visit(u, depth=-1)

    return res
