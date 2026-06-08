# Orchestrator — Product Review
**Date:** 2026-06-06  
**Reviewers:** engineering_architect · security_engineer · newcomer · skeptic · ai_engineer  
**Scope:** Documentation, security posture, usability, systems architecture, AI pipeline design

---

## 1. Engineering Architect — Systems Architecture

### Strengths
- **Clean module boundaries.** `config.py` as single source of truth, `executor.py` owns MiniMax + Ollama, `task_queue.py` owns SQLite — no surprising cross-dependencies (with the known lazy-import exception explained in comments).
- **Failure isolation is solid.** Quality gate fails closed. Path traversal guard blocks bad output. Atomic spend writes (`os.replace`) prevent corrupted state on crash. These are exactly the right safety nets for an overnight unattended system.
- **Thread safety addressed.** `_project_lock` in `orchestrator_main.py` was a real race — now correctly guarded. `mark_committed` writes atomically to SQLite.
- **Auto-commit flow is clean.** The `approval_required` branch split in `run_task()` is clear and the three outcomes (committed, pending_review, failed) map directly to task states.

### Concerns
- **`orchestrator_main.py` is doing too much.** Entry point + scheduler + nightly run wrapper + dashboard start + notify calls + seed tasks. As the system grows, split into `scheduler.py` + `startup.py` or similar. Right now any change to nightly logic touches the entry point.
- **No task dependency enforcement at runtime.** `depends_on` is checked in `get_next()`, but nothing prevents two concurrent scheduler ticks from picking tasks whose `blocks` fields conflict. The per-project lock helps, but cross-project dependencies aren't guarded.
- **SQLite under concurrent write load.** `task_queue.py` opens a new connection per operation with `sqlite3.connect()`. Under concurrent multi-project execution, WAL mode should be explicitly enabled. Current code doesn't set `PRAGMA journal_mode=WAL`, which means writer-blocks-reader under load.
- **No process supervisor.** If `orchestrator_main.py` crashes at 2am, it stays down. A systemd unit, launchd plist, or even a shell watchdog loop would make this production-grade. The PID file pattern prevents double-starts but not restarts after crash.

### Priority fixes
1. Add `PRAGMA journal_mode=WAL` to `task_queue.py._init_db()`
2. Add launchd plist (macOS) or supervisord config to ARCHITECTURE.md
3. Move scheduler setup out of `orchestrator_main.py` into `scheduler.py`

---

## 2. Security Engineer — Security Posture

### Strengths
- **Path traversal guard is correct.** `_safe_write()` uses `dest.resolve().is_relative_to(repo_path.resolve())` — this is the right check, not a string prefix match.
- **Secrets only via env vars.** No API keys in code, config, or logs. `validate.py` checks this at startup.
- **Quality gate fails closed.** Ollama parse error → `pass=False`. This is the right default and was explicitly engineered — means a broken gate doesn't auto-approve bad output.
- **No auto-deploy to production.** The explicit `approval_required` list (JWT auth, schema migrations, client deliverables) is correctly conservative. 90% of tasks bypass review safely.

### Concerns
- **Discord bot has no authentication.** `orchestrator_bot.py` listens in `#orchestrator-chat` and accepts `approve everything` from *any user* who can see that channel. No check that the sender is Jacob's user ID. If the server has multiple members, anyone can approve tasks.
- **`approve everything` with no confirmation in `_handle_approve`.** The spec says "asks confirmation first" — the current implementation skips this and bulk-approves immediately.
- **Diff files stored on disk unencrypted in `pending_review/`.** These contain the full AI-generated code changes. Not a high risk for a personal project, but worth noting for the tax project (client data).
- **SMTP password in env var, no keychain.** `SMTP_PASS` as a plain env var is acceptable but lower on the security ladder than macOS Keychain or a secrets manager. Fine for current scale.
- **No rate limiting on the `#chat` bot.** A misbehaving message loop could spam Ollama intent parsing. Low risk but worth a simple cooldown.

