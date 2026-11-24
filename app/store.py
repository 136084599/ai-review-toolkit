import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                status TEXT NOT NULL, progress INTEGER NOT NULL, stage TEXT NOT NULL,
                created TEXT NOT NULL, payload TEXT NOT NULL, report TEXT, error TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
                job_id TEXT NOT NULL, issue_id TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
                updated TEXT NOT NULL, PRIMARY KEY (job_id, issue_id))""")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def recover(self):
        with self.connect() as conn:
            conn.execute("UPDATE jobs SET status='failed', error='服务已重启，请重新提交审核', stage='任务中断' WHERE status IN ('queued','running')")

    def create(self, kind, title, payload, job_id=None):
        job_id = job_id or uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", (
                job_id, kind, title, "queued", 0, "等待处理", datetime.now(timezone.utc).isoformat(),
                json.dumps(payload, ensure_ascii=False), None, None))
        return job_id

    def update(self, job_id, **fields):
        assert set(fields) <= {"status", "progress", "stage", "report", "error"}
        if "report" in fields:
            fields["report"] = json.dumps(fields["report"], ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(f"UPDATE jobs SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                         [*fields.values(), job_id])

    def get(self, job_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("payload", "report"):
            result[key] = json.loads(result[key]) if result[key] else None
        with self.connect() as conn:
            result["decisions"] = {row["issue_id"]:dict(row) for row in conn.execute(
                "SELECT issue_id,status,note,updated FROM decisions WHERE job_id=?", (job_id,))}
        return result

    def decide(self, job_id, issue_id, status, note):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)", (
                job_id, issue_id, status, note, datetime.now(timezone.utc).isoformat()))

    def list(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id,kind,title,status,progress,stage,created,error FROM jobs ORDER BY created DESC LIMIT 100")]

    def delete(self, job_id):
        with self.connect() as conn:
            conn.execute("DELETE FROM decisions WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
