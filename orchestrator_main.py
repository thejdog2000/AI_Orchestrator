"""
orchestrator/main.py
Multi-project AI orchestrator — starter scaffold
Feed ORCHESTRATOR_CONTEXT.md to any AI session before iterating on this file.
Use str_replace for modifications — don't regenerate the whole file.

IMMEDIATE TODOs before first run:
  1. Set env vars (see bottom of file)
  2. Set hard spend caps in MiniMax + Anthropic dashboards
  3. Verify MiniMax M3 promo rate at platform.minimax.io
  4. Pre-load task JSON files in tasks/ directory
  5. Run language app pipeline ONLY first — validate before enabling others
"""

import subprocess
import json
import os
import time
import logging
from datetime import datetime
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
import requests

from task_queue import TaskQueue
from dashboard_generator import generate as generate_dashboard

# ── CONFIG ──────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
TASKS_DIR = BASE_DIR / "tasks"
PENDING_DIR = BASE_DIR / "pending_review"
LOGS_DIR = BASE_DIR / "logs"
DASHBOARD_DIR = BASE_DIR / "dashboard"

for d in [TASKS_DIR, PENDING_DIR, LOGS_DIR, DASHBOARD_DIR]:
    d.mkdir(exist_ok=True)

PROJECTS = ["lang", "meridian", "rts", "gamma", "ninja", "tax"]

# Projects enabled for overnight autonomous runs
# Start with lang only — enable others after validating pipeline
ENABLED_PROJECTS = ["lang"]  # expand after sprint day 1 validation

MAX_RETRIES = 3
QUEUE_REFILL_THRESHOLD = 10  # generate new tasks when queue drops below this