### Priority fixes
1. **Add sender ID check in `orchestrator_bot.py`.** Only process messages from `DISCORD_USER_ID` env var. Three-line fix: `if str(message.author.id) != os.environ.get("DISCORD_USER_ID"): return`
2. **Add confirmation for bulk approvals.** `approve all` and `approve everything` should post "⚠️ Approving N tasks — reply `confirm` to proceed" and wait for a follow-up message.
3. Add to `validate.py`: warn if `DISCORD_USER_ID` is not set (currently optional, should be strongly recommended).

---

## 3. Newcomer — Documentation & Usability

### Strengths
- **README.md is genuinely good.** The Mermaid diagram, non-technical "how it works," and "what you do each day" framing are clear. Someone with no prior context can understand the system's purpose and daily workflow in under 5 minutes.
- **ORCHESTRATOR_CONTEXT.md is the right pattern.** A single file to load at the start of an AI session — the repos, models, budget, and interface are all in one place.
- **`validate.py` is an excellent newcomer experience.** Running one command and seeing green/red with fix instructions is far better than a 20-step setup guide that goes stale.
- **Commit messages are clean and descriptive.** Anyone reading `git log` gets a real picture of what changed and why.

### Concerns
- **No quickstart sequence in one place.** The setup steps are spread across: BACKLOG.md (Discord setup), ARCHITECTURE.md (module map), ORCHESTRATOR_CONTEXT.md (repos and models), and README.md (overview). A newcomer needs to read 4 files before they can run anything.
- **`config.py` has hardcoded home directory paths.** `HOME / "Documents/claude/projects/..."` — these are Jacob's paths. Someone else (or Jacob on a new machine) would need to find and edit every REPO_PATH. These should come from env vars or a local config file excluded from git.
- **The `agents/` reorganization is mid-flight.** Root-level `orchestrator_bot.py` and `o.py` are now stubs pointing to `agents/`. This creates confusion: which one do you run? The README still says `python orchestrator_bot.py` in some places.
- **No explanation of what `lang_schedule.json` is or where it lives.** If a newcomer sees the tasks directory or state file, there's no doc linking them back to the pipeline.

### Priority fixes
1. Add `SETUP.md` — ordered quickstart: install Ollama, pull models, set env vars, run validate.py, start orchestrator_main.py, start orchestrator_bot.py.
2. Move `REPO_PATHS` in `config.py` to env vars or a local `.env.local` file (gitignored).
3. Update `README.md` to point to `agents/orchestrator_bot.py` explicitly.

---

## 4. Skeptic — What's Actually Going to Break

### Strengths
- The quality gate failing closed is the right call and correctly implemented.
- Spend cap enforcement is double-locked: code + platform-side limit. Good.
- The `description_hash` dedup is a real improvement — council re-runs won't pile up near-duplicates anymore.

### Concerns — these will break in production
- **`ollama_generate()` timeout is 180s.** If Ollama is under load (e.g., running a 30b model on a MacBook while other apps are open), this times out. The timeout isn't retried — it just returns `""` and the caller uses the task description as a fallback. That means quality gate silently passes with a degenerate prompt. This is a risk.
- **`_auto_commit()` does `git add -A`.** If there are untracked files in the repo that Jacob put there manually (a scratch file, a notes.txt), they get committed. This was flagged as MED-2 originally and "resolved by the rewrite" — but the rewrite still uses `git add -A`.
- **`run_nightly()` in `lang_pipeline.py` has no spend cap check.** `orchestrator_main.py`'s `execute_next_task()` calls `spend_tracker.check_caps()`, but `run_nightly()` calls `_minimax_generate()` directly without checking the cap. An expensive overnight lang run could blow past the cap.
- **The bot's `_handle_pause()` patches `orchestrator_main.ENABLED_PROJECTS` in-process.** This only works if the bot is running *in the same process* as the orchestrator — but they're designed to run as separate processes. The function currently says "can't pause — orchestrator_main not running in this process" in that case, which is the expected failure mode, but the happy path comment implies it works and it doesn't.
- **`task_queue.py` uses `sqlite3.connect()` per operation with no WAL mode.** Under concurrent multi-project execution, this will cause `database is locked` errors. This hasn't surfaced yet because only `lang` is in ENABLED_PROJECTS, but it will when you expand to 3+ projects.

