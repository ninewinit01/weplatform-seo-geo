"""SEO / GEO / 네이버 진단 엔진."""
from . import geo, naver, seo
from .base import CATEGORY_ENGINE, CATEGORY_LABELS, ENGINES, PageCheck, Result

MODULES = [seo, geo, naver]
PAGE_CHECKS: list[PageCheck] = [c for m in MODULES for c in m.PAGE_CHECKS]

__all__ = ["CATEGORY_ENGINE", "CATEGORY_LABELS", "ENGINES", "MODULES", "PAGE_CHECKS", "Result"]
