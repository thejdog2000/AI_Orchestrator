# Orchestrator — Feature Backlog
> Future features with full specs. Active near-term work is in TODO.md.

Priority: 🔴 highest | 🟠 high | 🟡 medium | ⚪ low

---

## ✅ FEAT-Discord: Discord Bot — Three-Channel PA Interface *(implemented 2026-06-06)*

**Implemented.** Files: `notify.py`, `orchestrator_bot.py`, `dashboard_server.py`, `o.py`.
See ARCHITECTURE.md Module Map for setup and usage details.

---

**The orchestrator's primary interface. Replaces polling, Python scripts, and manual approve.py runs.**
**Three dedicated channels, three distinct jobs.**

---

### Channel 1: `#orchestrator-live`
**Real-time task feed. Jacob can see exactly what the orchestrator is doing at any moment.**

Every task event posts a short message automatically:

```
⚙️  [lang] Starting: generate izakaya A0 scene  (speech_linguist · medium)

✅  [lang] Committed: scenes/ja/izakaya_01.js
    482 tokens · $0.0006 · monthly: $1.24 / $65
    commit: a3f91bc

❌  [lang] Failed after 3 attempts: generate izakaya A0 scene
    error: api_http_429 — rate limit
    next: retrying in 15 min

⏸  [meridian] Paused — approval required
    → see #orchestrator-blocked
```

Events posted to `#live`:
- Task started (with project, description truncated, perspective, complexity)
- Task committed (with file count, tokens, cost, commit hash)
- Task failed (with error, retry plan)
- Quality gate failed (routed to blocked)
- Spend milestone reached (50%, 75%, 85%, 100% of cap)
- Overnight run started / completed (summary: N tasks, $X.XX)

Rate: one message per event, no batching. This channel is the live view.

---

### Channel 2: `#orchestrator-blocked`
**Actionable items only. Nothing posts here unless Jacob needs to do something.**

Posts a rich embed when a task is blocked on approval or fails repeatedly:

```
🔴 APPROVAL REQUIRED
━━━━━━━━━━━━━━━━━━━━
Project:      meridian
Task:         Implement JWT auth endpoint for React Native login
Perspective:  security_engineer
Complexity:   high  ·  review_priority: 5/5
Blocked since: 2 hours ago

Why blocked: approval_required=True — JWT auth touches auth flow
Action needed: approve or reject

✅ approve meridian_jwt_001   ❌ reject meridian_jwt_001

🔗 Dashboard: http://localhost:8080
```

Each blocked embed includes:
- Color-coded by urgency (red = approval required, orange = repeated failure)
- Full task description
- Project + perspective + complexity + review_priority
- How long it's been blocked
- Exact approve/reject command to paste into `#chat`
- Link to local Kanban dashboard (works when at home; embed has full context for mobile)

Posts to `#blocked` when:
- `approval_required=True` task reaches queue front
- Task fails quality gate after all retries
- Project has been idle/blocked for >2 hours during overnight run

**Dashboard link:** Bot serves `dashboard/index.html` via local HTTP on port 8080.
Link works on local network. Discord embed contains full context so it's readable on mobile without clicking.

Optional enhancement (see BACKLOG): auto-publish dashboard to GitHub Pages for remote access.

---

### Channel 3: `#orchestrator-chat`
**Natural language interface. Message the bot to direct, query, or get summaries.**

Bot listens exclusively in this channel. Ollama (qwen3:14b) parses intent → action.

