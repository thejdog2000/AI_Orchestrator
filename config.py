"""
config.py
Single source of truth for all runtime configuration.
Imported by orchestrator_main, executor, task_generator, lang_pipeline, digests.

Update this file when:
  - Switching Ollama models (run `ollama list` to verify names)
  - MiniMax promo rate expires (update MINIMAX_RATES in spend.py too)
  - Adding a new project repo
  - Changing sprint phases / goals
"""

import os
from pathlib import Path

# ── DIRECTORIES ───────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
TASKS_DIR     = BASE_DIR / "tasks"
PENDING_DIR   = BASE_DIR / "pending_review"
APPROVED_DIR  = BASE_DIR / "approved"
LOGS_DIR      = BASE_DIR / "logs"
BACKUPS_DIR   = BASE_DIR / "backups"
DASHBOARD_DIR = BASE_DIR / "dashboard"
RETROS_DIR        = BASE_DIR / "retros"
PIPELINE_LOGS_DIR = BASE_DIR / "pipeline_logs"
PID_FILE      = BASE_DIR / "orchestrator.pid"
DB_PATH       = BASE_DIR / "orchestrator.db"

for _d in [TASKS_DIR, PENDING_DIR, APPROVED_DIR, LOGS_DIR, BACKUPS_DIR, DASHBOARD_DIR, RETROS_DIR, PIPELINE_LOGS_DIR]:
    _d.mkdir(exist_ok=True)

# ── PROJECTS ──────────────────────────────────────────────────────────────────

PROJECTS         = ["lang", "meridian", "rts", "gamma", "ninja", "tax"]
ENABLED_PROJECTS = ["lang"]   # expand after validating pipeline per project

# Which perspectives apply to which projects — used by task_generator council selection
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
PERSPECTIVES      = list(PERSPECTIVE_PROJECT_MAP.keys())
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

HOME = Path.home()

# Absolute paths — no cwd dependency, no relative path ambiguity
REPO_PATHS: dict[str, Path] = {
    "lang":     HOME / "Documents/Claude/Projects/Language Learning App",
    "gamma":    HOME / "Documents/claude/projects/gamma-tool",
    "meridian": HOME / "projects/fashionApp",
    "rts":      HOME / "projects/ironhold-rts",
    "ninja":    HOME / "projects/ninjatrader-algos",
    "tax":      HOME / "projects/tax-cloud-tools",
}

# ── API / MODEL CONFIG ────────────────────────────────────────────────────────

MINIMAX_API_BASE  = "https://api.minimax.io/v1"
MINIMAX_MODEL     = "MiniMax-M3"       # verify at platform.minimax.io before first run
MINIMAX_SPEND_CAP = 50.0               # USD/month hard cap

OLLAMA_BASE         = "http://localhost:11434"
OLLAMA_MODEL_CODE   = "qwen3-coder:30b"   # execution prompts, quality gate
OLLAMA_MODEL_DIGEST = "qwen3:14b"         # digest prose, CONTEXT.md updates (lighter)

# ── EXECUTION ─────────────────────────────────────────────────────────────────

MAX_RETRIES             = 3
QUEUE_REFILL_THRESHOLD  = 10
RETRY_BACKOFF_SECONDS   = [5, 15, 30]

# ── SPRINT STATE ──────────────────────────────────────────────────────────────
# Update as sprints progress. Drives council perspective selection.
# Phases: architecture | feature | polish | demo_prep | maintenance

SPRINT_PHASES = {
    "lang":     "feature",
    "meridian": "demo_prep",
    "rts":      "architecture",
    "gamma":    "maintenance",
    "ninja":    "maintenance",
    "tax":      "feature",
}

SPRINT_GOALS = {
    "lang":     "Generate Japanese A0 scenes (izakaya, konbini) with smoke tests passing",
    "meridian": "5 screens end-to-end for men's fashion publication demo",
    "rts":      "Playable vertical slice: buildings, units, basic AI, FPS possession",
    "gamma":    "Overnight backtest loop running, morning digest showing equity curve",
    "ninja":    "Saturday parameter sweep pipeline, Sunday digest with best performers",
    "tax":      "Azure AVD + PowerShell scripts ready for tax practice demo",
}

# ── DISCORD ───────────────────────────────────────────────────────────────────
# Set these env vars before running the bot:
#   export DISCORD_BOT_TOKEN="..."
#   export DISCORD_CHANNEL_LIVE="..."      # #orchestrator-live channel ID
#   export DISCORD_CHANNEL_BLOCKED="..."   # #orchestrator-blocked channel ID
#   export DISCORD_CHANNEL_CHAT="..."      # #orchestrator-chat channel ID
#   export DISCORD_USER_ID="..."           # Jacob's user ID (for DMs on critical events)
#   export DASHBOARD_PORT="8080"           # local dashboard port (default 8080)
#
# Channel IDs: right-click channel in Discord → "Copy Channel ID" (needs Dev Mode on)
# Bot token: discord.com/developers/applications → your app → Bot → Token
#
# Never hardcode tokens here — env vars only.
#
# DISCORD_CHANNEL_METRICS — new #orchestrator-metrics channel for FEAT-4 snapshots
# METRICS_INTERVAL_HOURS  — how often to post metrics (default 10)

DASHBOARD_PORT         = int(os.environ.get("DASHBOARD_PORT", "8080"))
METRICS_INTERVAL_HOURS = int(os.environ.get("METRICS_INTERVAL_HOURS", "10"))

# ── SHARED CFG DICT (passed to executor.configure, digests.configure) ─────────

CFG = {
    "BASE_DIR":               BASE_DIR,
    "PENDING_DIR":            PENDING_DIR,
    "APPROVED_DIR":           APPROVED_DIR,
    "DASHBOARD_DIR":          DASHBOARD_DIR,
    "DB_PATH":                DB_PATH,
    "PROJECTS":               PROJECTS,
    "ENABLED_PROJECTS":       ENABLED_PROJECTS,
    "RETROS_DIR":              RETROS_DIR,
    "PIPELINE_LOGS_DIR":       PIPELINE_LOGS_DIR,
    "PERSPECTIVE_PROJECT_MAP": PERSPECTIVE_PROJECT_MAP,
    "PROJECT_COLORS":         PROJECT_COLORS,
    "REPO_PATHS":             REPO_PATHS,
    "SPRINT_PHASES":          SPRINT_PHASES,
    "SPRINT_GOALS":           SPRINT_GOALS,
    "MAX_RETRIES":            MAX_RETRIES,
    "QUEUE_REFILL_THRESHOLD": QUEUE_REFILL_THRESHOLD,
    "RETRY_BACKOFF_SECONDS":  RETRY_BACKOFF_SECONDS,
    "MINIMAX_API_BASE":       MINIMAX_API_BASE,
    "MINIMAX_MODEL":          MINIMAX_MODEL,
    "MINIMAX_SPEND_CAP":      MINIMAX_SPEND_CAP,
    "OLLAMA_BASE":            OLLAMA_BASE,
    "OLLAMA_MODEL_CODE":      OLLAMA_MODEL_CODE,
    "OLLAMA_MODEL_DIGEST":    OLLAMA_MODEL_DIGEST,
    "DASHBOARD_PORT":          DASHBOARD_PORT,
    "METRICS_INTERVAL_HOURS":  METRICS_INTERVAL_HOURS,
}
