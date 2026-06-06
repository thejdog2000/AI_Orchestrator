# Orchestrator — Active TODOs

Near-term work only. Future features with full specs live in BACKLOG.md.
Priority: 🟡 medium / ⚪ low

---

## 🟡 Medium

**MED-3 `_detect_night_number` fragile in lang_pipeline**
Infers night by counting passed scenes — breaks if scenes are manually reordered/retried.
Fix: store night number explicitly in `lang_schedule.json`, increment intentionally.

---

## ⚪ Low

*(none remaining)*

---

## Recently completed

SEC-1 Path traversal guard — `_safe_write()` in executor.py + lang_pipeline.py
SEC-2 Quality gate fails closed — `pass=False` on Ollama parse error
SEC-3 Atomic spend writes — `os.replace()` in spend.py
HIGH-1/2/3 Shared config via config.py — task_generator + lang_pipeline import from it
HIGH-4 Ollama health check at startup — `executor.check_ollama()`
MED-1 update_context_md uses qwen3:14b (was wrongly using 30b code model)
MED-2 approve.py staging — resolved by FEAT-AutoCommit rewrite (now commits, not just stages)
MED-4 _project_running thread-safety — threading.Lock in orchestrator_main.py
MED-5 Task description dedup — description_hash in task_queue.py add_task()
LOW-1 git_watcher.py stale PID — os.kill(pid, 0) check in _check_stale_pid()
LOW-2/3 .gitignore + approve.py standalone safety
LOW-4 Inline imports in run_task — intentional lazy imports (circular dep); documented in code
LOW-5 Stale "Aider" docstring — fixed in task_generator.py
