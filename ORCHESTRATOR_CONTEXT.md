# Multi-Project AI Orchestrator — Master Context
> Feed this file at the start of every Cowork/Aider/Claude session to restore full context.
> Last updated: June 2026 sprint planning session.

---

## Who / Setup

- **Operator:** Jacob, software engineer (Microsoft Azure L60, ~4yr), Atlanta GA
- **Goal:** Use AI orchestrator + local Ollama to run 6 projects nearly 24/7 with Jacob as validator/approver only
- **Timeline:** 7-day MiniMax promo sprint active NOW, then ongoing post-sprint cadence
- **Role:** Jacob = world interfacer, creative director, human approval gate. AI = builder.

---

## Budget

| Tool | Purpose | Cost |
|---|---|---|
| Claude Pro | Jacob's interactive Claude Code sessions (not orchestrator) | $20/mo sub |
| ChatGPT Plus | Research, second opinions, job matching project | $20/mo sub |
| MiniMax M3 PAYG | Primary Aider backend, all overnight autonomous work | **$65/mo hard cap** |
| Claude API (Haiku first, Sonnet only if Haiku fails) | Escalations only — security code, hard bugs | **$35/mo hard cap** |
| Ollama 30B (local) | Orchestration, routing, digest prose, prompt writing | Free |
| **Total ceiling** | | **~$140/mo** |

**MiniMax M3 promo rate:** $0.30 input / $1.20 output / $0.06 cache read per 1M tokens (7-day launch promo from May 31 2026). Standard rate $0.60/$2.40. Verify at platform.minimax.io before first run. Fallback: MiniMax M2.7 at $0.28/$1.20 (stable).

**Hard caps must be set in dashboards before any overnight run:**
- MiniMax: platform.minimax.io → Billing → Spend limit → $65
- Anthropic: console.anthropic.com → Billing → Usage limits → $35

---

## Three-Layer Stack

```
LAYER 1 — ORCHESTRATION (free, local)
  Ollama 30B        task decomposition, Aider prompt writing, post-run triage,
                    CONTEXT.md updates, digest prose generation
  Python daemon     APScheduler cron, subprocess Aider calls, spend tracking,
                    approval queue management, dashboard rendering
  
  Ollama CANNOT: read full codebases, make direct API calls, call tools 
  autonomously, do hard codegen. Python makes all actual calls.
  Ollama speaks text → Python parses/validates → Python executes.

LAYER 2 — PRIMARY EXECUTION (~$70/mo)
  MiniMax M3 PAYG   primary Aider backend, language scene generation, 
                    RTS C# generation, all overnight autonomous work
  Aider CLI         repo-aware codegen via subprocess, --no-auto-commits always,
                    diffs queue for Jacob approval before any merge

LAYER 3 — ESCALATION (~$30/mo)  
  Claude Haiku API  when MiniMax output quality insufficient
  Claude Sonnet API only if Haiku also fails — not auto-escalated
  Never auto-escalate to Sonnet. Flag for human review instead.
```

---

## Orchestrator Core Logic

### Priority Queue (never idle, always draining across all projects)

```python
while True:
    task = queue.get_next(approval_required=False)  # unblocked tasks first
    if task is None:
        task = queue.get_next_any_project()          # cross-project fallback
    if task is None:
        task = generate_gap_fill_tasks()             # tests, docs, CONTEXT.md
    execute(task)
    if queue.size() < 10:
        ollama.generate_next_tasks(count=20)         # refill queue continuously
```

### Task Metadata Schema

```python
{
  "id": "project_NNN",
  "project": "meridian|rts|lang|gamma|ninja|tax",
  "description": "specific actionable task",
  "approval_required": False,      # True only for auth/security/user data/client deliverables
  "blocks": ["task_id_list"],      # tasks that can't run until this completes
  "estimated_tokens": 15000,
  "priority": 0,                   # 0=high, 1=medium, 2=gap-fill
  "status": "queued|running|pending_review|approved|rejected"
}
```

