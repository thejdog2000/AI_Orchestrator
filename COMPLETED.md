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
`_write_pid()` on startup, `_remove_pid()` on shutdown. Exits if PID file exists.

**#5 Unstaged changes compounding between retries**
`git checkout -- .` before every task attempt. Retry always starts clean.

**#6 Relative paths in `get_context_md` — silent failures**
Fixed: `REPO_PATHS` dict with absolute `Path.home()` anchored paths for all 6 projects.

**#7 Token estimation off by 10–100x**
Replaced word-count guessing with actual `usage.prompt_tokens / completion_tokens`
from MiniMax API response.

**#8 API key exposed in `ps aux`**
Resolved by Aider removal — key only in env var, never a CLI arg.

**#9 BlockingScheduler + 10-min interval — poor overnight throughput**
`BackgroundScheduler` + 2-min interval + per-project `_project_running` flags.
Lang + meridian run simultaneously. ~5x overnight throughput gain.

**#10 Spend cap not split by provider**
`MINIMAX_SPEND_CAP = 65.0` checked independently. Claude removed — single cap.

**#11 Escalation path compounding dirty git state**
Claude escalation path removed entirely. Fail → log → next task.

**#12 Aider full-repo token scan on every call**
Resolved by Aider removal — explicit context loading only.

**#13 Log file grows unbounded**
`RotatingFileHandler(maxBytes=5MB, backupCount=5)`.

**#14 `get_context_md` duplicated across modules**
Consolidated into `load_context()` in `executor.py`. Imported by both
`orchestrator_main.py` and `task_generator.py`. Single source of truth.

**#15 `SAMPLE_LANG_TASKS` side effect at import time**
Refactored into `_seed_sample_tasks()`, called only from `__main__`.

**#16 `__import__("time")` inline in task_generator.py**
`import time` at top of file.

**#19 Gap-fill task IDs — 1-second resolution collision**
`uuid4().hex[:8]` replaces `int(time.time())`. No more PRIMARY KEY conflicts.

---

## Features Built

**SQLite task queue (`task_queue.py`)**
Schema: `complexity`, `rationale`, `effort_category`, `perspective`, `review_priority` (1–5).
`PERSPECTIVE_PROJECT_MAP` maps council roles to relevant projects.

**Kanban dashboard (`dashboard_generator.py`)**
Static HTML, no server. Columns: Queued / Running / Pending Review / Completed / Failed.
Filters: project, perspective, complexity. Review priority bar per card.
Regenerated on each digest.

**Council task generation (`task_generator.py`)**
Sequential MiniMax calls per perspective + JSON merge pass.
Phase-weighted perspective selection (`PHASE_WEIGHTS`, `PHASE_PERSPECTIVE_PRIORITY`).
`json_mode=True` on merge. Runs when queue drops below threshold.

**Direct MiniMax execution (`executor.py` → `run_minimax_task`)**
Replaces Aider subprocess entirely. `<<<FILE: path>>>` delimiters, structured parsing.
Actual token counts from API response. Key via env only.

**CONTEXT.md feedback loop (`executor.py` → `update_context_md`)**
After each successful task: Ollama reads diff + old CONTEXT.md → writes updated summary.
MiniMax gets progressively better context each night. Closes the core feedback gap.

**Smarter retries (`executor.py` → `run_task`)**
Attempt 1: `write_execution_prompt` (fresh Ollama prompt).
Attempt 2+: `revise_execution_prompt` — Ollama targets specific failure reasons from
the quality gate evaluation. No more blind repeats of failed prompts.

**Module split — main.py 700 lines → 160 lines**
- `spend.py` — SpendTracker (tracks by project + day)
- `executor.py` — MiniMax execution, Ollama prompts, CONTEXT.md loop, quality gate, retries
- `digests.py` — digest generation (qwen3:14b), morning/afternoon/evening
- `orchestrator_main.py` — config, scheduler, entry point only

**Per-project execution locks**
`_project_running` dict — different projects run simultaneously, same project cannot overlap.

**Approval workflow (`approve.py`)**
`python approve.py` — list pending diffs by review priority (color-coded).
`python approve.py <task_id>` — git add (stages, does NOT commit), archive diff, mark complete.
`python approve.py --reject <task_id>` — mark failed, delete diff.
`python approve.py --open <task_id>` — open diff in $PAGER.

