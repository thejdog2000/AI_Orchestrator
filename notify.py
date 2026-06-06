"""
notify.py
Unified Discord notification layer for the orchestrator.

Called by:
  executor.py          — task start / commit / fail / quality gate
  orchestrator_main.py — overnight start/end, spend milestones
  orchestrator_bot.py  — blocked embeds for approval_required tasks

Uses the Discord REST API directly with the bot token — works even when
the bot process (orchestrator_bot.py) is not running.

Configuration (env vars — never hardcode):
  DISCORD_BOT_TOKEN       — bot token from Discord Developer Portal
  DISCORD_CHANNEL_LIVE    — channel ID for #orchestrator-live
  DISCORD_CHANNEL_BLOCKED — channel ID for #orchestrator-blocked
  DISCORD_CHANNEL_CHAT    — channel ID for #orchestrator-chat
  DASHBOARD_PORT          — port for local dashboard (default: 8080)
"""

import os
import logging

import requests

log = logging.getLogger(__name__)

# Channel name → env var mapping
_CHANNEL_VARS = {
    "live":    "DISCORD_CHANNEL_LIVE",
    "blocked": "DISCORD_CHANNEL_BLOCKED",
    "chat":    "DISCORD_CHANNEL_CHAT",
}

# Discord embed colors
_COLOR_RED    = 0xef4444
_COLOR_ORANGE = 0xf59e0b
_COLOR_GREEN  = 0x10b981
_COLOR_BLUE   = 0x3b82f6


# ── LOW-LEVEL ─────────────────────────────────────────────────────────────────

def _token() -> str:
    return os.environ.get("DISCORD_BOT_TOKEN", "")


def _channel_id(channel: str) -> str:
    return os.environ.get(_CHANNEL_VARS.get(channel, ""), "")


def _dashboard_port() -> str:
    return os.environ.get("DASHBOARD_PORT", "8080")


def post(channel: str, message: str = "", embed: dict = None) -> bool:
    """
    Post a message to a Discord channel via the REST API.

    channel: "live" | "blocked" | "chat"
    message: plain text content (supports Discord markdown)
    embed:   optional Discord embed dict (https://discord.com/developers/docs/resources/message#embed-object)

    Returns True on success, False on failure — never raises.
    If Discord is not configured (no token / channel), silently no-ops.
    """
    token      = _token()
    channel_id = _channel_id(channel)

    if not token or not channel_id:
        log.debug(f"Discord not configured — skipping notify to #{channel}")
        return False

    payload: dict = {}
    if message:
        payload["content"] = message[:2000]  # Discord hard limit
    if embed:
        payload["embeds"] = [embed]

    if not payload:
        return False

    try:
        resp = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.HTTPError as e:
        log.warning(f"Discord API error (#{channel}): {e.response.status_code} — {e.response.text[:200]}")
        return False
    except Exception as e:
        log.warning(f"Discord notify failed (#{channel}): {e}")
        return False


# ── TASK LIFECYCLE FORMATTERS ─────────────────────────────────────────────────

def task_started(task: dict) -> bool:
    """Post to #live when a task starts executing."""
    desc = task.get("description", "")[:70]
    if len(task.get("description", "")) > 70:
        desc += "…"
    msg = (
        f"⚙️  **[{task['project']}]** Starting: {desc}\n"
        f"    `{task.get('perspective', '?')}` · {task.get('complexity', '?')}"
    )
    return post("live", msg)


def task_committed(task: dict, result: dict, monthly_spend: float, spend_cap: float) -> bool:
    """Post to #live when a task is auto-committed successfully."""
    files     = result.get("files_written", [])
    tokens    = result.get("output_tokens", 0)
    cost      = result.get("cost_usd", 0.0)
    commit    = result.get("commit_hash", "")

    files_str  = ", ".join(files[:3])
    if len(files) > 3:
        files_str += f" +{len(files) - 3} more"

    commit_str = f"\n    commit: `{commit}`" if commit else ""

    msg = (
        f"✅  **[{task['project']}]** Committed: {files_str}\n"
        f"    {tokens:,} tokens · ${cost:.4f} · monthly: ${monthly_spend:.2f} / ${spend_cap:.0f}"
        f"{commit_str}"
    )
    return post("live", msg)


def task_pending_review(task: dict) -> bool:
    """Post to #live when a task completes and is queued for review."""
    desc = task.get("description", "")[:70]
    if len(task.get("description", "")) > 70:
        desc += "…"
    msg = (
        f"⏸  **[{task['project']}]** Pending review: {desc}\n"
        f"    → see #orchestrator-blocked"
    )
    post("live", msg)
    # Also post a blocked embed so Jacob sees it in #blocked
    return blocked_embed(task, reason="approval_required")


