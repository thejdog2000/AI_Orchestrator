# Orchestrator — Project Registry
> Load this when generating tasks, planning sprints, or needing project-specific context.

---

## 1. Language Travel App (`lang`)

**Repo:** `~/Documents/claude/projects/language-travel-app/`
**Status:** SPRINT — best overnight candidate, 90-95% autonomous
**Priority:** High — pure generation tasks, auto-testable, no approval needed

**Stack:** Vanilla JS modules, Three.js (3D scenes), browser SpeechRecognition/SpeechSynthesis, localStorage SRS. Node smoke tests. No framework, no build step.

**Languages:** Japanese (A0/A1) first → Spanish (A0/A1) second
**Target:** 10 scenes over 7 nights (2/night)

**7-night schedule:**
- Night 1: JA A0 — Izakaya + Konbini
- Night 2: JA A1 — Train station + Ramen shop
- Night 3: JA A1 — Temple/directions + randomization expand
- Night 4: ES A0 — Taco vendor + Mercado
- Night 5: ES A1 — Café + Taxi
- Night 6: ES A1 — Hotel check-in + SRS integration
- Night 7: Buffer — failed scenes, gap fill

**Scene schema:** See `lang_pipeline.py` → `SCENE_SCHEMA`. Full JS module spec injected into every generation prompt.

**Council perspectives:** speech_linguist, pedagogy_expert, game_designer, qa_tester

**AI split:** 95% AI. Jacob reviews if smoke tests fail repeatedly.

---

## 2. Meridian — Social Platform Mobile Port (`meridian`)

**Repo:** `~/projects/meridian-mobile/` (mobile), `~/projects/meridian/` (web)
**Status:** SPRINT — 5 screens for men's fashion publication demo
**Priority:** Highest external — warm buyer waiting for demo

**Stack:**
- Web: Next.js 14, TypeScript, Drizzle ORM, PostgreSQL/Supabase, NextAuth v5, Tiptap, Tailwind + shadcn/ui
- Mobile: React Native + Expo SDK 51, NativeWind, React Navigation, shared TypeScript types

**Sprint scope (5 screens only):**
1. Login/signup — `approval_required=True` (JWT auth, Jacob reviews every line)
2. Home feed — infinite scroll, post cards, `/api/v1/feed`
3. Post detail — content rendering, comments
4. User profile
5. Create post — text + image upload

**Overnight (auto-commit, no approval):** test generation, Supabase type sync, API route docs, Storybook stubs, type generation from routes

**Council perspectives:** product_manager, mobile_ux_designer, security_engineer, engineering_architect

**AI split:** 80-90% AI. Jacob handles: JWT auth review, app icon/splash, TestFlight.

**Post-sprint:** CI, push notifications (Expo EAS), Forth Atlanta pitch.

---

## 3. Ironhold RTS — Medieval RTS + FPS Hybrid (`rts`)

**Repo:** `~/projects/ironhold-rts/`
**Status:** SPRINT (architecture phase)
**Priority:** Medium — no external deadline

**Engine:** Unity 2022 LTS
**Packages:** Mirror (networking), Cinemachine (camera), NavMesh, UI Toolkit
**Inspiration:** Stronghold + Age of Empires + FPS possession mode

**Systems to generate:**
GameManager, EventBus, RTSCameraController, InputManager, Unit base class + UnitStats, ResourceSystem, BuildingData + BuildingPlacer, enemy AI FSM, NavMeshAgent wrapper, Mirror scaffold, Save/load, Editor scripts (IronholdSetup, PrefabFactory, ScriptableObjectFactory), Unity Test Framework tests

**AI split:** 90% C# systems AI. Editor setup scripts: Jacob runs in editor. NavMesh bake: Jacob (~5 min).

**Council perspectives:** game_designer, game_feel_engineer, systems_architect, qa_tester

**Sprint deliverable:** Playable vertical slice — place buildings, train units, basic AI, FPS possession working.

**Note:** No overnight runs until headless Unity testing is configured. Daytime only.

---

## 4. Gamma Exposure Tool (`gamma`)

**Repo:** `~/Documents/claude/projects/gamma-tool/`
**Status:** BACKGROUND (maintenance cadence)
**Priority:** Personal P&L — Jacob is the user

**Domain:** SPX 0DTE options — gamma/charm/vanna dealer flow, Trinity flow data, VolSignals, straddle pricing, put/call walls, expected move bands, pin-or-rip dynamics.

**Orchestrator role:** Overnight backtest loop. Morning digest: performance metrics, parameter suggestions, equity curve.

**Jacob's role:** Live session analysis (manual, Claude Pro). PDT restrictions active (sub-$25k).

**Council perspectives:** quant_analyst, risk_manager, systems_architect

---

## 5. NinjaTrader Algo (`ninja`)

**Repo:** `~/projects/ninjatrader-algos/`
**Status:** BACKGROUND (Saturday slot)
**Priority:** Low

**Stack:** NinjaScript (C#), NT8, ATR + Fibonacci retracements

**Cadence:** Saturday overnight parameter sweep. Sunday morning digest: best performers, drawdown analysis.

**Note:** NinjaScript is niche — more model errors than other projects. Watch output carefully.

**Council perspectives:** quant_analyst, risk_manager, systems_architect

---

## 6. Tax / Cloud Tools (`tax`)

**Repo:** `~/projects/tax-cloud-tools/`
**Status:** ACTIVE (builds when client engaged)
**Priority:** Highest revenue proximity — family member's tax practice as first client

**Stack:** Azure AVD, PowerShell, Entra ID, Intune, QuickBooks/TaxDome integrations

**Critical rule:** `approval_required=True` on ALL client deliverables. Never autonomous deploy to client infra. Ever.

**Council perspectives:** it_administrator, security_engineer, client_success, devops

**AI split:** 85% AI for scripts/docs. Jacob handles networking, sales, client comms.

---

## Cowork / Sandbox Repo Access

Symlinks exist at `~/projects/lang` and `~/projects/gamma` pointing to their repos, but the Cowork sandbox cannot follow symlinks that resolve outside the mounted folder. To read lang/gamma files from within a Cowork session, select the target folder directly via the Cowork folder picker (`~/Documents/Claude/Projects/Language Learning App` or `~/Documents/Claude/Projects/gamma-tool`). The orchestrator itself running natively on macOS can follow the symlinks fine.

---

## Sprint Phases (update in `config.py` as sprints progress)

| Project | Current Phase | Goal |
|---|---|---|
| lang | feature | Japanese A0 scenes with smoke tests passing |
| meridian | demo_prep | 5 screens for fashion pub demo |
| rts | architecture | Playable vertical slice |
| gamma | maintenance | Backtest loop running |
| ninja | maintenance | Saturday sweep pipeline |
| tax | feature | AVD + PowerShell ready for demo |

---

## Warm Opportunities

- **Meridian:** Men's fashion publication (warm inbound). Demo unlocks engagement. After onboarded → Forth Atlanta social club pitch.
- **Tax/Cloud:** Family member's tax practice = first client + design partner. Revenue-adjacent now.
- **Gamma + NinjaTrader:** Live equity curve = product demo for trading community.
