"""
orchestrator_main.py
Multi-project AI orchestrator daemon.
Feed ORCHESTRATOR_CONTEXT.md to any AI session before iterating on this file.

Before first run:
  1. export MINIMAX_API_KEY="..."
  2. Set MiniMax spend cap at platform.minimax.io → Billing → $65
  3. Run `ollama list` — verify qwen3-coder:30b and qwen3:14b are present
  4. Add task JSON files to tasks/ (or let council generate them)
  5. Enable lang only first — validate full pipeline before adding other projects
"""

import subprocess
import json
import os
import time
import logging
import logging.handlers
import signal
import sys
import re
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
import requests

from task_queue import TaskQueue
from dashboard_generator import generate as generate_dashboard
from task_generator import generate_tasks_all_projects

# ── CONFIG ───────────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
TASKS_DIR     = BASE_DIR / "tasks"
PENDING_DIR   = BASE_DIR / "pending_review"
LOGS_DIR      = BASE_DIR / "logs"
DASHBOARD_DIR = BASE_DIR / "dashboard"
PID_FILE      = BASE_DIR / "orchestrator.pid"

for d in [TASKS_DIR, PENDING_DIR, LOGS_DIR, DASHBOARD_DIR]:
    d.mkdir(exist_ok=True)

PROJECTS = ["lang", "meridian", "rts", "gamma", "ninja", "tax"]

# Start with lang only — validate pipeline before enabling others
ENABLED_PROJECTS = ["lang"]

MAX_RETRIES             = 3
QUEUE_REFILL_THRESHOLD  = 10
RETRY_BACKOFF_SECONDS   = [5, 15, 30]   # exponential-ish, not flat 5s

MINIMAX_API_BASE   = "https://api.minimax.io/v1"
MINIMAX_MODEL      = "minimax-m3"        # direct API model name (not Aider prefix)
MINIMAX_SPEND_CAP  = 65.0               # hard cap in USD/month

OLLAMA_BASE              = "http://localhost:11434"
OLLAMA_MODEL_CODE        = "qwen3-coder:30b"   # execution prompts, quality gate
OLLAMA_MODEL_DIGEST      = "qwen3:14b"         # digest prose — faster, lighter

# Absolute repo paths — no relative path ambiguity regardless of launch directory
HOME = Path.home()
REPO_PATHS: dict[str, Path] = {
    "lang":     HOME / "Documents/claude/projects/language-travel-app",
    "gamma":    HOME / "Documents/claude/projects/gamma-tool",
    "meridian": HOME / "projects/meridian-mobile",
    "rts":      HOME / "projects/ironhold-rts",
    "ninja":    HOME / "projects/ninjatrader-algos",
    "tax":      HOME / "projects/tax-cloud-tools",
}

# ── SPRINT STATE ─────────────────────────────────────────────────────────────
# Update these as sprints progress. Used by task_generator council calls.
# Phases: architecture | feature | polish | demo_prep | maintenance

SPRINT_PHASES = {
    "lang":     "feature",       # overnight scene generation
    "meridian": "demo_prep",     # 5-screen pitch sprint
    "rts":      "architecture",  # full C# system generation
    "gamma":    "maintenance",   # backtest loop
    "ninja":    "maintenance",   # Saturday param sweep
    "tax":      "feature",       # builds when client engaged
}

SPRINT_GOALS = {
    "lang":     "Generate 2 Japanese A0 scenes (izakaya, konbini) with smoke tests passing",
    "meridian": "5 screens working end-to-end for men's fashion publication demo",
    "rts":      "Playable vertical slice: place buildings, train units, basic AI opponent",
    "gamma":    "Overnight backtest loop running, morning digest showing equity curve",
    "ninja":    "Saturday parameter sweep pipeline running, Sunday digest with best performers",
    "tax":      "Azure AVD + PowerShell scripts ready for family tax practice demo",
}

