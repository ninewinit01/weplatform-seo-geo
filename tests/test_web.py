import socket
import threading
import time

import pytest
import requests
import uvicorn

from app import config, db
from app.main import app


@pytest.fixture(scope="module")
def server():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def wait_done(base, audit_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        a = requests.get(f"{base}/api/audits/{audit_id}").json()["audit"]
        if a["status"] in ("done", "failed"):
            return a
        time.sleep(0.2)
    raise AssertionError("audit timeout")


def test_full_flow(server, good_site):
    r = requests.post(f"{server}/clients", data={"name": "테스트광고주", "memo": "메모"}, allow_redirects=False)
    assert r.status_code == 303
    cid = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])

    assert "테스트광고주" in requests.get(server + "/clients").text
    assert "테스트광고주" not in requests.get(server + "/clients?q=없는이름").text

    # 잘못된 URL / 정상 URL
    r = requests.post(f"{server}/clients/{cid}/sites", data={"url": "ht tp://bad url"}, allow_redirects=False)
    assert "error=" in r.headers["location"]
    r = requests.post(f"{server}/clients/{cid}/sites", data={"url": good_site, "label": "공식몰"}, allow_redirects=False)
    assert r.status_code == 303
    site = db.list_sites(cid)[0]

    r = requests.post(f"{server}/sites/{site['id']}/audit", data={"max_pages": "20"}, allow_redirects=False)
    assert r.status_code == 303
    audit_id = int(r.headers["location"].rsplit("/", 1)[-1])

    a = wait_done(server, audit_id)
    assert a["status"] == "done", a
    assert 0 < a["score"] <= 100 and a["pages_crawled"] >= 4
    assert set(a["category_scores"]) >= {"technical", "geo", "naver", "structured"}

    html = requests.get(f"{server}/audits/{audit_id}").text
    assert "우선 개선 항목" in html and "오류 페이지" in html and "수동 확인 항목" in html
    assert "종합 점수" in html

    csv_resp = requests.get(f"{server}/audits/{audit_id}/export.csv")
    assert csv_resp.status_code == 200 and csv_resp.content.startswith(b"\xef\xbb\xbf")
    assert "개선 방법" in csv_resp.text and "오류 페이지" in csv_resp.text

    # 두 번째 진단 → 이전 대비 점수 변화 표시
    r = requests.post(f"{server}/sites/{site['id']}/audit", data={"max_pages": "20"}, allow_redirects=False)
    second = int(r.headers["location"].rsplit("/", 1)[-1])
    assert wait_done(server, second)["status"] == "done"
    assert "이전 진단" in requests.get(f"{server}/audits/{second}").text

    assert "공식몰" in requests.get(f"{server}/clients/{cid}").text
    assert "SEO" in html and "GEO" in html and "네이버" in html and "페이지별 현황" in html

    # 외부 공유 링크: 만들기 → 로그인 없이 열람 → 해제
    requests.post(f"{server}/audits/{second}/share", allow_redirects=False)
    token = db.get_audit(second)["share_token"]
    assert token
    shared = requests.get(f"{server}/r/{token}")
    assert shared.status_code == 200 and "종합 점수" in shared.text and "/logout" not in shared.text
    assert "공유 링크" in requests.get(f"{server}/audits/{second}").text
    requests.post(f"{server}/audits/{second}/unshare", allow_redirects=False)
    assert requests.get(f"{server}/r/{token}").status_code == 404

    # 수동 확인 체크
    manual = next(f for f in db.get_findings(second) if f["status"] == "manual")
    r = requests.post(f"{server}/findings/{manual['id']}/check", json={"checked": True})
    assert r.status_code == 200 and r.json()["checked"] == 1

    # 대시보드 · 진단 목록 · 기준
    assert "평균 종합 점수" in requests.get(server + "/").text
    assert "테스트광고주" in requests.get(server + "/audits").text
    assert "페이지 단위 자동 점검" in requests.get(server + "/criteria").text


