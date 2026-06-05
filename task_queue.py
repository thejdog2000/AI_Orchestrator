"""
task_queue.py
SQLite-backed task queue for the multi-project AI orchestrator.
Replaces the JSON-based task storage in orchestrator_main.py.

Schema additions vs original:
  complexity       — low / medium / high  (Ollama estimates at task creation)
  rationale        — one sentence: why this task exists
  effort_category  — feature / scaffold / test / docs / bugfix / gap-fill / refactor
  perspective      — which expert "requested" this task (see PERSPECTIVES list)
  review_priority  — 1–5 computed score; higher = review more carefully
"""

import sqlite3
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
import logging

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "orchestrator.db"

# ── TAXONOMY ─────────────────────────────────────────────────────────────────

# Map each perspective to the projects it most applies to.
# Ollama uses this when generating tasks — it only invokes relevant council members.
PERSPECTIVE_PROJECT_MAP = {
    "engineering_architect": ["meridian", "rts", "lang", "gamma", "ninja", "tax"],
    "security_engineer":     ["meridian", "tax"],
    "qa_tester":             ["meridian", "rts", "lang", "gamma", "ninja"],
    "product_manager":       ["meridian", "lang", "tax"],
    "mobile_ux_designer":    ["meridian", "lang"],
    "game_designer":         ["rts", "lang"],
    "game_feel_engineer":    ["rts"],
    "systems_architect":     ["rts", "gamma", "ninja"],
    "speech_linguist":       ["lang"],
    "pedagogy_expert":       ["lang"],
    "quant_analyst":         ["gamma", "ninja"],
    "risk_manager":          ["gamma", "ninja"],
    "devops":                ["meridian", "tax"],
    "it_administrator":      ["tax"],
    "client_success":        ["tax"],
}

PERSPECTIVES = list(PERSPECTIVE_PROJECT_MAP.keys())

EFFORT_CATEGORIES = ["feature", "scaffold", "test", "docs", "bugfix", "gap-fill", "refactor"]
COMPLEXITIES      = ["low", "medium", "high"]

PROJECT_COLORS = {
    "meridian": "#6366f1",
    "rts":      "#f59e0b",
    "lang":     "#10b981",
    "gamma":    "#3b82f6",
    "ninja":    "#8b5cf6",
    "tax":      "#ef4444",
}

# ── SCHEMA ───────────────────────────────────────────────────────────────────

_SCHEMA = """
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
    notes            TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_project ON tasks(project);
CREATE INDEX IF NOT EXISTS idx_status  ON tasks(status);
"""

# ── HELPERS ──────────────────────────────────────────────────────────────────

def _compute_review_priority(complexity: str, approval_required: bool) -> int:
    """1 (glance) → 5 (read every line). Higher = more scrutiny needed."""
    score = {"low": 1, "medium": 2, "high": 3}.get(complexity, 2)
    if approval_required:
        score += 2
    return min(score, 5)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["depends_on"]       = json.loads(d.get("depends_on") or "[]")
    d["blocks"]           = json.loads(d.get("blocks") or "[]")
    d["approval_required"] = bool(d.get("approval_required"))
    return d


# ── TASK QUEUE ───────────────────────────────────────────────────────────────

