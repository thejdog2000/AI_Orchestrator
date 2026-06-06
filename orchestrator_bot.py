"""
orchestrator_bot.py
Discord PA interface for the Orchestrator. Run as a separate process.

    python orchestrator_bot.py

Three channels (IDs set via env vars — see config.py):

  #orchestrator-live    read-only task feed posted by notify.py
  #orchestrator-blocked approval embeds posted by notify.py
  #orchestrator-chat    bot listens here; accepts natural language + direct commands

Natural language parsing: Ollama qwen3:14b parses messages to structured
intent JSON, then the bot dispatches to the appropriate action.

Required env vars:
  DISCORD_BOT_TOKEN
  DISCORD_CHANNEL_LIVE
  DISCORD_CHANNEL_BLOCKED
  DISCORD_CHANNEL_CHAT

Optional:
  DISCORD_USER_ID     — Jacob's user ID (for DMs on critical events)
  DASHBOARD_PORT      — defaults to 8080
"""

import os
import sys
import json
import logging
import asyncio
from pathlib import Path
from datetime import datetime

import discord
import requests

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("orchestrator_bot")

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent

# Add parent to sys.path so we can import orchestrator modules
sys.path.insert(0, str(BASE_DIR))

from config import CFG, DB_PATH, MINIMAX_SPEND_CAP
from spend  import SpendTracker
from task_queue import TaskQueue
import notify

OLLAMA_BASE        = CFG.get("OLLAMA_BASE",         "http://localhost:11434")
OLLAMA_MODEL_CHAT  = CFG.get("OLLAMA_MODEL_DIGEST", "qwen3:14b")
SPEND_LOG          = BASE_DIR / "logs" / "spend.json"
DASHBOARD_PORT     = int(os.environ.get("DASHBOARD_PORT", "8080"))

# ── DISCORD SETUP ─────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client  = discord.Client(intents=intents)


def _chat_channel_id() -> int | None:
    raw = os.environ.get("DISCORD_CHANNEL_CHAT", "")
    return int(raw) if raw.isdigit() else None


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
        return json.loads(raw)
    except Exception as e:
        log.warning(f"Intent parse failed: {e}")
        return {"action": "unknown"}


# ── ACTION HANDLERS ───────────────────────────────────────────────────────────

def _tq() -> TaskQueue:
    return TaskQueue(DB_PATH)


def _st() -> SpendTracker:
    return SpendTracker(SPEND_LOG, MINIMAX_SPEND_CAP)


def _handle_status(project: str = None) -> str:
    tq     = _tq()
    stats  = tq.stats()
    st     = _st()

    lines = ["**📊 Orchestrator Status**"]

    projects = [project] if project else CFG.get("ENABLED_PROJECTS", [])
    for proj in projects:
        # Get per-project queued count
        import sqlite3
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
    import sqlite3
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
    import sqlite3
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
    import sqlite3
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
    import sqlite3
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


def _handle_pause(project: str) -> str:
    # Pause is implemented by removing the project from ENABLED_PROJECTS at runtime.
    # For persistence across restarts, user must edit config.py.
    # This in-process change lasts until orchestrator_main.py restarts.
    try:
        import orchestrator_main as om
        if project == "all":
            om.ENABLED_PROJECTS.clear()
            return "⏸ All projects paused (until next restart). Edit config.py to make permanent."
        if project in om.ENABLED_PROJECTS:
            om.ENABLED_PROJECTS.remove(project)
            return f"⏸ `{project}` paused. Edit config.py to make permanent."
        return f"`{project}` was not in ENABLED_PROJECTS (already paused or invalid)."
    except ImportError:
        return "⚠️ Can't pause — orchestrator_main not running in this process. Start via orchestrator_main.py."


def _handle_resume(project: str) -> str:
    try:
        import orchestrator_main as om
        from config import PROJECTS
        if project == "all":
            for p in PROJECTS:
                if p not in om.ENABLED_PROJECTS:
                    om.ENABLED_PROJECTS.append(p)
            return f"▶️ All projects resumed: {', '.join(om.ENABLED_PROJECTS)}"
        if project not in om.ENABLED_PROJECTS:
            om.ENABLED_PROJECTS.append(project)
            return f"▶️ `{project}` resumed."
        return f"`{project}` is already running."
    except ImportError:
        return "⚠️ Can't resume — orchestrator_main not running in this process."


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
    elif action == "pause":
        return _handle_pause(project or "all")
    elif action == "resume":
        return _handle_resume(project or "all")
    elif action == "help":
        return _handle_help()
    elif action == "digest":
        # Try to load last written digest file
        digest_path = BASE_DIR / "logs" / f"digest_{period}.txt"
        if digest_path.exists():
            text = digest_path.read_text()[:1800]
            return f"**📋 {period.capitalize()} Digest**\n```\n{text}\n```"
        return f"No {period} digest found yet."
    else:
        return (
            f"Sorry, I couldn't parse that. Try `help` for a command list.\n"
            f"(Parsed intent: `{intent}`)"
        )


# ── BOT EVENTS ────────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    log.info(f"Bot ready — logged in as {client.user} (ID: {client.user.id})")
    notify.post("live", f"🤖  **Orchestrator bot online** — listening in #orchestrator-chat")


@client.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Only respond in the designated #chat channel
    chat_channel_id = _chat_channel_id()
    if chat_channel_id and message.channel.id != chat_channel_id:
        return

    content = message.content.strip()
    if not content:
        return

    log.info(f"Chat message from {message.author}: {content[:80]}")

    # Show typing indicator while processing
    async with message.channel.typing():
        # Parse intent via Ollama (runs in thread pool to avoid blocking event loop)
        intent   = await asyncio.get_event_loop().run_in_executor(
            None, _parse_intent, content
        )
        log.info(f"Parsed intent: {intent}")

        # Dispatch to handler (also blocking — run in executor)
        response = await asyncio.get_event_loop().run_in_executor(
            None, _dispatch, intent, content
        )

    # Split long responses to avoid Discord 2000-char limit
    if len(response) <= 2000:
        await message.reply(response)
    else:
        # Split on newlines, chunk to <2000 chars
        chunks   = []
        current  = []
        cur_len  = 0
        for line in response.split("\n"):
            if cur_len + len(line) + 1 > 1900:
                chunks.append("\n".join(current))
                current = [line]
                cur_len = len(line)
            else:
                current.append(line)
                cur_len += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        for chunk in chunks:
            await message.channel.send(chunk)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        log.error("DISCORD_BOT_TOKEN not set — export it before running the bot.")
        sys.exit(1)

    chat_id = os.environ.get("DISCORD_CHANNEL_CHAT", "")
    if not chat_id:
        log.warning("DISCORD_CHANNEL_CHAT not set — bot will respond in ANY channel it can see.")

    log.info("Starting Orchestrator Discord bot…")
    client.run(token)
