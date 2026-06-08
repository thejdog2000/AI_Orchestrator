"""
commands.py — Shared command logic for Orchestrator.

Imported by both agents/orchestrator_bot.py (Discord) and agents/o.py (CLI).
Neither should duplicate intent parsing or handler logic; both import from here.
"""

import os
import sys
import json
import logging
import sqlite3
from pathlib import Path

import requests

# ── PATH SETUP ────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent   # agents/
ORCHESTRATOR = BASE_DIR.parent         # Orchestrator/

sys.path.insert(0, str(ORCHESTRATOR))

from config import CFG, DB_PATH, MINIMAX_SPEND_CAP
from spend  import SpendTracker
from task_queue import TaskQueue
import notify

# ── CONFIG ────────────────────────────────────────────────────────────────────

log = logging.getLogger("orchestrator_bot")

OLLAMA_BASE        = CFG.get("OLLAMA_BASE",         "http://localhost:11434")
OLLAMA_MODEL_CHAT  = CFG.get("OLLAMA_MODEL_DIGEST", "qwen3:14b")
SPEND_LOG          = ORCHESTRATOR / "logs" / "spend.json"
DASHBOARD_PORT     = int(os.environ.get("DASHBOARD_PORT", "8080"))


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _tq() -> TaskQueue:
    return TaskQueue(DB_PATH)


def _st() -> SpendTracker:
    return SpendTracker(SPEND_LOG, MINIMAX_SPEND_CAP)


# ── OLLAMA INTENT PARSING ─────────────────────────────────────────────────────

_INTENT_SYSTEM = """You are an intent parser for an AI orchestrator assistant.
Parse the user message into a JSON action object.

Supported actions and their required fields:
  {"action": "status"}                            — queue status across all projects
  {"action": "status", "project": "lang"}         — status for one project
  {"action": "approve", "target": "task_id"}      — approve a specific task by ID
  {"action": "approve", "target": "all"}          — approve all pending tasks
  {"action": "approve", "target": "all lang"}     — approve all pending lang tasks
  {"action": "reject", "target": "task_id"}       — reject a specific task
  {"action": "digest", "period": "morning"}       — show digest (morning/afternoon/evening/week)
  {"action": "spend"}                             — show spend breakdown
  {"action": "queued"}                            — list queued tasks
  {"action": "blocked"}                           — list blocked/pending-review tasks
  {"action": "pause", "project": "lang"}          — pause a project
  {"action": "pause", "project": "all"}           — pause everything
  {"action": "resume", "project": "lang"}         — resume a project
  {"action": "requeue", "target": "task_id"}      — reset a failed task back to queued
  {"action": "running"}                           — what's running right now
  {"action": "completed"}                         — what was completed today
  {"action": "help"}                              — show available commands
  {"action": "unknown"}                           — cannot parse

Return ONLY the JSON object, no prose. If the message contains a bare task ID
like "approve lang_001", parse it as {"action": "approve", "target": "lang_001"}.
"""


def _parse_intent(message: str) -> dict:
    """Use Ollama to parse natural language into a structured intent dict."""
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model":  OLLAMA_MODEL_CHAT,
                "prompt": f"{_INTENT_SYSTEM}\n\nUser message: {message}",
                "stream": False,
                "format": "json",
                "options": {"num_ctx": 4096, "num_predict": 200},
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "{}")
        # qwen3 thinking models wrap output in <think>...</think> before the JSON
        if "<think>" in raw:
            raw = raw.split("</think>")[-1].strip()
        parsed = json.loads(raw)
        if not parsed or parsed.get("action") == "unknown":
            return _keyword_fallback(message)
        return parsed
    except Exception as e:
        log.warning(f"Intent parse failed: {e}")
        return _keyword_fallback(message)


