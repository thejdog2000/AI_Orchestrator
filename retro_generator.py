"""
retro_generator.py
Generates a daily retrospective from the last 24 hours of orchestrator activity.

Each retro covers: completed, failed, and pending-review tasks, per-project stats,
total cost, quality scores, and an Ollama-written narrative with wins, failure
patterns, and concrete recommendations.

Output: retros/YYYY-MM-DD.json  (one file per day, kept forever)
Called: daily at midnight by orchestrator_main.py scheduler
Also callable manually: python retro_generator.py
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from config import DB_PATH, RETROS_DIR, BASE_DIR

SPRINT_LEAD_PERSONA = (
    BASE_DIR / "agents" / "personas" / "review" / "sprint_lead.md"
)

log = logging.getLogger(__name__)


# ── DATA COLLECTION ──────────────────────────────────────────────────────────

def _query_window(hours: int = 24) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the retrospective window."""
    end   = datetime.now()
    start = end - timedelta(hours=hours)
    return start.isoformat(), end.isoformat()


def _load_tasks_in_window(start: str, end: str) -> dict:
    """
    Query the DB for tasks that changed status within the window.
    Groups into completed, failed, and pending_review.
    """
    if not DB_PATH.exists():
        return {"completed": [], "failed": [], "pending_review": []}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def fetch(status: str, ts_col: str) -> list[dict]:
        rows = conn.execute(f"""
            SELECT * FROM tasks
            WHERE status = ?
              AND {ts_col} >= ? AND {ts_col} <= ?
            ORDER BY {ts_col} DESC
        """, (status, start, end)).fetchall()
        return [dict(r) for r in rows]

    result = {
        "completed":      fetch("completed",      "completed_at"),
        "failed":         fetch("failed",          "completed_at"),
        "pending_review": fetch("pending_review",  "completed_at"),
    }
    conn.close()
    return result


def _compute_stats(tasks: dict) -> dict:
    """Aggregate stats across all task groups."""
    all_tasks = tasks["completed"] + tasks["failed"] + tasks["pending_review"]

    total_cost     = sum(t.get("cost_usd") or 0 for t in tasks["completed"])
    total_tokens   = sum(t.get("actual_tokens") or 0 for t in tasks["completed"])
    quality_scores = [t["quality_score"] for t in tasks["completed"] if t.get("quality_score")]
    quality_avg    = round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None

    projects = sorted({t["project"] for t in all_tasks})

    # Per-project breakdown including features landed by effort category
    by_project = {}
    for proj in projects:
        c = [t for t in tasks["completed"]      if t["project"] == proj]
        f = [t for t in tasks["failed"]         if t["project"] == proj]
        p = [t for t in tasks["pending_review"] if t["project"] == proj]
        by_category = {}
        for t in c:
            cat = t.get("effort_category") or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1
        by_project[proj] = {
            "completed":      len(c),
            "failed":         len(f),
            "pending_review": len(p),
            "cost":           round(sum(t.get("cost_usd") or 0 for t in c), 5),
            "by_category":    by_category,  # e.g. {"feature": 2, "bugfix": 1}
        }

    # Failure pattern: most common error codes
    error_counts = {}
    for t in tasks["failed"]:
        err = (t.get("notes") or "unknown").split(":")[0].strip()
        error_counts[err] = error_counts.get(err, 0) + 1
    top_errors = sorted(error_counts.items(), key=lambda x: -x[1])

    # Perspective performance
    persp_stats = {}
    for t in tasks["completed"] + tasks["failed"]:
        p = t.get("perspective") or "unknown"
        if p not in persp_stats:
            persp_stats[p] = {"completed": 0, "failed": 0}
        if t["status"] == "completed":
            persp_stats[p]["completed"] += 1
        else:
            persp_stats[p]["failed"] += 1

    return {
        "completed":      len(tasks["completed"]),
        "failed":         len(tasks["failed"]),
        "pending_review": len(tasks["pending_review"]),
        "total":          len(all_tasks),
        "total_cost_usd": round(total_cost, 5),
        "total_tokens":   total_tokens,
        "quality_avg":    quality_avg,
        "projects":       projects,
        "by_project":     by_project,
        "top_errors":     top_errors[:5],
        "perspectives":   persp_stats,
    }


