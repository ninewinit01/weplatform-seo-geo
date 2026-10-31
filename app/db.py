"""SQLite 저장소 (표준 라이브러리). 광고주 수백 곳 규모까지 충분합니다."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    status TEXT NOT NULL,              -- queued / running / done / failed
    max_pages INTEGER NOT NULL,
    pages_crawled INTEGER NOT NULL DEFAULT 0,
    score REAL,
    category_scores TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    check_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,              -- pass / warn / fail / manual / na
    weight INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    fix TEXT NOT NULL DEFAULT '',
    urls TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    status INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    chars INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    issues INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sites_client ON sites(client_id);
CREATE INDEX IF NOT EXISTS idx_audits_site ON audits(site_id);
CREATE INDEX IF NOT EXISTS idx_findings_audit ON findings(audit_id);
CREATE INDEX IF NOT EXISTS idx_pages_audit ON pages(audit_id);
"""

# 기존 DB에 없을 수 있는 컬럼 (가벼운 마이그레이션)
MIGRATIONS = {
    "clients": [("industry", "TEXT NOT NULL DEFAULT ''"), ("manager", "TEXT NOT NULL DEFAULT ''"),
                ("contact", "TEXT NOT NULL DEFAULT ''"), ("status", "TEXT NOT NULL DEFAULT 'active'")],
    "sites": [("keywords", "TEXT NOT NULL DEFAULT ''")],
    "audits": [("engine_scores", "TEXT NOT NULL DEFAULT '{}'"), ("share_token", "TEXT"),
               ("notes", "TEXT NOT NULL DEFAULT '[]'")],
    "findings": [("checked", "INTEGER NOT NULL DEFAULT 0")],
}

CLIENT_STATUSES = {"active": "진행 중", "prospect": "제안 단계", "paused": "중단"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    path = config.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, ddl in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_audits_share ON audits(share_token)")
        # 서버 재시작으로 중단된 진단 정리
        c.execute(
            "UPDATE audits SET status='failed', error='서버 재시작으로 중단됨', finished_at=? "
            "WHERE status IN ('queued','running')",
            (now(),),
        )


def _loads(d: dict, *keys: str) -> dict:
    for k in keys:
        if k in d:
            d[k] = json.loads(d[k] or ("[]" if k in ("urls", "notes") else "{}"))
    return d


# ---------- 광고주 ----------

def create_client(name: str, memo: str = "", industry: str = "", manager: str = "",
                  contact: str = "", status: str = "active") -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO clients(name, memo, industry, manager, contact, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, memo, industry, manager, contact, status, now()),
        )
        return cur.lastrowid


