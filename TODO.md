# Orchestrator — Issue Tracker

Issues found during senior AI/solutions engineering review.
Status: 🔴 open | ✅ fixed | ⏸ deferred

---

## 🔴 BREAKING — Must fix before overnight run

- [ ] **#1 Dead TaskQueue class in main.py shadows import from task_queue.py**
  Old JSON-based class (lines 140–221) overwrites `from task_queue import TaskQueue`.
  `task_queue = TaskQueue()` instantiates the wrong class. `.stats()`, `.get_completed_today()`,
  `.load_from_json()`, `.get_pending_review()` all missing — crash on first run.
  Fix: delete old class definition entirely.

- [ ] **#2 `generate_digest` references `task_queue.completed` — doesn't exist on SQLite class**
  Line 313 iterates `task_queue.completed` (old in-memory list attribute).
  SQLite class has no such attribute. Crashes every digest.
  Fix: replace with `task_queue.get_completed_today()`.

- [ ] **#3 `get_next(projects=ENABLED_PROJECTS)` — wrong kwarg on old class**
  Old class signature: `get_next(self, project: str = None)` — singular string.
  Caller passes `projects=` (list). Resolves when #1 is fixed.

- [ ] **#4 No process lock — double-execution on crash/restart**
  No PID file. Two simultaneous instances run the same task twice, double-spend,
  corrupt git state. Fatal for unattended overnight runs.
  Fix: write PID file on startup, check on start, delete on shutdown.

- [ ] **#5 Aider leaves unstaged changes between retries**
  Failed Aider/MiniMax run leaves dirty working tree. Retry compounds the bad diff.
  Escalation runs Haiku on top of MiniMax's broken output.
  Fix: `git checkout -- .` before every attempt including escalation.

- [ ] **#6 Relative paths in `get_context_md` — silent failures off expected cwd**
  `Path(f"../{project}/CONTEXT.md")` is relative to launch directory.
  All context loading silently fails if orchestrator isn't started from its own dir.
  Fix: use absolute paths anchored to BASE_DIR.parent.

---

## 🟡 Concerning — Fix for cost / optimization / risk

- [ ] **#7 Token estimation off by 10–100x — spend tracker is unreliable**
  Token count estimated from word count of prompt + diff. Aider/direct API passes
  full repo context — real cost is 10–100x higher. Hard cap tracking is fiction.
  Fix: parse actual usage from API response (`usage.prompt_tokens / completion_tokens`).

- [ ] **#8 API key exposed in subprocess args (ps aux visible)**
  MiniMax key passed as CLI arg to Aider. Visible in process listings.
  Fix: pass key via subprocess env dict, not CLI arg.
  (Moot once Aider is removed — direct API calls use env var natively.)

- [ ] **#9 BlockingScheduler + 10-min interval kills overnight throughput**
  Blocks main thread. If task takes 8 min, next 10-min fire is skipped.
  Max ~6 tasks/hour. Per-project locks + BackgroundScheduler allows parallel execution.
  Fix: BackgroundScheduler + per-project `_running` flags + 2-min interval.

- [ ] **#10 Spend caps not split by provider**
  Single $90 warning for combined $65 MiniMax + $35 Claude budget.
  Could blow MiniMax cap while tracker shows $65 total (under combined threshold).
  Fix: track MiniMax and Anthropic spend separately, check each against own cap.
  Note: Claude escalation removed — simplifies to single MiniMax cap check.

- [ ] **#11 Escalation path compounds dirty git state**
  Quality gate failure → run Claude Haiku on repo that still has MiniMax's bad diff.
  Haiku codes on top of broken output.
  Fix: removed entirely — escalation path dropped. Fail → log → next task.

- [ ] **#12 No `--files` targeting on Aider calls**
  Without explicit file targets Aider scans whole repo — expensive token burn.
  Fix: moot once Aider replaced with direct MiniMax calls (explicit file loading).

- [ ] **#13 Log file grows unbounded**
  `orchestrator.log` has no rotation. Week of 24/7 = large file, slow reads.
  Fix: `RotatingFileHandler(maxBytes=5MB, backupCount=5)`.

- [ ] **#14 `get_context_md` duplicated in main.py and task_generator.py**
  Two implementations with different path logic — will drift.
  Fix: consolidate into single `load_context(project)` in task_queue.py, import from both.

---

## ⚪ Less Concerning

- [ ] **#15 `SAMPLE_LANG_TASKS` writes a file at module import time**
  Runs on every `import orchestrator_main`, not just direct execution.
  Fix: move inside `if __name__ == "__main__":` block.

- [ ] **#16 `__import__("time")` inline in task_generator.py**
  Inside `_merge_and_extract`. Just add `import time` at top of file.

- [ ] **#17 No SQLite DB backup strategy**
  `orchestrator.db` gitignored (correct). Machine crash = task history lost.
  Fix: nightly `sqlite3 orchestrator.db .dump > backups/tasks_YYYY-MM-DD.sql`.

- [ ] **#18 No approval workflow — diffs accumulate in pending_review/ forever**
  Count grows in digest but never goes down. No way to approve/reject from CLI.
  Fix: build minimal `approve.py` script: `python approve.py <task_id>`.

- [ ] **#19 Gap-fill task IDs use `int(time.time())` — 1-second resolution**
  Two calls in the same second → SQLite PRIMARY KEY conflict, silently drops.
  Fix: use `uuid4()` or `time.time_ns()` for gap-fill IDs.

---

## Architecture Decisions (locked — don't re-litigate)

- **No Aider** — direct MiniMax API calls give full context control, token visibility, lower cost
- **No Claude escalation** — fail → log → next task. MiniMax + Ollama only.
- **qwen3-coder:30b** for Ollama coding tasks; **qwen3:14b** for digest prose (lighter)
- **Per-project locks** not global — lang + meridian can run simultaneously
- **Sequential council** not debate — one MiniMax call per perspective, merge pass separate
- **MiniMax for task generation** — 30B breaks council formats, $0.01/run is negligible
