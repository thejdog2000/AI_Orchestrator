# Orchestrator — AI Session Context
> Paste this file at the start of any Cowork/Claude session. Single source of truth.
> For deep architecture detail: `ARCHITECTURE.md`. For project-specific goals: `PROJECTS.md`.

---

## What This Is

Autonomous multi-project AI coding system. Runs overnight: generates its own backlog (council), executes tasks (MiniMax), evaluates quality (Ollama), auto-commits, and surfaces only what needs human attention via Discord. Jacob reviews ~10 min/morning.

**System is fully operational.** All core features implemented. No open TODOs.

---

## Jacob

- Software engineer, Microsoft Azure L60, Atlanta GA
- Role: world interfacer / creative director / final authority. AI = builder.
- Goal: run 6 active projects ~24/7 with ~10 min/day oversight
- Preferences: concise responses, no preamble, no bullet padding

---

## Projects

| Key | Repo | Phase | Goal |
|---|---|---|---|
| `lang` | `~/Documents/claude/projects/language-travel-app/` | feature | Japanese A0 scenes (izakaya, konbini) + smoke tests |
| `meridian` | `~/projects/meridian-mobile/` | demo_prep | 5 screens end-to-end for men's fashion publication demo |
| `rts` | `~/projects/ironhold-rts/` | architecture | Playable vertical slice: buildings, units, AI, FPS possession |
| `gamma` | `~/Documents/claude/projects/gamma-tool/` | maintenance | Overnight backtest loop, morning equity digest |
| `ninja` | `~/projects/ninjatrader-algos/` | maintenance | Saturday parameter sweep, Sunday digest |
| `tax` | `~/projects/tax-cloud-tools/` | feature | Azure AVD + PowerShell scripts for tax practice demo |

`ENABLED_PROJECTS = ["lang"]` in `config.py`. Expand after validating pipeline per project.

---

## Models

| Model | Used For | Cost |
|---|---|---|
| `qwen3-coder:30b` | Execution prompt writing, quality gate | Free (Ollama local) |
| `qwen3:14b` | Digest prose, CONTEXT.md updates, bot intent parsing | Free (Ollama local) |
| MiniMax M3 (`minimax-m3`) | All code generation + council task generation | ~$65/mo cap |

**Ollama critical:** always `num_ctx=8192`. Default 2048 silently truncates → quality gate failures.

**Temperatures (always explicit):**
- `0.1` — quality gate (deterministic)
- `0.2` — CONTEXT.md updates (factual)
- `0.3` — execution prompt writing (focused)
- `0.5` — digest prose
- `0.75` — council task generation (`COUNCIL_TEMPERATURE` env var)

---

## Module Map

