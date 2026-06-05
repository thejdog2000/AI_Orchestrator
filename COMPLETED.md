# Orchestrator — Completed Work

---

## Bugs Fixed

**#1 Dead TaskQueue class shadowing SQLite import**
Old JSON-based class in main.py overwrote `from task_queue import TaskQueue`.
Every downstream call crashed. Deleted old class entirely.

**#2 `generate_digest` crash — missing `.completed` attribute**
`task_queue.completed` doesn't exist on SQLite class.
Fixed: replaced with `task_queue.get_completed_today()`.

**#3 `get_next(projects=...)` wrong kwarg on old class**
Resolved by #1 — old class gone, SQLite class accepts `projects` list.

**#4 No process lock — double-execution on crash/restart**
Added `_write_pid()` on startup, `_remove_pid()` on shutdown.
Exits if PID file already exists.

**#5 Unstaged changes compounding between retries**
`git checkout -- .` before every task attempt in `_run_task()`.
Retry always starts from clean working tree.

**#6 Relative paths in `get_context_md` — silent failures**
`Path(f"../{project}/CONTEXT.md")` was relative to launch directory.
Fixed: `REPO_PATHS` dict with absolute `Path.home()` anchored paths for all 6 projects.

**#7 Token estimation off by 10–100x**
Word-count guessing replaced with actual `usage.prompt_tokens / completion_tokens`
from MiniMax API response.

**#8 API key exposed in `ps aux`**
Resolved by Aider removal — key only in env var, never a CLI arg.

**#9 BlockingScheduler + 10-min interval — poor overnight throughput**
Replaced with `BackgroundScheduler` + 2-min interval + per-project `_project_running`
flags. Lang + meridian can now run simultaneously. ~5x overnight throughput gain.

**#10 Spend cap not split by provider**
`MINIMAX_SPEND_CAP = 65.0` checked independently.
Claude removed — single cap, no combined-pool ambiguity.

**#11 Escalation path compounding dirty git state**
Entire Claude escalation path removed. Fail → log → next task.

**#12 Aider full-repo token scan on every call**
Resolved by Aider removal — `run_minimax_task()` loads explicit context only.

**#13 Log file grows unbounded**
`RotatingFileHandler(maxBytes=5MB, backupCount=5)`.

**#15 `SAMPLE_LANG_TASKS` side effect at import time**
Refactored into `_seed_sample_tasks()`, called only from `__main__`.

**#16 `__import__("time")` inline in task_generator.py**
`import time` moved to top of file.

---

## Features Built

**SQLite task queue (`task_queue.py`)**
Replaces JSON file per project. Schema includes: `complexity`, `rationale`,
`effort_category`, `perspective`, `review_priority` (1–5 computed).
`PERSPECTIVE_PROJECT_MAP` maps council roles to relevant projects.

**Kanban dashboard (`dashboard_generator.py`)**
Static HTML, no server needed. Columns: Queued / Running / Pending Review / Completed / Failed.
Filter by project, perspective, complexity. Review priority bar per card.
Regenerated on each digest.

**Council task generation (`task_generator.py`)**
Sequential MiniMax calls (one per perspective) + JSON merge pass.
Phase-weighted perspective selection. `json_mode=True` on merge for reliable output.
`PHASE_WEIGHTS` and `PHASE_PERSPECTIVE_PRIORITY` dicts drive council composition.

**Direct MiniMax execution (`run_minimax_task`)**
Replaces Aider subprocess. `<<<FILE: path>>>` delimiters for structured response parsing.
Actual token counts from API. Key via env only.

**Per-project execution locks**
`_project_running` dict — different projects execute simultaneously, same project cannot overlap.

**`ORCHESTRATOR_CONTEXT.md`**
Master context doc. Feed to any AI session to restore full context.
Includes repo paths, model names, budget, sprint schedule, architecture decisions.