Supported natural language commands:
```
"what happened overnight"          → morning digest summary
"what's running right now"         → live queue status per project
"what did we build this week"      → committed log summary by project
"how much have we spent"           → spend tracker with daily/monthly breakdown
"what's queued for tonight"        → lang schedule + pending task count per project
"show me blocked items"            → list everything in approval queue

"approve meridian_jwt_001"         → approve specific task
"approve all lang tasks"           → bulk approve all lang pending
"reject meridian_jwt_001"          → reject, mark failed, remove from blocked
"approve everything"               → bulk approve all pending (asks confirmation first)

"pause lang"                       → stop lang from running overnight
"resume lang"                      → unpause
"pause everything"                 → halt all projects (spend concern, debugging)

"change meridian to polish phase"  → updates SPRINT_PHASES in config.py
"what phase is rts in"             → reads current sprint phase + goal
"prioritize lang tonight"          → moves lang tasks to priority 0

"what decisions did the council make this week"  → summary from task rationale field
"why did it build X"               → reads task rationale + perspective from DB
```

Bot also accepts direct task IDs without natural language:
```
approve meridian_jwt_001
reject lang_003
status lang
```

---

### Implementation Plan *(completed — see below for first-run setup)*

**First-run setup:**
```bash
# 1. Install discord.py
pip install discord.py

# 2. Set env vars (add to ~/.zshrc or equivalent)
export DISCORD_BOT_TOKEN="..."          # Bot → Token in Discord Developer Portal
export DISCORD_CHANNEL_LIVE="..."       # Right-click #orchestrator-live → Copy Channel ID
export DISCORD_CHANNEL_BLOCKED="..."    # Right-click #orchestrator-blocked → Copy Channel ID
export DISCORD_CHANNEL_CHAT="..."       # Right-click #orchestrator-chat → Copy Channel ID
export DISCORD_USER_ID="..."            # Your user ID (Settings → Advanced → Dev Mode → copy)
export DASHBOARD_PORT=8080

# 3. Start the bot (separate terminal from orchestrator_main.py)
python orchestrator_bot.py

# 4. CLI alias (add to ~/.zshrc)
alias o="python3 ~/projects/Orchestrator/o.py"
```

**Files:**
- `orchestrator_bot.py` — discord.py bot process, separate from orchestrator_main.py
- `notify.py` — unified `post(channel, message, embed=None)` called by both bot and executor
- `dashboard_server.py` — simple `http.server` wrapper, serves `dashboard/` on port 8080

**Integration points:**
- `executor.py` → calls `notify.post("live", ...)` on task start/commit/fail
- `orchestrator_main.py` → calls `notify.post("live", ...)` on overnight start/end, spend warnings
- `orchestrator_bot.py` → calls `notify.post("blocked", embed)` on approval_required events
- Intent parsing: Ollama qwen3:14b with structured intent JSON (`{"action": "approve", "target": "all lang"}`)

**`o` CLI alias** — same backend as `#chat`, for terminal use:
```bash
o "what happened overnight"
o "approve all lang"
o status
```
Add to `~/.zshrc`: `alias o="python3 ~/projects/Orchestrator/agents/o.py"`

---

### GitHub Pages Enhancement (optional, after bot is working)
Auto-publish `dashboard/index.html` to `gh-pages` branch on each digest update.
Dashboard accessible from phone anywhere: `https://thejdog2000.github.io/AI_Orchestrator/`
Requires: `pip install ghp-import` + GitHub Pages enabled on repo settings.

---

## ✅ FEAT-AutoCommit: Remove pending_review, Auto-Commit Normal Tasks *(implemented 2026-06-06)*

**Removes the biggest daily friction point. 90-95% of tasks commit without Jacob's involvement.**

**New execution flow (replaces current pending_review accumulation):**
1. Task completes, path guard passes
2. `git add -A && git commit -m "[orchestrator] {project}: {task_description[:60]}"` 
3. Commit hash logged to SQLite task record
4. CONTEXT.md updated
5. Discord notification: "✓ committed: {task_description[:60]} ({project})"
6. For `approval_required=True`: skip steps 2-5, post Discord DM instead

**Changes required:**
- `executor.py` → `run_task()`: replace `mark_pending_review()` with auto-commit
- Add `committed_at` and `commit_hash` fields to task schema
- `approve.py` simplified: only lists/handles `approval_required` queue
- `pending_review/` directory: keep but only for `approval_required` tasks
- `git_watcher.py`: stays for Cowork session commits; not needed for orchestrator tasks
- Discord bot: post commit summary instead of "pending review" notifications
- `approval_required: True` list gets tight review — should be ≤5 task types total

