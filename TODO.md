# Orchestrator — Remaining TODOs

Priority: 🔴 critical/security | 🟠 high/breaks functionality | 🟡 medium/efficiency | ⚪ low/polish

---

## 🔴 Critical — Fix Before Any Run

**SEC-1 Path traversal in `_parse_file_blocks`**
MiniMax returns file paths written directly to disk with no bounds check.
`repo_path / rel_path` with no validation — model could output `../../.env`.
Fix: `dest.resolve().is_relative_to(repo_path.resolve())` before every write.

**SEC-2 Quality gate fails open on parse error**
Ollama JSON parse failure returns `{"pass": True}` — broken gate auto-approves everything.
Fix: fail closed → flag for human review on parse failure.

**SEC-3 `spend.py._save()` non-atomic write**
`write_text(json.dumps(...))` not atomic — crash mid-write corrupts spend file.
Corrupted spend file = no cap enforcement = uncapped overnight spend.
Fix: write to `.tmp`, then `os.replace()`.

---

## 🟠 High — Fix Before First Overnight Run

**HIGH-1 `task_generator.py` REPO_PATHS wrong for lang and gamma**
Bare strings: `{"lang": "language-travel-app"}` joined with `BASE_DIR.parent`
gives `~/projects/language-travel-app` — actual path is `~/Documents/claude/projects/`.
Both lang and gamma silently get "No CONTEXT.md" — council generates blind tasks.
Fix: import absolute REPO_PATHS from orchestrator_main or accept via configure().

**HIGH-2 `executor.configure()` has no guard**
`_cfg()` raises bare `KeyError` if called before `configure()`.
Any script, test, or bad import order gives a useless error message.
Fix: raise `RuntimeError("executor.configure(CFG) must be called")` if `_config` empty.

**HIGH-3 `lang_pipeline.py` and `task_generator.py` have hardcoded config**
Both define their own `MINIMAX_MODEL`, `OLLAMA_BASE`, `MINIMAX_API_BASE` independently.
Change the model in orchestrator_main.py → other modules stay wrong silently.
Fix: import from shared config or accept via configure().

**HIGH-4 No Ollama health check at startup**
If Ollama is down, `write_execution_prompt` returns `""`, fallback is raw task description.
Tasks proceed with no context refinement — degraded output all night, no warning.
Fix: verify Ollama reachable before scheduler starts; log clear error if not.

---

## 🟡 Medium — Before Multi-Project Run

**MED-1 `update_context_md` uses qwen3-coder:30b for prose summarization**
CONTEXT.md update is markdown summarization, not code generation.
qwen3:14b is faster and adequate. 30B adds unnecessary latency after every task.
Fix: use `OLLAMA_MODEL_DIGEST` in `update_context_md`.

**MED-2 `approve.py` stages everything with `git add -A`**
Unrelated unstaged changes in repo (failed task residue) get staged alongside approved diff.
Fix: use `git apply` against the specific diff file, or show `git status` and prompt confirmation.

**MED-3 `_detect_night_number` fragile in lang_pipeline**
Infers night by counting passed scenes — breaks if scenes are manually reordered/retried.
Fix: store night number explicitly in `lang_schedule.json`, increment intentionally.

**MED-4 `_project_running` dict not thread-safe**
Read-check-then-write not atomic under concurrent scheduler fires.
Fix: `threading.Lock` around the check-and-set.

**MED-5 No task description deduplication in council generation**
Two council runs generate different IDs for identical descriptions.
Queue accumulates semantic duplicates overnight.
Fix: hash description before insert, skip if hash exists.

---

## ⚪ Low — Polish

**LOW-1 `git_watcher.py` doesn't verify stale PID is alive**
Stores PID but doesn't call `os.kill(pid, 0)` to confirm it's running.

**LOW-2 `backups/` and `approved/` not in `.gitignore`**
SQL dumps and approved diffs will be tracked by git.

**LOW-3 `approved/` not created by `approve.py` standalone**
Created by orchestrator_main at startup — fails if approve.py runs first.
Fix: `APPROVED_DIR.mkdir(exist_ok=True)` at top of approve.py.

**LOW-4 Inline imports inside `run_task` in executor.py**
`from task_generator import ...` inside function body — hides dependency.
Fix: move to module level.

**LOW-5 Stale "Aider" references in task_generator.py docstring**
Says "Aider prompt writing only" — Aider is gone.
