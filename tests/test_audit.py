import pytest

from app import config
from app.audit.checks import run_checks
from app.audit.crawler import FetchBlocked, crawl
from app.audit.scoring import category_scores, overall_score


def by_id(results):
    return {r.check_id: r for r in results}


@pytest.fixture(scope="module")
def good(good_site):
    res = crawl(good_site, max_pages=20, delay=0)
    return res, by_id(run_checks(res))


@pytest.fixture(scope="module")
def blocked(blocked_site):
    res = crawl(blocked_site, max_pages=20, delay=0)
    return res, by_id(run_checks(res))


def test_crawl_collects_site_files_and_pages(good):
    res, _ = good
    assert res.robots_status == 200
    assert res.sitemap_found and len(res.sitemap_urls) == 4
    assert res.llms_txt_status == 200
    from urllib.parse import urlparse
    paths = {urlparse(p.final_url).path.lstrip("/") for p in res.pages}
    assert {"", "about", "bad", "missing", "extra"} <= paths
    assert next(p for p in res.pages if p.final_url.endswith("/missing")).status == 404
    # 사이트맵에만 있는 /extra 는 보충 크롤링으로 수집
    assert any(p.final_url.endswith("/extra") for p in res.pages)


def test_site_level_good(good):
    _, r = good
    for cid in ("googlebot_access", "yeti_access", "ai_search_bots", "ai_training_bots", "llms_txt",
                "sitemap", "sitemap_in_robots", "jsonld_any", "org_schema", "same_as", "faq_schema",
                "naver_verification"):
        assert r[cid].status == "pass", (cid, r[cid].message)


def test_broken_and_noindex_detected(good):
    _, r = good
    assert r["broken_pages"].status == "fail"
    assert any("/missing" in u for u in r["broken_pages"].urls)
    assert r["noindex"].status == "fail"
    assert any(u.endswith("/bad") for u in r["noindex"].urls)
    assert r["h1"].status in ("warn", "fail") and any(u.endswith("/bad") for u in r["h1"].urls)
    assert r["img_alt"].status in ("warn", "fail")
    assert r["heading_order"].status in ("warn", "fail")


def test_geo_page_checks_only_on_content_pages(good):
    _, r = good
    # /bad 는 본문이 짧아 GEO 본문형 검사 대상이 아님 → 통과
    assert r["answer_first"].status == "pass"
    assert r["author_date"].status == "pass"
    assert r["citations"].status == "pass"
    assert r["question_headings"].status == "pass"


def test_manual_items_not_scored(good):
    res, r = good
    manual = [x for x in r.values() if x.status == "manual"]
    assert len(manual) >= 6 and all(x.weight == 0 for x in manual)


def test_blocked_site(blocked):
    _, r = blocked
    assert r["googlebot_access"].status == "fail"
    assert r["yeti_access"].status == "fail"
    assert r["ai_search_bots"].status == "fail"
    assert "PerplexityBot" in r["ai_search_bots"].message and "OAI-SearchBot" in r["ai_search_bots"].message
    assert "ChatGPT-User" not in r["ai_search_bots"].message  # 허용된 봇은 목록에 없어야 함
    assert "ClaudeBot" not in r["ai_training_bots"].message
    assert r["ai_training_bots"].status == "warn" and "GPTBot" in r["ai_training_bots"].message
    assert r["sitemap"].status == "fail" and r["llms_txt"].status == "warn"


def test_scoring(good, blocked):
    g = overall_score(category_scores(list(good[1].values())))
    b = overall_score(category_scores(list(blocked[1].values())))
    assert 0 <= b < g <= 100


def test_private_hosts_blocked_by_default(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_PRIVATE_HOSTS", False)
    with pytest.raises(FetchBlocked):
        crawl("http://127.0.0.1:9/", max_pages=1, delay=0)
    with pytest.raises(FetchBlocked):
        crawl("http://169.254.169.254/latest/meta-data", max_pages=1, delay=0)


def test_engine_scores(good):
    from app.audit.scoring import engine_scores
    es = engine_scores(category_scores(list(good[1].values())))
    assert set(es) == {"seo", "geo", "naver"} and all(0 <= v <= 100 for v in es.values())


def test_new_checks_on_good_site(good):
    _, r = good
    assert r["frame_forwarding"].status == "pass"
    assert r["snippet_allowed"].status == "pass"
    assert r["yeti_meta"].status == "pass"
    assert r["rss_feed"].status == "warn"
    assert r["about_page"].status == "pass"
    assert r["freshness"].status in ("pass", "warn")
    assert "http_redirect" not in r  # http 사이트는 리다이렉트 항목 생략


def _page(html, url="https://example.com/"):
    from app.audit.crawler import Page, analyze_page
    p = Page(url=url, final_url=url, status=200, is_html=True)
    analyze_page(p, html.encode())
    return p


def test_page_level_engine_functions():
    from app.audit.engines import geo, naver, seo
    p = _page('<html><head><meta name="robots" content="max-snippet:0"><meta name="Yeti" content="noindex">'
              '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
              '</head><body><img src="http://cdn.example.com/a.png" alt=""></body></html>')
    assert geo.c_snippet_allowed(p) is False
    assert naver.c_yeti_meta(p) is False
    assert seo.c_mixed(p) is False
    assert p.feeds == ["https://example.com/feed.xml"]
    clean = _page("<html><body><p>ok</p></body></html>")
    assert geo.c_snippet_allowed(clean) and naver.c_yeti_meta(clean) and seo.c_mixed(clean)