def _keyword_fallback(message: str) -> dict:
    """Simple keyword matching when Ollama is unavailable."""
    m = message.lower().strip()
    if m.startswith("approve all "):
        proj = m.split("approve all ", 1)[1].strip()
        return {"action": "approve", "target": f"all {proj}"}
    if m in ("approve all", "approve everything"):
        return {"action": "approve", "target": "all"}
    if m.startswith("approve "):
        return {"action": "approve", "target": m.split("approve ", 1)[1].strip()}
    if m.startswith("reject "):
        return {"action": "reject", "target": m.split("reject ", 1)[1].strip()}
    if m in ("status", "stats"):
        return {"action": "status"}
    if "status" in m and len(m.split()) == 2:
        return {"action": "status", "project": m.split()[1]}
    if m in ("spend", "cost", "how much"):
        return {"action": "spend"}
    if any(x in m for x in ("blocked", "pending", "review")):
        return {"action": "blocked"}
    if any(x in m for x in ("queued", "queue", "what's queued", "tonight", "next up")):
        return {"action": "queued"}
    if any(x in m for x in ("running", "what's running", "currently", "active")):
        return {"action": "running"}
    if any(x in m for x in ("completed", "done today", "what did we build", "what was built", "what happened")):
        return {"action": "completed"}
    if m.startswith("pause "):
        return {"action": "pause", "project": m.split("pause ", 1)[1].strip()}
    if m.startswith("resume "):
        return {"action": "resume", "project": m.split("resume ", 1)[1].strip()}
    if m.startswith("requeue ") or m.startswith("retry "):
        task_id = m.split(" ", 1)[1].strip()
        return {"action": "requeue", "target": task_id}
    if m in ("help", "?", "commands"):
        return {"action": "help"}
    return {"action": "unknown"}


# ── ACTION HANDLERS ───────────────────────────────────────────────────────────

def _handle_status(project: str = None) -> str:
    tq     = _tq()
    stats  = tq.stats()
    st     = _st()

    lines = ["**📊 Orchestrator Status**"]

    projects = [project] if project else CFG.get("ENABLED_PROJECTS", [])
    for proj in projects:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project=? AND status='queued'", (proj,)
        ).fetchone()
        conn.close()
        queued = row[0] if row else 0
        lines.append(f"  **{proj}**: {queued} queued")

    lines.append("")
    for status, count in sorted(stats.items()):
        if status != "total_cost_usd":
            lines.append(f"  {status.replace('_', ' ')}: **{count}**")

    monthly = st.monthly_spend()
    lines.append(f"\n  Monthly spend: **${monthly:.2f}** / ${MINIMAX_SPEND_CAP:.0f}")

    return "\n".join(lines)


def _handle_approve(target: str) -> str:
    tq = _tq()

    if target == "all":
        tasks = tq.get_pending_review()
        if not tasks:
            return "No pending tasks to approve."
        for t in tasks:
            tq.mark_completed(t)
        return f"✅ Approved {len(tasks)} task(s)."

    if target.startswith("all "):
        proj   = target.split(" ", 1)[1].strip()
        tasks  = [t for t in tq.get_pending_review() if t["project"] == proj]
        if not tasks:
            return f"No pending tasks for project `{proj}`."
        for t in tasks:
            tq.mark_completed(t)
        return f"✅ Approved {len(tasks)} `{proj}` task(s)."

    # Specific task ID
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row  = conn.execute("SELECT * FROM tasks WHERE id=?", (target,)).fetchone()
    conn.close()

    if not row:
        return f"❌ Task `{target}` not found."

    task = dict(row)
    if task["status"] not in ("pending_review", "queued", "running"):
        return f"Task `{target}` has status `{task['status']}` — nothing to approve."

    tq.mark_completed(task)
    notify.post("live", f"✅  **[{task['project']}]** Task approved: `{target}`")
    return f"✅ Approved `{target}` ({task['project']}: {task['description'][:60]})"