```
config.py              Single source of truth. All modules import from here. Never hardcode.
                         CFG dict passed to executor.configure(), digests.configure().

orchestrator_main.py   Entry point: APScheduler (every 2min), PID lock, Ollama health check.
                         Auto-starts dashboard_server. Notifies Discord on start/stop.
                         Schedules: digests (8am/2pm/8pm), lang nightly (10pm),
                         metrics snapshot (every 10h), DB backup (3am).

executor.py            Core execution: Ollama prompt writing, MiniMax API, path guard,
                         quality gate, _auto_commit() (git add -A), CONTEXT.md feedback loop,
                         retry with revised prompts. Lazy imports task_generator +
                         dashboard_generator (circular dep).

task_queue.py          SQLite TaskQueue. WAL mode + busy_timeout=5000.
                         Key schema fields: description_hash (dedup), system_prompt,
                         quality_gate_skipped, rejection_reason, commit_hash, committed_at,
                         sprint_id, carryover. metrics_data(days) for FEAT-4.

task_generator.py      Council pipeline: _select_perspectives() → _call_perspective()
                         (3 MiniMax calls, one per persona file) → _merge_and_extract()
                         (json_mode=True, temp=0.3). COUNCIL_TEMPERATURE from env.

spend.py               SpendTracker with atomic writes (os.replace). check_caps() before
                         every task. Tracks daily/monthly by project.

digests.py             Morning/afternoon/evening digest via qwen3:14b.

lang_pipeline.py       7-night scene schedule. Night number explicit in lang_schedule.json
                         (not inferred). Spend cap checked at run start. Node smoke tests
                         after each scene.

dashboard_generator.py Static Kanban HTML (dashboard/index.html). Swimlane layout
                         (one row per project), two tabs: Kanban + Metrics (FEAT-4).
                         Carryover tasks show orange badge. Click card → task detail modal.

dashboard_server.py    http.server :8080. Auto-started by orchestrator_main. Regenerates
                         dashboard on GET /.

metrics.py             MetricsTracker: quality gate pass rate, cost/project, throughput,
                         perspective acceptance, queue health, recent failures.
                         Posts to #orchestrator-metrics every METRICS_INTERVAL_HOURS.
                         CLI: python metrics.py [--discord] [--days N]

sprint_manager.py      24-hour sprint lifecycle. end: condenses completed → sprint_NNN.md,
                         marks unfinished carryover=1, advances sprint_id.
                         State: sprint_state.json
                         CLI: python sprint_manager.py [status|start|end|review]

notify.py              Unified Discord REST poster. Works without bot process (direct REST).
                         post(channel, msg, embed). Channels: live/blocked/chat/metrics.
                         Fallbacks: ntfy.sh → SMTP → macOS osascript.
                         critical_alert() sends to ALL channels simultaneously.

validate.py            Pre-flight: Ollama, models, env vars, repos on disk, git remotes,
                         DB writable, git_watcher running, Discord token valid.
                         CLI: python validate.py [--fix]

approve.py             CLI for approval_required tasks. Commits (not just stages).
                         Sends Discord notification on approve/reject.

git_watcher.py         Runs on Mac (not in sandbox). Polls COMMIT_REQUEST.txt every 10s,
                         clears git lock files, commits + pushes. IPC via COMMIT_REQUEST.txt.

agents/
  orchestrator_bot.py  discord.py bot. #orchestrator-chat only. Sender auth via
                         DISCORD_USER_ID. Intent: Ollama qwen3:14b → JSON → dispatch.
                         Bulk approve requires 'confirm' follow-up within 60s.
                         Run: python agents/orchestrator_bot.py

  o.py                 CLI alias. Same intent parsing as #chat.
                         alias o="python3 ~/projects/Orchestrator/agents/o.py"

  personas/domain/     16 expert persona .md files injected as system prompts in council:
                         speech_linguist, pedagogy_expert, engineering_architect,
                         security_engineer, qa_tester, product_manager, mobile_ux_designer,
                         game_designer, game_feel_engineer, systems_architect, quant_analyst,
                         risk_manager, devops, it_administrator, client_success, ai_engineer

  personas/review/     4 reviewer personas: newcomer, skeptic, first_time_runner, contributor
```

---

## Execution Flow

```
1.  Scheduler fires (every 2 min)
2.  spend_tracker.check_caps() — halt if at $65 cap
3.  _project_lock (threading.Lock) — atomic check-and-set
4.  get_next() — respects depends_on, skips approval_required
    (if queue empty → get_gap_fill_tasks())
5.  notify.task_started() → #live
6.  git checkout -- . (clean working tree)
7.  Ollama writes execution prompt (task + CONTEXT.md, temp=0.3)
8.  MiniMax generates files in <<<FILE: path>>> ... <<<END>>> blocks (temp=0.2)
9.  _safe_write(): dest.resolve().is_relative_to(repo_path) — path traversal guard
10. Write files
11. Quality gate: Ollama evaluates diff (temp=0.1, json_mode=True)
      → fails CLOSED on parse error (pass=False, never pass=True)
      → high complexity: skip gate, quality_gate_skipped=1, goes to approval_required
12. Record spend. Notify milestones (50/75/85/100%)
13. update_context_md() via qwen3:14b (temp=0.2)
      → on failure: write CONTEXT_STALE sentinel file
14a. approval_required=False → _auto_commit() [git add -A + git commit]
       → mark_committed() → notify.task_committed() with commit hash + cost
14b. approval_required=True  → mark_pending_review()
       → notify.task_pending_review() → #live + blocked_embed() → #blocked
15. If total_unblocked() < QUEUE_REFILL_THRESHOLD (10):
       generate_tasks_all_projects() → council pipeline
       generate_dashboard()
16. _project_lock released
```