class TaskQueue:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ── WRITE ────────────────────────────────────────────────────────────────

    def add_task(self, task: dict) -> bool:
        """
        Insert a task. Silently skips if id already exists.
        Expected fields: id, project, description, and any optional fields.
        Returns True if inserted, False if skipped.
        """
        complexity        = task.get("complexity", "medium")
        approval_required = task.get("approval_required", False)

        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (task["id"],)
            ).fetchone()
            if existing:
                return False

            conn.execute("""
                INSERT INTO tasks (
                    id, project, description, status, priority, approval_required,
                    complexity, rationale, effort_category, perspective, review_priority,
                    depends_on, blocks, estimated_tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task["id"],
                task["project"],
                task["description"],
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
                datetime.now().isoformat(),
            ))
        return True

    def load_from_json(self, json_path: Path) -> int:
        """Bulk-load tasks from a JSON file. Returns count inserted."""
        if not json_path.exists():
            return 0
        tasks = json.loads(json_path.read_text())
        return sum(1 for t in tasks if self.add_task(t))

    def update_status(self, task_id: str, status: str, **kwargs):
        """Update task status and any additional fields passed as kwargs."""
        allowed = {
            "started_at", "completed_at", "diff_path", "aider_prompt",
            "actual_tokens", "cost_usd", "model_used", "quality_score", "notes"
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

    def mark_failed(self, task: dict, notes: str = ""):
        self.update_status(task["id"], "failed",
                           completed_at=datetime.now().isoformat(), notes=notes)

    # ── READ ─────────────────────────────────────────────────────────────────

    def get_next(self, projects: list = None) -> dict | None:
        """
        Return next runnable task: not approval_required, all depends_on satisfied,
        status=queued. Sorted by priority ASC, review_priority DESC (harder first).
        """
        projects = projects or []
        with self._conn() as conn:
            completed_ids = {
                r[0] for r in conn.execute(
                    "SELECT id FROM tasks WHERE status IN ('completed', 'pending_review')"
                )
            }

        with self._conn() as conn:
            placeholders = ",".join("?" * len(projects)) if projects else ""
            where_proj   = f"AND project IN ({placeholders})" if projects else ""
            rows = conn.execute(f"""
                SELECT * FROM tasks
                WHERE status = 'queued'
                  AND approval_required = 0
                  {where_proj}
                ORDER BY priority ASC, review_priority DESC
            """, projects).fetchall()

        for row in rows:
            task = _row_to_dict(row)
            if all(dep in completed_ids for dep in task["depends_on"]):
                return task
        return None

    def get_pending_review(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM tasks WHERE status = 'pending_review'
                ORDER BY review_priority DESC, completed_at ASC
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

    def total_unblocked(self, projects: list = None) -> int:
        projects = projects or []
        with self._conn() as conn:
            completed_ids = {
                r[0] for r in conn.execute(
                    "SELECT id FROM tasks WHERE status IN ('completed', 'pending_review')"
                )
            }
            placeholders = ",".join("?" * len(projects)) if projects else ""
            where_proj   = f"AND project IN ({placeholders})" if projects else ""
            rows = conn.execute(f"""
                SELECT id, depends_on FROM tasks
                WHERE status = 'queued' AND approval_required = 0 {where_proj}
            """, projects).fetchall()

        count = 0
        for row in rows:
            deps = json.loads(row["depends_on"] or "[]")
            if all(d in completed_ids for d in deps):
                count += 1
        return count

    def stats(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT status, COUNT(*) as n FROM tasks GROUP BY status
            """).fetchall()
            cost = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM tasks"
            ).fetchone()[0]
        return {r["status"]: r["n"] for r in rows} | {"total_cost_usd": round(cost, 4)}

    # ── GAP-FILL ─────────────────────────────────────────────────────────────

    def get_gap_fill_tasks(self) -> list[dict]:
        """Always-available low-stakes tasks when the main queue runs dry."""
        return [
            {
                "id": f"gap_lang_{uuid.uuid4().hex[:8]}",
                "project": "lang",
                "priority": 2,
                "description": "Expand randomization pools for any completed language scenes (add 5+ variants to each pool)",
                "approval_required": False,
                "complexity": "low",
                "rationale": "More dialogue variety reduces repetition for learners",
                "effort_category": "gap-fill",
                "perspective": "speech_linguist",
                "depends_on": [],
            },
            {
                "id": f"gap_meridian_{uuid.uuid4().hex[:8]}",
                "project": "meridian",
                "priority": 2,
                "description": "Generate JSDoc comments for all undocumented exported functions in meridian-mobile/src",
                "approval_required": False,
                "complexity": "low",
                "rationale": "Improves Aider context quality on subsequent tasks",
                "effort_category": "docs",
                "perspective": "engineering_architect",
                "depends_on": [],
            },
            {
                "id": f"gap_rts_{uuid.uuid4().hex[:8]}",
                "project": "rts",
                "priority": 2,
                "description": "Generate XML summary comments for all public C# methods missing documentation",
                "approval_required": False,
                "complexity": "low",
                "rationale": "Unity editor tooling uses XML docs for inspector tooltips",
                "effort_category": "docs",
                "perspective": "engineering_architect",
                "depends_on": [],
            },
        ]
