"""
orchestrator_main.py
Entry point and scheduler only. All logic lives in focused modules:
  executor.py   — MiniMax execution, Ollama prompts, CONTEXT.md feedback loop
  spend.py      — SpendTracker
  digests.py    — digest generation
  task_queue.py — SQLite task storage
  task_generator.py — MiniMax council task generation

Before first run:
  1. export MINIMAX_API_KEY="..."
  2. Set MiniMax spend cap at platform.minimax.io → Billing → $65
  3. ollama list — verify qwen3-coder:30b and qwen3:14b are present
  4. Enable lang only first (ENABLED_PROJECTS below), validate, then expand
"""

import os
import sys
import time
import logging
import logging.handlers
import subprocess
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

import executor
import digests
from spend       import SpendTracker
from task_queue  import TaskQueue
from dashboard_generator import generate as generate_dashboard
from task_generator      import generate_tasks_all_projects
from lang_pipeline       import run_nightly as run_lang_nightly

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
TASKS_DIR     = BASE_DIR / "tasks"
PENDING_DIR   = BASE_DIR / "pending_review"
APPROVED_DIR  = BASE_DIR / "approved"
LOGS_DIR      = BASE_DIR / "logs"
BACKUPS_DIR   = BASE_DIR / "backups"
DASHBOARD_DIR = BASE_DIR / "dashboard"
PID_FILE      = BASE_DIR / "orchestrator.pid"
DB_PATH       = BASE_DIR / "orchestrator.db"

for d in [TASKS_DIR, PENDING_DIR, APPROVED_DIR, LOGS_DIR, BACKUPS_DIR, DASHBOARD_DIR]:
    d.mkdir(exist_ok=True)

PROJECTS         = ["lang", "meridian", "rts", "gamma", "ninja", "tax"]
ENABLED_PROJECTS = ["lang"]   # expand after validating pipeline per project

MAX_RETRIES            = 3
QUEUE_REFILL_THRESHOLD = 10
RETRY_BACKOFF_SECONDS  = [5, 15, 30]

MINIMAX_API_BASE  = "https://api.minimax.io/v1"
MINIMAX_MODEL     = "minimax-m3"
MINIMAX_SPEND_CAP = 65.0

OLLAMA_BASE        = "http://localhost:11434"
OLLAMA_MODEL_CODE  = "qwen3-coder:30b"
OLLAMA_MODEL_DIGEST = "qwen3:14b"

HOME = Path.home()
REPO_PATHS: dict[str, Path] = {
    "lang":     HOME / "Documents/claude/projects/language-travel-app",
    "gamma":    HOME / "Documents/claude/projects/gamma-tool",
    "meridian": HOME / "projects/meridian-mobile",
    "rts":      HOME / "projects/ironhold-rts",
    "ninja":    HOME / "projects/ninjatrader-algos",
    "tax":      HOME / "projects/tax-cloud-tools",
}

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

# Shared config dict passed to all modules
CFG = {
    "BASE_DIR":              BASE_DIR,
    "PENDING_DIR":           PENDING_DIR,
    "APPROVED_DIR":          APPROVED_DIR,
    "DASHBOARD_DIR":         DASHBOARD_DIR,
    "DB_PATH":               DB_PATH,
    "ENABLED_PROJECTS":      ENABLED_PROJECTS,
    "REPO_PATHS":            REPO_PATHS,
    "SPRINT_PHASES":         SPRINT_PHASES,
    "SPRINT_GOALS":          SPRINT_GOALS,
    "MAX_RETRIES":           MAX_RETRIES,
    "QUEUE_REFILL_THRESHOLD":QUEUE_REFILL_THRESHOLD,
    "RETRY_BACKOFF_SECONDS": RETRY_BACKOFF_SECONDS,
    "MINIMAX_API_BASE":      MINIMAX_API_BASE,
    "MINIMAX_MODEL":         MINIMAX_MODEL,
    "MINIMAX_SPEND_CAP":     MINIMAX_SPEND_CAP,
    "OLLAMA_BASE":           OLLAMA_BASE,
    "OLLAMA_MODEL_CODE":     OLLAMA_MODEL_CODE,
    "OLLAMA_MODEL_DIGEST":   OLLAMA_MODEL_DIGEST,
}

# ── LOGGING ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOGS_DIR / "orchestrator.log", maxBytes=5*1024*1024, backupCount=5
        ),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ── MODULE INIT ───────────────────────────────────────────────────────────────

executor.configure(CFG)
digests.configure(CFG)

spend_tracker = SpendTracker(LOGS_DIR / "spend.json", MINIMAX_SPEND_CAP)
task_queue    = TaskQueue()

for _f in TASKS_DIR.glob("*.json"):
    _n = task_queue.load_from_json(_f)
    if _n:
        log.info(f"Loaded {_n} tasks from {_f.name}")

# ── PROCESS LOCK ──────────────────────────────────────────────────────────────

def _write_pid():
    if PID_FILE.exists():
        log.error(f"PID file exists ({PID_FILE.read_text().strip()}). "
                  f"Delete {PID_FILE} if no other instance is running.")
        sys.exit(1)
    PID_FILE.write_text(str(os.getpid()))

def _remove_pid():
    PID_FILE.unlink(missing_ok=True)

