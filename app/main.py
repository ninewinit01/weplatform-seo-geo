"""웹앱 (Starlette). 대시보드, 광고주/사이트 관리, 진단 실행, 리포트·공유 링크, CSV/JSON 내보내기."""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from starlette.applications import Starlette
from starlette.background import BackgroundTask, BackgroundTasks
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import config, db
from .audit.checks import CATEGORY_ENGINE, CATEGORY_LABELS, ENGINES, catalog
from .audit.crawler import normalize_url
from .audit.runner import run_audit
from .audit.scoring import CATEGORY_WEIGHTS, priority_key

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
KST = timezone(timedelta(hours=9))
STATUS_LABELS = {"pass": "통과", "warn": "개선 권장", "fail": "문제", "manual": "수동 확인", "na": "해당 없음"}
AUDIT_STATUS = {"done": "완료", "failed": "실패", "running": "진행 중", "queued": "대기"}
COOKIE = "sg_session"


def _kst(value: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).astimezone(KST).strftime(fmt)
    except ValueError:
        return value


def _score_class(score) -> str:
    if score is None:
        return "na"
    return "good" if score >= 80 else ("mid" if score >= 60 else "bad")


def _grade(score) -> str:
    if score is None:
        return "-"
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "E"


def _severity(weight: int) -> str:
    return "높음" if weight >= 4 else ("중간" if weight >= 2 else "낮음")


templates.env.filters["kst"] = _kst
templates.env.filters["score_class"] = _score_class
templates.env.filters["grade"] = _grade
templates.env.globals.update(
    CATEGORY_LABELS=CATEGORY_LABELS, STATUS_LABELS=STATUS_LABELS, AUDIT_STATUS=AUDIT_STATUS,
    ENGINES=ENGINES, CATEGORY_ENGINE=CATEGORY_ENGINE, CLIENT_STATUSES=db.CLIENT_STATUSES,
    INDUSTRIES=config.INDUSTRIES, BRAND=config.BRAND_NAME, APP_NAME=config.APP_NAME,
)


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    ctx = dict(ctx or {})
    ctx.setdefault("user", request.scope.get("user"))
    ctx.setdefault("nav", "")
    ctx.setdefault("error", request.query_params.get("error"))
    ctx.setdefault("notice", request.query_params.get("notice"))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str, error: str = "", notice: str = "") -> RedirectResponse:
    if error:
        url += ("&" if "?" in url else "?") + "error=" + quote(error)
    if notice:
        url += ("&" if "?" in url else "?") + "notice=" + quote(notice)
    return RedirectResponse(url, status_code=303)


def not_found(request: Request, what: str):
    return render(request, "message.html", {"title": "찾을 수 없음", "message": f"{what}을(를) 찾을 수 없습니다."},
                  status_code=404)


# ---------- 인증 (서명 쿠키) ----------

def _users() -> dict[str, str]:
    users = {}
    for pair in config.AUTH_USERS.split(","):
        if ":" in pair:
            u, _, p = pair.strip().partition(":")
            if u and p:
                users[u] = p
    if config.APP_PASSWORD:
        users.setdefault(config.APP_USER, config.APP_PASSWORD)
    return users