def _handle_reject(target: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row  = conn.execute("SELECT * FROM tasks WHERE id=?", (target,)).fetchone()
    conn.close()

    if not row:
        return f"❌ Task `{target}` not found."

    task = dict(row)
    tq   = _tq()
    tq.mark_failed(task, notes="rejected by Jacob via Discord")
    notify.post("live", f"❌  **[{task['project']}]** Task rejected: `{target}`")
    return f"❌ Rejected `{target}` — marked failed."


def _handle_spend() -> str:
    st      = _st()
    monthly = st.monthly_spend()
    daily   = st.daily_spend()
    pct     = monthly / MINIMAX_SPEND_CAP * 100

    return (
        f"**💸 Spend Tracker**\n"
        f"  Today:   **${daily:.4f}**\n"
        f"  Monthly: **${monthly:.2f}** / ${MINIMAX_SPEND_CAP:.0f} ({pct:.1f}%)\n"
        f"  Cap set at platform.minimax.io → Billing"
    )


def _handle_blocked() -> str:
    tq    = _tq()
    tasks = tq.get_pending_review()
    if not tasks:
        return "✅ No blocked or pending-review tasks."
    lines = [f"**⏸ Pending Review ({len(tasks)} tasks)**"]
    for t in tasks[:10]:
        lines.append(
            f"  `{t['id']}` [{t['project']}] {t['description'][:60]}… "
            f"(`{t.get('complexity','?')}` · rp:{t.get('review_priority','?')})"
        )
    if len(tasks) > 10:
        lines.append(f"  … and {len(tasks) - 10} more")
    lines.append(f"\n  Dashboard: http://localhost:{DASHBOARD_PORT}")
    return "\n".join(lines)


def _handle_queued() -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status='queued' ORDER BY priority ASC, review_priority DESC LIMIT 15"
    ).fetchall()
    conn.close()

    if not rows:
        return "No tasks queued."
    lines = [f"**📋 Queued Tasks ({len(rows)} shown)**"]
    for r in rows:
        t = dict(r)
        lines.append(
            f"  `{t['id']}` [{t['project']}] {t['description'][:60]}…"
        )
    return "\n".join(lines)


def _handle_completed() -> str:
    tq    = _tq()
    tasks = tq.get_completed_today()
    if not tasks:
        return "No tasks completed today yet."
    lines = [f"**✅ Completed Today ({len(tasks)} tasks)**"]
    for t in tasks[:15]:
        ts = (t.get("completed_at") or "")[:16]
        lines.append(f"  `{t['id']}` [{t['project']}] {t['description'][:60]}… ({ts})")
    if len(tasks) > 15:
        lines.append(f"  … and {len(tasks) - 15} more")
    return "\n".join(lines)


def _handle_running() -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
    conn.close()
    if not rows:
        return "Nothing running right now."
    lines = ["**⚙️ Currently Running**"]
    for r in rows:
        t = dict(r)
        lines.append(f"  `{t['id']}` [{t['project']}] {t['description'][:70]}…")
    return "\n".join(lines)


def _handle_requeue(target: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row  = conn.execute("SELECT * FROM tasks WHERE id=?", (target,)).fetchone()
    conn.close()

    if not row:
        return f"❌ Task `{target}` not found."

    task = dict(row)
    if task["status"] == "queued":
        return f"Task `{target}` is already queued."

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE tasks SET status='queued', notes=NULL, rejection_reason=NULL WHERE id=?",
        (target,),
    )
    conn.commit()
    conn.close()

    notify.post("live", f"🔁  **[{task['project']}]** Task requeued: `{target}`")
    return f"🔁 Requeued `{target}` — [{task['project']}] {task['description'][:60]}"