### approval_required: True — Short List (everything else is False)
- JWT auth implementation (Meridian)
- Any Meridian change touching user data schema or auth flow
- Tax/cloud client deliverables (never autonomous deploy to client infra)
- First run of any new project pipeline (sanity check)
- Tasks flagged low-confidence by quality gate

### Execution Flow (7 steps)
1. **Schedule** — Python APScheduler triggers task from queue
2. **Decompose** — Ollama reads task + CONTEXT.md → scoped Aider prompt (max 3 retries if malformed)
3. **Execute** — Aider via subprocess, MiniMax M3 backend, --no-auto-commits
4. **Quality gate** — Ollama evaluates diff vs acceptance criteria
5. **Escalate?** — if score < threshold, retry on Claude Haiku; if still failing, flag human review
6. **Queue + log** — approved diffs → pending_review/, tokens + outcome logged, CONTEXT.md updated
7. **Digest** — 3x daily Ollama reads logs, writes morning/afternoon/evening report

### Cross-Project Transition
If current project blocked on approval → skip to next project with unblocked tasks.
If all projects blocked → run gap-fill tasks (expand randomization pools, write tests, JSDoc, READMEs, CONTEXT.md updates).
Never sit idle.

### Gap-Fill Task Pool (always available)
- Expand dialogue randomization pools for completed lang scenes
- Generate missing JSDoc for completed RTS/Meridian systems
- Write additional smoke tests for language app
- Generate Expo component stubs for Meridian
- Create README sections for completed systems
- Update CONTEXT.md files per project

---

## Project Registry