def task_failed(task: dict, error: str, attempts: int) -> bool:
    """Post to #live when a task exhausts all retries."""
    desc = task.get("description", "")[:60]
    if len(task.get("description", "")) > 60:
        desc += "…"
    msg = (
        f"❌  **[{task['project']}]** Failed after {attempts} attempt(s): {desc}\n"
        f"    error: `{error}`"
    )
    return post("live", msg)


def quality_gate_failed(task: dict, reasoning: str) -> bool:
    """Post to #live and #blocked when Ollama quality gate rejects a diff."""
    desc = task.get("description", "")[:60]
    if len(task.get("description", "")) > 60:
        desc += "…"
    msg = f"🔍  **[{task['project']}]** Quality gate failed: {desc}"
    post("live", msg)
    return blocked_embed(task, reason="quality_gate_failed", detail=reasoning)


# ── BLOCKED CHANNEL EMBED ─────────────────────────────────────────────────────

def blocked_embed(task: dict, reason: str, detail: str = "", blocked_since: str = "") -> bool:
    """
    Post a rich embed to #blocked.
    reason: "approval_required" | "quality_gate_failed" | "repeated_failure"
    """
    color_map = {
        "approval_required":   _COLOR_RED,
        "quality_gate_failed": _COLOR_ORANGE,
        "repeated_failure":    _COLOR_ORANGE,
    }
    color = color_map.get(reason, _COLOR_RED)

    title_map = {
        "approval_required":   "🔴 APPROVAL REQUIRED",
        "quality_gate_failed": "⚠️ QUALITY GATE FAILED",
        "repeated_failure":    "⚠️ REPEATED FAILURE",
    }
    title = title_map.get(reason, "🔴 BLOCKED")

    rp = task.get("review_priority", 3)

    fields = [
        {"name": "Project",     "value": task.get("project", "?"),                                "inline": True},
        {"name": "Complexity",  "value": task.get("complexity", "?"),                             "inline": True},
        {"name": "Priority",    "value": f"{rp}/5",                                               "inline": True},
        {"name": "Perspective", "value": task.get("perspective", "?").replace("_", " "),           "inline": True},
        {"name": "Category",    "value": task.get("effort_category", "?"),                        "inline": True},
        {"name": "Task ID",     "value": f"`{task.get('id', '?')}`",                              "inline": True},
        {"name": "Description", "value": task.get("description", "")[:1000],                      "inline": False},
    ]

    if task.get("rationale"):
        fields.append({"name": "Rationale", "value": task["rationale"][:300], "inline": False})

    if blocked_since:
        fields.append({"name": "Blocked since", "value": blocked_since, "inline": True})

    if detail:
        fields.append({"name": "Detail", "value": detail[:500], "inline": False})

    if reason == "approval_required":
        task_id = task.get("id", "?")
        fields.append({
            "name":   "Actions (paste in #orchestrator-chat)",
            "value":  f"✅ `approve {task_id}`\n❌ `reject {task_id}`",
            "inline": False,
        })

    embed = {
        "color":  color,
        "title":  title,
        "fields": fields,
        "footer": {
            "text": f"Dashboard: http://localhost:{_dashboard_port()} | ID: {task.get('id', '?')}"
        },
    }

    return post("blocked", embed=embed)


# ── SYSTEM EVENTS ─────────────────────────────────────────────────────────────

def overnight_started(projects: list) -> bool:
    """Post to #live when nightly run begins."""
    msg = f"🌙  **Overnight run started** — projects: `{', '.join(projects)}`"
    return post("live", msg)


def overnight_completed(n_tasks: int, total_cost: float) -> bool:
    """Post to #live when nightly run finishes."""
    msg = f"☀️  **Overnight run complete** — {n_tasks} tasks committed · ${total_cost:.2f} total"
    return post("live", msg)


def spend_milestone(monthly: float, cap: float) -> bool:
    """Post to #live when spend crosses 50/75/85/100% of cap."""
    pct = int(monthly / cap * 100)
    emoji = "🔴" if pct >= 85 else "🟡"
    msg = f"{emoji}  **Spend milestone:** ${monthly:.2f} / ${cap:.0f} ({pct}%) this month"
    return post("live", msg)


def orchestrator_started(enabled_projects: list, monthly_spend: float, cap: float) -> bool:
    """Post to #live when the orchestrator process starts."""
    msg = (
        f"🚀  **Orchestrator started** — enabled: `{', '.join(enabled_projects)}`\n"
        f"    Monthly spend: ${monthly_spend:.2f} / ${cap:.0f}"
    )
    return post("live", msg)


def orchestrator_stopped() -> bool:
    """Post to #live when orchestrator shuts down."""
    return post("live", "🛑  **Orchestrator stopped**")
