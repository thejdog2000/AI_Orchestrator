#!/usr/bin/env python3
"""
o.py — Orchestrator CLI alias.

Same intent parsing as #orchestrator-chat, usable from terminal.

Setup (add to ~/.zshrc):
  alias o="python3 ~/projects/Orchestrator/o.py"

Usage:
  o "what happened overnight"
  o "approve all lang"
  o "how much have we spent"
  o status
  o "what's queued for tonight"
  o "show me blocked items"
  o help
"""

import sys
import os
import json
import logging
from pathlib import Path

# Silence noisy loggers when running interactively
logging.disable(logging.WARNING)

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config import CFG, DB_PATH, MINIMAX_SPEND_CAP
import requests


# ── OLLAMA INTENT PARSING ─────────────────────────────────────────────────────

OLLAMA_BASE       = CFG.get("OLLAMA_BASE",         "http://localhost:11434")
OLLAMA_MODEL_CHAT = CFG.get("OLLAMA_MODEL_DIGEST", "qwen3:14b")

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
    except Exception:
        # If Ollama is down, try simple keyword matching as fallback
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
    if m in ("blocked", "show blocked", "pending"):
        return {"action": "blocked"}
    if m in ("queued", "queue", "what's queued"):
        return {"action": "queued"}
    if m in ("running", "what's running"):
        return {"action": "running"}
    if m in ("completed", "done today", "what did we build"):
        return {"action": "completed"}
    if m.startswith("pause "):
        return {"action": "pause", "project": m.split("pause ", 1)[1].strip()}
    if m.startswith("resume "):
        return {"action": "resume", "project": m.split("resume ", 1)[1].strip()}
    if m in ("help", "?", "commands"):
        return {"action": "help"}
    return {"action": "unknown"}


# ── ACTION HANDLERS (same logic as orchestrator_bot.py) ──────────────────────

def _handle(intent: dict, raw: str) -> str:
    # Import handlers from bot module to avoid duplication
    # We call them directly since we don't need async here
    from orchestrator_bot import (
        _handle_status, _handle_approve, _handle_reject,
        _handle_spend, _handle_blocked, _handle_queued,
        _handle_completed, _handle_running, _handle_pause,
        _handle_resume, _handle_help, _dispatch,
    )
    return _dispatch(intent, raw)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: o <command or question>")
        print('Examples: o status  |  o "approve all lang"  |  o help')
        sys.exit(0)

    message = " ".join(sys.argv[1:])

    # Re-enable warnings for Ollama errors during CLI use
    logging.disable(logging.NOTSET)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    intent   = _parse_intent(message)
    response = _handle(intent, message)
    print(response)


if __name__ == "__main__":
    main()
