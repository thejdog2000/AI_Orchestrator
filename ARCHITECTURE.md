# Orchestrator — Architecture Reference
> Load this when building features, debugging, or understanding how the system works.

---

## Three-Layer Stack

```
LAYER 1 — ORCHESTRATION (free, local)
  qwen3-coder:30b   execution prompt writing, quality gate (low/medium tasks)
  qwen3:14b         digest prose, CONTEXT.md updates
  Python daemon     APScheduler, routing, spend tracking, auto-commit, Discord

  Ollama constraints: num_ctx must be 8192 (default 2048 silently truncates),
  json_mode=True on any call expecting JSON output.

LAYER 2 — EXECUTION (~$65/mo cap)
  MiniMax M3 PAYG   (a) all code generation via direct API calls
                    (b) council task generation (~$0.01/run)
  No Aider. No subprocess. Direct API, full token visibility, path traversal guard.

LAYER 3 — APPROVAL (Jacob, ~10 min/day)
  Discord bot       PA interface — pushes to Jacob, accepts natural language
  approve.py        CLI for approval_required tasks only
  Auto-commit       90-95% of tasks commit automatically after path guard passes
```

## Load Distribution

| Task | Model | Notes |
|---|---|---|
| Execution prompt writing | qwen3-coder:30b | Injected into MiniMax system prompt |
| Retry prompt revision | qwen3-coder:30b | Targets specific failure reasons |
| Quality gate (low/medium) | qwen3-coder:30b | Fails closed on parse error |
| Quality gate (high complexity) | Skip — human review | Local model unreliable on hard diffs |
| CONTEXT.md update | qwen3:14b | Prose summarization — lighter model adequate |
| Digest prose | qwen3:14b | Morning/afternoon/evening reports |
| Council task generation | MiniMax direct API | 3 perspective calls + 1 merge call |
| All codegen | MiniMax direct API | temperature=0.2 (deterministic) |

## Core Execution Flow

```
1. Scheduler fires (every 2 min, BackgroundScheduler)
2. Per-project lock check — skip busy projects
3. Get next task from SQLite queue (unblocked, not approval_required)
4. git checkout -- . (clean working tree)
5. Ollama writes execution prompt from task + CONTEXT.md
6. MiniMax generates files in <<<FILE: path>>> blocks
7. Path traversal guard: dest.resolve().is_relative_to(repo_path.resolve())
8. Write files to repo
9. Quality gate: Ollama evaluates diff (fails CLOSED on parse error)
10. Auto-commit: git add -A + git commit (no pending_review accumulation)
11. CONTEXT.md updated: Ollama reads diff → updates project state
12. Discord notification: "Task X committed in [project]"
13. If approval_required: pause, Discord DM to Jacob, wait
14. If queue < threshold: MiniMax council generates new tasks
```

## Task Schema (SQLite)

```python
{
  "id":               "project_NNN",
  "project":          "lang|meridian|rts|gamma|ninja|tax",
  "description":      "specific actionable task",
  "status":           "queued|running|committed|failed",
  "priority":         0,          # 0=high, 1=medium, 2=gap-fill
  "approval_required": False,     # True only for auth/security/schema/client
  "complexity":       "low|medium|high",
  "rationale":        "why this task exists",
  "effort_category":  "feature|scaffold|test|docs|bugfix|gap-fill|refactor",
  "perspective":      "speech_linguist|engineering_architect|...",
  "review_priority":  3,          # 1-5 computed: complexity + approval_required
  "depends_on":       [],
  "blocks":           [],
  "estimated_tokens": 8000,
  "input_tokens":     0,          # actual from API response
  "output_tokens":    0,
  "cost_usd":         0.0,
}
```

## Approval Model

`approval_required: True` is a short list — everything else auto-commits:
- JWT auth implementation (Meridian)
- User data schema migrations
- Client deliverables (tax — never autonomous deploy)
- Tasks the council flags as major architecture decisions

No `pending_review/` accumulation. Auto-committed tasks appear in git log and Discord digest.

## Module Map

```
config.py              Single source of truth — edit to change anything
orchestrator_main.py   Entry point: scheduler, PID lock, health checks, Discord import
executor.py            MiniMax calls, Ollama prompts, path guard, CONTEXT.md loop, retries
spend.py               SpendTracker with atomic writes (tmp + os.replace)
digests.py             Digest generation (qwen3:14b)
task_queue.py          SQLite TaskQueue, PERSPECTIVE_PROJECT_MAP, gap-fill
task_generator.py      Council pipeline: perspective calls → merge → JSON insert
lang_pipeline.py       7-night scene schedule, schema-aware prompts, Node smoke tests
dashboard_generator.py Static Kanban HTML — no server needed
approve.py             CLI for approval_required tasks only
git_watcher.py         Auto-commit daemon: polls COMMIT_REQUEST.txt, clears locks
orchestrator_bot.py    TODO: Discord bot PA interface (see BACKLOG.md FEAT-Discord)
notify.py              TODO: multi-channel push notifications (see BACKLOG.md FEAT-Discord)
validate.py            TODO: pre-flight checklist (see BACKLOG.md)
personas/domain/       TODO: domain expert persona files (see BACKLOG.md FEAT-1)
personas/review/       TODO: reader persona files (see BACKLOG.md FEAT-1)
```

## Council Prompting Design

Sequential, not debate. Debate format breaks past ~2 personas on any model.

```
1. Select 3 perspectives for this project + sprint phase (phase-weighted)
2. One MiniMax call per perspective → 3 task proposals in structured text
3. One merge call (json_mode=True, temperature=0.3) → deduplicated JSON tasks
```

Phase → perspective priority (from `PHASE_PERSPECTIVE_PRIORITY` in task_generator.py):
- `architecture` → engineering_architect, systems_architect, devops
- `feature`      → product_manager, mobile_ux_designer/game_designer, engineering_architect
- `polish`       → qa_tester, game_feel_engineer/speech_linguist, product_manager
- `demo_prep`    → product_manager, game_designer/mobile_ux_designer, client_success

Perspective definitions live in `personas/domain/` (see BACKLOG.md FEAT-1).
Currently: bare role strings injected into prompts. Persona files will replace these.

## CONTEXT.md Pattern (per project)

Each project repo has a CONTEXT.md updated by Ollama after every committed task.
Keep under 2000 tokens. Template:

```markdown
# [Project] CONTEXT.md
Last updated: [date]

## Current state
[2-3 sentences: what exists, what works, what's broken]

## Architecture
[Key files, their roles, how they connect]

## Active sprint goal
[What we're building right now]

## Completed this week
[Bulleted list of recent commits]

## Known issues / blockers
[Anything flagged by the orchestrator]

## Next tasks
[Top 5 queued tasks]
```

## Token Estimates

| Scenario | MiniMax M3 cost/mo |
|---|---|
| 1-2 projects, good optimization | ~$14 |
| 3-4 projects, average | ~$42 |
| All 6 projects, no optimization | ~$102 |

$65/mo cap enforced in code. Set matching limit at platform.minimax.io.

## Security Properties

- **Path traversal guard**: all AI-written file paths validated with `is_relative_to()` before write
- **Quality gate fails closed**: Ollama parse error → `pass=False`, not `pass=True`
- **Atomic spend writes**: `os.replace()` prevents corrupted spend.json on crash
- **No auto-deploy**: nothing touches client infra, production, or external systems without Jacob
- **API key via env only**: never in CLI args or logged
- **Per-project git locks**: git checkout before every attempt, no compounding bad diffs
