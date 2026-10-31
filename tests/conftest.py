import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# app 임포트 전에 테스트용 환경 설정
os.environ["ALLOW_PRIVATE_HOSTS"] = "1"
os.environ["CRAWL_DELAY"] = "0"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

GOOD_HOME = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>위플랫폼 테스트 브랜드 - 공식 사이트</title>
<meta name="description" content="위플랫폼 테스트 브랜드의 서비스를 소개하는 공식 사이트입니다. 자주 묻는 질문과 가격 정보를 확인하세요.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="naver-site-verification" content="abc123">
<meta property="og:title" content="t"><meta property="og:description" content="d"><meta property="og:image" content="i">
<link rel="canonical" href="/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","name":"테스트","sameAs":["https://blog.naver.com/test"]},
 {"@type":"FAQPage","mainEntity":[]}]}</script></head>
<body><h1>테스트 브랜드 소개</h1>
<p>테스트 브랜드는 중소기업을 위한 마케팅 자동화 서비스를 제공합니다. 핵심은 간단합니다.</p>
<h2>서비스는 무엇인가요?</h2>
<p>""" + ("본문 내용이 충분히 길어야 본문형 페이지로 분류됩니다. " * 25) + """</p>
<ul><li>항목 하나</li><li>항목 둘</li></ul>
<time datetime="2026-10-01">2026-10-01</time>
<a href="https://www.kostat.go.kr/">통계청 출처</a>
<a href="/about">소개</a> <a href="/bad">나쁜 페이지</a> <a href="/missing">깨진 링크</a>
<img src="a.png" alt="로고"></body></html>"""

GOOD_ABOUT = GOOD_HOME.replace("<h1>테스트 브랜드 소개</h1>", "<h1>회사 소개</h1>").replace(
    "위플랫폼 테스트 브랜드 - 공식 사이트", "회사 소개 - 위플랫폼 테스트 브랜드")

BAD_PAGE = """<html><head><meta name="robots" content="noindex,follow"></head>
<body><h1>하나</h1><h1>둘</h1><h4>건너뜀</h4><p>짧음</p><img src="x.png"></body></html>"""

ROBOTS_GOOD = "User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n"
ROBOTS_BLOCKED = """User-agent: Googlebot
Disallow: /

User-agent: Yeti
Disallow: /

User-agent: PerplexityBot
Disallow: /

User-agent: OAI-SearchBot
Disallow: /

User-agent: GPTBot
Disallow: /

User-agent: *
Allow: /
"""


def make_handler(mode):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            path = self.path.split("?")[0]
            if path == "/robots.txt":
                return self._send(200, (ROBOTS_BLOCKED if mode == "blocked" else ROBOTS_GOOD).format(base=base), "text/plain")
            if path == "/sitemap.xml" and mode == "good":
                locs = "".join(f"<url><loc>{base}{p}</loc></url>" for p in ("/", "/about", "/bad", "/extra"))
                return self._send(200, f'<?xml version="1.0"?><urlset>{locs}</urlset>', "application/xml")
            if path == "/llms.txt" and mode == "good":
                return self._send(200, "# 테스트 브랜드", "text/plain")
            if path == "/":
                return self._send(200, GOOD_HOME)
            if path == "/about":
                return self._send(200, GOOD_ABOUT)
            if path == "/extra" and mode == "good":
                return self._send(200, GOOD_ABOUT.replace("회사 소개", "추가 페이지"))
            if path == "/bad":
                return self._send(200, BAD_PAGE)
            return self._send(404, "not found")

    return H


def _serve(mode):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mode))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="session")
def good_site():
    srv = _serve("good")
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="session")
def blocked_site():
    srv = _serve("blocked")
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
