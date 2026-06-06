# Orchestrator — Feature Backlog
> Future features with full specs. Active near-term work is in TODO.md.

Priority: 🔴 highest | 🟠 high | 🟡 medium | ⚪ low

---

## 🔴 FEAT-Discord: Discord Bot PA Interface

**The orchestrator's primary interface. Replaces polling, Python scripts, and manual approve.py runs.**

Jacob messages the bot in `#orchestrator`. The bot responds and executes. The bot also proactively posts when it needs Jacob.

**Push (orchestrator → Jacob):**
- `approval_required` task blocked — DM with task description + `approve <id>` command
- Morning digest at 8am — what committed overnight, spend, what's queued
- Spend ≥ 85% cap — warning with current/cap figures
- 3+ consecutive API errors — possible key issue or outage
- (Rate limited: max 1 push per trigger type per hour)

**Pull (Jacob → bot):**
Natural language routed through Ollama → action:
- "what happened overnight" / "morning summary" → digest
- "what's running right now" → queue status per project
- "approve everything" / "approve lang tasks" → runs approve.py logic
- "reject <task_id>" / "reject that last one" → mark failed, log reason
- "pause lang" / "stop overnight" → sets project to paused in DB
- "resume lang" / "start again" → unpauses
- "what's queued for tonight" → tonight's lang schedule + pending tasks
- "how much have we spent" → spend tracker summary
- "what did we build this week" → committed log summary
- "change meridian to polish phase" → updates config.py SPRINT_PHASES

**Implementation:**
- `orchestrator_bot.py` — discord.py bot, listens in `#orchestrator`
- Ollama (qwen3:14b) for intent parsing — lightweight, fast, free
- Intent → function map (approve, reject, status, pause, resume, etc.)
- All actions write to same DB/config as orchestrator_main.py
- `notify.py` — unified send() used by both bot pushes and orchestrator events

**Environment vars:**
```bash
export DISCORD_BOT_TOKEN="..."
export DISCORD_CHANNEL_ID="..."   # #orchestrator channel ID
export DISCORD_USER_ID="..."      # Jacob's user ID for DMs
```

**`o` CLI alias** — thin 20-line wrapper sending to the same bot backend:
```bash
o "what happened overnight"   # same as messaging Discord bot
o "approve all lang"
o status
```
Add to `~/.zshrc`: `alias o="python3 ~/projects/Orchestrator/o.py"`

---

## 🔴 FEAT-AutoCommit: Remove pending_review, Auto-Commit Normal Tasks

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

## 🟠 FEAT-Validate: Pre-Flight Checklist (`validate.py`)

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

## 🟠 FEAT-Temperature: Explicit Temperature on All Ollama Calls

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

## 🟡 FEAT-1: Persona Files

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

## 🟡 FEAT-2: Notifications (email/SMS fallback)

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

## ⚪ FEAT-3: Rejection Feedback Loop

**Lower priority per Jacob — Ollama quality gate isn't reliable enough to make this high-value yet.**

When Jacob rejects a diff via Discord or approve.py:
- Capture optional reason ("wrong approach", "too broad", etc.)
- Store in task record
- Include recent rejections in next council prompt for that project
- Track rejection rate per perspective over time

Hold until quality gate reliability improves or we upgrade to a stronger local model.

---

## ⚪ FEAT-4: Evaluation Metrics Dashboard

**Lower priority per Jacob. Useful for system optimization but not blocking anything.**

Track: quality gate pass rate, cost per project/task type, which perspectives generate accepted tasks, overnight throughput. Add to dashboard/index.html or a separate page.

Hold until system is stable and running consistently overnight.
