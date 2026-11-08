"""SQLite 数据访问层：病例、复核工单、复核记录、审计日志、SUS 问卷。

设计要点
--------
* 一次判读 = 一条 case；是否转人工由 server.engine 判定后写 status。
* 转人工生成 ticket（工单），ticket 有优先级与 SLA；医生"领取 -> 复核"两条状态迁移。
* 每一次状态变化都写 audit 表，形成可追溯的安全审计链（医疗 AI 事后追溯必需）。
"""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id              TEXT PRIMARY KEY,
    device_id       TEXT,
    created_at      REAL NOT NULL,
    image_path      TEXT NOT NULL,
    image_sha256    TEXT,
    width           INTEGER,
    height          INTEGER,
    ai_class        INTEGER,
    ai_probs        TEXT,           -- JSON [p0,p1,p2]（校准后、级联融合）
    ai_raw_probs    TEXT,           -- JSON 校准前，用于展示校准效果
    ai_confidence   REAL,
    temperature     REAL,
    quality_score   REAL,
    quality_flags   TEXT,
    review_reasons  TEXT,           -- JSON，转人工原因列表
    user_meta       TEXT,           -- JSON：年龄、部位、受伤时长、自评面积、红旗症状
    status          TEXT,           -- auto / pending_review / in_review / reviewed
    final_class     INTEGER,        -- 复核后的最终类别（未复核时等于 ai_class）
    advice_level    INTEGER,
    ticket_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_cases_created ON cases(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

CREATE TABLE IF NOT EXISTS tickets (
    id           TEXT PRIMARY KEY,
    case_id      TEXT NOT NULL,
    created_at   REAL NOT NULL,
    priority     TEXT NOT NULL,      -- high / normal
    status       TEXT NOT NULL,      -- pending / claimed / done
    sla_due_at   REAL,
    claimed_by   TEXT,
    claimed_at   REAL,
    completed_at REAL,
    FOREIGN KEY(case_id) REFERENCES cases(id)
);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
    id            TEXT PRIMARY KEY,
    ticket_id     TEXT NOT NULL,
    case_id       TEXT NOT NULL,
    doctor        TEXT NOT NULL,
    decision      TEXT NOT NULL,     -- confirm / correct
    corrected_class INTEGER,
    note          TEXT,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    case_id   TEXT,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit(case_id);

CREATE TABLE IF NOT EXISTS sus (
    id         TEXT PRIMARY KEY,
    device_id  TEXT,
    created_at REAL NOT NULL,
    answers    TEXT,
    total      REAL,
    task_time_s REAL,
    completed  INTEGER
);
"""


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


@contextmanager
def conn():
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    c = sqlite3.connect(config.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    # 库文件可能被外部删除/重建：按需幂等建表（一次轻量探测，开销可忽略）
    row = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cases'").fetchone()
    if not row:
        c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def _row_to_case(r) -> dict:
    if r is None:
        return None
    d = dict(r)
    for k in ("ai_probs", "ai_raw_probs", "review_reasons", "user_meta", "quality_flags"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


def create_case(**kw) -> str:
    if not kw.get("id"):
        kw["id"] = new_id("C")
    cid = kw["id"]
    kw.setdefault("created_at", time.time())
    for k in ("ai_probs", "ai_raw_probs", "review_reasons", "user_meta", "quality_flags"):
        if k in kw and not isinstance(kw[k], str):
            kw[k] = json.dumps(kw[k], ensure_ascii=False)
    cols = ",".join(kw.keys())
    ph = ",".join("?" * len(kw))
    with conn() as c:
        c.execute(f"INSERT INTO cases ({cols}) VALUES ({ph})", list(kw.values()))
    return cid


def update_case(cid: str, **kw) -> None:
    for k in ("ai_probs", "ai_raw_probs", "review_reasons", "user_meta", "quality_flags"):
        if k in kw and not isinstance(kw[k], str):
            kw[k] = json.dumps(kw[k], ensure_ascii=False)
    sets = ",".join(f"{k}=?" for k in kw)
    with conn() as c:
        c.execute(f"UPDATE cases SET {sets} WHERE id=?", list(kw.values()) + [cid])


def get_case(cid: str) -> dict:
    with conn() as c:
        return _row_to_case(c.execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone())


def create_ticket(case_id: str, priority: str) -> str:
    tid = new_id("T")
    now = time.time()
    due = now + config.SLA_MINUTES.get(priority, 240) * 60
    with conn() as c:
        c.execute(
            "INSERT INTO tickets (id,case_id,created_at,priority,status,sla_due_at)"
            " VALUES (?,?,?,?,?,?)",
            (tid, case_id, now, priority, "pending", due),
        )
    return tid


def list_tickets(status: str | None = None, limit: int = 100) -> list:
    q = (
        "SELECT t.*, c.ai_class, c.ai_confidence, c.ai_probs, c.final_class, c.created_at AS case_created,"
        " c.user_meta, c.review_reasons, c.image_path, c.quality_score"
        " FROM tickets t JOIN cases c ON c.id = t.case_id"
    )
    args: list = []
    if status:
        q += " WHERE t.status = ?"
        args.append(status)
    q += (
        " ORDER BY CASE t.priority WHEN 'high' THEN 0 ELSE 1 END, t.created_at ASC LIMIT ?"
    )
    args.append(limit)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("ai_probs", "user_meta", "review_reasons"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        out.append(d)
    return out


def claim_ticket(tid: str, doctor: str) -> bool:
    with conn() as c:
        cur = c.execute(
            "UPDATE tickets SET status='claimed', claimed_by=?, claimed_at=?"
            " WHERE id=? AND status='pending'",
            (doctor, time.time(), tid),
        )
        if cur.rowcount == 0:
            return False
        c.execute(
            "UPDATE cases SET status='in_review' WHERE id=(SELECT case_id FROM tickets WHERE id=?)",
            (tid,),
        )
    return True


def finish_ticket(tid: str, doctor: str, decision: str, corrected: int | None, note: str) -> dict:
    now = time.time()
    with conn() as c:
        t = c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        if t is None:
            raise ValueError("ticket not found")
        if t["status"] == "done":
            raise ValueError("ticket already completed")
        case = c.execute("SELECT * FROM cases WHERE id=?", (t["case_id"],)).fetchone()
        final_class = corrected if decision == "correct" and corrected is not None else case["ai_class"]
        c.execute(
            "INSERT INTO reviews (id,ticket_id,case_id,doctor,decision,corrected_class,note,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (new_id("R"), tid, t["case_id"], doctor, decision, corrected, note, now),
        )
        c.execute(
            "UPDATE tickets SET status='done', completed_at=?, claimed_by=COALESCE(claimed_by,?) WHERE id=?",
            (now, doctor, tid),
        )
        c.execute(
            "UPDATE cases SET status='reviewed', final_class=? WHERE id=?",
            (final_class, t["case_id"]),
        )
    return {"case_id": t["case_id"], "final_class": final_class}


def get_reviews(case_id: str) -> list:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM reviews WHERE case_id=? ORDER BY created_at ASC", (case_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def audit(actor: str, action: str, case_id: str | None = None, detail: dict | None = None) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO audit (ts,actor,action,case_id,detail) VALUES (?,?,?,?,?)",
            (time.time(), actor, action, case_id, json.dumps(detail or {}, ensure_ascii=False)),
        )


def stats() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM cases").fetchone()["n"]
        reviewed = c.execute("SELECT COUNT(*) n FROM cases WHERE status='reviewed'").fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) n FROM tickets WHERE status='pending'").fetchone()["n"]
        claimed = c.execute("SELECT COUNT(*) n FROM tickets WHERE status='claimed'").fetchone()["n"]
        done = c.execute("SELECT COUNT(*) n FROM tickets WHERE status='done'").fetchone()["n"]
        avg = c.execute(
            "SELECT AVG(completed_at - created_at) v FROM tickets WHERE status='done'"
        ).fetchone()["v"]
        class_rows = c.execute(
            "SELECT COALESCE(final_class, ai_class) k, COUNT(*) n FROM cases GROUP BY k"
        ).fetchall()
        correct = c.execute(
            "SELECT COUNT(*) n FROM reviews WHERE decision='correct'"
        ).fetchone()["n"]
    return {
        "cases": total,
        "reviewed": reviewed,
        "tickets_pending": pending,
        "tickets_claimed": claimed,
        "tickets_done": done,
        "referral_rate": (pending + claimed + done) / total if total else 0.0,
        "avg_review_seconds": avg or 0.0,
        "revision_rate": correct / done if done else 0.0,
        "class_distribution": {str(r["k"]): r["n"] for r in class_rows if r["k"] is not None},
    }


def save_sus(device_id: str, answers: list, total: float, task_time: float, completed: bool) -> str:
    sid = new_id("S")
    with conn() as c:
        c.execute(
            "INSERT INTO sus (id,device_id,created_at,answers,total,task_time_s,completed)"
            " VALUES (?,?,?,?,?,?,?)",
            (sid, device_id, time.time(), json.dumps(answers), total, task_time, int(completed)),
        )
    return sid


def sus_stats() -> dict:
    with conn() as c:
        rows = c.execute("SELECT total, completed FROM sus").fetchall()
    if not rows:
        return {"n": 0, "avg": 0.0, "pass_rate": 0.0, "task_completion": 0.0}
    totals = [r["total"] for r in rows]
    return {
        "n": len(rows),
        "avg": sum(totals) / len(totals),
        "pass_rate": sum(1 for t in totals if t >= 68) / len(totals),
        "task_completion": sum(r["completed"] for r in rows) / len(rows),
    }