def _compute_sprint_features(days: int = 7) -> dict:
    """
    Count completed tasks by effort_category per project over the sprint window.
    Used to show cumulative features landed this sprint, not just the last 24h.
    """
    if not DB_PATH.exists():
        return {}
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT project, effort_category, COUNT(*) as n
        FROM tasks
        WHERE status = 'completed' AND completed_at >= ?
        GROUP BY project, effort_category
        ORDER BY project, effort_category
    """, (cutoff,)).fetchall()
    conn.close()

    result = {}
    for r in rows:
        proj = r["project"]
        if proj not in result:
            result[proj] = {}
        result[proj][r["effort_category"]] = r["n"]
    return result


# ── OLLAMA NARRATIVE ─────────────────────────────────────────────────────────

def _load_sprint_lead_persona() -> str:
    """Load the sprint lead persona, falling back to a brief inline version."""
    if SPRINT_LEAD_PERSONA.exists():
        return SPRINT_LEAD_PERSONA.read_text().strip()
    return (
        "You are a technical sprint lead running a daily retrospective. "
        "Be direct, analytical, and specific. No generic encouragement."
    )


def _build_narrative_prompt(stats: dict, tasks: dict, date: str, sprint_features: dict) -> str:
    """Build the Ollama prompt for the retrospective narrative."""

    def task_lines(task_list: list, max_items: int = 8) -> str:
        lines = []
        for t in task_list[:max_items]:
            status_note = f"[{t.get('notes','')[:60]}]" if t.get("notes") else ""
            score = f" score={t['quality_score']}/10" if t.get("quality_score") else ""
            lines.append(f"  - [{t['project']}] {t['description'][:80]}{score} {status_note}")
        if len(task_list) > max_items:
            lines.append(f"  ... and {len(task_list) - max_items} more")
        return "\n".join(lines) if lines else "  None"

    errors = ", ".join(f"{e}×{n}" for e, n in stats["top_errors"]) or "none"

    sprint_lines = []
    for proj, cats in sprint_features.items():
        cats_str = ", ".join(f"{n} {cat}" for cat, n in sorted(cats.items()))
        sprint_lines.append(f"  {proj}: {cats_str}")
    sprint_block = "\n".join(sprint_lines) if sprint_lines else "  No data"

    persona = _load_sprint_lead_persona()

    return f"""{persona}

---

Daily retrospective for {date}. Write your analysis now.

24H STATS:
- Completed: {stats['completed']} | Failed: {stats['failed']} | Pending review: {stats['pending_review']}
- Total cost: ${stats['total_cost_usd']:.4f} | Quality avg: {stats['quality_avg'] or 'n/a'}/10
- Active projects: {', '.join(stats['projects']) or 'none'}
- Top errors: {errors}

SPRINT FEATURES LANDED (last 7 days, by effort category):
{sprint_block}

COMPLETED TODAY:
{task_lines(tasks['completed'])}

FAILED TODAY:
{task_lines(tasks['failed'])}

PENDING REVIEW:
{task_lines(tasks['pending_review'])}

Return ONLY this JSON (no markdown, no preamble):
{{
  "summary": "2-3 sentence overview of the day",
  "wins": "What went well and why — specific to these tasks",
  "failures": "Pattern across failures, root cause if identifiable",
  "patterns": "Cross-task trends, quality signals, systemic observations",
  "recommendations": "3-5 specific actionable suggestions for the next 24 hours",
  "sprint_health": "1-2 sentences on sprint progress based on features landed vs pending"
}}"""


def _generate_narrative(stats: dict, tasks: dict, date: str, sprint_features: dict) -> dict:
    """Call Ollama to write the retrospective narrative. Falls back to empty strings."""
    _KEYS = ("summary", "wins", "failures", "patterns", "recommendations", "sprint_health")
    try:
        import executor
        from config import CFG
        if not executor._config:
            executor.configure(CFG)
        from executor import ollama_generate
        raw = ollama_generate(
            _build_narrative_prompt(stats, tasks, date, sprint_features),
            max_tokens=1200,
            json_mode=True,
            temperature=0.4,
        )
        result = json.loads(raw)
        for key in _KEYS:
            if key not in result:
                result[key] = ""
        return result
    except Exception as e:
        log.warning(f"Retro narrative generation failed: {e}")
        return {k: "" for k in _KEYS}


# ── SAVE & LOAD ──────────────────────────────────────────────────────────────

def _save_retro(retro: dict) -> Path:
    RETROS_DIR.mkdir(exist_ok=True)
    path = RETROS_DIR / f"{retro['date']}.json"
    path.write_text(json.dumps(retro, indent=2, default=str))
    log.info(f"Retro saved → {path}")
    return path


def load_all_retros(max_days: int = 90) -> list[dict]:
    """Load all saved retrospectives, newest first, up to max_days."""
    if not RETROS_DIR.exists():
        return []
    files = sorted(RETROS_DIR.glob("*.json"), reverse=True)[:max_days]
    retros = []
    for f in files:
        try:
            retros.append(json.loads(f.read_text()))
        except Exception:
            pass
    return retros


# ── PUBLIC API ───────────────────────────────────────────────────────────────

def generate_retrospective(hours: int = 24) -> dict:
    """
    Generate and save a retrospective covering the last `hours` of activity.
    Returns the retro dict (also written to retros/YYYY-MM-DD.json).
    """
    date  = datetime.now().strftime("%Y-%m-%d")
    start, end = _query_window(hours)

    log.info(f"Generating retrospective for {date} ({hours}h window)")

    tasks           = _load_tasks_in_window(start, end)
    stats           = _compute_stats(tasks)
    sprint_features = _compute_sprint_features(days=7)
    narrative       = _generate_narrative(stats, tasks, date, sprint_features)

    retro = {
        "date":            date,
        "generated_at":    datetime.now().isoformat(),
        "window_hours":    hours,
        "period":          {"start": start, "end": end},
        "stats":           stats,
        "sprint_features": sprint_features,
        "narrative":       narrative,
        "tasks": {
            "completed":      tasks["completed"],
            "failed":         tasks["failed"],
            "pending_review": tasks["pending_review"],
        },
    }

    _save_retro(retro)
    log.info(f"Retro complete: {stats['completed']} done, {stats['failed']} failed, "
             f"${stats['total_cost_usd']:.4f} spent")
    return retro


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    retro = generate_retrospective(hours=hours)
    print(json.dumps(retro["narrative"], indent=2))