def test_client_edit_and_audit_all(server, good_site):
    cid = db.create_client("수정전")
    r = requests.post(f"{server}/clients/{cid}/edit", data={"name": "수정후", "industry": "뷰티", "status": "prospect",
                                                           "manager": "홍길동"}, allow_redirects=False)
    assert r.status_code == 303
    c = db.get_client(cid)
    assert (c["name"], c["industry"], c["status"], c["manager"]) == ("수정후", "뷰티", "prospect", "홍길동")
    assert requests.get(f"{server}/clients?industry=뷰티").text.count("수정후") >= 1

    db.add_site(cid, good_site, "A")
    db.add_site(cid, good_site + "/about", "B")
    requests.post(f"{server}/clients/{cid}/audit-all", data={"max_pages": "3"}, allow_redirects=False)
    sites = db.list_sites(cid)
    assert all(s["last_audit_id"] for s in sites)
    for s in sites:
        assert wait_done(server, s["last_audit_id"])["status"] == "done"


def test_failed_audit_reports_reason(server):
    cid = db.create_client("실패케이스")
    sid = db.add_site(cid, "http://127.0.0.1:1/")  # 연결 불가 포트
    r = requests.post(f"{server}/sites/{sid}/audit", data={"max_pages": "5"}, allow_redirects=False)
    audit_id = int(r.headers["location"].rsplit("/", 1)[-1])
    a = wait_done(server, audit_id)
    assert a["status"] == "failed" and a["error"]
    assert "진단 실패" in requests.get(f"{server}/audits/{audit_id}").text


def test_login(server, monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "pw123")
    r = requests.get(server + "/", allow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert requests.get(server + "/api/audits/1").status_code == 401
    assert requests.post(server + "/clients", data={"name": "x"}, allow_redirects=False).status_code == 401
    assert requests.get(server + "/healthz").status_code == 200
    assert requests.get(server + "/login").status_code == 200
    assert requests.get(server + "/static/style.css").status_code == 200

    s = requests.Session()
    bad = s.post(server + "/login", data={"username": config.APP_USER, "password": "wrong"}, allow_redirects=False)
    assert bad.status_code == 401 and "sg_session" not in s.cookies
    ok = s.post(server + "/login?next=/clients", data={"username": config.APP_USER, "password": "pw123"},
                allow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/clients"
    assert s.get(server + "/").status_code == 200
    # 외부 리다이렉트 방지
    ok2 = s.post(server + "/login?next=//evil.example", data={"username": config.APP_USER, "password": "pw123"},
                 allow_redirects=False)
    assert ok2.headers["location"] == "/"
    s.get(server + "/logout")
    assert s.get(server + "/", allow_redirects=False).status_code == 303


def test_multi_users(server, monkeypatch):
    monkeypatch.setattr(config, "AUTH_USERS", "kim:a1,lee:b2")
    s = requests.Session()
    assert s.post(server + "/login", data={"username": "lee", "password": "b2"}, allow_redirects=False).status_code == 303
    assert "lee" in s.get(server + "/").text


def test_not_found(server):
    assert requests.get(server + "/audits/99999").status_code == 404
    assert requests.get(server + "/clients/99999").status_code == 404


def test_backup_restore(server):
    cid = db.create_client("백업대상")
    data = requests.get(server + "/settings/backup.json").json()
    assert any(c["name"] == "백업대상" for c in data["tables"]["clients"])
    db.delete_client(cid)
    assert not any(c["name"] == "백업대상" for c in db.list_clients())
    import json as _json
    r = requests.post(server + "/settings/restore",
                      files={"file": ("b.json", _json.dumps(data).encode(), "application/json")}, allow_redirects=False)
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    assert any(c["name"] == "백업대상" for c in db.list_clients())
    bad = requests.post(server + "/settings/restore", files={"file": ("b.json", b"{}", "application/json")},
                        allow_redirects=False)
    assert "error=" in bad.headers["location"]
    assert "백업 파일 받기" in requests.get(server + "/settings").text


def test_require_auth_locks_public_server(server, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_AUTH", True)
    r = requests.get(server + "/")
    assert r.status_code == 503 and "AUTH_USERS" in r.text
    assert requests.get(server + "/healthz").status_code == 200