def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(user: str) -> str:
    payload = base64.urlsafe_b64encode(f"{user}|{int(time.time()) + config.SESSION_DAYS * 86400}".encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(value: str) -> str | None:
    try:
        payload, sig = value.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        user, _, exp = base64.urlsafe_b64decode(payload.encode()).decode().rpartition("|")
        if int(exp) < time.time() or user not in _users():
            return None
        return user
    except Exception:  # noqa: BLE001
        return None


PUBLIC_PREFIXES = ("/healthz", "/login", "/static/", "/r/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not _users():
            if config.REQUIRE_AUTH and not path.startswith(("/healthz", "/static/", "/r/")):
                return render(request, "message.html", {
                    "title": "관리자 계정 설정이 필요합니다", "public": True,
                    "message": "서버 환경변수 AUTH_USERS(예: weplatform:비밀번호)를 설정한 뒤 다시 접속하세요."},
                    status_code=503)
            request.scope["user"] = "local"
            return await call_next(request)
        if path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        user = read_session(request.cookies.get(COOKIE, ""))
        if user:
            request.scope["user"] = user
            return await call_next(request)
        if path.startswith("/api/") or request.method != "GET":
            return JSONResponse({"error": "login required"}, status_code=401)
        return RedirectResponse("/login?next=" + quote(path), status_code=303)


async def login(request: Request):
    nxt = request.query_params.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    if request.method == "GET":
        return render(request, "login.html", {"next": nxt})
    form = await request.form()
    user, pw = str(form.get("username", "")).strip(), str(form.get("password", ""))
    expected = _users().get(user)
    if expected is None or not secrets.compare_digest(pw, expected):
        return render(request, "login.html", {"next": nxt, "error": "아이디 또는 비밀번호가 올바르지 않습니다."},
                      status_code=401)
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(COOKIE, make_session(user), max_age=config.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=request.url.scheme == "https")
    return resp


async def logout(request: Request):
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ---------- 대시보드 ----------

async def dashboard(request: Request):
    stats = db.dashboard_stats()
    latest = stats.pop("latest")
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    engine_avg = {e: avg([es[e] for _, es in latest if e in es]) for e in ENGINES}
    dist = {"good": 0, "mid": 0, "bad": 0}
    for s, _ in latest:
        if s is not None:
            dist[_score_class(s)] += 1
    return render(request, "dashboard.html", {
        "nav": "dashboard", "stats": stats, "overall_avg": avg([s for s, _ in latest if s is not None]),
        "engine_avg": engine_avg, "dist": dist, "audited_sites": len(latest),
        "recent": db.recent_audits(8), "low": db.low_score_sites(6),
    })


# ---------- 광고주 ----------

def _client_fields(form) -> dict:
    status = str(form.get("status", "active"))
    industry = str(form.get("industry", "")).strip()
    return {
        "name": str(form.get("name", "")).strip()[:100],
        "memo": str(form.get("memo", "")).strip()[:500],
        "industry": industry if industry in config.INDUSTRIES else "",
        "manager": str(form.get("manager", "")).strip()[:50],
        "contact": str(form.get("contact", "")).strip()[:120],
        "status": status if status in db.CLIENT_STATUSES else "active",
    }


async def clients_page(request: Request):
    qp = request.query_params
    q, industry, status = qp.get("q", "").strip(), qp.get("industry", ""), qp.get("status", "")
    return render(request, "clients.html", {
        "nav": "clients", "clients": db.list_clients(q, industry, status),
        "q": q, "industry": industry, "status": status,
    })


async def create_client(request: Request):
    fields = _client_fields(await request.form())
    if not fields["name"]:
        return redirect("/clients", error="광고주명을 입력하세요")
    cid = db.create_client(**fields)
    return redirect(f"/clients/{cid}", notice="광고주를 추가했습니다. 사이트 URL을 등록하세요.")


async def client_detail(request: Request):
    cid = request.path_params["client_id"]
    client = db.get_client(cid)
    if not client:
        return not_found(request, "광고주")
    sites = db.list_sites(cid)
    for s in sites:
        s["history"] = [h for h in reversed(db.list_audits(s["id"], 8)) if h["status"] == "done"]
    return render(request, "client.html", {
        "nav": "clients", "client": client, "sites": sites,
        "default_pages": config.DEFAULT_MAX_PAGES, "max_pages": config.MAX_PAGES_LIMIT,
    })


async def edit_client(request: Request):
    cid = request.path_params["client_id"]
    if not db.get_client(cid):
        return not_found(request, "광고주")
    fields = _client_fields(await request.form())
    if not fields["name"]:
        return redirect(f"/clients/{cid}", error="광고주명을 입력하세요")
    db.update_client(cid, **fields)
    return redirect(f"/clients/{cid}", notice="광고주 정보를 저장했습니다.")


async def delete_client(request: Request):
    db.delete_client(request.path_params["client_id"])
    return redirect("/clients", notice="광고주를 삭제했습니다.")


def _clean_url(raw: str) -> str | None:
    if not raw:
        return None
    url = normalize_url(raw)
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or " " in url:
        return None
    if "." not in p.hostname and p.hostname != "localhost":
        return None
    return url[:500]


def _pages(form) -> int:
    try:
        pages = int(form.get("max_pages") or config.DEFAULT_MAX_PAGES)
    except ValueError:
        pages = config.DEFAULT_MAX_PAGES
    return max(1, min(pages, config.MAX_PAGES_LIMIT))


async def add_site(request: Request):
    cid = request.path_params["client_id"]
    if not db.get_client(cid):
        return not_found(request, "광고주")
    form = await request.form()
    raw = str(form.get("url", "")).strip()
    if not raw:
        return redirect(f"/clients/{cid}", error="URL을 입력하세요")
    url = _clean_url(raw)
    if not url:
        return redirect(f"/clients/{cid}", error="올바른 URL이 아닙니다")
    sid = db.add_site(cid, url, str(form.get("label", "")).strip()[:100], str(form.get("keywords", "")).strip()[:300])
    if form.get("run_now"):
        audit_id = db.create_audit(sid, _pages(form))
        return RedirectResponse(f"/audits/{audit_id}", status_code=303, background=BackgroundTask(run_audit, audit_id))
    return redirect(f"/clients/{cid}", notice="사이트를 추가했습니다.")


async def audit_all(request: Request):
    cid = request.path_params["client_id"]
    if not db.get_client(cid):
        return not_found(request, "광고주")
    form = await request.form()
    sites = db.list_sites(cid)
    if not sites:
        return redirect(f"/clients/{cid}", error="등록된 사이트가 없습니다")
    tasks = BackgroundTasks()
    for s in sites:
        if s["last_status"] in ("queued", "running"):
            continue
        tasks.add_task(run_audit, db.create_audit(s["id"], _pages(form)))
    resp = redirect(f"/clients/{cid}", notice=f"사이트 {len(tasks.tasks)}개 진단을 시작했습니다. 잠시 후 새로고침하세요.")
    resp.background = tasks
    return resp


# ---------- 사이트 ----------

async def edit_site(request: Request):
    site = db.get_site(request.path_params["site_id"])
    if not site:
        return not_found(request, "사이트")
    form = await request.form()
    url = _clean_url(str(form.get("url", "")).strip())
    if not url:
        return redirect(f"/clients/{site['client_id']}", error="올바른 URL이 아닙니다")
    db.update_site(site["id"], url=url, label=str(form.get("label", "")).strip()[:100],
                   keywords=str(form.get("keywords", "")).strip()[:300])
    return redirect(f"/clients/{site['client_id']}", notice="사이트 정보를 저장했습니다.")


async def delete_site(request: Request):
    site = db.get_site(request.path_params["site_id"])
    if not site:
        return not_found(request, "사이트")
    db.delete_site(site["id"])
    return redirect(f"/clients/{site['client_id']}", notice="사이트를 삭제했습니다.")


async def start_audit(request: Request):
    site = db.get_site(request.path_params["site_id"])
    if not site:
        return not_found(request, "사이트")
    audit_id = db.create_audit(site["id"], _pages(await request.form()))
    return RedirectResponse(f"/audits/{audit_id}", status_code=303, background=BackgroundTask(run_audit, audit_id))


# ---------- 진단 / 리포트 ----------

async def audits_page(request: Request):
    status = request.query_params.get("status", "")
    return render(request, "audits.html", {
        "nav": "audits", "audits": db.recent_audits(200, status if status in AUDIT_STATUS else ""), "status": status,
    })


def _load_report(audit: dict | None):
    if not audit:
        return None
    site = db.get_site(audit["site_id"])
    client = db.get_client(site["client_id"]) if site else None
    findings = sorted(db.get_findings(audit["id"]), key=priority_key)
    return audit, site, client, findings


def _report_ctx(audit: dict, site, client, findings, public: bool) -> dict:
    for f in findings:
        f["severity"] = _severity(f["weight"])
        f["engine"] = CATEGORY_ENGINE.get(f["category"], "seo")
    todo = [f for f in findings if f["status"] in ("fail", "warn")]
    manual = [f for f in findings if f["status"] == "manual"]
    passed = [f for f in findings if f["status"] in ("pass", "na")]
    prev = db.previous_done_audit(site["id"], audit["id"]) if site else None
    delta = (round(audit["score"] - prev["score"], 1)
             if prev and audit["score"] is not None and prev["score"] is not None else None)
    engine_delta = {}
    if prev:
        for e, s in audit["engine_scores"].items():
            if e in prev["engine_scores"]:
                engine_delta[e] = round(s - prev["engine_scores"][e], 1)
    cats = [(c, CATEGORY_LABELS.get(c, c), audit["category_scores"][c], CATEGORY_WEIGHTS.get(c, 10))
            for c in CATEGORY_LABELS if c in audit["category_scores"]]
    engine_counts = {e: {"fail": 0, "warn": 0} for e in ENGINES}
    for f in todo:
        engine_counts[f["engine"]][f["status"]] += 1
    history = db.list_audits(site["id"], 12) if site else []
    return {
        "audit": audit, "site": site, "client": client, "todo": todo, "manual": manual, "passed": passed,
        "cats": cats, "prev": prev, "delta": delta, "engine_delta": engine_delta, "engine_counts": engine_counts,
        "quick_wins": [f for f in todo if f["weight"] >= 3][:5],
        "history": history, "trend": [h for h in reversed(history) if h["status"] == "done"],
        "pages": db.get_pages(audit["id"]),
        "running": audit["status"] in ("queued", "running"),
        "counts": {"fail": sum(f["status"] == "fail" for f in findings),
                   "warn": sum(f["status"] == "warn" for f in findings),
                   "pass": sum(f["status"] == "pass" for f in findings)},
        "public": public, "nav": "audits",
    }


async def audit_report(request: Request):
    loaded = _load_report(db.get_audit(request.path_params["audit_id"]))
    if not loaded:
        return not_found(request, "진단 결과")
    ctx = _report_ctx(*loaded, public=False)
    if ctx["audit"].get("share_token"):
        ctx["share_url"] = str(request.base_url).rstrip("/") + "/r/" + ctx["audit"]["share_token"]
    return render(request, "report.html", ctx)


async def shared_report(request: Request):
    audit = db.get_audit_by_token(request.path_params["token"])
    if not audit or audit["status"] != "done":
        return render(request, "message.html", {"title": "리포트를 열 수 없습니다",
                                                 "message": "공유가 해제되었거나 잘못된 링크입니다.", "public": True},
                      status_code=404)
    ctx = _report_ctx(*_load_report(audit), public=True)
    ctx["history"] = []
    return render(request, "report.html", ctx)


async def share_audit(request: Request):
    audit = db.get_audit(request.path_params["audit_id"])
    if not audit:
        return not_found(request, "진단 결과")
    if audit["status"] != "done":
        return redirect(f"/audits/{audit['id']}", error="완료된 진단만 공유할 수 있습니다")
    db.ensure_share_token(audit["id"])
    return redirect(f"/audits/{audit['id']}", notice="외부 공유 링크를 만들었습니다. 로그인 없이 열람 가능합니다.")


async def unshare_audit(request: Request):
    audit = db.get_audit(request.path_params["audit_id"])
    if not audit:
        return not_found(request, "진단 결과")
    db.revoke_share_token(audit["id"])
    return redirect(f"/audits/{audit['id']}", notice="공유 링크를 해제했습니다.")


async def toggle_finding(request: Request):
    data = await request.json()
    r = db.set_finding_checked(request.path_params["finding_id"], bool(data.get("checked")))
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(r)


async def audit_csv(request: Request):
    loaded = _load_report(db.get_audit(request.path_params["audit_id"]))
    if not loaded:
        return not_found(request, "진단 결과")
    audit, site, client, findings = loaded
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["엔진", "분류", "항목", "상태", "중요도", "설명", "개선 방법", "대상 URL(최대 10개)"])
    for f in findings:
        w.writerow([ENGINES[CATEGORY_ENGINE.get(f["category"], "seo")]["label"],
                    CATEGORY_LABELS.get(f["category"], f["category"]), f["title"],
                    STATUS_LABELS[f["status"]] + (" (확인 완료)" if f["status"] == "manual" and f["checked"] else ""),
                    _severity(f["weight"]) if f["weight"] else "-", f["message"], f["fix"],
                    "\n".join(f["urls"][:10])])
    name = f"audit-{audit['id']}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def audit_api(request: Request):
    loaded = _load_report(db.get_audit(request.path_params["audit_id"]))
    if not loaded:
        return JSONResponse({"error": "not found"}, status_code=404)
    audit, site, client, findings = loaded
    return JSONResponse({"audit": {k: v for k, v in audit.items() if k != "share_token"},
                         "site": site, "client": client, "findings": findings})


async def criteria(request: Request):
    items = catalog()
    return render(request, "criteria.html", {"nav": "criteria", "items": items, "weights": CATEGORY_WEIGHTS})


async def settings_page(request: Request):
    stats = db.dashboard_stats()
    return render(request, "settings.html", {"nav": "settings", "stats": stats,
                                             "users": sorted(_users()), "db_path": config.DB_PATH})


async def backup(request: Request):
    data = json.dumps(db.export_all(), ensure_ascii=False)
    name = f"seo-geo-backup-{datetime.now(KST):%Y%m%d-%H%M}.json"
    return Response(data, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def restore(request: Request):
    form = await request.form()
    upload = form.get("file")
    if not upload or not hasattr(upload, "read"):
        return redirect("/settings", error="백업 파일을 선택하세요")
    raw = await upload.read()
    if len(raw) > 50_000_000:
        return redirect("/settings", error="파일이 너무 큽니다 (최대 50MB)")
    try:
        counts = db.import_all(json.loads(raw.decode("utf-8-sig")))
    except (ValueError, UnicodeDecodeError) as e:
        return redirect("/settings", error=f"복원 실패: {e}")
    return redirect("/settings", notice=f"복원 완료: 광고주 {counts['clients']}곳, 사이트 {counts['sites']}개, 진단 {counts['audits']}건")


async def healthz(request: Request):
    return PlainTextResponse("ok")


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


routes = [
    Route("/", dashboard),
    Route("/healthz", healthz),
    Route("/login", login, methods=["GET", "POST"]),
    Route("/logout", logout, methods=["GET", "POST"]),
    Route("/clients", clients_page),
    Route("/clients", create_client, methods=["POST"]),
    Route("/clients/{client_id:int}", client_detail),
    Route("/clients/{client_id:int}/edit", edit_client, methods=["POST"]),
    Route("/clients/{client_id:int}/delete", delete_client, methods=["POST"]),
    Route("/clients/{client_id:int}/sites", add_site, methods=["POST"]),
    Route("/clients/{client_id:int}/audit-all", audit_all, methods=["POST"]),
    Route("/sites/{site_id:int}/edit", edit_site, methods=["POST"]),
    Route("/sites/{site_id:int}/delete", delete_site, methods=["POST"]),
    Route("/sites/{site_id:int}/audit", start_audit, methods=["POST"]),
    Route("/audits", audits_page),
    Route("/audits/{audit_id:int}", audit_report),
    Route("/audits/{audit_id:int}/export.csv", audit_csv),
    Route("/audits/{audit_id:int}/share", share_audit, methods=["POST"]),
    Route("/audits/{audit_id:int}/unshare", unshare_audit, methods=["POST"]),
    Route("/findings/{finding_id:int}/check", toggle_finding, methods=["POST"]),
    Route("/r/{token}", shared_report),
    Route("/criteria", criteria),
    Route("/settings", settings_page),
    Route("/settings/backup.json", backup),
    Route("/settings/restore", restore, methods=["POST"]),
    Route("/api/audits/{audit_id:int}", audit_api),
    Mount("/static", StaticFiles(directory=str(BASE / "static")), name="static"),
]

app = Starlette(routes=routes, middleware=[Middleware(AuthMiddleware)], lifespan=lifespan)
