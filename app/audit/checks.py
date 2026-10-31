"""진단 실행 진입점: 엔진(seo/geo/naver)의 페이지·사이트 항목을 모아 결과 목록을 만듭니다.

항목 정의와 가중치는 app/audit/engines/ 의 각 엔진 파일에서 수정하세요.
"""
from __future__ import annotations

from .crawler import CrawlResult, Page
from .engines import CATEGORY_ENGINE, CATEGORY_LABELS, ENGINES, MODULES, PAGE_CHECKS, Result
from .engines.base import aggregate, ok_pages, page_url
from .engines.geo import AI_SEARCH_BOTS, AI_TRAINING_BOTS  # noqa: F401  (외부 참조용)

__all__ = ["CATEGORY_ENGINE", "CATEGORY_LABELS", "ENGINES", "Result", "run_checks", "page_issue_counts", "catalog"]


def run_checks(res: CrawlResult) -> list[Result]:
    ok = ok_pages(res)
    results: list[Result] = []
    if ok:
        results.extend(aggregate(c, ok) for c in PAGE_CHECKS)
    for m in MODULES:
        results.extend(m.site_checks(res))
    return results


def page_issue_counts(res: CrawlResult) -> dict[str, int]:
    """페이지별로 실패한 페이지 단위 항목 수 (리포트의 '페이지별 현황'용)."""
    counts: dict[str, int] = {}
    for p in ok_pages(res):
        n = 0
        for c in PAGE_CHECKS:
            if c.applies(p) and c.fn(p) is False:
                n += 1
        counts[page_url(p)] = n
    return counts


def catalog() -> list[dict]:
    """진단 기준 화면에 보여줄 자동 점검 항목 목록 (페이지 단위만; 사이트 단위는 실행해야 나옴)."""
    return [{"check_id": c.check_id, "category": c.category, "title": c.title, "weight": c.weight,
             "fix": c.fix, "scope": "페이지"} for c in PAGE_CHECKS]
