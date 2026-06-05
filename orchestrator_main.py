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
from config      import (CFG, BASE_DIR, TASKS_DIR, PENDING_DIR, APPROVED_DIR,
                          LOGS_DIR, BACKUPS_DIR, DASHBOARD_DIR, PID_FILE, DB_PATH,
                          PROJECTS, ENABLED_PROJECTS, MINIMAX_SPEND_CAP, REPO_PATHS)
from spend       import SpendTracker
from task_queue  import TaskQueue
from dashboard_generator import generate as generate_dashboard
from task_generator      import generate_tasks_all_projects
from lang_pipeline       import run_nightly as run_lang_nightly

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

    # Health checks — loud failure at startup beats silent failure at 2am
    if not executor.check_ollama():
        log.warning("Ollama unavailable — execution prompts will fall back to raw task descriptions")

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