MINIMAX_API_BASE = "https://api.minimax.io/v1"
MINIMAX_MODEL = "minimax/minimax-m3"

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "llama3"  # update to your actual 30B model name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "orchestrator.log"),
        logging.StreamHandler()
    ]
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
        # MiniMax M3 promo rates — update if promo expires
        rates = {
            "minimax/minimax-m3": (0.30, 1.20),   # per 1M tokens input/output
            "claude-haiku-4-5":   (1.00, 5.00),
            "claude-sonnet-4-6":  (3.00, 15.00),
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
        """Returns False if monthly spend approaching hard caps."""
        monthly = self.monthly_spend()
        if monthly > 90:  # warn at $90, caps are $100
            log.warning(f"Monthly API spend ${monthly:.2f} approaching cap. Pausing overnight work.")
            return False
        return True


spend_tracker = SpendTracker()

# ── TASK QUEUE ───────────────────────────────────────────────────────────────

class TaskQueue:
    def __init__(self):
        self.completed_file = LOGS_DIR / "completed.json"
        self.completed = self._load_completed()

    def _load_completed(self):
        if self.completed_file.exists():
            return json.loads(self.completed_file.read_text())
        return []

    def _save_completed(self):
        self.completed_file.write_text(json.dumps(self.completed, indent=2))

    def load_project_tasks(self, project: str) -> list:
        task_file = TASKS_DIR / f"{project}.json"
        if not task_file.exists():
            return []
        tasks = json.loads(task_file.read_text())
        completed_ids = {t["id"] for t in self.completed}
        pending_review_ids = {f.stem for f in PENDING_DIR.glob(f"{project}_*.diff")}
        return [
            t for t in tasks
            if t["id"] not in completed_ids
            and t["id"] not in pending_review_ids
            and t.get("status", "queued") == "queued"
        ]

    def get_next(self, project: str = None) -> dict | None:
        """Get next unblocked task. Cross-project if project not specified."""
        projects = [project] if project else ENABLED_PROJECTS
        for proj in projects:
            tasks = self.load_project_tasks(proj)
            unblocked = [
                t for t in tasks
                if not t.get("approval_required", False)
                and self._blocks_satisfied(t)
            ]
            if unblocked:
                # Sort by priority (0=highest)
                unblocked.sort(key=lambda t: t.get("priority", 1))
                return unblocked[0]
        return None

    def _blocks_satisfied(self, task: dict) -> bool:
        """Check if all tasks this task depends on are completed."""
        completed_ids = {t["id"] for t in self.completed}
        depends = task.get("depends_on", [])
        return all(dep in completed_ids for dep in depends)

    def total_unblocked(self) -> int:
        count = 0
        for proj in ENABLED_PROJECTS:
            tasks = self.load_project_tasks(proj)
            count += len([t for t in tasks if not t.get("approval_required", False)])
        return count

    def mark_pending_review(self, task: dict, diff_path: Path):
        """Task generated diff, awaiting Jacob approval."""
        task["status"] = "pending_review"
        task["diff_path"] = str(diff_path)
        task["completed_at"] = datetime.now().isoformat()
        log.info(f"[{task['project']}] Task {task['id']} pending review → {diff_path.name}")

    def mark_completed(self, task: dict):
        task["status"] = "completed"
        task["completed_at"] = datetime.now().isoformat()
        self.completed.append(task)
        self._save_completed()

    def get_gap_fill_tasks(self) -> list:
        """Always-available low-stakes tasks when queue is empty."""
        return [
            {"id": f"gap_{int(time.time())}", "project": "lang", "priority": 2,
             "description": "Expand randomization pools for any completed language scenes",
             "approval_required": False, "blocks": []},
            {"id": f"gap_{int(time.time())+1}", "project": "meridian", "priority": 2,
             "description": "Generate JSDoc comments for any undocumented exported functions",
             "approval_required": False, "blocks": []},
            {"id": f"gap_{int(time.time())+2}", "project": "rts", "priority": 2,
             "description": "Generate XML summary comments for all public C# methods",
             "approval_required": False, "blocks": []},
        ]


task_queue = TaskQueue()  # SQLite-backed — see task_queue.py

# Load any pre-existing JSON task backlogs into the DB on startup
for _json_file in TASKS_DIR.glob("*.json"):
    _project = _json_file.stem
    _n = task_queue.load_from_json(_json_file)
    if _n:
        log.info(f"Loaded {_n} new tasks from {_json_file.name}")

# ── OLLAMA CLIENT ─────────────────────────────────────────────────────────────

def ollama_generate(prompt: str, max_tokens: int = 1000) -> str:
    """Call local Ollama. Returns text. Never makes tool calls."""
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        log.error(f"Ollama error: {e}")
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
    """Ollama evaluates generated diff quality. Returns score + reasoning."""
    prompt = f"""Evaluate this code diff for quality and correctness.
Task was: {task['description']}
Project: {task['project']}

Diff (first 3000 chars):
{diff_text[:3000]}

Respond in JSON only:
{{"score": 0-10, "pass": true/false, "issues": ["issue1"], "reasoning": "brief"}}"""
    result = ollama_generate(prompt, max_tokens=300)
    try:
        # Strip any markdown fences if present
        clean = result.strip().strip("```json").strip("```").strip()
        return json.loads(clean)
    except Exception:
        return {"score": 5, "pass": True, "issues": [], "reasoning": "Could not parse evaluation"}


def generate_digest(period: str) -> str:
    """Ollama writes human-readable digest from structured log data."""
    today = datetime.now().strftime("%Y-%m-%d")
    completed_today = [
        t for t in task_queue.completed
        if t.get("completed_at", "").startswith(today)
    ]
    pending_count = len(list(PENDING_DIR.glob("*.diff")))
    monthly_spend = spend_tracker.monthly_spend()

    prompt = f"""Write a brief {period} digest for Jacob's multi-project AI orchestrator.
Tone: direct, no fluff. Format: plain text, short bullets.

DATA:
- Tasks completed today: {len(completed_today)}
- Completed tasks: {json.dumps([t['description'] for t in completed_today[-10:]], indent=2)}
- Diffs awaiting Jacob review: {pending_count}
- Monthly API spend so far: ${monthly_spend:.2f} / $100 cap
- Enabled projects: {ENABLED_PROJECTS}

Write the {period} digest (5-8 bullet points max):"""
    return ollama_generate(prompt, max_tokens=600)

# ── AIDER RUNNER ─────────────────────────────────────────────────────────────

def get_context_md(project: str) -> str:
    """Load project CONTEXT.md. Returns empty string if not found."""
    # Check common locations
    candidates = [
        Path(f"../{project}/CONTEXT.md"),
        Path(f"../ironhold-rts/CONTEXT.md") if project == "rts" else None,
        Path(f"../meridian-mobile/CONTEXT.md") if project == "meridian" else None,
        Path(f"../language-travel-app/CONTEXT.md") if project == "lang" else None,
    ]
    for path in candidates:
        if path and path.exists():
            return path.read_text()[:3000]
    return f"No CONTEXT.md found for {project}. Proceeding with task description only."


def run_aider_task(task: dict, model: str = MINIMAX_MODEL) -> dict:
    """
    Run Aider on a task. Returns result dict with success, diff_path, tokens.
    Uses --no-auto-commits always. Diffs saved to pending_review/.
    """
    project = task["project"]
    
    # Map project to repo path
    repo_paths = {
        "meridian": "../meridian-mobile",
        "rts": "../ironhold-rts", 
        "lang": "../language-travel-app",
        "gamma": "../gamma-tool",
        "ninja": "../ninjatrader-algos",
        "tax": "../tax-cloud-tools",
    }
    repo_path = Path(repo_paths.get(project, f"../{project}"))

    if not repo_path.exists():
        log.error(f"Repo path not found: {repo_path}")
        return {"success": False, "error": "repo_not_found"}

    # Get context and write Aider prompt
    context_md = get_context_md(project)
    aider_message = write_aider_prompt(task, context_md)
    
    if not aider_message:
        log.error(f"Ollama failed to generate prompt for task {task['id']}")
        return {"success": False, "error": "prompt_generation_failed"}

    # Build Aider command
    if "minimax" in model:
        cmd = [
            "aider",
            "--openai-api-base", MINIMAX_API_BASE,
            "--openai-api-key", os.environ.get("MINIMAX_API_KEY", ""),
            "--model", model,
            "--no-auto-commits",
            "--yes-always",
            "--message", aider_message,
        ]
    else:
        cmd = [
            "aider",
            "--model", model,
            "--no-auto-commits",
            "--yes-always",
            "--message", aider_message,
        ]

    log.info(f"[{project}] Running Aider task: {task['id']}")
    log.info(f"[{project}] Prompt: {aider_message[:200]}...")

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout per task
        )
        
        # Capture git diff of unstaged changes (--no-auto-commits means changes are uncommitted)
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=repo_path,
            capture_output=True,
            text=True
        )
        
        diff_text = diff_result.stdout
        
        if not diff_text.strip():
            log.warning(f"[{project}] Aider produced no diff for task {task['id']}")
            return {"success": False, "error": "no_diff_produced", "stdout": result.stdout}

        # Save diff to pending_review
        diff_filename = f"{project}_{task['id']}_{int(time.time())}.diff"
        diff_path = PENDING_DIR / diff_filename
        diff_path.write_text(diff_text)

        # Estimate tokens from stdout (Aider reports usage)
        # TODO: parse actual token counts from Aider output
        estimated_tokens = len(aider_message.split()) * 1.3 + len(diff_text.split()) * 1.3

        return {
            "success": True,
            "diff_path": diff_path,
            "diff_text": diff_text,
            "stdout": result.stdout,
            "estimated_input_tokens": int(estimated_tokens * 0.7),
            "estimated_output_tokens": int(estimated_tokens * 0.3),
        }

    except subprocess.TimeoutExpired:
        log.error(f"[{project}] Aider timed out on task {task['id']}")
        return {"success": False, "error": "timeout"}
    except Exception as e:
        log.error(f"[{project}] Aider error on task {task['id']}: {e}")
        return {"success": False, "error": str(e)}

