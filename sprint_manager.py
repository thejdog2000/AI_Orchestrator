"""
sprint_manager.py
24-hour sprint lifecycle manager for the Orchestrator.

Sprint = 24-hour window. At the end of each sprint:
  1. Condense completed tasks → one-line snippets in sprint_review_NNN.md
  2. Carry unfinished queued/running tasks to the next sprint (flagged carryover=1)
  3. Advance sprint counter

Usage:
  python sprint_manager.py status          — show current sprint + time remaining
  python sprint_manager.py start           — start a new sprint now
  python sprint_manager.py end             — close sprint, generate review, carry over
  python sprint_manager.py review          — print the last sprint review

Scheduled close: add to orchestrator_main.py scheduler at midnight, or run manually.

sprint_state.json schema:
  { "sprint": 1, "started_at": "<iso>", "ends_at": "<iso>" }
"""

import json
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR    = Path(__file__).parent
STATE_FILE  = BASE_DIR / "sprint_state.json"
REVIEWS_DIR = BASE_DIR / "sprint_reviews"
DB_PATH     = BASE_DIR / "orchestrator.db"

SPRINT_HOURS = 24


# ── STATE ──────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _current_sprint() -> dict:
    state = _load_state()
    if not state:
        return None
    state["started_at_dt"] = datetime.fromisoformat(state["started_at"])
    state["ends_at_dt"]    = datetime.fromisoformat(state["ends_at"])
    return state


# ── DB HELPERS ─────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _ensure_sprint_columns():
    """Add sprint_id and carryover columns if not present (schema migration)."""
    with _conn() as conn:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "sprint_id" not in existing:
            conn.execute("ALTER TABLE tasks ADD COLUMN sprint_id INTEGER DEFAULT 1")
        if "carryover" not in existing:
            conn.execute("ALTER TABLE tasks ADD COLUMN carryover INTEGER DEFAULT 0")


# ── COMMANDS ───────────────────────────────────────────────────────────────────

def cmd_status():
    state = _current_sprint()
    if not state:
        print("No active sprint. Run: python sprint_manager.py start")
        return

    now       = datetime.now()
    ends_at   = state["ends_at_dt"]
    remaining = ends_at - now

    if remaining.total_seconds() <= 0:
        print(f"Sprint {state['sprint']} ENDED {abs(int(remaining.total_seconds()//60))} min ago")
        print("Run: python sprint_manager.py end  to close it and start the next sprint")
        return

    h, m = divmod(int(remaining.total_seconds() // 60), 60)
    print(f"\nSprint {state['sprint']}")
    print(f"  Started:   {state['started_at']}")
    print(f"  Ends:      {state['ends_at']}")
    print(f"  Remaining: {h}h {m}m")

    if DB_PATH.exists():
        with _conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM tasks "
                "WHERE sprint_id=? GROUP BY status",
                (state["sprint"],)
            ).fetchall()
        for r in rows:
            print(f"  {r['status']:<16} {r['n']}")
    print()


def cmd_start(sprint_num: int = None):
    state = _load_state()
    if state:
        existing_num = state.get("sprint", 1)
        print(f"Sprint {existing_num} already active (started {state['started_at']})")
        print("Run 'end' first to close it, or edit sprint_state.json to reset.")
        return

    num        = sprint_num or 1
    now        = datetime.now()
    ends_at    = now + timedelta(hours=SPRINT_HOURS)
    new_state  = {
        "sprint":     num,
        "started_at": now.isoformat(timespec="seconds"),
        "ends_at":    ends_at.isoformat(timespec="seconds"),
    }
    _save_state(new_state)

    if DB_PATH.exists():
        _ensure_sprint_columns()
        with _conn() as conn:
            conn.execute(
                "UPDATE tasks SET sprint_id=? WHERE sprint_id IS NULL OR sprint_id=0",
                (num,)
            )

    print(f"Sprint {num} started — ends {ends_at.strftime('%Y-%m-%d %H:%M')}")


def cmd_end():
    state = _current_sprint()
    if not state:
        print("No active sprint. Run: python sprint_manager.py start")
        return

    sprint_num = state["sprint"]
    print(f"Closing Sprint {sprint_num}...")

    if not DB_PATH.exists():
        print("No orchestrator.db found — nothing to carry over.")
        _advance_sprint(state)
        return

    _ensure_sprint_columns()

    with _conn() as conn:
        # All tasks in this sprint
        completed = conn.execute(
            "SELECT * FROM tasks WHERE sprint_id=? AND status='completed' "
            "ORDER BY completed_at ASC",
            (sprint_num,)
        ).fetchall()

        carryover = conn.execute(
            "SELECT * FROM tasks WHERE sprint_id=? AND status IN ('queued','running','pending_review')",
            (sprint_num,)
        ).fetchall()

        failed = conn.execute(
            "SELECT * FROM tasks WHERE sprint_id=? AND status='failed'",
            (sprint_num,)
        ).fetchall()

    # Generate sprint review
    _generate_review(sprint_num, state, completed, carryover, failed)

    # Advance sprint
    next_num = sprint_num + 1
    with _conn() as conn:
        # Mark carried-over tasks with new sprint number + carryover flag
        ids = [r["id"] for r in carryover]
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE tasks SET sprint_id=?, carryover=1 WHERE id IN ({placeholders})",
                [next_num] + ids
            )

    _advance_sprint(state)
    print(f"Sprint {sprint_num} closed → Sprint {next_num} started")
    print(f"  Completed: {len(completed)} tasks")
    print(f"  Carried over: {len(carryover)} tasks")
    print(f"  Review: sprint_reviews/sprint_{sprint_num:03d}.md")