**Commit message format:**
```
[orchestrator] lang: generate izakaya A0 scene (speech_linguist)
[orchestrator] meridian: add TypeScript types for feed API
```

---

## ✅ FEAT-Validate: Pre-Flight Checklist (`validate.py`) *(implemented 2026-06-06)*

**One command before first overnight run. Loud failures beat silent 2am debugging.**

```bash
python validate.py
```

Checks and reports pass/fail for each:
- Ollama reachable at localhost:11434
- qwen3-coder:30b loaded
- qwen3:14b loaded
- MINIMAX_API_KEY set (does NOT make an API call — just checks env var)
- DISCORD_BOT_TOKEN set
- All REPO_PATHS in config.py exist on disk
- Each project repo has a git remote configured
- orchestrator.db writable
- git_watcher.py running (check for .git_watcher.pid)
- ENABLED_PROJECTS has at least one entry
- Spend cap set reminder (can't verify programmatically — just prompts)

Output: green checkmarks or red X with fix instructions. Zero ambiguity.

---

## ✅ FEAT-Temperature: Explicit Temperature on All Ollama Calls *(implemented 2026-06-06)*

**Small change, measurable quality improvement. Ollama's default temperature varies by model.**

```python
# evaluation — deterministic, not creative
ollama_generate(prompt, max_tokens=300, json_mode=True, temperature=0.1)

# prompt writing — focused but slight variation acceptable
ollama_generate(prompt, max_tokens=600, temperature=0.3)

# digest prose — readable variety acceptable
ollama_generate(prompt, max_tokens=600, model=OLLAMA_MODEL_DIGEST, temperature=0.5)

# CONTEXT.md update — factual summarization
ollama_generate(prompt, max_tokens=1500, model=OLLAMA_MODEL_DIGEST, temperature=0.2)
```

Requires: add `temperature` param to `ollama_generate()` in `executor.py`, update all call sites.

---

## ✅ FEAT-1: Persona Files *(implemented 2026-06-06)*

**Full definitions for all council perspectives. Replaces bare role strings with rich context.**

Build `personas/` directory:
```
personas/domain/    → injected into task_generator.py council calls
personas/review/    → used for documentation + architecture review prompts
```

Each file: identity, primary concern, what they look for (project-specific), what they don't care about, 3-5 questions they always ask, when to invoke. Keep under 300 words. Second person ("You are...").

Domain experts (15): speech_linguist, pedagogy_expert, game_designer, game_feel_engineer, engineering_architect, security_engineer, qa_tester, product_manager, mobile_ux_designer, systems_architect, quant_analyst, risk_manager, devops, it_administrator, client_success

Reader personas (4): newcomer, skeptic, first_time_runner, contributor

Wire up: `task_generator.py` loads persona file content into each perspective call. Quality jump is large — specificity beats vague role strings.

---

## ✅ FEAT-2: Notifications (email/SMS fallback) *(implemented 2026-06-06)*

**Fallback channels when Discord bot isn't available. Lower priority since Discord handles the primary case.**

Channels (in priority order after Discord):
1. ntfy.sh push — free, zero setup, good mobile coverage
2. Email via SMTP — good for digest-length summaries
3. macOS notification via osascript — local only, zero setup, daytime fallback

Build `notify.py` with `send(subject, body, channel="all")` interface.
Used by Discord bot for push messages and as standalone fallback.
All channels optional — graceful no-op if env vars not set.

```bash
export NTFY_TOPIC="jacobs-orchestrator-abc123"
export SMTP_USER="..." SMTP_PASS="..." NOTIFY_EMAIL="..."
```

---

## 🟡 FEAT-3: Rejection Feedback Loop

**Prerequisite: FEAT-4 metrics tracking must be in place first (need rejection rate data).**

When Jacob rejects a task via Discord (`reject task_id`) or `approve.py --reject`:

**Data capture:**
- Prompt for optional reason via Discord: "Why rejected? (wrong approach / too broad / bad quality / off-phase / other)"
- Store `rejection_reason TEXT` and `rejected_by TEXT` in task record
- Add `rejection_count` per perspective to metrics

**Feedback injection (council prompt augmentation):**
- Before each council run for a project, load the last 5 rejections for that project
- Inject into the perspective system prompt: "Recent rejections for this project: ..."
- Format: "Rejected: {description} — Reason: {reason}"

**Tracking:**
- `metrics.py` (FEAT-4) tracks rejection rate per perspective per project
- Perspectives with >30% rejection rate get deprioritized in `_select_perspectives()`

**Files:**
- `task_queue.py`: add `rejection_reason TEXT` and `rejected_at TEXT` columns; `mark_rejected(task, reason)` method
- `agents/orchestrator_bot.py`: after `reject` command, ask for reason and call `mark_rejected`
- `approve.py`: `--reject` prompts for reason, stores it
- `task_generator.py`: load recent rejections, inject into `_call_perspective()` system prompt

**Hold condition:** implement after FEAT-4 gives us the rejection rate signal to validate the feedback is working.

---

## ✅ FEAT-4: Evaluation Metrics Dashboard + Discord #metrics Channel *(implemented 2026-06-06)*

**Implement now. Unblocks FEAT-3. Required for system observability as projects expand.**

### What to track (per task, stored in SQLite)

Already in schema: `quality_score`, `cost_usd`, `actual_tokens`, `model_used`, `complexity`, `perspective`, `status`, `committed_at`

New columns needed: `system_prompt TEXT`, `rejection_reason TEXT`, `quality_gate_skipped BOOL`

### Metrics to compute

| Metric | How |
|---|---|
| Quality gate pass rate | `pass / total` per project, per perspective, per complexity |
| Cost per project/day/week | Sum `cost_usd` grouped by project + date |
| Throughput | Committed tasks per night, per project |
| Perspective acceptance | Tasks committed / tasks attempted per perspective |
| Avg cost per task type | `cost_usd` grouped by `effort_category` |
| Queue health | Queued / running / failed counts per project |

### Discord #orchestrator-metrics channel

New channel. Bot posts a metrics snapshot every 10 hours (configurable).

```
📊 Metrics Snapshot — 2026-06-06 08:00
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quality gate:  87% pass (last 50 tasks)
Throughput:    14 tasks committed last night
Monthly spend: $3.21 / $65 (4.9%)

By project:
  lang     12 committed · $2.80 · 91% pass rate
  meridian  2 committed · $0.41 · 80% pass rate

Top perspectives this week:
  speech_linguist    8 tasks · 100% accepted
  engineering_arch   6 tasks ·  83% accepted
  qa_tester          3 tasks ·  67% accepted

Failed last night: 0
Pending review:    0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Dashboard: http://localhost:8080
```

### Files

- `metrics.py` — compute all metrics from SQLite; `MetricsTracker` class
- Update `task_queue.py` — add `system_prompt`, `rejection_reason`, `quality_gate_skipped` columns
- Update `dashboard_generator.py` — add a Metrics tab to the Kanban dashboard
- Update `orchestrator_main.py` — schedule metrics post every 10 hours
- Update `notify.py` — add `metrics_snapshot(data)` formatter
- Update `config.py` — add `DISCORD_CHANNEL_METRICS` env var

### Dashboard tab

Add a second tab to `dashboard/index.html`:
- Quality gate pass rate chart (last 30 days)
- Cost per project bar chart
- Throughput per night line chart
- Perspective leaderboard (acceptance rate + task count)

### env vars

```bash
export DISCORD_CHANNEL_METRICS="..."   # new channel ID
```

Hold until system is stable and running consistently overnight.