# ── MAIN EXECUTION LOOP ───────────────────────────────────────────────────────

def execute_next_task():
    """Core loop iteration — called by scheduler."""
    
    # Check spend caps before running anything
    if not spend_tracker.check_caps():
        log.info("Spend cap approaching — skipping task execution this cycle")
        return

    # Get next task (cross-project, unblocked, no approval required)
    task = task_queue.get_next(projects=ENABLED_PROJECTS)
    
    if task is None:
        # Try gap-fill tasks
        gap_tasks = task_queue.get_gap_fill_tasks()
        if gap_tasks:
            task = gap_tasks[0]
            log.info(f"Queue empty — running gap-fill: {task['description']}")
        else:
            log.info("No tasks available — all projects blocked or queue empty")
            return

    log.info(f"Executing: [{task['project']}] {task['description']}")

    # Run with retries
    result = None
    for attempt in range(MAX_RETRIES):
        result = run_aider_task(task)
        if result["success"]:
            break
        log.warning(f"Attempt {attempt + 1} failed: {result.get('error')}. Retrying...")
        time.sleep(5)

    if not result or not result["success"]:
        log.error(f"Task {task['id']} failed after {MAX_RETRIES} attempts. Flagging for human review.")
        # Write failure to pending_review for morning digest
        failure_file = PENDING_DIR / f"FAILED_{task['project']}_{task['id']}.json"
        failure_file.write_text(json.dumps({"task": task, "result": result}, indent=2))
        return

    # Quality gate
    evaluation = evaluate_diff(result["diff_text"], task)
    log.info(f"Quality gate: score={evaluation.get('score')}, pass={evaluation.get('pass')}")

    if not evaluation.get("pass", True):
        # Escalate to Claude Haiku
        log.info(f"Quality gate failed — escalating {task['id']} to Claude Haiku")
        result = run_aider_task(task, model="claude-haiku-4-5")
        
        if not result["success"]:
            log.error(f"Claude Haiku also failed — flagging {task['id']} for human review")
            failure_file = PENDING_DIR / f"ESCALATION_FAILED_{task['project']}_{task['id']}.json"
            failure_file.write_text(json.dumps({"task": task, "evaluation": evaluation}, indent=2))
            return

    # Record spend
    cost = spend_tracker.record(
        task["project"],
        result.get("estimated_input_tokens", 0),
        result.get("estimated_output_tokens", 0),
        MINIMAX_MODEL
    )
    log.info(f"Task complete. Estimated cost: ${cost:.4f}. Monthly total: ${spend_tracker.monthly_spend():.2f}")

    # Queue diff for Jacob review
    task_queue.mark_pending_review(task, result["diff_path"])

    # Check if queue needs refilling
    if task_queue.total_unblocked(projects=ENABLED_PROJECTS) < QUEUE_REFILL_THRESHOLD:
        log.info("Queue low — generating new tasks via Ollama")
        generate_new_tasks()


