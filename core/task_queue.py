"""
task_queue.py
SQLite-backed task queue for the multi-project AI orchestrator.

Schema fields:
  complexity       — low / medium / high
  rationale        — one sentence: why this task exists
  effort_category  — feature / scaffold / test / docs / bugfix / gap-fill / refactor
  perspective      — which expert "requested" this task
  review_priority  — 1–5 computed score; higher = review more carefully
"""

import sqlite3
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
import logging

from config import DB_PATH

log = logging.getLogger(__name__)

# ── SCHEMA ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS epics (
    id         TEXT PRIMARY KEY,   -- e.g. "epic_stt_reliability"
    project    TEXT NOT NULL,
    name       TEXT NOT NULL,      -- display name
    color      TEXT DEFAULT '#6366f1',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS pbis (
    id                   TEXT PRIMARY KEY,   -- e.g. "pbi_lang_001"
    epic_id              TEXT REFERENCES epics(id),
    project              TEXT NOT NULL,
    title                TEXT NOT NULL,
    description          TEXT DEFAULT '',
    acceptance_criteria  TEXT DEFAULT '',
    affected_files       TEXT DEFAULT '[]',  -- JSON array of repo-relative paths
    handoff_notes        TEXT DEFAULT '{}',  -- JSON {task_id: "150-word summary"} per completed task
    pr_url               TEXT DEFAULT '',    -- GitHub PR URL once created
    status               TEXT DEFAULT 'active',  -- active | complete | cancelled
    created_at           TEXT
);

CREATE INDEX IF NOT EXISTS idx_pbis_epic    ON pbis(epic_id);
CREATE INDEX IF NOT EXISTS idx_pbis_project ON pbis(project);
CREATE INDEX IF NOT EXISTS idx_pbis_status  ON pbis(status);

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    project          TEXT    NOT NULL,
    description      TEXT    NOT NULL,
    status           TEXT    DEFAULT 'queued',
    priority         INTEGER DEFAULT 1,
    approval_required INTEGER DEFAULT 0,

    complexity       TEXT    DEFAULT 'medium',
    rationale        TEXT    DEFAULT '',
    effort_category  TEXT    DEFAULT 'feature',
    perspective      TEXT    DEFAULT 'engineering_architect',
    review_priority  INTEGER DEFAULT 3,

    depends_on       TEXT    DEFAULT '[]',
    blocks           TEXT    DEFAULT '[]',

    estimated_tokens INTEGER DEFAULT 0,
    actual_tokens    INTEGER DEFAULT 0,
    cost_usd         REAL    DEFAULT 0.0,
    model_used       TEXT    DEFAULT '',
    diff_path        TEXT    DEFAULT '',
    aider_prompt     TEXT    DEFAULT '',
    quality_score    INTEGER DEFAULT 0,

    created_at       TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    committed_at     TEXT,
    commit_hash      TEXT    DEFAULT '',
    description_hash TEXT    DEFAULT '',
    system_prompt    TEXT    DEFAULT '',
    quality_gate_skipped INTEGER DEFAULT 0,
    rejection_reason TEXT    DEFAULT '',
    rejected_at      TEXT,
    notes            TEXT    DEFAULT '',
    pbi_id           TEXT    REFERENCES pbis(id)
);

