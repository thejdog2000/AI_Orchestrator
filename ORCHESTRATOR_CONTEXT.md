# AI Orchestrator — Session Context
> Feed this file at the start of every AI session. For project details load PROJECTS.md. For architecture load ARCHITECTURE.md.

---

## Who

- **Jacob** — software engineer (Microsoft Azure L60, ~4yr), Atlanta GA
- **Role** — world interfacer, creative director, final authority. AI = builder.
- **Goal** — run 6 projects nearly 24/7. Jacob's daily touch is ~10 min via Discord.

## Repos (absolute paths)

```
~/Documents/claude/projects/language-travel-app/   ← lang
~/Documents/claude/projects/gamma-tool/            ← gamma
~/projects/meridian-mobile/                        ← meridian (mobile sprint)
~/projects/meridian/                               ← meridian web
~/projects/ironhold-rts/                           ← rts
~/projects/ninjatrader-algos/                      ← ninja
~/projects/tax-cloud-tools/                        ← tax
~/projects/Orchestrator/                           ← this repo
```

## Models (verified)

```
qwen3-coder:30b   — execution prompts, code-adjacent tasks
qwen3:14b         — digest prose, CONTEXT.md updates (lighter/faster)
```

## Budget

| Component | Cost |
|---|---|
| MiniMax M3 PAYG — all codegen | **$65/mo hard cap** |
| Ollama — orchestration, prompts, digests | **Free** |
| Claude Pro — Jacob's deep-work sessions (Cowork) | $20/mo |

## Interface — How Jacob Interacts

**Discord** (`#orchestrator` channel) — primary PA interface
- Orchestrator pushes to Jacob: morning digest, approval needed, spend warnings
- Jacob messages bot: "what happened overnight", "approve the RTS tasks", "pause lang"
- Natural language → Ollama → action

**Cowork (Claude)** — deep-work sessions
- Sprint planning, architecture reviews, feature building, complex diffs
- This is where you are now

**`o` CLI alias** — thin wrapper, same Discord backend, for terminal use

## Daily Rhythm

**Triggered (no schedule — Discord pushes to Jacob):**
- `approval_required=True` task blocked → Discord DM
- Spend ≥ 85% cap → Discord DM
- Morning digest → Discord at 8am

**Morning (~10 min):**
- Read Discord digest
- If approval needed: message bot "approve <task_id>" or `python approve.py`
- Nothing else unless something looks wrong

**Anytime:**
- Message Discord bot or open Cowork to redirect, get status, review decisions

## Approval Model

**90-95% of tasks: auto-commit.** No review needed. Everything is in git — bad output gets `git revert`.

`approval_required: True` is reserved for:
- JWT auth implementation (Meridian)
- User data schema migrations
- Client deliverables (tax practice — never autonomous deploy)
- Major architecture decisions flagged by council

## Key Documents

| File | When to load |
|---|---|
| `ARCHITECTURE.md` | Building features, debugging, understanding the codebase |
| `PROJECTS.md` | Task generation, sprint planning, project-specific context |
| `TODO.md` | Active near-term work |
| `BACKLOG.md` | Future features with full specs |
| `COMPLETED.md` | History of everything built and fixed |

## Config

Edit `config.py` to change models, repo paths, spend cap, sprint phases/goals. Single source of truth — all modules import from it.

## Key Decisions (don't re-litigate)

- **No Aider** — direct MiniMax API calls, full token visibility
- **No Claude escalation** — fail → log → next task
- **Auto-commit** — 90-95% of tasks commit automatically; `approval_required` is rare
- **Discord as PA** — orchestrator pushes to Jacob, Jacob directs via natural language
- **Sequential council** — one MiniMax call per perspective, merge pass, not debate
- **Per-project locks** — lang + meridian run simultaneously (different repos)
- **MiniMax for codegen + council** — Ollama for prompts + digests only
- **qwen3-coder:30b / qwen3:14b** — only models installed; never assume llama3