def _handle_pause(project: str) -> str:
    # IPC via pause state file — orchestrator_main polls this every cycle.
    # Works across separate processes (bot + orchestrator run independently).
    import json as _json
    pause_file = ORCHESTRATOR / "pause_state.json"
    try:
        state = _json.loads(pause_file.read_text()) if pause_file.exists() else {"paused": []}
        if project in ("all", "everything"):
            from config import PROJECTS
            state["paused"] = list(PROJECTS)
            pause_file.write_text(_json.dumps(state, indent=2))
            return "⏸ All projects paused. Send `resume all` to unpause."
        if project not in state["paused"]:
            state["paused"].append(project)
        pause_file.write_text(_json.dumps(state, indent=2))
        return f"⏸ `{project}` paused. Send `resume {project}` to unpause."
    except Exception as e:
        return f"⚠️ Pause failed: {e}"


def _handle_resume(project: str) -> str:
    import json as _json
    pause_file = ORCHESTRATOR / "pause_state.json"
    try:
        state = _json.loads(pause_file.read_text()) if pause_file.exists() else {"paused": []}
        if project in ("all", "everything"):
            state["paused"] = []
            pause_file.write_text(_json.dumps(state, indent=2))
            return "▶️ All projects resumed."
        if project in state["paused"]:
            state["paused"].remove(project)
        pause_file.write_text(_json.dumps(state, indent=2))
        return f"▶️ `{project}` resumed."
    except Exception as e:
        return f"⚠️ Resume failed: {e}"


def _handle_help() -> str:
    return """**🤖 Orchestrator Bot — Available Commands**

**Query**
  `what's running` / `running`              — active tasks
  `what happened overnight` / `digest`      — morning digest
  `status` / `status lang`                  — queue stats
  `how much have we spent` / `spend`        — cost breakdown
  `what's queued` / `queued`                — queued task list
  `blocked` / `show blocked`                — pending review items
  `what did we build today` / `completed`   — completed today

**Actions**
  `approve lang_001`                         — approve specific task
  `approve all lang`                         — bulk approve all lang tasks
  `approve everything` / `approve all`       — approve all pending (caution!)
  `reject lang_001`                          — reject task, mark failed
  `requeue lang_003`                         — reset a failed task back to queued

**Control**
  `pause lang` / `pause everything`          — stop project from running
  `resume lang` / `resume all`               — re-enable project

**Dashboard**
  http://localhost:8080  (when at home)
"""


# ── DISPATCH ──────────────────────────────────────────────────────────────────

def _dispatch(intent: dict, raw_message: str) -> str:
    """Map a parsed intent to a handler. Returns string response."""
    action  = intent.get("action", "unknown")
    project = intent.get("project", "").strip().lower()
    target  = intent.get("target", "").strip()
    period  = intent.get("period", "morning")

    # Normalize natural language "everything" → "all"
    if project == "everything":
        project = "all"
    if target == "everything":
        target = "all"

    if action == "status":
        return _handle_status(project or None)
    elif action == "approve":
        if not target:
            return "Usage: `approve <task_id>` or `approve all [project]`"
        return _handle_approve(target)
    elif action == "reject":
        if not target:
            return "Usage: `reject <task_id>`"
        return _handle_reject(target)
    elif action == "spend":
        return _handle_spend()
    elif action == "blocked":
        return _handle_blocked()
    elif action == "queued":
        return _handle_queued()
    elif action == "completed":
        return _handle_completed()
    elif action == "running":
        return _handle_running()
    elif action == "requeue":
        if not target:
            return "Usage: `requeue <task_id>`"
        return _handle_requeue(target)
    elif action == "pause":
        return _handle_pause(project or "all")
    elif action == "resume":
        return _handle_resume(project or "all")
    elif action == "help":
        return _handle_help()
    elif action == "digest":
        digest_path = ORCHESTRATOR / "logs" / f"digest_{period}.txt"
        if digest_path.exists():
            text = digest_path.read_text()[:1800]
            return f"**📋 {period.capitalize()} Digest**\n```\n{text}\n```"
        return f"No {period} digest found yet."
    else:
        return (
            f"Sorry, I couldn't parse that. Try `help` for a command list.\n"
            f"(Parsed intent: `{intent}`)"
        )
