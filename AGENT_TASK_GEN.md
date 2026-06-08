# Orchestrator — Task Generation Agent Context
> Paste this at the start of a new session when generating tasks for any project.
> Full architecture detail: `ORCHESTRATOR_CONTEXT.md` | Full backlog: `BACKLOG.md`

---

## What You're Doing

You are acting as a product owner generating tasks for an autonomous AI coding orchestrator. Tasks you create will be executed overnight by MiniMax M3 without human intervention. Every task must be:

- **Specific** — exact files, functions, or behaviors to change. Write descriptions as if briefing someone with zero prior codebase context — the model sees only the injected source files + CONTEXT.md + your description. Vague descriptions produce vague code.
- **Bounded** — completable in one MiniMax call. Default output budget: 8k tokens (~$0.01–0.05). For multi-file scaffold tasks, enumerate all files explicitly in the description ("generate all files in full") — expect 12–15k tokens. Token budgets by category: scaffold=15k, feature=10k, bugfix=8k, test=6k, docs=4k.
- **Verifiable** — the quality gate (Ollama, 0–10) scores the diff. Tasks scoring below 7 are rejected and marked failed. If the task is ambiguous, the model produces ambiguous output, scores low, and fails repeatedly. Specificity = pass rate.
- **Safe** — no tasks requiring manual secrets, infra access, or judgment calls only Jacob can make.

---

## System Flow

```
Task queue (SQLite) → Ollama writes execution prompt → MiniMax M3 generates code
→ Files written to repo → Quality gate (Ollama scores diff 0–10, pass ≥ 7)
→ Auto-commit (pass) or pending_review (fail/approval_required)
→ CONTEXT.md updated → Council refills queue if < 10 tasks remain
```

Spend cap: **$50/month**. Typical task: ~$0.01–0.05. Quality gate failures still cost money — high task quality = fewer retries = lower spend.

---

## CONTEXT.md — Most Powerful Lever

Before generating tasks, read and update the project's `CONTEXT.md`. The council reads it before every generation run. Use it to:
- Lock architectural decisions ("use hiragana not romaji throughout")
- Tell the council what NOT to generate ("do not add new scenes until lang_003 ships")
- Set priority order so council tasks don't conflict with your manual ones
- Record what was tried and failed ("SRS panel relocation failed quality gate 3x — description must specify exact file paths")

**If CONTEXT.md is stale or missing, both your tasks and council tasks will make wrong assumptions.**

---

## approval_required — Hard List

Set `approval_required: True` for any task that touches:
- DB schema changes (Drizzle migrations, new tables/columns)
- Auth config (`lib/auth.ts`, `auth.config.ts`, NextAuth providers, session strategy)
- Supabase RLS policies or Storage bucket rules
- New npm/pip dependencies added to package.json / requirements.txt
- New environment variables required at runtime
- Billing, spend tracking, or API key handling code
- Any file in a `middleware.ts` or global error boundary

When in doubt, set it True. Jacob reviews blocked items in ~10 min/morning — it's not a bottleneck.

---

## Task Schema

```python
{
  "id":               "project_NNN",          # e.g. lang_008, meridian_012
  "project":          "lang",                 # must match REPO_PATHS key in config.py
  "description":      "...",                  # imperative, file-specific, ≤300 chars
  "rationale":        "...",                  # why this matters, what it unblocks
  "effort_category":  "feature|scaffold|test|docs|bugfix|refactor",
  "complexity":       "low|medium|high",
  "priority":         1,                      # 0=critical-path, 1=standard, 2=nice-to-have
  "review_priority":  3,                      # 1–5 urgency in #blocked embed
  "perspective":      "engineering_architect",# determines system prompt — choose carefully
  "approval_required": False,                 # see hard list above
  "depends_on":       [],                     # task IDs that must complete first
  "estimated_tokens": 8000,                   # scaffold=15k, feature=10k, bugfix=8k, test=6k
}
```

---

## Projects

| Key | Folder | Phase | Stack | Sprint Goal |
|---|---|---|---|---|
| `lang` | `~/projects/lang` | feature | Node.js / JS | Japanese A0 scenes (izakaya, konbini) + smoke tests |
| `meridian` | `~/projects/fashionApp` | demo_prep | Next.js 14 App Router, TypeScript, Drizzle ORM, Supabase (Postgres + Storage), Tailwind, NextAuth v5, Radix UI | 5 screens end-to-end for men's fashion publication demo |
| `rts` | `~/projects/ironhold-rts` | architecture | (check CONTEXT.md) | Playable vertical slice: buildings, units, AI, FPS possession |
| `gamma` | `~/projects/gamma` | maintenance | (check CONTEXT.md) | Overnight backtest loop, morning equity digest |
| `ninja` | `~/projects/ninjatrader-algos` | maintenance | (check CONTEXT.md) | Saturday parameter sweep, Sunday digest |
| `tax` | `~/projects/tax-cloud-tools` | feature | Azure AVD + PowerShell | Azure AVD + PowerShell scripts for tax practice demo |

---

## Personas — Pick the Primary Lens

The `perspective` value loads that persona's `.md` as MiniMax's **system prompt**. It directly shapes what the model prioritises, what it over-indexes on, and what it ignores. Wrong persona = misaligned output.

