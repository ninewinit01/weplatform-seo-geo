"""SEO 엔진: 기술 SEO · 콘텐츠 · 구조화 데이터 (구글 검색 기준)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..crawler import CrawlResult, Page
from .base import (Collector, PageCheck, Result, as_list, home_page, manual, meta, ok_pages,
                   page_url, title_of)


# ---------- 페이지 단위 ----------

def c_title(p):
    return 10 <= len(title_of(p)) <= 60


def c_description(p):
    return 40 <= len(meta(p, "description")) <= 160


def c_h1(p):
    return len(p.soup.find_all("h1")) == 1


def c_canonical(p):
    tag = p.soup.find("link", rel="canonical")
    return bool(tag and (tag.get("href") or "").strip())


def c_noindex(p):
    robots = meta(p, "robots").lower() + " " + meta(p, "googlebot").lower()
    header = p.headers.get("x-robots-tag", "").lower()
    return "noindex" not in robots and "noindex" not in header


def c_img_alt(p):
    imgs = p.soup.find_all("img")
    if not imgs:
        return None
    return all(img.has_attr("alt") for img in imgs)


def c_lang(p):
    html = p.soup.find("html")
    return bool(html and (html.get("lang") or "").strip())


def c_viewport(p):
    return bool(meta(p, "viewport"))


def c_slow(p):
    return p.elapsed_ms <= 2500


def c_mixed(p):
    if urlparse(page_url(p)).scheme != "https":
        return None
    return not p.insecure_resources


def c_url_hygiene(p):
    u = urlparse(page_url(p))
    return len(page_url(p)) <= 115 and u.query.count("&") < 3


def c_thin(p):
    return len(p.text) >= 300


def c_heading_order(p):
    levels = [int(h.name[1]) for h in p.soup.find_all(re.compile(r"^h[1-6]$"))]
    if len(levels) < 2:
        return None
    return all(b - a <= 1 for a, b in zip(levels, levels[1:]))


def c_jsonld_valid(p):
    if not p.soup.find("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        return None
    return p.jsonld_errors == 0


PAGE_CHECKS: list[PageCheck] = [
    PageCheck("title", "technical", "title 태그 (10~60자)", 4, c_title,
              "페이지마다 고유한 title을 10~60자로 작성하고 핵심 키워드를 앞쪽에 배치하세요."),
    PageCheck("meta_description", "technical", "meta description (40~160자)", 3, c_description,
              "페이지 내용을 요약하는 meta description을 40~160자로 작성하세요. 검색 결과 스니펫과 클릭률에 영향을 줍니다."),
    PageCheck("h1", "technical", "H1 태그 1개", 3, c_h1,
              "페이지당 H1을 정확히 1개 두고 페이지 주제를 담으세요."),
    PageCheck("canonical", "technical", "canonical 태그", 2, c_canonical,
              "중복 URL 방지를 위해 <link rel=\"canonical\">로 대표 URL을 지정하세요."),
    PageCheck("noindex", "technical", "의도치 않은 noindex", 8, c_noindex,
              "검색 노출이 필요한 페이지에 noindex(메타 robots 또는 X-Robots-Tag)가 걸려 있습니다. 즉시 제거를 검토하세요.",
              strict=True),
    PageCheck("img_alt", "technical", "이미지 alt 속성", 2, c_img_alt,
              "모든 이미지에 alt를 작성하세요(장식용은 alt=\"\"). 이미지 검색과 접근성에 필요합니다."),
    PageCheck("lang", "technical", "html lang 속성", 1, c_lang,
              "<html lang=\"ko\">처럼 문서 언어를 지정하세요."),
    PageCheck("viewport", "technical", "모바일 viewport 설정", 3, c_viewport,
              "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">을 추가하세요. 모바일 우선 색인에 필요합니다."),
    PageCheck("slow_response", "technical", "서버 응답 속도 (2.5초 이내)", 2, c_slow,
              "서버 응답/전송이 느립니다. 캐시, CDN, 이미지 최적화를 검토하고 PageSpeed Insights로 상세 진단하세요."),
    PageCheck("mixed_content", "technical", "혼합 콘텐츠 (https 페이지의 http 리소스)", 2, c_mixed,
              "https 페이지에서 http:// 이미지·스크립트·스타일을 불러옵니다. 브라우저 경고·차단 원인이므로 모두 https로 바꾸세요."),
    PageCheck("url_hygiene", "technical", "URL 구조 (짧고 읽기 쉬운 주소)", 1, c_url_hygiene,
              "URL이 너무 길거나 파라미터가 많습니다. 의미 있는 짧은 경로(/category/product-name)를 사용하세요."),
    PageCheck("thin_content", "content", "본문 분량 (300자 이상)", 3, c_thin,
              "본문이 매우 짧은 페이지입니다. 검색 의도에 답하는 내용을 보강하거나 통합/noindex를 검토하세요."),
    PageCheck("heading_order", "content", "제목 계층 구조(H1→H2→H3)", 1, c_heading_order,
              "제목 레벨을 건너뛰지 않도록 정리하세요(H2 다음에 바로 H4 등). AI와 검색엔진이 구조를 파악하는 데 도움이 됩니다."),
    PageCheck("jsonld_valid", "structured", "JSON-LD 문법 유효성", 2, c_jsonld_valid,
              "JSON-LD가 올바른 JSON이 아닙니다. 구조화 데이터 테스트 도구로 검증하세요.", strict=True),
]


# ---------- 사이트 단위 ----------

ORG_TYPES = {"Organization", "LocalBusiness", "Corporation", "OnlineStore", "Store",
             "MedicalClinic", "Restaurant", "EducationalOrganization"}


def org_objects(res: CrawlResult) -> list[dict]:
    objs = [o for p in ok_pages(res) for o in p.jsonld_objects]
    return [o for o in objs if ORG_TYPES & set(as_list(o.get("@type")))]


def site_checks(res: CrawlResult) -> list[Result]:
    col = Collector()
    add = col.add
    ok = ok_pages(res)
    home = home_page(res)

    if res.final_scheme == "https":
        add("https", "technical", "HTTPS 적용", "pass", 4, "HTTPS로 접속됩니다.")
    else:
        add("https", "technical", "HTTPS 적용", "fail", 4, "HTTP로 접속됩니다.",
            "SSL 인증서를 적용하고 http→https 301 리다이렉트를 설정하세요. 구글·네이버 모두 HTTPS를 선호합니다.")

    if res.http_redirects_to_https is True:
        add("http_redirect", "technical", "http → https 리다이렉트", "pass", 2, "http:// 접속 시 https로 이동합니다.")
    elif res.http_redirects_to_https is False and res.final_scheme == "https":
        add("http_redirect", "technical", "http → https 리다이렉트", "warn", 2,
            "http:// 주소로 접속해도 https로 이동하지 않습니다.",
            "http 요청을 https로 301 리다이렉트하세요. 같은 페이지가 두 주소로 나뉘어 색인되는 것을 막습니다.")

    if res.robots_status == 200:
        add("robots_txt", "technical", "robots.txt 존재", "pass", 1, "robots.txt가 있습니다.")
    else:
        detail = res.robots_error or f"응답 코드 {res.robots_status}"
        add("robots_txt", "technical", "robots.txt 존재", "warn", 1,
            f"robots.txt를 읽지 못했습니다({detail}). 모든 크롤러 허용으로 해석됩니다.",
            "루트에 robots.txt를 만들고 Sitemap 경로를 명시하세요.")

    if res.can_fetch("Googlebot"):
        add("googlebot_access", "technical", "Googlebot 접근 허용", "pass", 8, "Googlebot이 홈을 수집할 수 있습니다.")
    else:
        add("googlebot_access", "technical", "Googlebot 접근 허용", "fail", 8, "robots.txt가 Googlebot의 접근을 막고 있습니다.",
            "robots.txt의 Disallow 규칙을 점검하세요. 사이트 전체 차단이라면 구글 검색에 노출되지 않습니다.")

    if res.sitemap_found and res.sitemap_urls:
        add("sitemap", "technical", "XML 사이트맵", "pass", 3, f"사이트맵에서 {len(res.sitemap_urls)}개 URL을 확인했습니다.")
    else:
        add("sitemap", "technical", "XML 사이트맵", "fail", 3, "사이트맵(sitemap.xml)을 찾지 못했거나 URL이 없습니다.",
            "sitemap.xml을 만들고 Search Console과 네이버 서치어드바이저에 제출하세요.")

    if res.robots_sitemaps:
        add("sitemap_in_robots", "technical", "robots.txt에 Sitemap 명시", "pass", 1, "robots.txt에 Sitemap이 명시되어 있습니다.")
    else:
        add("sitemap_in_robots", "technical", "robots.txt에 Sitemap 명시", "warn", 1, "robots.txt에 Sitemap 줄이 없습니다.",
            "robots.txt 끝에 'Sitemap: https://도메인/sitemap.xml'을 추가하세요.")

    broken = [p for p in res.pages if p.status == 0 or p.status >= 400]
    if broken:
        urls = [f"{page_url(p)} ({p.status or p.error or '오류'})" for p in broken]
        add("broken_pages", "technical", "오류 페이지(4xx/5xx)", "fail", 4,
            f"크롤링 중 {len(broken)}개 페이지에서 오류 응답을 받았습니다.",
            "깨진 링크를 수정하거나 301 리다이렉트로 대체 페이지에 연결하세요.", urls)
    else:
        add("broken_pages", "technical", "오류 페이지(4xx/5xx)", "pass", 4, "오류 응답 페이지가 없습니다.")

    if home and len(home.text) < 200 and home.script_count >= 5:
        add("js_dependency", "technical", "JS 렌더링 의존", "warn", 3,
            "홈 HTML에 텍스트가 거의 없고 스크립트가 많습니다. 본문을 JS가 그리는 구조일 수 있습니다.",
            "SSR/프리렌더링을 적용하세요. AI 크롤러 상당수는 JS를 실행하지 않아 본문을 못 읽을 수 있습니다. "
            "(이 도구는 정적 HTML 기준으로 진단하므로 다른 항목 결과도 과소평가될 수 있습니다.)",
            [home.final_url])
    elif home:
        add("js_dependency", "technical", "JS 렌더링 의존", "pass", 3, "홈 HTML에서 본문 텍스트를 읽을 수 있습니다.")

    # --- 콘텐츠: 중복 title / description ---
    for cid, label, getter in (("duplicate_titles", "중복 title", title_of),
                               ("duplicate_descriptions", "중복 meta description", lambda p: meta(p, "description"))):
        groups: dict[str, list[str]] = {}
        for p in ok:
            v = getter(p)
            if v:
                groups.setdefault(v, []).append(page_url(p))
        dups = {t: u for t, u in groups.items() if len(u) > 1}
        weight = 2 if cid == "duplicate_titles" else 1
        if dups:
            add(cid, "content", label, "warn", weight,
                f"동일한 값을 쓰는 페이지 그룹이 {len(dups)}개 있습니다.",
                "페이지마다 고유한 값을 쓰세요. 중복은 어떤 페이지가 노출돼야 하는지 혼란을 줍니다.",
                [u for g in dups.values() for u in g])
        elif len(ok) > 1:
            add(cid, "content", label, "pass", weight, "중복이 없습니다.")

    # --- 구조화 데이터 ---
    all_types = set().union(*(p.jsonld_types for p in ok)) if ok else set()
    if all_types:
        add("jsonld_any", "structured", "구조화 데이터(JSON-LD) 사용", "pass", 3,
            "발견된 타입: " + ", ".join(sorted(all_types)[:12]))
    else:
        add("jsonld_any", "structured", "구조화 데이터(JSON-LD) 사용", "fail", 3, "JSON-LD가 발견되지 않았습니다.",
            "Organization, WebSite, Article, FAQPage, Product 등 업종에 맞는 Schema.org JSON-LD를 추가하세요.")

    orgs = org_objects(res)
    if orgs:
        add("org_schema", "structured", "조직/브랜드 스키마", "pass", 2, "Organization 계열 스키마가 있습니다.")
    else:
        add("org_schema", "structured", "조직/브랜드 스키마", "warn", 2, "Organization/LocalBusiness 스키마가 없습니다.",
            "브랜드 정보를 Organization(또는 LocalBusiness) JSON-LD로 명시하세요. 검색엔진·AI가 브랜드 엔티티를 인식하는 데 도움이 됩니다.")

    if any(o.get("sameAs") for o in orgs):
        add("same_as", "structured", "sameAs (외부 프로필 연결)", "pass", 1, "sameAs로 외부 프로필이 연결되어 있습니다.")
    else:
        add("same_as", "structured", "sameAs (외부 프로필 연결)", "warn", 1, "Organization에 sameAs가 없습니다.",
            "공식 SNS, 네이버 플레이스/블로그, 위키 등 외부 프로필 URL을 sameAs로 연결하세요.")

    if "FAQPage" in all_types:
        add("faq_schema", "structured", "FAQPage 스키마", "pass", 2, "FAQPage 스키마가 있습니다.")
    else:
        add("faq_schema", "structured", "FAQPage 스키마", "warn", 2, "FAQPage 스키마가 없습니다.",
            "자주 묻는 질문 콘텐츠가 있다면 FAQPage JSON-LD를 적용하세요. (구글의 FAQ 리치결과 노출은 제한됐지만 AI의 이해에는 여전히 유용합니다.)")

    if all_types & {"BreadcrumbList"}:
        add("breadcrumb_schema", "structured", "BreadcrumbList 스키마", "pass", 1, "탐색 경로 스키마가 있습니다.")
    elif len(ok) > 3:
        add("breadcrumb_schema", "structured", "BreadcrumbList 스키마", "warn", 1, "BreadcrumbList 스키마가 없습니다.",
            "하위 페이지에 BreadcrumbList JSON-LD를 넣으면 검색 결과에 경로가 표시되고 사이트 구조 이해에 도움이 됩니다.")

    col.out.extend(manual([
        ("m_google_gsc", "technical", "Search Console 색인·보안 문제 확인",
         "Search Console에서 색인 생성 오류, 수동 조치, 보안 문제, Core Web Vitals 상태를 확인하세요."),
        ("m_google_psi", "technical", "PageSpeed Insights 모바일 성능",
         "pagespeed.web.dev에서 홈과 주요 상품/서비스 페이지의 모바일 LCP·INP·CLS를 확인하세요."),
    ]))
    return col.out
