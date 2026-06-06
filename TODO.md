# Orchestrator — Active TODOs

Near-term work only. Future features with full specs live in BACKLOG.md.
Priority: 🟡 medium / ⚪ low

---

## 🟡 Medium

**MED-2 `approve.py` stages everything with `git add -A`**
Unrelated unstaged changes get staged alongside the approved diff.
Fix: use `git apply` against the specific diff file, or show `git status` before staging.
Note: becomes simpler once FEAT-AutoCommit lands — approve.py only handles `approval_required` tasks.

**MED-3 `_detect_night_number` fragile in lang_pipeline**
Infers night by counting passed scenes — breaks if scenes are manually reordered/retried.
Fix: store night number explicitly in `lang_schedule.json`, increment intentionally.

**MED-4 `_project_running` dict not thread-safe**
Read-check-then-write not atomic under concurrent scheduler fires.
Fix: `threading.Lock` around the check-and-set in `orchestrator_main.py`.

**MED-5 No task description deduplication in council generation**
Two council runs generate different IDs for semantically identical descriptions.
Queue accumulates near-duplicates overnight.
Fix: hash description before insert; skip if hash already exists in DB.

---

## ⚪ Low

**LOW-1 `git_watcher.py` doesn't verify stale PID is alive**
Reads PID file but doesn't call `os.kill(pid, 0)` to confirm the process is running.

**LOW-4 Inline imports inside `run_task` in executor.py**
`from task_generator import ...` and `from dashboard_generator import ...` inside function body.
Fix: move to module level.

**LOW-5 Stale "Aider" references in task_generator.py docstring**
Says "Aider prompt writing only" — Aider is gone.

---

## Recently completed (move to COMPLETED.md)

SEC-1 Path traversal guard — `_safe_write()` in executor.py + lang_pipeline.py
SEC-2 Quality gate fails closed — `pass=False` on Ollama parse error
SEC-3 Atomic spend writes — `os.replace()` in spend.py
HIGH-1/2/3 Shared config via config.py — task_generator + lang_pipeline import from it
HIGH-4 Ollama health check at startup — `executor.check_ollama()`
MED-1 update_context_md uses qwen3:14b (was wrongly using 30b code model)
LOW-2/3 .gitignore + approve.py standalone safety
