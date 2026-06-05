# Orchestrator — Remaining TODOs

Priority: 🔴 do before first overnight run | 🟡 do before multi-project run | ⚪ polish / low risk

---

## 🔴 Before First Overnight Run

**#18 No approval workflow — diffs accumulate in pending_review/ forever**
Diffs are written but never cleared. Count grows in digest, never goes down.
Without this, you have no way to act on overnight output from the CLI.
Build `approve.py`:
- `python approve.py <task_id>` — applies diff, marks task completed in DB, moves diff to `approved/`
- `python approve.py --list` — shows all pending with review_priority order
- `python approve.py --reject <task_id>` — marks failed, deletes diff

---

## 🟡 Before Multi-Project Run

**#14 `get_context_md` duplicated in main.py and task_generator.py**
Two implementations with slightly different path logic — will silently drift.
Consolidate into `load_context(project)` in `task_queue.py`, import from both.

**CONTEXT.md updater — closes the core feedback loop**
Currently MiniMax's output never feeds back to improve future prompts.
After each successful task, Ollama should read `diff + old CONTEXT.md + task description`
and write an updated CONTEXT.md: what was built, which files changed, known issues.
Next prompt-writing call reads the updated context → MiniMax gets better instructions.
Without this, every overnight run starts with a static project snapshot.
Lives in `executor.py` (after module split). ~40 lines. High value.

**Smarter retries — prompt revision on failure**
Current retry: fail → clean git → same prompt again. No learning.
Better: on quality gate failure, Ollama reads `[task + diff + evaluation.issues]`
and writes a revised prompt addressing the specific failure before attempt 2.
One extra Ollama call per failed attempt, meaningfully higher retry success rate.

**#17 No SQLite DB backup**
`orchestrator.db` gitignored (correct). Machine crash = all task history lost.
Add nightly job: `sqlite3 orchestrator.db .dump > backups/tasks_YYYY-MM-DD.sql`
Schedule via APScheduler alongside digests. Keep 7 days of backups.

---

## ⚪ Polish / Low Risk

**Split main.py into modules**
File is 500+ lines and growing. Large reads burn tokens in every AI session.
Target split:
- `orchestrator_main.py` — scheduler, entry point, config (~100 lines)
- `executor.py` — `run_minimax_task`, `_parse_file_blocks`, `_minimax_execute`, `_run_task`
- `spend.py` — `SpendTracker`
- `digests.py` — `generate_digest`, `_write_digest`, digest scheduler functions
Smaller files = targeted reads, cheaper future sessions.

**#19 Gap-fill task IDs — 1-second resolution collision**
`int(time.time())` in `task_queue.get_gap_fill_tasks()`.
Two calls in the same second → SQLite PRIMARY KEY conflict, silently drops insertion.
Fix: `uuid4()` for gap-fill IDs.

**lang_pipeline.py — dedicated language scene pipeline**
Scene generation is currently handled by the generic `run_minimax_task` path.
A dedicated pipeline would: follow the 7-night schedule explicitly, run Node smoke tests
automatically after each scene, retry failed scenes the next night, track scene-specific
pass/fail separately from code task history.
Lower priority since generic path works, but gives better observability for the lang project.