### 1. Meridian — Social Platform Mobile Port
**Status:** SPRINT (days 1-3 daytime + ongoing overnight)
**Priority:** Highest — warm buyer (men's fashion publication contact) waiting for demo

**Tech stack:**
- Web: Next.js 14 App Router, TypeScript, Drizzle ORM, PostgreSQL/Supabase, NextAuth v5, Tiptap, Tailwind + shadcn/ui, Resend, Sentry, Node.js server
- Mobile: React Native + Expo SDK 51, NativeWind (Tailwind conventions), React Navigation, shared TypeScript types from web repo

**Sprint scope (5 screens only — enough for fashion pub pitch):**
1. Login/signup (JWT auth for native — review required, do day 1 first)
2. Home feed (calls /api/v1/feed, infinite scroll, post cards)
3. Post detail (content rendering, comments)
4. User profile
5. Create post (simplified — text + image upload, no full Tiptap)

**Key backend addition needed:** JWT auth endpoint for native clients (NextAuth v5 + custom JWT). Security-sensitive — Jacob reviews every line before anything else is built on top of it.

**AI split:** 80-90% AI for screen scaffolding, API wiring, type imports. Jacob handles: JWT auth review, app icon/splash, TestFlight setup.

**Overnight tasks (low-stakes, no approval needed):**
- Automated test generation for new screens
- Supabase type sync scripts
- API route documentation
- Expo component Storybook stubs
- API client type generation from routes

**Repo structure assumption:** meridian/ (web), meridian-mobile/ (new Expo project), shared types imported from ../meridian/types

**Post-sprint:** CI pipeline, push notifications (Expo EAS), full feature parity, Forth Atlanta pitch after fashion pub onboarded.

---

### 2. Ironhold RTS — Medieval RTS + FPS Hybrid
**Status:** SPRINT (day 4 generation, day 5 editor execution)
**Priority:** Medium — no external deadline, architecture sprint only

**Engine:** Unity 2022 LTS
**Key packages:** Mirror (networking), Cinemachine (camera), NavMesh (pathfinding), A* Pathfinding Project (optional), UI Toolkit
**Inspiration:** Stronghold (castle building) + Age of Empires (unit variety) + FPS possession mode

**AI split (revised):**
- C# runtime systems: 90% AI
- Editor setup scripts: 55% AI (Aider writes scripts Jacob runs in editor)
- Scene hierarchy: 60% AI (via editor scripts)
- Prefab creation: 55% AI (via PrefabFactory editor script)
- UI layout: 55% AI (UI Toolkit)
- NavMesh bake: Jacob clicks Bake (~5 min)
- Terrain/assets/feel tuning: Jacob (V2)

**Sprint deliverable (7 days):** Playable vertical slice — place buildings, train units, select/move them, basic AI opponent, FPS possession mode working, resource HUD visible.

**Systems to generate (one large Aider session day 4):**
- GameManager (singleton, game state FSM)
- EventBus (ScriptableObject-based, all systems communicate via this)
- RTSCameraController (pan/zoom/rotate/edge scroll + Cinemachine FPS blend)
- InputManager (click select, drag box select, right-click move, Tab=possess)
- Unit base class + UnitStats ScriptableObject
- ResourceSystem (Wood/Stone/Food/Gold) + collector units
- BuildingData ScriptableObject + BuildingPlacer (grid snap)
- Basic enemy AI FSM (Idle/Gather/Attack/ReturnToBase)
- NavMeshAgent movement wrapper + formation offset
- Mirror multiplayer scaffold (NetworkManager, NetworkUnit sync)
- Save/load (JSON serialization)
- **Editor scripts (IronholdSetup EditorWindow, PrefabFactory, ScriptableObjectFactory)**
- Unity Test Framework tests (ResourceSystem, pathfinding, building placement)

**Overnight (day 6):** Unity Test Framework run — logic validation, not visual. Failures surfaced in morning digest.

**No overnight Aider on Unity repo** — untestable output without headless Unity. Daytime only.

---

### 3. Language Travel App
**Status:** SPRINT — best overnight candidate, 90% autonomous
**Priority:** High during promo week — pure generation tasks, parallelizable, no approval needed

**Tech stack:** Vanilla JS modules, Three.js (3D scenes), browser SpeechRecognition/SpeechSynthesis, localStorage SRS. Node-based smoke tests. No framework, no build step — ideal for AI generation.

**Languages:** Japanese (A0/A1) first, Spanish (A0/A1) second
**Target:** 10 scenes total over 7 nights (2 per night)

**Scene schedule:**
- Night 1: JA A0 — Izakaya + Konbini
- Night 2: JA A1 — Train station + Ramen shop
- Night 3: JA A1 — Temple/directions + review pass
- Night 4: ES A0 — Taco vendor + Mercado
- Night 5: ES A1 — Café + Taxi
- Night 6: ES A1 — Hotel check-in + SRS integration
- Night 7: Buffer — failed scenes, randomization expansion, gap fill

**Scene JS module schema:**
```javascript
export const scene = {
  id: 'scene_id',
  language: 'ja' | 'es',
  level: 'a0' | 'a1',
  location: 'human readable location name',
  three: {
    cameraPosition: { x, y, z },
    ambientLight: { color: 0xHEX, intensity: float },
    pointLights: [{ position, color, intensity }],
    assets: [{
      id: 'asset_id',
      sketchfabQuery: 'search string for asset sourcing',
      position: { x, y, z },
      scale: float
    }]
  },
  npc: { name: string, role: string },
  dialogue: {
    opening: { ja/es: string, en: string },
    vocabularyFocus: string[],  // 5-8 target words
    grammarFocus: string,       // one grammar point
    randomizationPool: {
      playerGreetings: string[],   // 5+ variants
      orderOptions: string[],
      correctResponses: string[],
      incorrectAttempts: string[]
    },
    branches: [{
      trigger: string,
      npcResponse: { ja/es: string, en: string },
      playerOptions: string[]
    }],
    successEnding: { ja/es: string, en: string },
    failureRecovery: { ja/es: string, en: string }
  },
  srs: {
    newCards: string[],
    reviewTrigger: 'scene_complete'
  }
}
```

**Pipeline per scene:**
1. Ollama writes generation prompt from schema template
2. MiniMax M3 generates full scene JS module
3. Separate MiniMax call generates Three.js scene config
4. Python writes to /scenes/{language}/{scene_id}.js
5. Node smoke test runs automatically
6. Pass/fail logged to digest

---

### 4. Gamma Exposure Tool
**Status:** BACKGROUND (post-sprint, maintenance cadence)
**Priority:** Personal P&L tool — Jacob is the user

**Domain:** SPX 0DTE options, gamma/charm/vanna dealer flow frameworks, Trinity flow data, VolSignals dashboards, straddle pricing, put/call walls, expected move bands, pin-or-rip dynamics.

**Orchestrator role:** Backtest loop runs overnight. Ollama orchestrates iterations. MiniMax generates strategy/indicator code. Morning digest: performance metrics, parameter suggestions, equity curve.

**Jacob's role:** Live session analysis (manual, uses Claude Pro interactively). Review overnight backtest results. PDT restrictions active (sub-$25k margin account).

---

### 5. NinjaTrader Algo
**Status:** BACKGROUND (dedicated Saturday slot)
**Priority:** Low during sprint week

**Stack:** NinjaScript (C#), NT8, ATR + Fibonacci retracements
**Note:** NinjaScript is niche — models make more errors here. Watch output carefully. More retries expected than other projects.

**Cadence:** Saturday overnight parameter sweep. Sunday morning digest: best performers, drawdown analysis, next iteration suggestions.

---

### 6. Tax / Cloud Tools (Consulting)
**Status:** ACTIVE (revenue-first, builds when client engaged)
**Priority:** Highest revenue proximity — warm lead exists (family member's tax practice)

**Stack:** Azure AVD, PowerShell, Entra ID, Intune, QuickBooks/TaxDome integrations
**AI split:** 85% AI for scripts/docs. Jacob handles all networking, sales, client comms.
**Critical rule:** No autonomous deployment to client infra. Ever. Jacob reviews all deliverables before sending.

---

## 7-Day Sprint Schedule

| Day | Daytime Focus (Jacob present) | Overnight Autonomous |
|---|---|---|
| 1 | Meridian: JWT auth review + Expo scaffold | Lang: JA A0 — Izakaya + Konbini |
| 2 | Meridian: Feed + post detail screens | Lang: JA A1 — Train station + Ramen |
| 3 | Meridian: Profile + create post + nav polish | Lang: JA A1 — Temple + review pass |
| 4 | RTS: ONE large Aider session — full C# + editor scripts | Lang: ES A0 — Taco vendor + Mercado |
| 5 | Unity editor: run setup scripts, bake NavMesh, import assets (~2hr) | Lang: ES A1 — Café + Taxi + RTS test suite |
| 6 | RTS: fix test failures, verify vertical slice. Meridian: TestFlight | Lang: ES A1 — Hotel + SRS. Meridian: API type gen |
| 7 | Review all pending diffs, merge clean, assess demo readiness | Buffer: failed scenes, week 2 task queue generation |

**Key principle:** Orchestrator doesn't follow this schedule rigidly. It runs continuously across all projects. Schedule shows Jacob's daytime focus. Overnight queue drains all unblocked tasks from all projects simultaneously.

---

## Jacob's Daily Rhythm (Validator Role)

**Morning (~20 min):**
- Read overnight digest
- Review + approve/reject pending diff batch
- Check API spend vs daily budget
- Set daytime priority

**Afternoon (~15 min):**
- Mid-day digest check
- Unblock any stalled tasks needing human input
- Adjust overnight queue priorities

**Evening (~10 min):**
- Review afternoon output
- Approve remaining diffs
- Confirm overnight queue loaded + caps set

---

## Aider Configuration

```bash
# Primary: MiniMax M3 via OpenAI-compatible endpoint
aider \
  --openai-api-base https://api.minimax.io/v1 \
  --openai-api-key $MINIMAX_API_KEY \
  --model minimax/minimax-m3 \
  --no-auto-commits \          # diffs queue for approval, never auto-merge
  --yes-always \               # don't prompt for confirmations in subprocess mode
  --message "$TASK_PROMPT"

# Escalation: Claude Haiku
aider \
  --model claude-haiku-4-5 \
  --no-auto-commits \
  --yes-always \
  --message "$TASK_PROMPT"
```

---

## Files / Repo Structure Expected

```
~/projects/
  orchestrator/
    main.py               # APScheduler daemon (see CODE file)
    task_queue.py         # priority queue logic
    aider_runner.py       # subprocess wrapper
    quality_gate.py       # Ollama evaluation
    digest.py             # report generation
    spend_tracker.py      # token/cost logging
    tasks/
      meridian.json       # pre-loaded task backlog
      rts.json
      lang.json
      gamma.json
      ninja.json
      tax.json
    pending_review/       # diffs waiting for Jacob approval
    logs/
      spend.json          # daily token/cost log
      completed.json      # task completion history
    dashboard/
      index.html          # morning digest render
  
  meridian/               # existing Next.js web app
  meridian-mobile/        # new Expo project (sprint day 1)
  ironhold-rts/           # Unity project
  language-travel-app/    # vanilla JS + Three.js
  gamma-tool/             # Python backtesting
  ninjatrader-algos/      # NinjaScript strategies
```

---

## CONTEXT.md Pattern (per project)

Each project maintains a CONTEXT.md that Ollama reads to write task prompts and updates after each completed session. Keep under 2000 tokens.

```markdown
# [Project] CONTEXT.md
Last updated: [date] by orchestrator

## Current state
[2-3 sentences: what exists, what works, what's broken]

## Architecture
[Key files, their roles, how they connect]

## Active sprint goal
[What we're building right now]

## Completed this week
[Bulleted list of merged diffs]

## Pending review
[Diffs awaiting Jacob approval]

## Known issues / blockers
[Anything the orchestrator flagged]

## Next tasks (pre-generated)
[Top 5 queued tasks]
```

---

## Token Estimates

| Scenario | Monthly tokens | MiniMax M3 cost | Claude API cost | Total API |
|---|---|---|---|---|
| Low (great optimization, 1-2 projects) | ~8M | ~$14 | ~$10 | ~$24 |
| Medium (avg, 3-4 projects) | ~25M | ~$42 | ~$25 | ~$67 |
| High (no optimization, all 6) | ~60M | ~$102 | ~$35 | ~$137 |
| No Ollama at all | ~120M | ~$204 | — | $204+ |

Target: medium scenario. Ollama handles routing/digest/orchestration (free). MiniMax handles bulk coding. Claude API handles escalations only.

---

## Key Decisions Made (don't re-litigate)

- **MiniMax M3 PAYG** over subscription plans — better for bursty async workloads, no rate windows
- **Aider** over custom codegen — already installed, handles repo context, file selection, diff application
- **Ollama as orchestrator only** — not executor. Python makes all actual calls.
- **--no-auto-commits always** — diffs never merge without Jacob seeing them
- **No overnight Aider on Unity** — untestable without headless Unity, daytime only
- **Language app first** for pipeline testing — lowest stakes, auto-testable with Node smoke tests
- **React Native + Expo** for Meridian mobile — shares all existing API routes, TypeScript types, auth logic
- **Unity over Godot** for RTS — better AI training data coverage, Mirror networking, Cinemachine, DOTS for scale
- **Vanilla JS + Three.js** for language app (existing stack) — no build step, ideal for AI generation
- **JWT auth for Meridian native** — Jacob reviews every line, done day 1 before anything else
- **5 screens only for Meridian sprint** — enough for fashion publication pitch, everything else V2
- **Single large Aider session for RTS** — generate entire C# package at once, Jacob runs editor scripts day 5

---

## Warm Opportunities (context for prioritization)

- **Meridian:** Men's fashion publication contact (warm inbound) wants community platform. Demo = unlock. After fashion pub onboarded → pitch to Forth Atlanta social club members.
- **Tax/Cloud:** Family member's tax practice = first client + design partner. Warm vertical.
- **Gamma tool + NinjaTrader:** Personal P&L proof of concept. Live equity curve = product demo.

---

## What This File Is For

Feed to any AI (Claude, Aider, Cowork, ChatGPT) at the start of a session to restore full context without re-explaining. Use str_replace to update sections as state changes — don't regenerate the whole file. Key sections to keep current: project statuses, completed tasks, active sprint goal, known blockers.
