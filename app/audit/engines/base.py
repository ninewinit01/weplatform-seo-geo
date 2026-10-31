"""진단 엔진 공통 타입·헬퍼.

- 페이지 단위 항목(PageCheck): 크롤링한 페이지마다 검사 후 사이트 전체 결과로 집계
- 사이트 단위 항목: 엔진별 site_checks(res) 함수
- 수동 확인 항목(status='manual'): 자동으로 알 수 없는 것(점수에 반영하지 않음)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..crawler import CrawlResult, Page

# 엔진 → 분류. 리포트의 3대 점수(SEO / GEO / 네이버)는 이 묶음 기준으로 계산합니다.
ENGINES = {
    "seo": {"label": "SEO", "desc": "구글 검색 기준 기술·콘텐츠·구조화 데이터",
            "categories": ["technical", "content", "structured"]},
    "geo": {"label": "GEO", "desc": "ChatGPT·Perplexity·Claude·AI 개요 등 AI 검색 인용 대응",
            "categories": ["geo"]},
    "naver": {"label": "네이버", "desc": "Yeti 수집·서치어드바이저·공유 미리보기",
              "categories": ["naver"]},
}

CATEGORY_LABELS = {
    "technical": "기술 SEO",
    "content": "콘텐츠",
    "structured": "구조화 데이터",
    "geo": "GEO (AI 검색 대응)",
    "naver": "네이버",
}

CATEGORY_ENGINE = {c: e for e, v in ENGINES.items() for c in v["categories"]}


@dataclass
class Result:
    check_id: str
    category: str
    title: str
    status: str            # pass / warn / fail / manual / na
    weight: int
    message: str = ""
    fix: str = ""
    urls: list[str] = field(default_factory=list)


@dataclass
class PageCheck:
    check_id: str
    category: str
    title: str
    weight: int
    fn: Callable[[Page], Optional[bool]]     # True=통과, False=문제, None=해당없음
    fix: str
    applies: Callable[[Page], bool] = lambda p: True
    strict: bool = False        # 하나라도 실패하면 fail


# ---------- 페이지 헬퍼 ----------

def title_of(p: Page) -> str:
    return p.title


def meta(p: Page, name: str) -> str:
    tag = p.soup.find("meta", attrs={"name": re.compile(f"^{re.escape(name)}$", re.I)})
    return (tag.get("content") or "").strip() if tag else ""


def meta_prop(p: Page, prop: str) -> str:
    tag = p.soup.find("meta", attrs={"property": re.compile(f"^{re.escape(prop)}$", re.I)})
    return (tag.get("content") or "").strip() if tag else ""


def is_content_page(p: Page) -> bool:
    return len(p.text) >= 500


def page_url(p: Page) -> str:
    return p.final_url or p.url


def ok_pages(res: CrawlResult) -> list[Page]:
    return [p for p in res.pages if p.is_html and p.status == 200 and p.soup is not None]


def home_page(res: CrawlResult) -> Page | None:
    ok = ok_pages(res)
    return ok[0] if ok and ok[0].depth == 0 else None


def as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def aggregate(chk: PageCheck, pages: list[Page]) -> Result:
    applicable, failed = 0, []
    for p in pages:
        if not chk.applies(p):
            continue
        r = chk.fn(p)
        if r is None:
            continue
        applicable += 1
        if not r:
            failed.append(page_url(p))
    if applicable == 0:
        return Result(chk.check_id, chk.category, chk.title, "na", chk.weight, "해당하는 페이지가 없습니다.")
    if not failed:
        return Result(chk.check_id, chk.category, chk.title, "pass", chk.weight,
                      f"검사한 {applicable}개 페이지 모두 통과")
    ratio = len(failed) / applicable
    status = "fail" if (chk.strict or ratio >= 0.3) else "warn"
    return Result(chk.check_id, chk.category, chk.title, status, chk.weight,
                  f"{applicable}개 중 {len(failed)}개 페이지에서 문제 발견", chk.fix, failed)


def manual(items: list[tuple[str, str, str, str]]) -> list[Result]:
    return [Result(i, c, t, "manual", 0, "자동 진단 불가 - 담당자 확인 필요", fix) for i, c, t, fix in items]


class Collector:
    """사이트 단위 결과를 모으는 작은 도우미."""

    def __init__(self):
        self.out: list[Result] = []

    def add(self, *a, **k):
        self.out.append(Result(*a, **k))
