"""진단 실행기: 크롤링 → 검사 → 점수화 → 저장. 백그라운드 스레드에서 실행됩니다."""
from __future__ import annotations

import logging
import threading

from .. import config, db
from .checks import page_issue_counts, run_checks
from .crawler import FetchBlocked, crawl
from .scoring import category_scores, engine_scores, overall_score

log = logging.getLogger("audit")
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_AUDITS)


def run_audit(audit_id: int) -> None:
    audit = db.get_audit(audit_id)
    if not audit:
        return
    with _slots:  # 동시 실행 제한 (대기 중에는 queued 상태 유지)
        db.update_audit(audit_id, status="running")
        try:
            site = db.get_site(audit["site_id"])
            if not site:
                raise RuntimeError("사이트가 삭제되었습니다.")
            result = crawl(site["url"], max_pages=audit["max_pages"])
            if not any(p.status == 200 for p in result.pages):
                raise RuntimeError("홈페이지를 정상(200)으로 불러오지 못했습니다. 주소와 접속 가능 여부를 확인하세요.")
            findings = run_checks(result)
            cats = category_scores(findings)
            issues = page_issue_counts(result)
            db.save_findings(audit_id, findings)
            db.save_pages(audit_id, [
                ((p.final_url or p.url)[:500], p.status, p.title[:200], len(p.text), p.elapsed_ms, p.depth,
                 issues.get(p.final_url or p.url, 1 if (p.status == 0 or p.status >= 400) else 0))
                for p in result.pages])
            notes = list(result.notes)
            if result.blocked_by_robots:
                notes.append(f"robots.txt 규칙으로 {len(result.blocked_by_robots)}개 URL은 수집하지 않았습니다.")
            db.update_audit(audit_id, status="done", score=overall_score(cats), category_scores=cats,
                            engine_scores=engine_scores(cats), notes=notes[:20],
                            pages_crawled=len(result.pages), finished_at=db.now())
        except (FetchBlocked, RuntimeError) as e:
            db.update_audit(audit_id, status="failed", error=str(e)[:300], finished_at=db.now())
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 화면에 사유를 남김
            log.exception("audit %s failed", audit_id)
            db.update_audit(audit_id, status="failed", error=f"{type(e).__name__}: {str(e)[:300]}",
                            finished_at=db.now())
