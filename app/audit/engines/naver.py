"""네이버 엔진: Yeti 수집, 서치어드바이저, 공유 미리보기, 프레임 구조."""
from __future__ import annotations

from ..crawler import CrawlResult
from .base import Collector, PageCheck, Result, home_page, manual, meta, meta_prop, ok_pages


def c_og(p):
    return all(meta_prop(p, k) for k in ("og:title", "og:description", "og:image"))


def c_iframe(p):
    if not p.soup.find("iframe"):
        return None
    return len(p.text) >= 300


def c_yeti_meta(p):
    v = (meta(p, "yeti") + " " + meta(p, "naverbot")).lower()
    return "noindex" not in v


PAGE_CHECKS: list[PageCheck] = [
    PageCheck("yeti_meta", "naver", "Yeti 전용 noindex 메타태그", 6, c_yeti_meta,
              "<meta name=\"Yeti\" content=\"noindex\">가 있어 네이버에서 이 페이지가 색인되지 않습니다. 의도한 것이 아니라면 제거하세요.",
              strict=True),
    PageCheck("og_tags", "naver", "OG 태그 (공유·미리보기)", 1, c_og,
              "og:title, og:description, og:image를 지정하세요. 네이버·카카오 공유 미리보기에 사용됩니다."),
    PageCheck("iframe_content", "naver", "iframe에 갇힌 본문", 2, c_iframe,
              "본문이 iframe 안에 있고 페이지 자체 텍스트가 적습니다. 검색 수집이 어려우므로 본문을 페이지 HTML에 직접 넣으세요."),
]


def site_checks(res: CrawlResult) -> list[Result]:
    col = Collector()
    add = col.add
    ok = ok_pages(res)
    home = home_page(res)

    if res.can_fetch("Yeti"):
        add("yeti_access", "naver", "네이버 검색봇(Yeti) 접근 허용", "pass", 6, "Yeti가 홈을 수집할 수 있습니다.")
    else:
        add("yeti_access", "naver", "네이버 검색봇(Yeti) 접근 허용", "fail", 6, "robots.txt가 네이버 검색봇(Yeti)을 막고 있습니다.",
            "robots.txt에서 Yeti 허용 규칙을 확인하세요. 차단 시 네이버 검색에 노출되지 않습니다.")

    if home:
        if home.soup.find("meta", attrs={"name": "naver-site-verification"}):
            add("naver_verification", "naver", "서치어드바이저 소유확인 메타태그", "pass", 2, "naver-site-verification 메타태그가 있습니다.")
        else:
            add("naver_verification", "naver", "서치어드바이저 소유확인 메타태그", "warn", 2,
                "홈에 naver-site-verification 메타태그가 없습니다.",
                "서치어드바이저에서 HTML 태그 방식으로 소유확인하면 이 태그가 생깁니다. (HTML 파일 업로드 등 다른 방식으로 인증했다면 무시해도 됩니다.)")

        framed = bool(home.soup.find("frameset")) or (
            len(home.soup.find_all("iframe")) == 1 and len(home.text) < 100 and not home.internal_links)
        if framed:
            add("frame_forwarding", "naver", "프레임(포워딩) 구조", "fail", 5,
                "홈이 frameset 또는 전체 화면 iframe 하나로 구성되어 있습니다. 도메인 포워딩 구조로 보입니다.",
                "도메인을 실제 서버(쇼핑몰 솔루션)에 직접 연결하세요. 프레임 포워딩은 네이버·구글 모두 본문을 수집하지 못합니다.",
                [home.final_url])
        else:
            add("frame_forwarding", "naver", "프레임(포워딩) 구조", "pass", 5, "프레임 포워딩 구조가 아닙니다.")

    feeds = sorted({f for p in ok for f in p.feeds})
    if feeds:
        add("rss_feed", "naver", "RSS 피드 제공", "pass", 2, f"RSS/Atom 피드 {len(feeds)}개를 찾았습니다.", urls=feeds)
    elif ok:
        add("rss_feed", "naver", "RSS 피드 제공", "warn", 2, "페이지에서 RSS 피드 링크를 찾지 못했습니다.",
            "블로그·소식·상품 등 갱신되는 콘텐츠가 있다면 RSS를 만들어 서치어드바이저에 제출하세요. 새 글 수집이 빨라집니다. "
            "(<link rel=\"alternate\" type=\"application/rss+xml\">로 페이지에 명시)")

    if home:
        desc = meta(home, "description")
        if desc and len(desc) <= 80:
            add("naver_desc_len", "naver", "홈 설명문 길이 (네이버 노출 80자 이내)", "pass", 1,
                f"홈 meta description {len(desc)}자")
        elif desc:
            add("naver_desc_len", "naver", "홈 설명문 길이 (네이버 노출 80자 이내)", "warn", 1,
                f"홈 meta description이 {len(desc)}자입니다. 네이버 검색 결과에서는 뒷부분이 잘릴 수 있습니다.",
                "브랜드명과 핵심 서비스를 앞 80자 안에 담으세요.")

    col.out.extend(manual([
        ("m_naver_register", "naver", "서치어드바이저 사이트 등록·소유확인",
         "searchadvisor.naver.com에 사이트가 등록되고 소유확인이 완료됐는지 확인하세요."),
        ("m_naver_submit", "naver", "네이버 사이트맵·RSS 제출 및 수집 요청",
         "서치어드바이저의 '요청 > 사이트맵 제출/RSS 제출/웹 페이지 수집'에서 제출 상태와 수집 오류를 확인하세요."),
        ("m_naver_report", "naver", "서치어드바이저 웹마스터 도구 리포트 확인",
         "'검증 > 웹 페이지 최적화' 및 '리포트'에서 권고/오류 항목이 있는지 확인하세요."),
        ("m_naver_nap", "naver", "네이버 플레이스·블로그·스마트스토어 정보 일치",
         "상호명, 주소, 전화번호, 영업시간이 웹사이트와 네이버 플레이스/블로그/스마트스토어에서 일치하는지 확인하세요."),
        ("m_naver_brand", "naver", "네이버 브랜드 검색 결과 확인",
         "브랜드명 검색 시 공식 사이트·플레이스·블로그가 상단에 노출되는지, 부정적 결과는 없는지 확인하세요."),
    ]))
    return col.out
