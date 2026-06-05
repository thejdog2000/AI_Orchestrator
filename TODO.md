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

## 🟡 Medium — Planned Features

**FEAT-1 Persona files for every council member and reader persona**
Currently council perspectives are bare role strings ("speech_linguist", "game_designer").
Fleshing out full definitions dramatically improves task quality — specificity beats vagueness.

Build `personas/` directory with two subdirectories:

`personas/domain/` — used in task generation council (task_generator.py)
  Each file: identity + background, primary concern, what they look for (project-specific),
  what they explicitly don't care about, questions they always ask, which projects/phases to invoke.
  Files to build:
  - speech_linguist.md        (lang: Japanese/Spanish A0-A1 dialogue naturalness)
  - pedagogy_expert.md        (lang: SRS design, vocabulary sequencing, learner psychology)
  - game_designer.md          (rts: Stronghold/AoE inspiration, feel loops, player agency)
  - game_feel_engineer.md     (rts: juice, feedback, moment-to-moment satisfaction)
  - engineering_architect.md  (all: system design, scalability, tech debt tradeoffs)
  - security_engineer.md      (meridian, tax: trust boundaries, auth, data exposure)
  - qa_tester.md              (all: edge cases, failure modes, regression risk)
  - product_manager.md        (meridian, lang, tax: user value, scope, pitch-readiness)
  - mobile_ux_designer.md     (meridian: React Native/Expo patterns, gesture UX)
  - systems_architect.md      (rts, gamma, ninja: concurrency, state machines, performance)
  - quant_analyst.md          (gamma, ninja: signal quality, drawdown, statistical validity)
  - risk_manager.md           (gamma, ninja: PDT rules, position sizing, tail risk)
  - devops.md                 (meridian, tax: CI/CD, EAS build, Azure deployment)
  - it_administrator.md       (tax: Entra ID, Intune, AVD, QuickBooks/TaxDome integration)
  - client_success.md         (tax: deliverable clarity, client communication, handoff docs)

`personas/review/` — used for documentation, architecture decisions, PR review
  Each file: who this reader is, what they need to get from the doc, what makes them bounce,
  what question they come with, what they'll do next if the doc answers it.
  Files to build:
  - newcomer.md               (non-technical, first contact — needs value before mechanism)
  - skeptic.md                (technical evaluator — needs proof, not claims)
  - first_time_runner.md      (already convinced, wants to try it NOW — needs steps not vision)
  - contributor.md            (wants to extend or fix something — needs structure navigation)

AI engineering best practices for each file:
  - Keep under 300 words — anything longer gets truncated or ignored by the model
  - "What you don't care about" section is as important as "what you look for"
  - Include 3-5 specific questions the persona always asks — forces concrete output
  - Add a "when to invoke" line so task_generator.py can load selectively
  - Write in second person ("You are...") for direct prompt injection

Wire up: update task_generator.py to load persona file content into each
perspective call instead of relying on the bare role name string.

---

**FEAT-2 Proactive approval notifications — push alerts when blocked**
Currently: Jacob has to remember to check approve.py / digest / logs.
Should be: orchestrator pushes a concise alert the moment it needs Jacob.

This is a pull → push shift. The system knows when it's blocked. Jacob shouldn't have to poll.

**Trigger conditions (each sends a notification):**
1. `approval_required=True` task reaches queue front — literally cannot proceed without Jacob
2. Pending review count crosses threshold (configurable, default 5) — don't let diffs pile up
3. Same task fails quality gate 2+ times — may need human judgment to unblock
4. Spend reaches 85% of cap — Jacob should know before it halts
5. MiniMax API errors on 3+ consecutive tasks — likely key issue or outage
6. Morning digest generated (daily summary push — optional, low priority)

**Notification content — concise, actionable:**
  - What is blocked (task ID, project, one-line description)
  - Why it's blocked (approval_required / quality_failed / spend_warning)
  - Exact command to run: `python approve.py <task_id>`
  - Current spend: $X.XX / $65 cap
  - How long it's been waiting

**Channels to implement (in priority order):**
1. Email via SMTP — most universal, good for digest-length summaries
   Config: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL in config.py
2. SMS via Twilio — most immediate, ideal for approval_required blocks
   Config: TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, NOTIFY_PHONE in config.py
3. ntfy.sh push — free, no account needed, good for mobile
   Config: NTFY_TOPIC in config.py (e.g. "jacobs-orchestrator-abc123")
4. macOS notification via osascript — local/daytime only, zero setup
   Fallback when no external channel configured

**Implementation:**
  - New `notify.py` module — `send(subject, body, channel="all")` interface
  - Called from executor.py (task blocked), orchestrator_main.py (spend warning, startup)
  - Never blocks task execution — fire-and-forget with timeout
  - Rate limiting: max 1 notification per trigger type per hour (no spam)
  - All channels optional — graceful no-op if not configured

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