# ── PER-PROJECT EXECUTION LOCKS ───────────────────────────────────────────────

_project_running: dict[str, bool] = {p: False for p in PROJECTS}

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────

def execute_next_task():
    """
    Called every 2 min by BackgroundScheduler.
    Tries each enabled project in order, skips busy ones.
    Multiple projects can run simultaneously (different repos).
    """
    if not spend_tracker.check_caps():
        return

    task = None
    for project in ENABLED_PROJECTS:
        if _project_running.get(project):
            continue
        candidate = task_queue.get_next(projects=[project])
        if candidate:
            task = candidate
            break

    if task is None:
        for project in ENABLED_PROJECTS:
            if not _project_running.get(project):
                gaps = task_queue.get_gap_fill_tasks()
                if gaps:
                    task = gaps[0]
                    break

    if task is None:
        return

    project = task["project"]
    _project_running[project] = True
    log.info(f"[{project}] → {task['id']}: {task['description'][:80]}")

    try:
        executor.run_task(task, spend_tracker, task_queue)
    finally:
        _project_running[project] = False

# ── BACKUP ────────────────────────────────────────────────────────────────────

def backup_db():
    """Nightly SQLite dump — 7-day rolling window."""
    stamp   = datetime.now().strftime("%Y-%m-%d")
    outfile = BACKUPS_DIR / f"tasks_{stamp}.sql"
    try:
        subprocess.run(
            ["sqlite3", str(DB_PATH), f".output {outfile}", ".dump", ".quit"],
            check=True, capture_output=True,
        )
        log.info(f"DB backup → {outfile.name}")
        # Prune backups older than 7 days
        for old in sorted(BACKUPS_DIR.glob("tasks_*.sql"))[:-7]:
            old.unlink()
    except Exception as e:
        log.error(f"DB backup failed: {e}")

# ── SEED ──────────────────────────────────────────────────────────────────────

def _seed_sample_tasks():
    """Seed starter lang tasks on first run. Only called from __main__."""
    sample = [
        {
            "id": "lang_001", "project": "lang", "priority": 0, "status": "queued",
            "complexity": "medium", "effort_category": "feature",
            "perspective": "speech_linguist", "approval_required": False,
            "depends_on": [], "blocks": [],
            "description": (
                "Generate complete scene JS module for Japanese A0 izakaya scenario. "
                "Include dialogue tree, randomization pools (5+ variants each), "
                "Three.js config, SRS card list. Save to scenes/ja/izakaya_01.js"
            ),
            "rationale": "First scene — validates full generation pipeline end-to-end",
            "estimated_tokens": 8000,
        },
        {
            "id": "lang_002", "project": "lang", "priority": 0, "status": "queued",
            "complexity": "medium", "effort_category": "feature",
            "perspective": "speech_linguist", "approval_required": False,
            "depends_on": [], "blocks": [],
            "description": (
                "Generate complete scene JS module for Japanese A0 konbini scenario. "
                "Save to scenes/ja/konbini_01.js"
            ),
            "rationale": "Night-1 second scene",
            "estimated_tokens": 8000,
        },
        {
            "id": "lang_003", "project": "lang", "priority": 0, "status": "queued",
            "complexity": "low", "effort_category": "test",
            "perspective": "qa_tester", "approval_required": False,
            "depends_on": [], "blocks": [],
            "description": (
                "Generate Node.js smoke test: validates scene exports correctly, "
                "required schema fields present, randomizationPool has 5+ items per key, "
                "Three.js config valid. Save to tests/smoke.js"
            ),
            "rationale": "Automated pass/fail gate for every generated scene",
            "estimated_tokens": 5000,
        },
    ]
    n = sum(1 for t in sample if task_queue.add_task(t))
    if n:
        log.info(f"Seeded {n} starter lang tasks")

# ── SCHEDULER ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()
scheduler.add_job(execute_next_task, "interval", minutes=2,
                  id="main_loop", max_instances=1, coalesce=True)
scheduler.add_job(
    lambda: digests.write_digest("morning",   task_queue, spend_tracker),
    "cron", hour=8,  minute=0, id="morning_digest",
)
scheduler.add_job(
    lambda: digests.write_digest("afternoon", task_queue, spend_tracker),
    "cron", hour=14, minute=0, id="afternoon_digest",
)
scheduler.add_job(
    lambda: digests.write_digest("evening",   task_queue, spend_tracker),
    "cron", hour=20, minute=0, id="evening_digest",
)
scheduler.add_job(backup_db,         "cron", hour=3,  minute=0,  id="db_backup")
scheduler.add_job(run_lang_nightly,  "cron", hour=22, minute=0,  id="lang_nightly")

# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _write_pid()

    log.info("=" * 60)
    log.info(f"Orchestrator starting — enabled: {ENABLED_PROJECTS}")
    log.info(f"Monthly spend: ${spend_tracker.monthly_spend():.2f} / ${MINIMAX_SPEND_CAP}")
    log.info("=" * 60)

    if task_queue.total_unblocked(projects=ENABLED_PROJECTS) == 0:
        _seed_sample_tasks()

    log.info(f"Queue: {task_queue.stats()}")
    generate_dashboard()

    try:
        scheduler.start()
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Stopping...")
        scheduler.shutdown(wait=False)
        _remove_pid()
        log.info("Stopped.")
