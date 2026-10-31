"""GEO 엔진: AI 검색(ChatGPT 검색·Perplexity·Claude·구글 AI 개요) 인용 대응.

GEO 항목은 아직 업계 표준이 확립되지 않은 영역입니다. 가중치와 기준은 경험칙 기반이므로
이 파일의 weight 값을 팀 경험에 맞춰 조정하세요.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from ..crawler import CrawlResult, Page
from .base import (Collector, PageCheck, Result, is_content_page, manual, meta, meta_prop,
                   ok_pages, page_url)
from .seo import org_objects

# 검색/인용용 봇은 막으면 AI 답변에서 빠질 수 있고, 학습용 봇 차단은 정책적 선택입니다.
AI_SEARCH_BOTS = ["OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "Claude-SearchBot", "Claude-User"]
AI_TRAINING_BOTS = ["GPTBot", "ClaudeBot", "Google-Extended", "CCBot", "Applebot-Extended"]

QUESTION_RE = re.compile(r"(\?|？|나요|인가요|일까요|할까요|어떻게|무엇|왜\s|차이|방법)")
ABOUT_RE = re.compile(r"(about|company|brand|intro|회사|브랜드|소개)", re.I)
DATE_RE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")


def c_answer_first(p):
    h1 = p.soup.find("h1")
    candidates = h1.find_all_next("p") if h1 else p.soup.find_all("p")
    for para in candidates:
        t = para.get_text(" ", strip=True)
        if len(t) >= 20:
            return len(t) <= 350
    return False


def c_structure(p):
    return bool(p.soup.find(["ul", "ol", "table"]))


def c_author_date(p):
    if meta(p, "author") or meta_prop(p, "article:published_time") or meta_prop(p, "article:author"):
        return True
    if p.soup.find("time") or p.soup.find(attrs={"rel": "author"}):
        return True
    return any(("author" in o or "datePublished" in o) for o in p.jsonld_objects)


def c_citations(p):
    return len(p.external_links) >= 1


def c_snippet_allowed(p):
    """nosnippet / max-snippet:0 은 구글 AI 개요·검색 스니펫 인용을 막습니다."""
    robots = (meta(p, "robots") + " " + meta(p, "googlebot") + " " + p.headers.get("x-robots-tag", "")).lower()
    return "nosnippet" not in robots and "max-snippet:0" not in robots.replace(" ", "")


PAGE_CHECKS: list[PageCheck] = [
    PageCheck("snippet_allowed", "geo", "AI 인용 차단 지시어 (nosnippet)", 4, c_snippet_allowed,
              "nosnippet 또는 max-snippet:0이 설정되어 있으면 검색 스니펫과 AI 개요에서 본문이 인용되지 않습니다. 의도한 것이 아니라면 제거하세요.",
              strict=True),
    PageCheck("answer_first", "geo", "도입부 핵심 요약 (두괄식)", 2, c_answer_first,
              "H1 직후에 핵심 답을 1~3문장(350자 이내)으로 먼저 제시하세요. AI 답변이 인용하기 좋은 형태입니다.",
              applies=is_content_page),
    PageCheck("structured_elements", "geo", "목록/표 등 구조화된 본문", 2, c_structure,
              "비교·절차·수치는 목록(ul/ol)이나 표(table)로 정리하세요. AI가 정보를 추출하기 쉬워집니다.",
              applies=is_content_page),
    PageCheck("author_date", "geo", "작성자·발행일 표기", 2, c_author_date,
              "작성자와 발행/수정일(time 태그, meta, JSON-LD)을 표기하세요. 신뢰성(E-E-A-T)과 최신성 신호입니다.",
              applies=is_content_page),
    PageCheck("citations", "geo", "외부 출처 인용 링크", 2, c_citations,
              "통계·주장에는 공신력 있는 출처 링크를 달아 근거를 제시하세요. 출처가 있는 콘텐츠가 인용될 가능성이 높다고 알려져 있습니다.",
              applies=is_content_page),
]


def _latest_date(p: Page) -> datetime | None:
    found = []
    for o in p.jsonld_objects:
        for k in ("dateModified", "datePublished"):
            if isinstance(o.get(k), str):
                found.append(o[k])
    for k in ("article:modified_time", "article:published_time"):
        if meta_prop(p, k):
            found.append(meta_prop(p, k))
    for t in p.soup.find_all("time"):
        found.append(t.get("datetime") or t.get_text(strip=True))
    best = None
    for v in found:
        m = DATE_RE.search(v or "")
        if not m:
            continue
        try:
            d = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        except ValueError:
            continue
        best = d if best is None or d > best else best
    return best


def site_checks(res: CrawlResult) -> list[Result]:
    col = Collector()
    add = col.add
    ok = ok_pages(res)

    blocked_search = [b for b in AI_SEARCH_BOTS if not res.can_fetch(b)]
    if not blocked_search:
        add("ai_search_bots", "geo", "AI 검색/인용 크롤러 허용", "pass", 5,
            "ChatGPT·Perplexity·Claude 등의 검색용 봇이 접근 가능합니다.")
    else:
        add("ai_search_bots", "geo", "AI 검색/인용 크롤러 허용", "fail", 5,
            "차단된 봇: " + ", ".join(blocked_search),
            "이 봇들은 AI 답변 생성 시 페이지를 읽는 용도입니다. 차단하면 AI 답변에서 인용되기 어렵습니다. 의도한 차단이 아니라면 robots.txt에서 허용하세요.")

    blocked_train = [b for b in AI_TRAINING_BOTS if not res.can_fetch(b)]
    if not blocked_train:
        add("ai_training_bots", "geo", "AI 학습용 크롤러 허용", "pass", 1, "학습용 봇이 접근 가능합니다.")
    else:
        add("ai_training_bots", "geo", "AI 학습용 크롤러 허용", "warn", 1,
            "차단된 봇: " + ", ".join(blocked_train),
            "학습용 봇 차단은 콘텐츠 보호를 위한 정책적 선택일 수 있습니다. 광고주와 방침을 확인하세요. (검색용 봇 차단과는 별개 항목입니다.)")

    if res.llms_txt_status == 200:
        add("llms_txt", "geo", "llms.txt", "pass", 1, "llms.txt가 있습니다.")
    else:
        add("llms_txt", "geo", "llms.txt", "warn", 1, "llms.txt가 없습니다.",
            "AI에게 사이트 핵심 페이지를 안내하는 제안 형식입니다. 주요 AI 검색의 공식 지원은 확인되지 않았으므로 우선순위는 낮습니다.")

    content_pages = [p for p in ok if is_content_page(p)]
    if content_pages:
        q_pages = [p for p in content_pages if any(
            QUESTION_RE.search(h.get_text(" ", strip=True)) for h in p.soup.find_all(["h2", "h3"]))]
        if len(q_pages) / len(content_pages) >= 0.3:
            add("question_headings", "geo", "질문형 소제목", "pass", 3,
                f"본문 페이지 {len(content_pages)}개 중 {len(q_pages)}개가 질문형 소제목을 사용합니다.")
        else:
            add("question_headings", "geo", "질문형 소제목", "warn", 3,
                f"본문 페이지 {len(content_pages)}개 중 {len(q_pages)}개만 질문형 소제목을 사용합니다.",
                "사용자가 실제로 묻는 질문 형태(예: '~는 무엇인가요?', '~하는 방법')로 H2/H3를 작성하세요. AI 답변과 매칭되기 쉽습니다.",
                [page_url(p) for p in content_pages if p not in q_pages])

        dates = [d for d in (_latest_date(p) for p in content_pages) if d]
        if not dates:
            add("freshness", "geo", "콘텐츠 최신성 (12개월 이내 갱신)", "warn", 2,
                "본문 페이지에서 발행/수정일을 찾지 못했습니다.",
                "dateModified(JSON-LD) 또는 time 태그로 갱신일을 표기하고, 핵심 페이지는 정기적으로 업데이트하세요. AI 검색은 최신 정보를 선호합니다.")
        elif max(dates) >= datetime.now(timezone.utc) - timedelta(days=365):
            add("freshness", "geo", "콘텐츠 최신성 (12개월 이내 갱신)", "pass", 2,
                f"가장 최근 갱신일: {max(dates):%Y-%m-%d}")
        else:
            add("freshness", "geo", "콘텐츠 최신성 (12개월 이내 갱신)", "warn", 2,
                f"가장 최근 갱신일이 {max(dates):%Y-%m-%d}로 1년이 넘었습니다.",
                "주요 페이지의 수치·가격·사례를 최신으로 갱신하고 수정일을 표기하세요.")

    # 브랜드 소개 페이지: AI가 '이 회사는 무엇을 하는가'를 답할 근거
    all_internal = {l for p in ok for l in p.internal_links} | {page_url(p) for p in ok}
    if any(ABOUT_RE.search(urlparse(u).path) for u in all_internal):
        add("about_page", "geo", "브랜드/회사 소개 페이지", "pass", 2, "소개 페이지로 보이는 URL이 있습니다.")
    elif ok:
        add("about_page", "geo", "브랜드/회사 소개 페이지", "warn", 2, "회사·브랜드 소개 페이지를 찾지 못했습니다.",
            "회사 개요, 대표 서비스, 연혁, 연락처를 담은 소개 페이지(/about)를 만들고 메뉴에 연결하세요. AI가 브랜드를 설명할 때 근거로 씁니다.")

    orgs = org_objects(res)
    if orgs:
        fields = {"name", "url", "logo", "description"}
        missing = sorted(fields - set().union(*(set(o) for o in orgs)))
        if not missing:
            add("entity_fields", "geo", "브랜드 엔티티 정보 완성도", "pass", 2,
                "Organization에 name·url·logo·description이 모두 있습니다.")
        else:
            add("entity_fields", "geo", "브랜드 엔티티 정보 완성도", "warn", 2,
                "Organization에 빠진 속성: " + ", ".join(missing),
                "Organization JSON-LD에 name, url, logo, description을 채우세요. AI가 브랜드를 정확히 소개하는 데 쓰입니다.")

    col.out.extend(manual([
        ("m_geo_entity", "geo", "브랜드 엔티티 일관성",
         "브랜드명·대표 서비스·설명이 웹사이트, 구글 비즈니스 프로필, SNS, 위키 등에서 일관되는지 확인하세요."),
        ("m_geo_mentions", "geo", "제3자 언급·리뷰",
         "뉴스, 리뷰 사이트, 커뮤니티 등 외부 매체에서 브랜드가 언급되는지 확인하세요. AI 답변은 외부 언급을 근거로 삼는 경우가 많습니다."),
        ("m_geo_prompt", "geo", "AI 답변 노출 직접 확인",
         "ChatGPT·Perplexity·Gemini에 '○○ 추천', '○○ 비교' 등 핵심 질문을 해보고 브랜드가 언급·인용되는지 기록하세요."),
    ]))
    return col.out