---

## Discord Channels

| Channel | Env Var | Purpose |
|---|---|---|
| `#orchestrator-live` | `DISCORD_CHANNEL_LIVE` | Every task event: start, commit, fail, quality gate, spend milestones |
| `#orchestrator-blocked` | `DISCORD_CHANNEL_BLOCKED` | Actionable only: approval_required, quality gate fails, repeated failures |
| `#orchestrator-chat` | `DISCORD_CHANNEL_CHAT` | NL commands: approve/reject/status/pause/digest |
| `#orchestrator-metrics` | `DISCORD_CHANNEL_METRICS` | Metrics snapshot every METRICS_INTERVAL_HOURS (default 10) |

Bot security: silently ignores messages from anyone other than `DISCORD_USER_ID`.

---

## Council Pipeline

Sequential, not debate (debate degrades past ~2 personas on 30B models):
1. `_select_perspectives(project, sprint_phase, max_count=3)` — phase-weighted priority order
2. One MiniMax call per perspective (persona .md as system prompt) → 3 proposals in structured text
3. One merge call (json_mode=True, temp=0.3) → deduplicated JSON task array (target 8 tasks)
4. `add_task()` checks `description_hash` (sha256[:16] of `project:normalized_description`)

Phase → perspective priority:
- `architecture` → engineering_architect, systems_architect, devops, security_engineer
- `feature` → product_manager, mobile_ux_designer, game_designer, speech_linguist, engineering_architect
- `polish` → qa_tester, game_feel_engineer, speech_linguist, pedagogy_expert, product_manager
- `demo_prep` → product_manager, game_designer, mobile_ux_designer, client_success
- `maintenance` → qa_tester, devops, security_engineer, engineering_architect

---

## Task Schema (key fields)

```python
{
  "id":                  "project_NNN",
  "project":             "lang|meridian|rts|gamma|ninja|tax",
  "description":         "actionable sentence (exact files/functions/commands)",
  "status":              "queued|running|pending_review|completed|failed",
  "priority":            0,          # 0=P0/critical-path, 1=standard, 2=nice-to-have
  "approval_required":   False,      # True: auth/schema/client/major arch
  "complexity":          "low|medium|high",
  "effort_category":     "feature|scaffold|test|docs|bugfix|gap-fill|refactor",
  "perspective":         "speech_linguist|engineering_architect|...",
  "review_priority":     3,          # 1-5: complexity + approval_required
  "rationale":           "why this task exists",
  "estimated_tokens":    8000,       # scaffold:15k, feature:10k, test:6k, docs:4k, bugfix:8k
  "commit_hash":         "a3f91bc",
  "cost_usd":            0.0042,
  "sprint_id":           1,
  "carryover":           0,
  "description_hash":    "sha256[:16]",
  "system_prompt":       "...",      # stored for auditability
  "quality_gate_skipped": 0,
  "rejection_reason":    "",
}
```

---

## Sprint System

24-hour windows. State in `sprint_state.json`. `sprint_id` and `carryover` columns added to DB via `_ensure_sprint_columns()` (schema migration, safe to re-run).

On `sprint end`: completed → `sprint_reviews/sprint_NNN.md` (one line per task). Unfinished → carryover=1, advance sprint_id.