### Priority fixes
1. Add spend cap check at the top of `lang_pipeline.run_nightly()`.
2. Fix `_auto_commit()` to use `git add` on specific files from `files_written` instead of `git add -A`.
3. Add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` to SQLite init.
4. Remove the `_handle_pause()` in-process hack or document clearly it doesn't work as a separate process. Replace with a `PAUSE_PROJECTS.txt` file-based IPC (same pattern as `COMMIT_REQUEST.txt`).

---

## 5. AI Engineer — AI Pipeline Design

### Strengths
- **Two-model split is correct.** qwen3-coder:30b for code-adjacent tasks, qwen3:14b for prose/digests. This is well-reasoned and documented.
- **Council pipeline design (sequential, not debate) is right.** Debate format breaks past ~2 personas. Sequential independent proposals + merge is the correct pattern for reliable structured output.
- **Temperature is now explicit and differentiated.** 0.1 for evaluation, 0.3 for prompt writing, 0.5 for digest prose — this is the right gradient and was the right call to add.
- **`description_hash` dedup prevents the classic LLM feedback problem** where the same task gets re-generated every council run.
- **Persona files are a real quality improvement.** Injecting the full persona system prompt (identity, focus, questions) vs. a bare role string will meaningfully improve task specificity.

### Concerns
- **No output quality tracking over time.** We know each task's quality gate pass/fail, but we have no view into whether quality is trending up or down across 100 tasks. If the council starts generating worse tasks (due to CONTEXT.md drift, model updates, or phase mismatch), we have no signal.
- **CONTEXT.md has no freshness check.** `update_context_md()` updates on every committed task, which is good. But if the update fails (Ollama returns nothing), the CONTEXT.md silently stays stale. There's a warning log but no metric, no Discord notification, and no flag in the task record.
- **Prompt storage is ad-hoc.** System prompts generated by `write_execution_prompt()` are returned in the result dict but only stored in the diff file (if at all). There's no way to audit "why did it write this code?" for a committed task two weeks later.
- **The quality gate skips high-complexity tasks.** `complexity=high` goes straight to `approval_required=True` — this is pragmatic but means the model's worst outputs get the least automated scrutiny.
- **Council temperature is 0.75 for proposals, 0.3 for merge.** The 0.75 is reasonable for creative task generation, but this value isn't documented or easily adjustable. As the project matures, this should be in config.py.

### Priority fixes
1. Store `system_prompt` in the task record at execution time (add `system_prompt TEXT` column to SQLite schema). Enables post-hoc "why did it do this?" queries.
2. Add a CONTEXT.md staleness flag: if `update_context_md()` fails, set `context_stale=True` on the project and post to `#live`.
3. Add `COUNCIL_TEMPERATURE` to `config.py` (default 0.75) — referenced from `task_generator.py`.
4. This is FEAT-4 territory: track quality gate pass rates per perspective and per project over time.

---

## Summary Table

| Area | Status | Top Fix |
|---|---|---|
| Systems Architecture | ✅ Solid | WAL mode for SQLite, process supervisor |
| Security | ⚠️ One real gap | Auth check on Discord bot (sender ID) |
| Documentation / Usability | ✅ Good, gaps exist | SETUP.md, REPO_PATHS from env vars |
| What Will Break | ⚠️ Several real risks | Spend cap in lang_pipeline, git add -A scope |
| AI Pipeline | ✅ Well-designed | Prompt storage, CONTEXT.md staleness signal |

---

## Action Items (ordered by risk)

**Do before next overnight run:**
1. Add Discord bot sender ID check (`if message.author.id != DISCORD_USER_ID: return`)
2. Add `spend_tracker.check_caps()` at top of `lang_pipeline.run_nightly()`
3. Add `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` to `task_queue._init_db()`

**Do this sprint:**
4. Scope + implement FEAT-4 metrics tracking (pass rate, cost/project, throughput)
5. Add `system_prompt TEXT` column to task schema
6. Write `SETUP.md` quickstart
7. Fix `_auto_commit()` to stage only `files_written` files, not `git add -A`

**Backlog:**
8. Move REPO_PATHS to env vars / `.env.local`
9. Add CONTEXT.md staleness Discord notification
10. Add launchd plist for auto-start and crash recovery
11. Add `COUNCIL_TEMPERATURE` to config.py
12. Confirmation step for bulk Discord approvals