def generate_new_tasks():
    """
    Ollama generates next wave of tasks based on completed work.
    TODO: implement per-project task generation and append to tasks/*.json
    """
    # TODO: for each enabled project, read CONTEXT.md + completed log,
    # ask Ollama to generate 20 more tasks, validate JSON, append to tasks/{project}.json
    log.info("Task generation not yet implemented — add tasks manually to tasks/*.json")


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

scheduler = BlockingScheduler()

# Main execution loop — every 10 minutes
# Adjust frequency based on task complexity and token budget
scheduler.add_job(execute_next_task, 'interval', minutes=10, id='main_loop')

# Digest schedule
scheduler.add_job(morning_digest,   'cron', hour=8,  minute=0, id='morning_digest')
scheduler.add_job(afternoon_digest, 'cron', hour=14, minute=0, id='afternoon_digest')
scheduler.add_job(evening_digest,   'cron', hour=20, minute=0, id='evening_digest')


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("="*60)
    log.info("Orchestrator starting")
    log.info(f"Enabled projects: {ENABLED_PROJECTS}")
    log.info(f"Monthly spend so far: ${spend_tracker.monthly_spend():.2f}")
    log.info("="*60)

    # Safety check
    unblocked = task_queue.total_unblocked(projects=ENABLED_PROJECTS)
    log.info(f"Unblocked tasks ready: {unblocked}")
    log.info(f"DB stats: {task_queue.stats()}")
    if unblocked == 0:
        log.warning("No tasks loaded. Add tasks to tasks/*.json before running.")
        log.warning("Example task structure in ORCHESTRATOR_CONTEXT.md")
    generate_dashboard()
    log.info(f"Dashboard → {DASHBOARD_DIR / 'index.html'}")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Orchestrator stopped by user")
        scheduler.shutdown()


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


# ── SAMPLE TASK JSON (save to tasks/lang.json to start) ──────────────────────
SAMPLE_LANG_TASKS = """
[
  {
    "id": "lang_001",
    "project": "lang",
    "description": "Generate complete scene JS module for Japanese A0 izakaya scenario following the scene schema. Include dialogue tree, randomization pools (5+ variants each), Three.js config, and SRS card list. Save to scenes/ja/izakaya_01.js",
    "approval_required": false,
    "depends_on": [],
    "blocks": ["lang_002"],
    "estimated_tokens": 8000,
    "priority": 0,
    "status": "queued"
  },
  {
    "id": "lang_002",
    "project": "lang",
    "description": "Generate complete scene JS module for Japanese A0 convenience store (konbini) scenario. Save to scenes/ja/konbini_01.js",
    "approval_required": false,
    "depends_on": [],
    "blocks": [],
    "estimated_tokens": 8000,
    "priority": 0,
    "status": "queued"
  },
  {
    "id": "lang_003",
    "project": "lang",
    "description": "Generate Node.js smoke test runner that validates: scene exports correctly, all required schema fields present, randomizationPool has 5+ items per key, Three.js config has valid structure. Save to tests/smoke.js",
    "approval_required": false,
    "depends_on": [],
    "blocks": [],
    "estimated_tokens": 5000,
    "priority": 0,
    "status": "queued"
  }
]
"""

# Save sample tasks on first run if file doesn't exist
if not (TASKS_DIR / "lang.json").exists():
    (TASKS_DIR / "lang.json").write_text(SAMPLE_LANG_TASKS)
    log.info("Created sample lang.json task file")
