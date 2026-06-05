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

from pathlib import Path

# ── DIRECTORIES ───────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
TASKS_DIR     = BASE_DIR / "tasks"
PENDING_DIR   = BASE_DIR / "pending_review"
APPROVED_DIR  = BASE_DIR / "approved"
LOGS_DIR      = BASE_DIR / "logs"
BACKUPS_DIR   = BASE_DIR / "backups"
DASHBOARD_DIR = BASE_DIR / "dashboard"
PID_FILE      = BASE_DIR / "orchestrator.pid"
DB_PATH       = BASE_DIR / "orchestrator.db"

for _d in [TASKS_DIR, PENDING_DIR, APPROVED_DIR, LOGS_DIR, BACKUPS_DIR, DASHBOARD_DIR]:
    _d.mkdir(exist_ok=True)

# ── PROJECTS ──────────────────────────────────────────────────────────────────

PROJECTS         = ["lang", "meridian", "rts", "gamma", "ninja", "tax"]
ENABLED_PROJECTS = ["lang"]   # expand after validating pipeline per project

HOME = Path.home()

# Absolute paths — no cwd dependency, no relative path ambiguity
REPO_PATHS: dict[str, Path] = {
    "lang":     HOME / "Documents/claude/projects/language-travel-app",
    "gamma":    HOME / "Documents/claude/projects/gamma-tool",
    "meridian": HOME / "projects/meridian-mobile",
    "rts":      HOME / "projects/ironhold-rts",
    "ninja":    HOME / "projects/ninjatrader-algos",
    "tax":      HOME / "projects/tax-cloud-tools",
}

# ── API / MODEL CONFIG ────────────────────────────────────────────────────────

MINIMAX_API_BASE  = "https://api.minimax.io/v1"
MINIMAX_MODEL     = "minimax-m3"       # verify at platform.minimax.io before first run
MINIMAX_SPEND_CAP = 65.0               # USD/month hard cap

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

# ── SHARED CFG DICT (passed to executor.configure, digests.configure) ─────────

CFG = {
    "BASE_DIR":               BASE_DIR,
    "PENDING_DIR":            PENDING_DIR,
    "APPROVED_DIR":           APPROVED_DIR,
    "DASHBOARD_DIR":          DASHBOARD_DIR,
    "DB_PATH":                DB_PATH,
    "PROJECTS":               PROJECTS,
    "ENABLED_PROJECTS":       ENABLED_PROJECTS,
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
}
