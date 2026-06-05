# Orchestrator — Issue Tracker

Issues found during senior AI/solutions engineering review.
Status: 🔴 open | ✅ fixed | ⏸ deferred

---

## BREAKING — Must fix before overnight run

- ✅ **#1 Dead TaskQueue class in main.py shadows import from task_queue.py**
  Old JSON-based class overwrote `from task_queue import TaskQueue`. Fixed: deleted.

- ✅ **#2 `generate_digest` references `task_queue.completed` — doesn't exist on SQLite class**
  Fixed: replaced with `task_queue.get_completed_today()`.

- ✅ **#3 `get_next(projects=ENABLED_PROJECTS)` — wrong kwarg on old class**
  Resolved by #1 fix — old class gone, SQLite class accepts `projects` list.

- ✅ **#4 No process lock — double-execution on crash/restart**
  Fixed: `_write_pid()` on startup, `_remove_pid()` on shutdown. Exits if PID file exists.

- ✅ **#5 Aider/MiniMax leaves unstaged changes between retries**
  Fixed: `git checkout -- .` before every task attempt in `_run_task()`.

- ✅ **#6 Relative paths in `get_context_md` — silent failures off expected cwd**
  Fixed: `REPO_PATHS` dict with absolute `Path.home()` anchored paths for all 6 projects.

---

## Concerning — Fix for cost / optimization / risk

- ✅ **#7 Token estimation off by 10–100x — spend tracker unreliable**
  Fixed: `_minimax_execute()` returns actual `usage.prompt_tokens / completion_tokens`
  from API response. No more word-count guessing.

- ✅ **#8 API key exposed in subprocess args (ps aux visible)**
  Fixed (via Aider removal): key now only in env var, never passed as CLI arg.

- ✅ **#9 BlockingScheduler + 10-min interval kills overnight throughput**
  Fixed: `BackgroundScheduler` + 2-min interval + per-project `_project_running` flags.
  Lang + meridian can now run simultaneously. `max_instances=1, coalesce=True` on job.

- ✅ **#10 Spend caps not split by provider**
  Fixed: `MINIMAX_SPEND_CAP = 65.0` checked independently. Claude removed — single cap.

- ✅ **#11 Escalation path compounds dirty git state**
  Fixed: escalation path removed entirely. Fail → log → next task. No Claude dependency.

- ✅ **#12 No `--files` targeting on Aider calls**
  Fixed (via Aider removal): `run_minimax_task()` loads explicit context, no repo scan.

- ✅ **#13 Log file grows unbounded**
  Fixed: `RotatingFileHandler(maxBytes=5MB, backupCount=5)`.

- 🔴 **#14 `get_context_md` duplicated in main.py and task_generator.py**
  Two implementations with different path logic — will drift over time.
  Fix: consolidate into single `load_context(project)` in `task_queue.py`, import from both.

---

## Less Concerning

- ✅ **#15 `SAMPLE_LANG_TASKS` writes a file at module import time**
  Fixed: refactored into `_seed_sample_tasks()`, called only from `__main__`.

- ✅ **#16 `__import__("time")` inline in task_generator.py**
  Fixed: `import time` at top of file, inline call removed.

- 🔴 **#17 No SQLite DB backup strategy**
  `orchestrator.db` gitignored (correct). Machine crash = task history lost.
  Fix: nightly `sqlite3 orchestrator.db .dump > backups/tasks_YYYY-MM-DD.sql`.

- 🔴 **#18 No approval workflow — diffs accumulate in pending_review/ forever**
  Count grows in digest but never goes down. No CLI to approve/reject.
  Fix: build `approve.py`: `python approve.py <task_id>` applies diff, marks completed.

- 🔴 **#19 Gap-fill task IDs use `int(time.time())` — 1-second resolution**
  Two calls in the same second → SQLite PRIMARY KEY conflict, silently drops insertion.
  Fix: `uuid4()` or `time.time_ns()` for gap-fill IDs in `task_queue.py`.

---

## Architecture Decisions (locked — don't re-litigate)

- **No Aider** — direct MiniMax API calls: full context control, token visibility, no subprocess black box
- **No Claude escalation** — fail → log → next task. MiniMax + Ollama only.
- **qwen3-coder:30b** for execution prompts / quality gate; **qwen3:14b** for digest prose
- **Per-project locks** not global — lang + meridian can run simultaneously
- **Sequential council** not debate — one MiniMax call per perspective, merge pass separate
- **MiniMax for task generation** — 30B unreliable on council formats, $0.01/run negligible
- **Absolute repo paths** — `REPO_PATHS` dict, no cwd dependency