def cmd_review():
    REVIEWS_DIR.mkdir(exist_ok=True)
    reviews = sorted(REVIEWS_DIR.glob("sprint_*.md"), reverse=True)
    if not reviews:
        print("No sprint reviews yet.")
        return
    latest = reviews[0]
    print(latest.read_text())


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _advance_sprint(state: dict):
    next_num = state["sprint"] + 1
    now      = datetime.now()
    ends_at  = now + timedelta(hours=SPRINT_HOURS)
    _save_state({
        "sprint":     next_num,
        "started_at": now.isoformat(timespec="seconds"),
        "ends_at":    ends_at.isoformat(timespec="seconds"),
    })


def _one_liner(task) -> str:
    """Condense a task to a single digestible line for sprint review."""
    d = dict(task)
    effort = d.get("effort_category", "feature")
    proj   = d.get("project", "?")
    desc   = d.get("description", "")

    # Pull first clause (up to first period, comma, or 80 chars)
    for ch in [".", ",", " — ", " and "]:
        if ch in desc[:90]:
            desc = desc[:desc.index(ch, 0, 90)].strip()
            break
    else:
        desc = desc[:75].rstrip()
    if len(d.get("description","")) > len(desc)+2:
        desc += "…"

    commit = d.get("commit_hash", "")
    cost   = d.get("cost_usd", 0) or 0
    suffix = f"  `{commit}`" if commit else ""
    cost_s = f"  ${cost:.4f}" if cost > 0 else ""
    return f"- [{proj}/{effort}] {desc}{suffix}{cost_s}"


def _generate_review(sprint_num: int, state: dict, completed, carryover, failed):
    REVIEWS_DIR.mkdir(exist_ok=True)
    now = datetime.now()

    lines = [
        f"# Sprint {sprint_num} Review",
        f"**Period:** {state['started_at']} → {state['ends_at']}",
        f"**Closed:**  {now.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Summary stats
    total_cost = sum((dict(r).get("cost_usd") or 0) for r in list(completed) + list(failed))
    lines += [
        "## Summary",
        f"| | Count |",
        f"|---|---|",
        f"| Completed | {len(completed)} |",
        f"| Carried over | {len(carryover)} |",
        f"| Failed | {len(failed)} |",
        f"| Total cost | ${total_cost:.4f} |",
        "",
    ]

    # Completed — one line each
    if completed:
        lines += ["## Completed", ""]
        for t in completed:
            lines.append(_one_liner(t))
        lines.append("")

    # Carried over
    if carryover:
        lines += ["## Carried Over to Next Sprint", ""]
        for t in carryover:
            d = dict(t)
            lines.append(f"- [{d.get('project','?')}] {d.get('description','')[:80]}…  `{d.get('id','')}`")
        lines.append("")

    # Failed
    if failed:
        lines += ["## Failed", ""]
        for t in failed:
            d = dict(t)
            notes = d.get("notes","") or ""
            lines.append(f"- [{d.get('project','?')}] {d.get('description','')[:70]}…  _{notes[:60]}_")
        lines.append("")

    out = REVIEWS_DIR / f"sprint_{sprint_num:03d}.md"
    out.write_text("\n".join(lines))
    return out


# ── ENTRY POINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        cmd_status()
    elif cmd == "start":
        num = int(sys.argv[2]) if len(sys.argv) > 2 else None
        cmd_start(num)
    elif cmd == "end":
        cmd_end()
    elif cmd == "review":
        cmd_review()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python sprint_manager.py [status|start|end|review]")