**Language scene pipeline (`lang_pipeline.py`)**
7-night schedule with per-scene state tracking in `tasks/lang_schedule.json`.
Schema-aware MiniMax prompts (full JS module format embedded in prompt).
Node.js smoke test runs after each scene. Failed scenes retry next available night.
`python lang_pipeline.py --status` shows schedule progress.

**Nightly DB backup (`orchestrator_main.py` → `backup_db`)**
`sqlite3` dump at 3am, 7-day rolling window in `backups/`.

**git_watcher.py — auto-commit without manual touch**
Polls `COMMIT_REQUEST.txt` every 10s. When present: clears git lock files (full Mac
permissions), `git add -A`, commits with the message, pushes to origin/main, deletes
the request file. Logs to `logs/git_watcher.log`.
Claude writes `COMMIT_REQUEST.txt` → watcher handles the rest.

**`ORCHESTRATOR_CONTEXT.md`**
Master context doc. Feed to any AI session to restore full context without re-explaining.
Includes verified repo paths, Ollama model names, budget, sprint schedule,
architecture decisions, council prompting design.

**Previous TODO items resolved (pre-review)**
#14 load_context consolidated in executor.py — single source of truth.
#17 No DB backup — nightly sqlite3 dump, 7-day rolling window.
#18 No approval workflow — approve.py built (list/approve/reject/open).
#19 Gap-fill UUID — uuid4().hex[:8] replaces int(time.time()).

---

## Security + AI Engineering Review Fixes

**SEC-1 Path traversal in `_parse_file_blocks`**
MiniMax response file paths now validated with `dest.resolve().is_relative_to(repo_path.resolve())`
before any write. Blocked paths are logged as errors and skipped. Applied in both
executor.py and lang_pipeline.py. If all writes are blocked, task fails rather than silently succeeding.

**SEC-2 Quality gate fails closed**
Ollama JSON parse failure now returns `{"pass": False}` with a human-review flag.
Broken gate no longer auto-approves everything. Raw Ollama output included in reasoning for debugging.

**SEC-3 Atomic spend writes**
`spend.py._save()` writes to `.tmp` then `os.replace()`. No more corrupted spend file on crash.
Corrupted spend file = no cap enforcement — this was the highest-consequence data loss risk.

**HIGH-1/2/3 Shared config via `config.py`**
Single source of truth for all runtime config: paths, models, API endpoints, spend cap.
`task_generator.py` and `lang_pipeline.py` were hardcoding their own model strings/paths
independently — both now import from `config.py`. `executor.configure()` guard added:
raises `RuntimeError` with a clear message if called before `configure()`.

**HIGH-4 Ollama health check at startup**
`executor.check_ollama()` called before scheduler starts. Verifies reachability and that
required models (qwen3-coder:30b, qwen3:14b) are loaded. Logs clear error with remediation
steps rather than silently degrading all night.

**MED-1 `update_context_md` wrong model**
Was using qwen3-coder:30b (code model) for prose summarization. Fixed to OLLAMA_MODEL_DIGEST
(qwen3:14b) — faster, adequate for markdown summarization, reduces per-task latency.

**LOW-2/3 .gitignore + approve.py standalone safety**
`backups/` and `approved/` added to .gitignore. approve.py now creates its own dirs on
startup rather than relying on orchestrator_main having run first.

---

## Documentation Restructure

Split `ORCHESTRATOR_CONTEXT.md` (was 600+ lines, ~8000 tokens) into focused docs:
- `ORCHESTRATOR_CONTEXT.md` — slim entry point (~600 tokens). Load every session.
- `ARCHITECTURE.md` — technical design, execution flow, module map, security.
- `PROJECTS.md` — all 6 projects, sprint phases, warm opportunities.
- `BACKLOG.md` — future features with full specs (split from TODO.md).
- `TODO.md` — active near-term work only.

AI sessions now load only what's relevant — task-specific sessions save ~5000 tokens.

## Design Decisions Locked (from design conversation)

**Auto-commit (90-95% of tasks):** `pending_review/` accumulation removed.
Tasks auto-commit after path guard passes. Everything's in git — bad output gets `git revert`.
`approval_required=True` stays for JWT auth, schema migrations, client deliverables only.

**Discord bot as primary interface:** PA model — orchestrator pushes to Jacob,
Jacob directs via natural language. Replaces polling approve.py / reading logs.
Cowork (Claude) handles deep-work sessions. `o` CLI alias for terminal use.

**Quality gate and feedback loops deprioritized:** local Ollama isn't reliable enough
for meaningful evaluation. Auto-commit + git revert is the practical safety net.