CREATE INDEX IF NOT EXISTS idx_project          ON tasks(project);
CREATE INDEX IF NOT EXISTS idx_status           ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_description_hash ON tasks(description_hash);
CREATE INDEX IF NOT EXISTS idx_pbi_id           ON tasks(pbi_id);
"""

# ── HELPERS ──────────────────────────────────────────────────────────────────

def _compute_review_priority(complexity: str, approval_required: bool) -> int:
    """1 (glance) → 5 (read every line). Higher = more scrutiny needed."""
    score = {"low": 1, "medium": 2, "high": 3}.get(complexity, 2)
    if approval_required:
        score += 2
    return min(score, 5)


def _description_hash(description: str, project: str) -> str:
    """
    16-char hex hash of lowercased, whitespace-normalized description + project.
    Used to skip near-duplicate tasks from council re-runs.
    Per-project scoped so identical tasks in different projects are allowed.
    """
    normalized = " ".join(description.lower().split())
    return hashlib.sha256(f"{project}:{normalized}".encode()).hexdigest()[:16]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["depends_on"]        = json.loads(d.get("depends_on") or "[]")
    d["blocks"]            = json.loads(d.get("blocks") or "[]")
    d["approval_required"] = bool(d.get("approval_required"))
    return d


# ── TASK QUEUE ───────────────────────────────────────────────────────────────

class TaskQueue:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        """
        Open a connection with WAL mode and busy_timeout applied.
        WAL allows concurrent readers without blocking writers.
        busy_timeout makes writers wait up to 5s instead of failing immediately.
        Both must be set per-connection — not just at init time.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── WRITE ────────────────────────────────────────────────────────────────

    def add_task(self, task: dict) -> bool:
        """
        Insert a task. Silently skips if:
          - id already exists (exact duplicate)
          - description_hash already exists in queued/running state (semantic duplicate)
        Returns True if inserted, False if skipped.
        """
        complexity        = task.get("complexity", "medium")
        approval_required = task.get("approval_required", False)
        project           = task["project"]
        description       = task["description"]
        desc_hash         = _description_hash(description, project)

        with self._conn() as conn:
            if conn.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task["id"],)
            ).fetchone():
                return False

            if conn.execute(
                "SELECT 1 FROM tasks WHERE description_hash = ? AND project = ? "
                "AND status IN ('queued', 'running')",
                (desc_hash, project),
            ).fetchone():
                log.debug(f"[{project}] Skipping duplicate: {description[:60]}")
                return False

            conn.execute("""
                INSERT INTO tasks (
                    id, project, description, status, priority, approval_required,
                    complexity, rationale, effort_category, perspective, review_priority,
                    depends_on, blocks, estimated_tokens, description_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task["id"],
                project,
                description,
                task.get("status", "queued"),
                task.get("priority", 1),
                int(approval_required),
                complexity,
                task.get("rationale", ""),
                task.get("effort_category", "feature"),
                task.get("perspective", "engineering_architect"),
                _compute_review_priority(complexity, approval_required),
                json.dumps(task.get("depends_on", [])),
                json.dumps(task.get("blocks", [])),
                task.get("estimated_tokens", 0),
                desc_hash,
                datetime.now().isoformat(),
            ))
        return True

    def load_from_json(self, json_path: Path) -> int:
        """Bulk-load tasks from a JSON file. Returns count inserted."""
        if not json_path.exists():
            return 0
        tasks = json.loads(json_path.read_text())
        if not isinstance(tasks, list):
            return 0
        return sum(1 for t in tasks if isinstance(t, dict) and self.add_task(t))

    def update_status(self, task_id: str, status: str, **kwargs):
        """Update task status and any additional fields passed as kwargs."""
        allowed = {
            "started_at", "completed_at", "committed_at", "commit_hash",
            "diff_path", "aider_prompt", "system_prompt", "quality_gate_skipped",
            "rejection_reason", "rejected_at",
            "actual_tokens", "cost_usd", "model_used", "quality_score", "notes",
            "pbi_id",
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        fields["status"] = status

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values     = list(fields.values()) + [task_id]

        with self._conn() as conn:
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)

    def mark_running(self, task: dict):
        self.update_status(task["id"], "running", started_at=datetime.now().isoformat())

    def mark_pending_review(self, task: dict, diff_path: Path):
        self.update_status(
            task["id"], "pending_review",
            diff_path=str(diff_path),
            completed_at=datetime.now().isoformat()
        )
        log.info(f"[{task['project']}] {task['id']} → pending review ({diff_path.name})")

    def mark_completed(self, task: dict, **kwargs):
        self.update_status(task["id"], "completed",
                           completed_at=datetime.now().isoformat(), **kwargs)

    def mark_committed(self, task: dict, commit_hash: str = "", diff_path: str = "", **kwargs):
        """Mark a task as committed (auto-commit flow). Sets status=completed."""
        now = datetime.now().isoformat()
        self.update_status(
            task["id"], "completed",
            completed_at=now,
            committed_at=now,
            commit_hash=commit_hash,
            diff_path=diff_path,
            **kwargs,
        )
        log.info(f"[{task['project']}] {task['id']} → committed ({commit_hash or 'no hash'})")

    def mark_failed(self, task: dict, notes: str = ""):
        self.update_status(task["id"], "failed",
                           completed_at=datetime.now().isoformat(), notes=notes)

    def mark_rejected(self, task: dict, reason: str = ""):
        """Mark a pending_review task as rejected with an optional reason."""
        self.update_status(
            task["id"], "failed",
            completed_at=datetime.now().isoformat(),
            rejected_at=datetime.now().isoformat(),
            rejection_reason=reason,
            notes=f"rejected: {reason}" if reason else "rejected",
        )
        log.info(f"[{task['project']}] {task['id']} → rejected ({reason or 'no reason'})")

    # ── EPICS & PBIs ─────────────────────────────────────────────────────────

    def add_epic(self, epic: dict) -> bool:
        """Insert an epic. Returns True if inserted, False if id already exists."""
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM epics WHERE id=?", (epic["id"],)).fetchone():
                return False
            conn.execute(
                "INSERT INTO epics (id, project, name, color, created_at) VALUES (?,?,?,?,?)",
                (
                    epic["id"],
                    epic["project"],
                    epic["name"],
                    epic.get("color", "#6366f1"),
                    datetime.now().isoformat(),
                ),
            )
        return True

    def add_pbi(self, pbi: dict) -> bool:
        """Insert a PBI. Returns True if inserted, False if id already exists."""
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM pbis WHERE id=?", (pbi["id"],)).fetchone():
                return False
            conn.execute(
                """INSERT INTO pbis
                   (id, epic_id, project, title, description, acceptance_criteria,
                    affected_files, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    pbi["id"],
                    pbi.get("epic_id"),
                    pbi["project"],
                    pbi["title"],
                    pbi.get("description", ""),
                    pbi.get("acceptance_criteria", ""),
                    json.dumps(pbi.get("affected_files", [])),
                    pbi.get("status", "active"),
                    datetime.now().isoformat(),
                ),
            )
        return True

    def get_pbi(self, pbi_id: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pbis WHERE id=?", (pbi_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["affected_files"] = json.loads(d.get("affected_files") or "[]")
        return d

    def get_epic(self, epic_id: str) -> "dict | None":
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM epics WHERE id=?", (epic_id,)).fetchone()
        return dict(row) if row else None

    def all_epics(self, project: str = None) -> list[dict]:
        with self._conn() as conn:
            if project:
                rows = conn.execute(
                    "SELECT * FROM epics WHERE project=? ORDER BY created_at", (project,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM epics ORDER BY project, created_at").fetchall()
        return [dict(r) for r in rows]

    def pbis_for_epic(self, epic_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pbis WHERE epic_id=? ORDER BY created_at", (epic_id,)
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["affected_files"] = json.loads(d.get("affected_files") or "[]")
            result.append(d)
        return result

    def mark_blocked(self, task: dict, question: str = "") -> None:
        """Mark a task as blocked because the model asked a clarifying question."""
        self.update_status(
            task["id"], "blocked",
            notes=f"model_question: {question[:500]}" if question else "blocked: model asked question",
            completed_at=datetime.now().isoformat(),
        )
        log.info(f"[{task['project']}] {task['id']} → blocked (model asked question)")

    def update_pbi_handoff(self, pbi_id: str, task_id: str, summary: str) -> None:
        """Store a post-task handoff summary for the next PBI task to read."""
        with self._conn() as conn:
            row = conn.execute("SELECT handoff_notes FROM pbis WHERE id=?", (pbi_id,)).fetchone()
            if not row:
                return
            notes = json.loads(row[0] or "{}")
            notes[task_id] = summary
            conn.execute("UPDATE pbis SET handoff_notes=? WHERE id=?",
                         (json.dumps(notes), pbi_id))

    def get_pbi_handoff_notes(self, pbi_id: str) -> dict:
        """Return {task_id: summary} for all completed tasks in this PBI."""
        with self._conn() as conn:
            row = conn.execute("SELECT handoff_notes FROM pbis WHERE id=?", (pbi_id,)).fetchone()
        return json.loads(row[0] or "{}") if row else {}

    def set_pbi_pr_url(self, pbi_id: str, pr_url: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE pbis SET pr_url=? WHERE id=?", (pr_url, pbi_id))

    def update_pbi_affected_files(self, pbi_id: str, new_files: list) -> None:
        """Append newly discovered files to pbi.affected_files (no duplicates)."""
        if not new_files:
            return
        with self._conn() as conn:
            row = conn.execute("SELECT affected_files FROM pbis WHERE id=?", (pbi_id,)).fetchone()
            if not row:
                return
            existing = json.loads(row[0] or "[]")
            merged   = existing + [f for f in new_files if f not in existing]
            conn.execute("UPDATE pbis SET affected_files=? WHERE id=?",
                         (json.dumps(merged), pbi_id))
        if new_files:
            log.info(f"PBI {pbi_id}: added {len(new_files)} new file(s) to affected_files: {new_files}")

    def tasks_for_pbi(self, pbi_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE pbi_id=? ORDER BY priority ASC, created_at ASC",
                (pbi_id,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def pbi_progress(self, pbi_id: str) -> dict:
        """Returns {total, completed, failed, queued} task counts for a PBI."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM tasks WHERE pbi_id=? GROUP BY status",
                (pbi_id,),
            ).fetchall()
        counts = {r["status"]: r["n"] for r in rows}
        total  = sum(counts.values())
        return {
            "total":     total,
            "completed": counts.get("completed", 0),
            "failed":    counts.get("failed", 0),
            "queued":    counts.get("queued", 0),
            "running":   counts.get("running", 0),
        }

    # ── READ ─────────────────────────────────────────────────────────────────

    def _completed_ids(self, conn: sqlite3.Connection) -> set:
        """
        IDs of tasks considered done for dependency resolution.
        pending_review counts as done — the work exists, even if not yet committed.
        """
        return {
            r[0] for r in conn.execute(
                "SELECT id FROM tasks WHERE status IN ('completed', 'pending_review')"
            )
        }

    def _queued_rows(self, conn: sqlite3.Connection, projects: list) -> list:
        """Fetch all queued, non-approval-required tasks for the given projects."""
        placeholders = ",".join("?" * len(projects)) if projects else ""
        where_proj   = f"AND project IN ({placeholders})" if projects else ""
        return conn.execute(f"""
            SELECT * FROM tasks
            WHERE status = 'queued'
              AND approval_required = 0
              {where_proj}
            ORDER BY priority ASC, review_priority DESC
        """, projects).fetchall()

    def get_next(self, projects: list = None) -> "dict | None":
        """
        Return the next runnable task: queued, not approval_required, all
        dependencies satisfied. Sorted by priority ASC, review_priority DESC.
        """
        projects = projects or []
        with self._conn() as conn:
            completed = self._completed_ids(conn)
            rows      = self._queued_rows(conn, projects)

        for row in rows:
            task = _row_to_dict(row)
            if all(dep in completed for dep in task["depends_on"]):
                return task
        return None

    def total_unblocked(self, projects: list = None) -> int:
        """Count queued tasks whose dependencies are all satisfied."""
        projects = projects or []
        with self._conn() as conn:
            completed = self._completed_ids(conn)
            rows      = self._queued_rows(conn, projects)

        return sum(
            1 for row in rows
            if all(d in completed for d in json.loads(row["depends_on"] or "[]"))
        )

    def get_pending_review(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks WHERE status = 'pending_review'
                ORDER BY review_priority DESC, completed_at ASC
            """).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_gap_fill_tasks(self) -> list[dict]:
        """Return simple gap-fill tasks when the main queue is empty."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks
                WHERE status = 'queued' AND effort_category = 'gap-fill'
                ORDER BY priority ASC LIMIT 3
            """).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_completed_today(self) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks
                WHERE status = 'completed' AND completed_at LIKE ?
                ORDER BY completed_at DESC
            """, (f"{today}%",)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def stats(self) -> dict:
        """Task status counts only. Use SpendTracker for cost — it's the single
        source of truth and includes timeouts/partial spends the DB never sees."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT status, COUNT(*) as n FROM tasks GROUP BY status
            """).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ── METRICS ──────────────────────────────────────────────────────────────

    def metrics_data(self, days: int = 30) -> dict:
        """
        Return all data needed for the metrics dashboard.
        Covers: pass rate, cost, throughput, perspective performance, queue health.
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._conn() as conn:
            gate_rows = conn.execute("""
                SELECT quality_score, quality_gate_skipped, project, perspective, complexity
                FROM tasks
                WHERE created_at >= ? AND status IN ('completed', 'failed')
            """, (cutoff,)).fetchall()

            cost_rows = conn.execute("""
                SELECT project, SUM(cost_usd) as total, COUNT(*) as n
                FROM tasks
                WHERE created_at >= ? AND status = 'completed'
                GROUP BY project
            """, (cutoff,)).fetchall()

            throughput_rows = conn.execute("""
                SELECT date(committed_at) as day, COUNT(*) as n
                FROM tasks
                WHERE committed_at >= ? AND status = 'completed'
                GROUP BY day ORDER BY day
            """, (cutoff,)).fetchall()

            perspective_rows = conn.execute("""
                SELECT perspective,
                       COUNT(*) as attempted,
                       SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as committed,
                       SUM(cost_usd) as cost
                FROM tasks
                WHERE created_at >= ?
                GROUP BY perspective
            """, (cutoff,)).fetchall()

            queue_rows = conn.execute("""
                SELECT status, COUNT(*) as n, project
                FROM tasks
                GROUP BY status, project
            """).fetchall()

            fail_rows = conn.execute("""
                SELECT id, project, description, notes, completed_at
                FROM tasks WHERE status='failed'
                ORDER BY completed_at DESC LIMIT 10
            """).fetchall()

        gate_total  = len(gate_rows)
        gate_passed = sum(1 for r in gate_rows if r["quality_score"] and r["quality_score"] >= 6)
        gate_rate   = round(gate_passed / gate_total * 100, 1) if gate_total else 0.0

        return {
            "quality_gate": {
                "pass_rate": gate_rate,
                "passed":    gate_passed,
                "total":     gate_total,
            },
            "cost_by_project": {
                r["project"]: {"total": round(r["total"] or 0, 4), "n": r["n"]}
                for r in cost_rows
            },
            "throughput_by_day": {r["day"]: r["n"] for r in throughput_rows if r["day"]},
            "perspectives": [
                {
                    "name":      r["perspective"],
                    "attempted": r["attempted"],
                    "committed": r["committed"],
                    "rate":      round(r["committed"] / r["attempted"] * 100, 1) if r["attempted"] else 0,
                    "cost":      round(r["cost"] or 0, 4),
                }
                for r in perspective_rows if r["perspective"]
            ],
            "queue_health":    [dict(r) for r in queue_rows],
            "recent_failures": [dict(r) for r in fail_rows],
        }
