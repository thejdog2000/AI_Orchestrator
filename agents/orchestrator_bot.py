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
import logging
import asyncio
from pathlib import Path
from collections import deque

import discord

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("orchestrator_bot")

# ── PATH SETUP ────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent   # agents/
ORCHESTRATOR = BASE_DIR.parent         # Orchestrator/

sys.path.insert(0, str(ORCHESTRATOR))

import notify
from agents.commands import _parse_intent, _dispatch

# ── PID GUARD — only one instance allowed ─────────────────────────────────────

PID_FILE = ORCHESTRATOR / ".bot.pid"

def _check_pid():
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text().strip())
            os.kill(existing, 0)  # signal 0 = existence check
            log.error(f"Bot already running (pid {existing}). Kill it first: kill {existing}")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            pass  # stale PID — clear it
    PID_FILE.write_text(str(os.getpid()))

def _clear_pid():
    PID_FILE.unlink(missing_ok=True)

# ── MESSAGE DEDUP — ignore replayed/duplicate message IDs ─────────────────────

_seen_message_ids: deque = deque(maxlen=256)

# ── DISCORD SETUP ─────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
client  = discord.Client(intents=intents)


def _chat_channel_id() -> "int | None":
    raw = os.environ.get("DISCORD_CHANNEL_CHAT", "")
    return int(raw) if raw.isdigit() else None


def _authorized_user_id() -> "int | None":
    raw = os.environ.get("DISCORD_USER_ID", "")
    return int(raw) if raw.isdigit() else None


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

    # Security: only process commands from the authorized user (Jacob)
    # Set DISCORD_USER_ID env var. If not set, warn but allow (backward compat).
    authorized = _authorized_user_id()
    if authorized and message.author.id != authorized:
        log.warning(f"Ignoring message from unauthorized user {message.author} ({message.author.id})")
        return

    # Dedup — Discord occasionally delivers the same message twice
    if message.id in _seen_message_ids:
        return
    _seen_message_ids.append(message.id)

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

    _check_pid()
    try:
        log.info("Starting Orchestrator Discord bot…")
        client.run(token)
    finally:
        _clear_pid()