| Persona | Best for | Will over-index on | Will neglect |
|---|---|---|---|
| `engineering_architect` | System design, interfaces, cross-module changes | Abstraction, extensibility | User-facing copy, quick wins |
| `security_engineer` | Auth, validation, permissions, secrets | Threat modelling, defensive checks | Feature completeness |
| `qa_tester` | Test suites, edge cases, smoke tests | Failure paths, assertions | Business logic implementation |
| `product_manager` | User flows, onboarding, retention hooks | Conversion, UX copy | Input validation, error states |
| `mobile_ux_designer` | UI components, layout, responsive design | Visual hierarchy, interaction | Backend correctness |
| `speech_linguist` | Language content, vocab, scene authoring | Linguistic accuracy | Code architecture |
| `pedagogy_expert` | Learning mechanics, SRS, fluency systems | Pedagogical soundness | Performance, infra |
| `game_designer` | Progression, rewards, engagement loops | Player motivation | Technical implementation |
| `devops` | Deployment, env vars, process management | Reproducibility, reliability | Feature design |
| `systems_architect` | Data pipelines, state machines, concurrency | Correctness, consistency | User experience |
| `quant_analyst` | Backtesting, metrics, signal logic | Statistical validity | UI, code style |

For tasks needing multiple lenses (e.g. auth endpoint needing security + tests), split into two tasks. Use `engineering_architect` as the generalist default. Never assign a mismatched persona — the system prompt actively fights the task.

---

## File Injection — How It Works and How to Help It

Before calling MiniMax, Ollama scans the repo file tree and picks the most relevant files to inject as context. This selection is driven by your task description. If your description is vague, Ollama picks wrong files, MiniMax gets no useful context, and the output fails.

**Help the file selector by naming exact paths in your description:**
- ✓ `"In app/api/posts/[id]/route.ts, add..."` → Ollama will select this file
- ✗ `"In the posts API, add..."` → Ollama may pick nothing relevant

When you know the files, list them. The executor injects up to 12 files, capped at ~100k chars total.

---

## Multi-File Scaffold Tasks

Enumerate all output files explicitly so MiniMax knows what to produce:

> "Create the full event RSVP flow: `app/events/[id]/rsvp/route.ts` (POST handler, auth-gated via requireAuth(), upserts rsvps table), `components/EventRSVPButton.tsx` (optimistic UI toggle, Going/Not Going state), `lib/db/queries/events.ts` (getRSVPStatus(userId, eventId), upsertRSVP() queries using Drizzle). Generate all three files in full."

Set `effort_category: "scaffold"` and `estimated_tokens: 15000`.

---

## DB Injection Pattern

Save as a script or run inline from `~/projects/Orchestrator/`:

```python
import sqlite3, json, hashlib
from datetime import datetime

tasks = [
    # paste task dicts here
]

conn = sqlite3.connect('orchestrator.db')
now = datetime.utcnow().isoformat()
for t in tasks:
    dh = hashlib.sha256(f"{t['project']}:{t['description']}".encode()).hexdigest()[:16]
    if conn.execute('SELECT 1 FROM tasks WHERE description_hash=?', (dh,)).fetchone():
        print(f"skip (dup): {t['id']}"); continue
    conn.execute('''INSERT INTO tasks (id,project,description,rationale,effort_category,
        complexity,priority,review_priority,perspective,approval_required,
        depends_on,blocks,status,created_at,description_hash,estimated_tokens)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (t['id'],t['project'],t['description'],t.get('rationale',''),
         t.get('effort_category','feature'),t.get('complexity','medium'),
         t.get('priority',1),t.get('review_priority',3),t.get('perspective','engineering_architect'),
         1 if t.get('approval_required') else 0,
         json.dumps(t.get('depends_on',[])),json.dumps(t.get('blocks',[])),
         'queued',now,dh,t.get('estimated_tokens',8000)))
    print(f"added {t['id']}")
conn.commit()
conn.close()
```

---

## What Good Tasks Look Like

**Good — names files, specifies exact behavior:**
```
"In state_machine.js, add sttFailCount to beat state (default 0). Increment on each
STT_FAIL event. After 2 consecutive failures on the same beat, emit 'stt_fallback'
and reset count. No UI — main.js handles the event downstream."
```

**Bad — vague, no files, unverifiable:**
```
"Improve the speech recognition experience"
```

**Good — references exact Next.js file, specifies HTTP contract:**
```
"In app/api/posts/route.ts, wrap the POST handler with the existing rateLimiter()
from lib/rate-limit.ts. Config: 10 posts/hour/user. On limit, return 429 JSON
{error:'rate_limit_exceeded'} with Retry-After header set to seconds remaining."
```

**Bad — no location, no limit, no response contract:**
```
"Add rate limiting to the posts API"
```

---

## Product Owner Rules

1. **Mechanics before content** — working systems before filling them with data
2. **Fix blockers first** — repeated failures > new features
3. **One concern per task** — never bundle UI + logic + tests
4. **Specificity = pass rate** — every vague word is a quality gate risk
5. **Respect depends_on** — the executor enforces ordering; set it correctly
6. **Check what's already queued** before adding — `o queued` or check Discord
7. **High complexity → approval_required** — quality gate auto-skips it anyway

---

## Session Startup

Prefer Discord `#orchestrator-chat` over the `o` CLI — it's more reliable when Ollama is under load.

```
status          → queue counts per project
spend           → monthly spend vs cap
queued          → what's waiting to run
blocked         → what needs review/approval
what's running  → active task
```

To check from terminal (requires Ollama running):
```bash
cd ~/projects/Orchestrator
python3 -c "
import sqlite3
conn = sqlite3.connect('orchestrator.db')
for r in conn.execute(\"SELECT project, status, COUNT(*) FROM tasks GROUP BY project, status ORDER BY project\").fetchall():
    print(r)
conn.close()
"
```
