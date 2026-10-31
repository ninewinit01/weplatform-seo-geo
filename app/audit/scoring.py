"""가중치 기반 점수 계산."""
from __future__ import annotations

from .engines import ENGINES, Result

CATEGORY_WEIGHTS = {"technical": 30, "content": 15, "structured": 15, "geo": 25, "naver": 15}
STATUS_FACTOR = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
STATUS_ORDER = {"fail": 0, "warn": 1, "manual": 2, "pass": 3, "na": 4}


def _ratio(results: list[Result], keep) -> float | None:
    got = total = 0.0
    for r in results:
        if r.status not in STATUS_FACTOR or r.weight <= 0 or not keep(r):
            continue
        got += r.weight * STATUS_FACTOR[r.status]
        total += r.weight
    return round(got / total * 100, 1) if total else None


def category_scores(results: list[Result]) -> dict[str, float]:
    cats = {r.category for r in results}
    out = {c: _ratio(results, lambda r, c=c: r.category == c) for c in cats}
    return {c: s for c, s in out.items() if s is not None}


def engine_scores(cat_scores: dict[str, float]) -> dict[str, float]:
    """엔진(SEO/GEO/네이버) 점수 = 소속 분류 점수의 분류 가중 평균."""
    out = {}
    for e, spec in ENGINES.items():
        s = overall_score({c: cat_scores[c] for c in spec["categories"] if c in cat_scores})
        if s is not None:
            out[e] = s
    return out


def overall_score(cat_scores: dict[str, float]) -> float | None:
    num = den = 0.0
    for c, s in cat_scores.items():
        w = CATEGORY_WEIGHTS.get(c, 10)
        num += s * w
        den += w
    return round(num / den, 1) if den else None


def priority_key(r: dict | Result):
    status = r["status"] if isinstance(r, dict) else r.status
    weight = r["weight"] if isinstance(r, dict) else r.weight
    return (STATUS_ORDER.get(status, 9), -weight)
