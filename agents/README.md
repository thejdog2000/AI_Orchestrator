# agents/

Agent scripts and persona definitions for the Orchestrator.

## Scripts

| File | Purpose | Run |
|---|---|---|
| `orchestrator_bot.py` | Discord PA bot — #live, #blocked, #chat | `python agents/orchestrator_bot.py` |
| `o.py` | CLI alias — same as #chat | `alias o="python3 .../agents/o.py"` |

## Personas

`personas/domain/` — 15 domain expert personas injected into MiniMax council calls.
Each file defines the expert's identity, focus areas, and questions they always ask.

| Persona | Projects |
|---|---|
| speech_linguist | lang |
| pedagogy_expert | lang |
| engineering_architect | all |
| security_engineer | meridian, tax |
| qa_tester | all except tax |
| product_manager | meridian, lang, tax |
| mobile_ux_designer | meridian, lang |
| game_designer | rts, lang |
| game_feel_engineer | rts |
| systems_architect | rts, gamma, ninja |
| quant_analyst | gamma, ninja |
| risk_manager | gamma, ninja |
| devops | meridian, tax |
| it_administrator | tax |
| client_success | tax |

`personas/review/` — 4 reviewer perspectives for product/doc/architecture reviews.
- `newcomer` — first-time reader, no assumed knowledge
- `skeptic` — experienced engineer looking for what's wrong
- `first_time_runner` — trying to run the system from scratch
- `contributor` — developer adding a new project or pipeline