def update_client(client_id: int, **fields) -> None:
    allowed = {"name", "memo", "industry", "manager", "contact", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE clients SET {cols} WHERE id=?", (*fields.values(), client_id))


def list_clients(q: str = "", industry: str = "", status: str = "") -> list[dict]:
    sql = """
    SELECT c.*,
           (SELECT COUNT(*) FROM sites s WHERE s.client_id = c.id) AS site_count,
           (SELECT a.score FROM audits a JOIN sites s ON s.id = a.site_id
             WHERE s.client_id = c.id AND a.status='done'
             ORDER BY a.id DESC LIMIT 1) AS last_score,
           (SELECT a.engine_scores FROM audits a JOIN sites s ON s.id = a.site_id
             WHERE s.client_id = c.id AND a.status='done'
             ORDER BY a.id DESC LIMIT 1) AS last_engines,
           (SELECT a.created_at FROM audits a JOIN sites s ON s.id = a.site_id
             WHERE s.client_id = c.id ORDER BY a.id DESC LIMIT 1) AS last_run
    FROM clients c WHERE 1=1
    """
    args: list = []
    if q:
        sql += " AND (c.name LIKE ? OR c.memo LIKE ? OR c.manager LIKE ?)"
        args += [f"%{q}%"] * 3
    if industry:
        sql += " AND c.industry = ?"
        args.append(industry)
    if status:
        sql += " AND c.status = ?"
        args.append(status)
    sql += " ORDER BY c.name COLLATE NOCASE"
    with connect() as c:
        rows = [dict(r) for r in c.execute(sql, args)]
    for r in rows:
        r["last_engines"] = json.loads(r["last_engines"] or "{}")
    return rows


def get_client(client_id: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        return dict(r) if r else None


def delete_client(client_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM clients WHERE id=?", (client_id,))


# ---------- 사이트 ----------

def add_site(client_id: int, url: str, label: str = "", keywords: str = "") -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO sites(client_id, url, label, keywords, created_at) VALUES (?,?,?,?,?)",
            (client_id, url, label, keywords, now()),
        )
        return cur.lastrowid


def update_site(site_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in {"url", "label", "keywords"}}
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE sites SET {cols} WHERE id=?", (*fields.values(), site_id))


def get_site(site_id: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        return dict(r) if r else None


def delete_site(site_id: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM sites WHERE id=?", (site_id,))


def list_sites(client_id: int) -> list[dict]:
    sql = """
    SELECT s.*,
      (SELECT a.id FROM audits a WHERE a.site_id=s.id ORDER BY a.id DESC LIMIT 1) AS last_audit_id,
      (SELECT a.status FROM audits a WHERE a.site_id=s.id ORDER BY a.id DESC LIMIT 1) AS last_status,
      (SELECT a.score FROM audits a WHERE a.site_id=s.id AND a.status='done' ORDER BY a.id DESC LIMIT 1) AS last_score,
      (SELECT a.engine_scores FROM audits a WHERE a.site_id=s.id AND a.status='done' ORDER BY a.id DESC LIMIT 1) AS last_engines,
      (SELECT a.created_at FROM audits a WHERE a.site_id=s.id ORDER BY a.id DESC LIMIT 1) AS last_run,
      (SELECT COUNT(*) FROM audits a WHERE a.site_id=s.id) AS audit_count
    FROM sites s WHERE s.client_id=? ORDER BY s.id
    """
    with connect() as c:
        rows = [dict(r) for r in c.execute(sql, (client_id,))]
    for r in rows:
        r["last_engines"] = json.loads(r["last_engines"] or "{}")
    return rows


# ---------- 진단 ----------

def create_audit(site_id: int, max_pages: int) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO audits(site_id, status, max_pages, created_at) VALUES (?, 'queued', ?, ?)",
            (site_id, max_pages, now()),
        )
        return cur.lastrowid


def update_audit(audit_id: int, **fields) -> None:
    for k in ("category_scores", "engine_scores", "notes"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE audits SET {cols} WHERE id=?", (*fields.values(), audit_id))


def get_audit(audit_id: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone()
        return _loads(dict(r), "category_scores", "engine_scores", "notes") if r else None


def get_audit_by_token(token: str) -> dict | None:
    if not token:
        return None
    with connect() as c:
        r = c.execute("SELECT * FROM audits WHERE share_token=?", (token,)).fetchone()
        return _loads(dict(r), "category_scores", "engine_scores", "notes") if r else None


def ensure_share_token(audit_id: int) -> str:
    a = get_audit(audit_id)
    if a and a.get("share_token"):
        return a["share_token"]
    token = secrets.token_urlsafe(16)
    update_audit(audit_id, share_token=token)
    return token


def revoke_share_token(audit_id: int) -> None:
    update_audit(audit_id, share_token=None)


def list_audits(site_id: int, limit: int = 10) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, status, score, engine_scores, pages_crawled, created_at FROM audits "
            "WHERE site_id=? ORDER BY id DESC LIMIT ?",
            (site_id, limit),
        ).fetchall()
        return [_loads(dict(r), "engine_scores") for r in rows]


def recent_audits(limit: int = 50, status: str = "") -> list[dict]:
    sql = """
    SELECT a.id, a.status, a.score, a.engine_scores, a.pages_crawled, a.created_at, a.error,
           s.id AS site_id, s.url, s.label, c.id AS client_id, c.name AS client_name, c.industry
    FROM audits a JOIN sites s ON s.id=a.site_id JOIN clients c ON c.id=s.client_id
    """
    args: list = []
    if status:
        sql += " WHERE a.status=?"
        args.append(status)
    sql += " ORDER BY a.id DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [_loads(dict(r), "engine_scores") for r in c.execute(sql, args)]


def previous_done_audit(site_id: int, before_id: int) -> dict | None:
    with connect() as c:
        r = c.execute(
            "SELECT id, score, engine_scores, created_at FROM audits WHERE site_id=? AND status='done' AND id<? "
            "ORDER BY id DESC LIMIT 1",
            (site_id, before_id),
        ).fetchone()
        return _loads(dict(r), "engine_scores") if r else None


def dashboard_stats() -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    with connect() as c:
        one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
        latest = c.execute("""
            SELECT a.score, a.engine_scores FROM audits a
            WHERE a.status='done' AND a.id IN (SELECT MAX(id) FROM audits WHERE status='done' GROUP BY site_id)
        """).fetchall()
        return {
            "clients": one("SELECT COUNT(*) FROM clients"),
            "active_clients": one("SELECT COUNT(*) FROM clients WHERE status='active'"),
            "sites": one("SELECT COUNT(*) FROM sites"),
            "audits_30d": one("SELECT COUNT(*) FROM audits WHERE created_at >= ?", since),
            "running": one("SELECT COUNT(*) FROM audits WHERE status IN ('queued','running')"),
            "latest": [(r["score"], json.loads(r["engine_scores"] or "{}")) for r in latest],
        }


def low_score_sites(limit: int = 6) -> list[dict]:
    sql = """
    SELECT a.id AS audit_id, a.score, a.engine_scores, a.created_at, s.url, s.label,
           c.id AS client_id, c.name AS client_name
    FROM audits a JOIN sites s ON s.id=a.site_id JOIN clients c ON c.id=s.client_id
    WHERE a.id IN (SELECT MAX(id) FROM audits WHERE status='done' GROUP BY site_id)
    ORDER BY a.score ASC LIMIT ?
    """
    with connect() as c:
        return [_loads(dict(r), "engine_scores") for r in c.execute(sql, (limit,))]


# ---------- 결과 ----------

def save_findings(audit_id: int, results) -> None:
    rows = [
        (audit_id, r.check_id, r.category, r.title, r.status, r.weight, r.message, r.fix,
         json.dumps(r.urls[:200], ensure_ascii=False))
        for r in results
    ]
    with connect() as c:
        c.executemany(
            "INSERT INTO findings(audit_id, check_id, category, title, status, weight, message, fix, urls) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_findings(audit_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM findings WHERE audit_id=? ORDER BY id", (audit_id,)).fetchall()
    return [_loads(dict(r), "urls") for r in rows]


def set_finding_checked(finding_id: int, checked: bool) -> dict | None:
    with connect() as c:
        c.execute("UPDATE findings SET checked=? WHERE id=?", (1 if checked else 0, finding_id))
        r = c.execute("SELECT id, audit_id, checked FROM findings WHERE id=?", (finding_id,)).fetchone()
        return dict(r) if r else None


def save_pages(audit_id: int, rows: list[tuple]) -> None:
    with connect() as c:
        c.executemany(
            "INSERT INTO pages(audit_id, url, status, title, chars, elapsed_ms, depth, issues) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(audit_id, *r) for r in rows],
        )


def get_pages(audit_id: int) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM pages WHERE audit_id=? ORDER BY issues DESC, id", (audit_id,))]


# ---------- 백업 / 복원 (무료 서버는 재시작 시 디스크가 초기화되므로 JSON으로 보관) ----------

BACKUP_TABLES = ["clients", "sites", "audits", "findings", "pages"]


def export_all() -> dict:
    with connect() as c:
        return {"version": 1, "exported_at": now(),
                "tables": {t: [dict(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY id")] for t in BACKUP_TABLES}}


def import_all(data: dict) -> dict[str, int]:
    """백업 JSON으로 전체 데이터를 교체합니다."""
    tables = data.get("tables") if isinstance(data, dict) else None
    if not isinstance(tables, dict) or "clients" not in tables:
        raise ValueError("백업 파일 형식이 아닙니다.")
    counts = {}
    with connect() as c:
        for t in reversed(BACKUP_TABLES):
            c.execute(f"DELETE FROM {t}")
        for t in BACKUP_TABLES:
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
            rows = tables.get(t) or []
            for row in rows:
                row = {k: v for k, v in row.items() if k in have}
                if not row:
                    continue
                cols = ", ".join(row)
                c.execute(f"INSERT INTO {t} ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
            counts[t] = len(rows)
        c.execute("UPDATE audits SET status='failed', error='복원 시점에 진행 중이던 진단' WHERE status IN ('queued','running')")
    return counts