# ── LOGGING (rotating — no unbounded growth) ─────────────────────────────────

_log_handler = logging.handlers.RotatingFileHandler(
    LOGS_DIR / "orchestrator.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB per file
    backupCount=5,
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[_log_handler, logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── SPEND TRACKER ────────────────────────────────────────────────────────────

class SpendTracker:
    def __init__(self):
        self.log_file = LOGS_DIR / "spend.json"
        self.data = self._load()

    def _load(self):
        if self.log_file.exists():
            return json.loads(self.log_file.read_text())
        return {"daily": {}, "total_input_tokens": 0, "total_output_tokens": 0, "total_usd": 0.0}

    def _save(self):
        self.log_file.write_text(json.dumps(self.data, indent=2))

    def record(self, project: str, input_tokens: int, output_tokens: int, model: str):
        # MiniMax M3 promo rates — verify at platform.minimax.io, update when promo expires
        rates = {
            "minimax-m3": (0.30, 1.20),   # per 1M tokens, promo rate
        }
        input_rate, output_rate = rates.get(model, (0.30, 1.20))
        cost = (input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate)

        today = datetime.now().strftime("%Y-%m-%d")
        if today not in self.data["daily"]:
            self.data["daily"][today] = {"usd": 0.0, "tasks": 0}
        self.data["daily"][today]["usd"] += cost
        self.data["daily"][today]["tasks"] += 1
        self.data["total_usd"] += cost
        self.data["total_input_tokens"] += input_tokens
        self.data["total_output_tokens"] += output_tokens
        self._save()
        return cost

    def monthly_spend(self) -> float:
        month = datetime.now().strftime("%Y-%m")
        return sum(v["usd"] for k, v in self.data["daily"].items() if k.startswith(month))

    def check_caps(self) -> bool:
        """Returns False if monthly MiniMax spend is at or above cap."""
        monthly = self.monthly_spend()
        warn_at = MINIMAX_SPEND_CAP * 0.85   # warn at 85% of cap
        if monthly >= MINIMAX_SPEND_CAP:
            log.error(f"MiniMax spend ${monthly:.2f} hit ${MINIMAX_SPEND_CAP} cap. Halting.")
            return False
        if monthly >= warn_at:
            log.warning(f"MiniMax spend ${monthly:.2f} approaching ${MINIMAX_SPEND_CAP} cap.")
        return True


spend_tracker = SpendTracker()

# ── PROCESS LOCK (prevent double-execution on restart) ────────────────────────

def _write_pid():
    if PID_FILE.exists():
        existing = PID_FILE.read_text().strip()
        log.error(
            f"PID file exists ({existing}). Another instance may be running. "
            f"If not, delete {PID_FILE} and restart."
        )
        sys.exit(1)
    PID_FILE.write_text(str(os.getpid()))

def _remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()

# ── PER-PROJECT EXECUTION LOCKS ───────────────────────────────────────────────
# One flag per project — lang + meridian can run simultaneously (different repos)
# Two tasks in the same project cannot overlap (would corrupt git state)

_project_running: dict[str, bool] = {p: False for p in PROJECTS}

# ── TASK QUEUE + STARTUP LOAD ─────────────────────────────────────────────────

task_queue = TaskQueue()  # SQLite-backed — see task_queue.py

# Load any pre-existing JSON task backlogs into the DB on startup
for _json_file in TASKS_DIR.glob("*.json"):
    _n = task_queue.load_from_json(_json_file)
    if _n:
        log.info(f"Loaded {_n} new tasks from {_json_file.name}")

# ── OLLAMA CLIENT ─────────────────────────────────────────────────────────────

def ollama_generate(
    prompt: str,
    max_tokens: int = 1000,
    json_mode: bool = False,
    model: str = None,
) -> str:
    """
    Call local Ollama. Used for:
      - digest prose        → model=qwen3:14b   (faster, lighter)
      - execution prompts   → model=qwen3-coder:30b (code-aware)
    NOT used for task generation (MiniMax — see task_generator.py).

    num_ctx always set to 8192 — Ollama defaults to 2048 which silently truncates.
    """
    model = model or OLLAMA_MODEL_CODE
    try:
        payload: dict = {
            "model":   model,
            "prompt":  prompt,
            "stream":  False,
            "options": {"num_ctx": 8192, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json=payload,
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        log.error(f"Ollama error ({model}): {e}")
        return ""


def write_aider_prompt(task: dict, context_md: str) -> str:
    """Ollama decomposes task into a specific Aider instruction."""
    prompt = f"""You are writing an instruction for Aider, an AI coding tool.
Given this task and project context, write a single clear Aider instruction.
Be specific about which files to create or modify. No preamble, just the instruction.

TASK: {task['description']}
PROJECT: {task['project']}
CONTEXT:
{context_md[:2000]}

Write the Aider instruction now:"""
    result = ollama_generate(prompt, max_tokens=500)
    return result.strip() if result else task["description"]


def evaluate_diff(diff_text: str, task: dict) -> dict:
    """
    Ollama evaluates diff quality for low/medium complexity tasks.
    High complexity tasks skip this and go straight to pending_review for Jacob.

    Uses json_mode=True — without it Ollama intermittently wraps output in markdown
    fences and the silent fallback (score:5, pass:True) masks the failure.
    """
    complexity = task.get("complexity", "medium")
    if complexity == "high":
        # Don't trust Ollama on hard diffs — flag for human review regardless
        log.info(f"[{task['project']}] High-complexity diff — skipping Ollama gate, queuing for review")
        return {"score": 7, "pass": True, "issues": [], "reasoning": "High complexity — human review required"}

    prompt = (
        f"Evaluate this code diff. Task: {task['description']}\n"
        f"Project: {task['project']}\n\n"
        f"Diff (first 2000 chars):\n{diff_text[:2000]}\n\n"
        f'Return JSON with keys: "score" (0-10), "pass" (true/false), '
        f'"issues" (array of strings), "reasoning" (string).'
    )
    result = ollama_generate(prompt, max_tokens=300, json_mode=True)
    try:
        return json.loads(result)
    except Exception:
        log.warning(f"[{task['project']}] Ollama quality gate returned unparseable JSON — defaulting pass")
        return {"score": 5, "pass": True, "issues": [], "reasoning": "Parse failed"}


def generate_digest(period: str) -> str:
    """qwen3:14b writes human-readable digest — lighter model, faster for prose."""
    completed_today = task_queue.get_completed_today()   # fix: was task_queue.completed (missing attr)
    pending_count   = len(list(PENDING_DIR.glob("*.diff")))
    monthly_spend   = spend_tracker.monthly_spend()
    stats           = task_queue.stats()

    prompt = (
        f"Write a brief {period} digest for Jacob's multi-project AI orchestrator.\n"
        f"Tone: direct, no fluff. Plain text, short bullets. 5-8 points max.\n\n"
        f"DATA:\n"
        f"- Tasks completed today: {len(completed_today)}\n"
        f"- Descriptions: {json.dumps([t['description'] for t in completed_today[-10:]])}\n"
        f"- Diffs awaiting review: {pending_count}\n"
        f"- Monthly API spend: ${monthly_spend:.2f} / ${MINIMAX_SPEND_CAP:.0f} cap\n"
        f"- Queue stats: {stats}\n"
        f"- Enabled projects: {ENABLED_PROJECTS}\n\n"
        f"Write the {period} digest:"
    )
    return ollama_generate(prompt, max_tokens=600, model=OLLAMA_MODEL_DIGEST)

# ── EXECUTION ────────────────────────────────────────────────────────────────

def get_context_md(project: str) -> str:
    """
    Load project CONTEXT.md using absolute REPO_PATHS — no cwd dependency.
    Truncated to 3000 chars to keep Ollama prompt manageable.
    """
    repo = REPO_PATHS.get(project)
    if repo:
        path = repo / "CONTEXT.md"
        if path.exists():
            return path.read_text()[:3000]
    log.warning(f"No CONTEXT.md found for {project} at {repo}")
    return f"No CONTEXT.md found for {project}. Proceeding with task description only."


# MiniMax response file delimiter — unambiguous, avoids markdown code fence edge cases
_FILE_START = "<<<FILE:"
_FILE_END   = "<<<END>>>"


def _parse_file_blocks(response: str) -> dict:
    """
    Parse file blocks from MiniMax response.
    Expected format:
      <<<FILE: relative/path/to/file.ext>>>
      <complete file content>
      <<<END>>>
    Returns {relative_path: content}.
    """
    pattern = re.compile(r"<<<FILE:\s*(.+?)>>>\s*\n(.*?)<<<END>>>", re.DOTALL)
    return {m.group(1).strip(): m.group(2) for m in pattern.finditer(response)}


def _minimax_execute(system: str, user: str, max_tokens: int = 8000) -> tuple:
    """
    Direct MiniMax chat/completions call. Key via env — never in CLI args.
    Returns (content, input_tokens, output_tokens).
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise EnvironmentError("MINIMAX_API_KEY not set")

    resp = requests.post(
        f"{MINIMAX_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model":       MINIMAX_MODEL,
            "messages":    [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": 0.2,
            "max_tokens":  max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data   = resp.json()
    usage  = data.get("usage", {})
    return (
        data["choices"][0]["message"]["content"],
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
    )


def run_minimax_task(task: dict) -> dict:
    """
    Execute a task via direct MiniMax API.
    Replaces Aider subprocess — full token visibility, explicit file control.

    Flow:
      1. Ollama writes focused system prompt from task + CONTEXT.md
      2. MiniMax generates files in <<<FILE: path>>> delimited blocks
      3. Python writes files to repo, captures git diff
      4. Saves diff to pending_review/ for Jacob approval
      5. Returns actual token counts from API response
    """
    project   = task["project"]
    repo_path = REPO_PATHS.get(project)

    if not repo_path or not repo_path.exists():
        log.error(f"[{project}] Repo not found: {repo_path}")
        return {"success": False, "error": "repo_not_found"}

    if not os.environ.get("MINIMAX_API_KEY"):
        log.error("MINIMAX_API_KEY not set")
        return {"success": False, "error": "no_api_key"}

    context_md    = get_context_md(project)
    system_prompt = write_aider_prompt(task, context_md)

    user_message = (
        f"Task: {task['description']}\n\n"
        f"Output every file you create or modify using this exact format — no exceptions:\n"
        f"<<<FILE: relative/path/to/file.ext>>>\n"
        f"<complete file content here>\n"
        f"<<<END>>>\n\n"
        f"One block per file. Output ALL changed files. No prose or explanation outside the blocks."
    )

    log.info(f"[{project}] Executing {task['id']} via MiniMax direct API")

    try:
        content, input_tokens, output_tokens = _minimax_execute(system_prompt, user_message)
    except EnvironmentError as e:
        return {"success": False, "error": str(e)}
    except requests.HTTPError as e:
        log.error(f"[{project}] MiniMax HTTP {e.response.status_code}: {e}")
        return {"success": False, "error": f"api_http_{e.response.status_code}"}
    except Exception as e:
        log.error(f"[{project}] MiniMax call failed: {e}")
        return {"success": False, "error": str(e)}

    file_blocks = _parse_file_blocks(content)
    if not file_blocks:
        log.warning(f"[{project}] No file blocks in response for {task['id']}")
        log.debug(f"Response preview: {content[:400]}")
        return {"success": False, "error": "no_file_blocks"}

    for rel_path, file_content in file_blocks.items():
        dest = repo_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(file_content)
        log.info(f"[{project}] Wrote {rel_path}")

    # Stage new files so they appear in git diff
    subprocess.run(["git", "add", "--intent-to-add", "."],
                   cwd=repo_path, capture_output=True)
    diff_result = subprocess.run(
        ["git", "diff"], cwd=repo_path, capture_output=True, text=True
    )
    diff_text = diff_result.stdout

    diff_path = PENDING_DIR / f"{project}_{task['id']}_{int(time.time())}.diff"
    diff_path.write_text(diff_text or f"# No diff captured\n# Files written: {list(file_blocks)}")

    log.info(f"[{project}] {input_tokens} in / {output_tokens} out tokens")

    return {
        "success":       True,
        "diff_path":     diff_path,
        "diff_text":     diff_text,
        "files_written": list(file_blocks.keys()),
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
    }

# ── MAIN EXECUTION LOOP ───────────────────────────────────────────────────────

def execute_next_task():
    """
    Core loop iteration — called every 2 min by BackgroundScheduler.
    Tries each enabled project in order, skips any that are already running.
    Multiple projects can execute simultaneously (different repos, no conflict).
    """
    if not spend_tracker.check_caps():
        log.info("Spend cap hit — halting execution")
        return

    # Find a project that has work and isn't currently executing
    task = None
    for project in ENABLED_PROJECTS:
        if _project_running.get(project):
            continue
        candidate = task_queue.get_next(projects=[project])
        if candidate:
            task = candidate
            break

    if task is None:
        # All projects either busy or empty — try gap-fill
        for project in ENABLED_PROJECTS:
            if not _project_running.get(project):
                gap = task_queue.get_gap_fill_tasks()
                if gap:
                    task = gap[0]
                    break
    
    if task is None:
        log.info("No tasks available — all projects busy or queue empty")
        return

    project = task["project"]
    _project_running[project] = True
    log.info(f"[{project}] Starting {task['id']}: {task['description'][:80]}")

    try:
        _run_task(task)
    finally:
        _project_running[project] = False


def _run_task(task: dict):
    """Inner execution — called inside per-project lock."""
    project   = task["project"]

    # Run with retries — exponential backoff, git clean before each attempt
    result = None
    repo_path = REPO_PATHS.get(project)
    for attempt in range(MAX_RETRIES):
        # Clean any leftover changes from a prior failed attempt (#5)
        if repo_path and repo_path.exists():
            subprocess.run(["git", "checkout", "--", "."], cwd=repo_path,
                           capture_output=True)

        result = run_minimax_task(task)
        if result["success"]:
            break
        wait = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        log.warning(f"[{task['project']}] Attempt {attempt + 1} failed: "
                    f"{result.get('error')}. Retrying in {wait}s...")
        time.sleep(wait)

    if not result or not result["success"]:
        log.error(f"Task {task['id']} failed after {MAX_RETRIES} attempts.")
        failure_file = PENDING_DIR / f"FAILED_{task['project']}_{task['id']}.json"
        failure_file.write_text(json.dumps({"task": task, "result": result}, indent=2))
        task_queue.mark_failed(task, notes=str(result.get("error", "unknown")))
        return

    # Quality gate (Ollama — skips high-complexity, those go straight to review)
    evaluation = evaluate_diff(result.get("diff_text", ""), task)
    log.info(f"Quality gate: score={evaluation.get('score')}, pass={evaluation.get('pass')}")

    if not evaluation.get("pass", True):
        # No escalation — log and flag for Jacob. Fail → next task.
        log.warning(f"[{task['project']}] Quality gate failed for {task['id']} — queuing for review")
        failure_file = PENDING_DIR / f"QUALITY_FAILED_{task['project']}_{task['id']}.json"
        failure_file.write_text(json.dumps({"task": task, "evaluation": evaluation}, indent=2))
        task_queue.mark_failed(task, notes=f"quality gate: {evaluation.get('reasoning','')}")
        return

    # Record actual spend from API response
    cost = spend_tracker.record(
        task["project"],
        result.get("input_tokens", 0),
        result.get("output_tokens", 0),
        MINIMAX_MODEL,
    )
    log.info(f"Task complete. Cost: ${cost:.4f}. Monthly: ${spend_tracker.monthly_spend():.2f}")

    # Queue diff for Jacob review
    task_queue.mark_pending_review(task, result["diff_path"])

    # Check if queue needs refilling
    if task_queue.total_unblocked(projects=ENABLED_PROJECTS) < QUEUE_REFILL_THRESHOLD:
        log.info("Queue low — generating new tasks via Ollama")
        generate_new_tasks()


def generate_new_tasks():
    """
    MiniMax council generates next wave of tasks when queue drops below threshold.
    Called automatically from the main loop. See task_generator.py for full design.
    """
    inserted = generate_tasks_all_projects(
        task_queue       = task_queue,
        enabled_projects = ENABLED_PROJECTS,
        sprint_phases    = SPRINT_PHASES,
        sprint_goals     = SPRINT_GOALS,
        threshold        = QUEUE_REFILL_THRESHOLD,
    )
    if inserted:
        log.info(f"Council generated {inserted} new tasks across enabled projects")
        generate_dashboard()  # refresh dashboard after new tasks land


# ── DIGEST SCHEDULER ─────────────────────────────────────────────────────────

def _write_digest(period: str):
    digest  = generate_digest(period)
    pending = list(PENDING_DIR.glob("*.diff"))
    monthly = spend_tracker.monthly_spend()
    stats   = task_queue.stats()
    report  = (
        f"\n{'='*60}\n{period.upper()} DIGEST — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{'='*60}\n"
        f"{digest}\n\n"
        f"PENDING REVIEW : {len(pending)} diffs\n"
        f"MONTHLY SPEND  : ${monthly:.2f} / $100 cap\n"
        f"TASK STATS     : {stats}\n"
        f"{'='*60}\n"
    )
    print(report)
    (DASHBOARD_DIR / "latest_digest.txt").write_text(report)
    dashboard_path = generate_dashboard()
    log.info(f"{period.capitalize()} digest done. Dashboard → {dashboard_path}")


def morning_digest():
    _write_digest("morning")

def afternoon_digest():
    _write_digest("afternoon")

def evening_digest():
    _write_digest("evening")


# ── SCHEDULER SETUP ───────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()

# Main loop: 2-min interval (was 10 min with BlockingScheduler — 5x throughput gain)
# Per-project locks prevent overlapping tasks within the same repo
scheduler.add_job(execute_next_task, "interval", minutes=2,
                  id="main_loop", max_instances=1, coalesce=True)

# Digest schedule
scheduler.add_job(morning_digest,   "cron", hour=8,  minute=0, id="morning_digest")
scheduler.add_job(afternoon_digest, "cron", hour=14, minute=0, id="afternoon_digest")
scheduler.add_job(evening_digest,   "cron", hour=20, minute=0, id="evening_digest")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _write_pid()    # crash-safe: exits if another instance is running

    log.info("=" * 60)
    log.info("Orchestrator starting")
    log.info(f"Enabled projects: {ENABLED_PROJECTS}")
    log.info(f"Monthly spend so far: ${spend_tracker.monthly_spend():.2f}")
    log.info("=" * 60)

    # Seed starter tasks if queue is empty
    if task_queue.total_unblocked(projects=ENABLED_PROJECTS) == 0:
        _seed_sample_tasks()

    unblocked = task_queue.total_unblocked(projects=ENABLED_PROJECTS)
    log.info(f"Unblocked tasks ready: {unblocked}")
    log.info(f"DB stats: {task_queue.stats()}")

    generate_dashboard()
    log.info(f"Dashboard → {DASHBOARD_DIR / 'index.html'}")

    try:
        scheduler.start()
        # Keep main thread alive for BackgroundScheduler
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Orchestrator stopping...")
        scheduler.shutdown(wait=False)
        _remove_pid()
        log.info("Orchestrator stopped.")


# ── ENV VARS REQUIRED ─────────────────────────────────────────────────────────
"""
Required environment variables:

export MINIMAX_API_KEY="your_minimax_key"
export ANTHROPIC_API_KEY="your_anthropic_key"

Optional overrides:
export OLLAMA_MODEL="your_30b_model_name"   # default: llama3
export MINIMAX_MODEL="minimax/minimax-m3"    # default as above

Install dependencies:
pip install apscheduler requests

Aider must be installed and on PATH:
pip install aider-chat
"""


# ── LANGUAGE APP SCENE PIPELINE ──────────────────────────────────────────────
"""
Separate pipeline for language app scene generation.
Runs nightly, most autonomous part of the stack.
TODO: move to lang_pipeline.py

Scene generation flow per night:
1. Load scene schedule (which scenes are due tonight)
2. For each scene:
   a. Ollama writes scene generation prompt from schema template
   b. MiniMax API call (direct, not via Aider) generates scene JS module
   c. Second MiniMax call generates Three.js config
   d. Python writes to language-travel-app/scenes/{lang}/{scene_id}.js
   e. Run Node smoke test: node tests/smoke.js scenes/{lang}/{scene_id}.js
   f. Pass → log success. Fail → log for morning digest, retry next night.
3. Morning digest includes: N scenes generated, M passed smoke test, failures listed

Scene schedule (7 nights):
Night 1: ja/izakaya_01, ja/konbini_01
Night 2: ja/train_station_01, ja/ramen_shop_01
Night 3: ja/temple_01, ja/review_pass (expand randomization on nights 1-2)
Night 4: es/taco_vendor_01, es/mercado_01
Night 5: es/cafe_01, es/taxi_01
Night 6: es/hotel_01, es/srs_integration
Night 7: buffer — failed scenes, gap fill

Scene schema in ORCHESTRATOR_CONTEXT.md → "Scene JS module schema"
"""


def _seed_sample_tasks():
    """Seed lang.json with starter tasks if no tasks exist yet. Call only from __main__."""
    sample = [
        {
            "id": "lang_001", "project": "lang", "priority": 0, "status": "queued",
            "complexity": "medium", "effort_category": "feature",
            "perspective": "speech_linguist", "approval_required": False,
            "depends_on": [], "blocks": [],
            "description": "Generate complete scene JS module for Japanese A0 izakaya scenario. Include dialogue tree, randomization pools (5+ variants each), Three.js config, SRS card list. Save to scenes/ja/izakaya_01.js",
            "rationale": "First scene validates the full generation pipeline",
            "estimated_tokens": 8000,
        },
        {
            "id": "lang_002", "project": "lang", "priority": 0, "status": "queued",
            "complexity": "medium", "effort_category": "feature",
            "perspective": "speech_linguist", "approval_required": False,
            "depends_on": [], "blocks": [],
            "description": "Generate complete scene JS module for Japanese A0 konbini (convenience store) scenario. Save to scenes/ja/konbini_01.js",
            "rationale": "Second night-1 scene",
            "estimated_tokens": 8000,
        },
        {
            "id": "lang_003", "project": "lang", "priority": 0, "status": "queued",
            "complexity": "low", "effort_category": "test",
            "perspective": "qa_tester", "approval_required": False,
            "depends_on": [], "blocks": [],
            "description": "Generate Node.js smoke test runner: validates scene exports correctly, required schema fields present, randomizationPool has 5+ items per key, Three.js config valid. Save to tests/smoke.js",
            "rationale": "Automated pass/fail gate for every generated scene",
            "estimated_tokens": 5000,
        },
    ]
    inserted = sum(1 for t in sample if task_queue.add_task(t))
    if inserted:
        log.info(f"Seeded {inserted} starter lang tasks into queue")