```bash
python sprint_manager.py status   # current sprint + time remaining
python sprint_manager.py start    # begin sprint (pass number optionally)
python sprint_manager.py end      # close sprint, generate review, advance
python sprint_manager.py review   # print last review
```

---

## Daily Interface (Jacob)

**Morning (~10 min):**
- `#live` — scan overnight activity
- `#blocked` — approve/reject pending items
- `#metrics` — quality + spend snapshot
- `python sprint_manager.py end` if 24h passed

**NL commands in `#chat` or `o` CLI:**
```
o status                    → queue stats per project
o "what happened overnight" → digest summary
o "approve all lang"        → bulk approve (needs 'confirm' follow-up)
o "approve lang_042"        → single task
o "reject lang_042"         → reject + prompts for reason
o "pause lang"              → stop lang from running tonight
o "how much have we spent"  → spend breakdown
```

---

## Environment Variables

```bash
# Required
export MINIMAX_API_KEY="..."
export DISCORD_BOT_TOKEN="..."
export DISCORD_CHANNEL_LIVE="..."
export DISCORD_CHANNEL_BLOCKED="..."
export DISCORD_CHANNEL_CHAT="..."
export DISCORD_CHANNEL_METRICS="..."
export DISCORD_USER_ID="..."

# Optional tuning
export DASHBOARD_PORT="8080"
export METRICS_INTERVAL_HOURS="10"
export COUNCIL_TEMPERATURE="0.75"

# Fallback notifications (all optional)
export NTFY_TOPIC="jacobs-orchestrator-abc123"
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="..."
export SMTP_PASS="..."
export NOTIFY_EMAIL="..."
```

---

## Settled Design Decisions (don't re-litigate)

| Decision | Reason |
|---|---|
| No Aider | Direct MiniMax API — full token visibility, no subprocess black box |
| No Claude for codegen | Cost. MiniMax M3 at $0.30/$1.20/1M token covers all 6 projects for $65/mo |
| Auto-commit 90-95% of tasks | Daily approve-all was biggest friction. Bad output → git revert |
| Sequential council (not debate) | Debate degrades past ~2 personas on 30B models |
| Ollama for prompts/digests only | Free local inference for orchestration; paid API for codegen only |
| MiniMax for council too | Council prompt alone exceeds Ollama's reliable context window |
| Per-project threading.Lock | Concurrent execution (lang + meridian simultaneously) needs isolation |
| Quality gate fails CLOSED | Ollama parse error → pass=False. Never auto-approve uncertain output |
| High-complexity skips gate | Goes straight to approval_required — human review instead |
| Lazy imports in run_task() | task_generator imports load_context from executor → circular dep |
| CONTEXT_STALE sentinel file | Silent CONTEXT.md failures need visible signal to next prompt |
| git add -A in _auto_commit | Stages all tracked changes, not just files_written list |

---

## Current Status (2026-06-06)

**Fully implemented and operational:**
- Full pipeline: council → queue → executor → quality gate → auto-commit → Discord
- 4-channel Discord with sender auth, bulk-approve confirmation, intent parsing
- Metrics (FEAT-4): pass rate, cost/project, throughput, perspective leaderboard
- Sprint manager: 24hr windows, carryover, sprint review generation
- Swimlane Kanban dashboard + Metrics tab, click-to-expand task modal
- 16 domain expert personas + 4 review personas
- Fallback notifications: ntfy.sh / SMTP / macOS osascript
- validate.py pre-flight checklist
- WAL SQLite, thread-safe project locks, description_hash dedup
- Gap-fill tasks when main queue runs dry

**No open TODOs.** (TODO.md is empty. BACKLOG.md has future feature specs.)

**Only `lang` in ENABLED_PROJECTS.** Add projects to `config.py` → `ENABLED_PROJECTS` after validating pipeline per project.
