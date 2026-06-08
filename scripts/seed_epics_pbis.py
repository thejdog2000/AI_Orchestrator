"""
seed_epics_pbis.py
Creates initial epics, PBIs, and split tasks for lang_001/005/007
and orchestrator_006/008. Cancels the original fat tasks.
Safe to re-run — uses add_epic/add_pbi which skip existing IDs.
"""
import sys, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from task_queue import TaskQueue
from config import DB_PATH

tq = TaskQueue(DB_PATH)

# ─────────────────────────────────────────────────────────────────────────────
# EPICS
# ─────────────────────────────────────────────────────────────────────────────

epics = [
    {"id": "epic_lang_progression",   "project": "lang",        "name": "Scene Progression & SRS",   "color": "#10b981"},
    {"id": "epic_lang_stt",           "project": "lang",        "name": "STT Infrastructure",         "color": "#f59e0b"},
    {"id": "epic_orch_infrastructure","project": "orchestrator","name": "Orchestrator Infrastructure","color": "#6366f1"},
]
for e in epics:
    ok = tq.add_epic(e)
    print(f"Epic {'added' if ok else 'exists'}: {e['id']}")

# ─────────────────────────────────────────────────────────────────────────────
# PBIs
# ─────────────────────────────────────────────────────────────────────────────

pbis = [
    # ── lang_001 split ──
    {
        "id":      "pbi_lang_srs_gate",
        "epic_id": "epic_lang_progression",
        "project": "lang",
        "title":   "Pre-session SRS gate",
        "description": (
            "Move the SRS review panel to before the main scene starts. "
            "For scenes at practiced+ tier (2+ completed sessions), the gate is non-skippable: "
            "show 3 due phrases from completed scenes, grade each via browser STT, "
            "then unlock the main scene. Scenes below practiced+ tier see the panel "
            "but can skip it."
        ),
        "acceptance_criteria": (
            "- SRS gate appears before scene HUD on practiced+ scenes\n"
            "- Gate shows exactly 3 due phrases from SRS queue\n"
            "- Each phrase graded via STT (lang='ja-JP'); pass/fail recorded\n"
            "- Cold recall rate ≥70% by session N+2 (tracked in persistence)\n"
            "- Non-practiced scenes show panel but allow skip\n"
            "- Session continues normally after gate regardless of score"
        ),
        "affected_files": [
            "persistence.js", "localStorage.js", "scene_manager.js",
            "main.js", "src/3d/srsUi.js", "components/feedback.js", "config/constants.js"
        ],
        "status": "active",
    },
    # ── lang_005 split ──
    {
        "id":      "pbi_lang_passport_stamp",
        "epic_id": "epic_lang_progression",
        "project": "lang",
        "title":   "Passport stamp reward screen",
        "description": (
            "When all arc scenes reach practiced+ tier, show a passport stamp reward screen "
            "with total phrases earned. Elevate the day-arc ribbon to primary progress meter "
            "with a % counter and next-stop nudge. Target: ≥60% of players completing 3 scenes "
            "return within 7 days."
        ),
        "acceptance_criteria": (
            "- Passport stamp screen fires when all arc scenes hit practiced+\n"
            "- Screen shows total unique phrases earned across arc\n"
            "- Day-arc ribbon visible on main HUD with % complete + next-stop label\n"
            "- Arc completion state persisted across sessions\n"
            "- Stamp screen dismissable; returns to scene menu"
        ),
        "affected_files": [
            "persistence.js", "localStorage.js", "scenes/ja/index.js",
            "src/3d/rewardUi.js", "main.js", "scene_manager.js", "config/constants.js"
        ],
        "status": "active",
    },
    # ── lang_007 split ──
    {
        "id":      "pbi_lang_stt_abstraction",
        "epic_id": "epic_lang_stt",
        "project": "lang",
        "title":   "STT provider abstraction layer",
        "description": (
            "Wrap webkitSpeechRecognition and OpenAI Whisper behind a single src/stt.js "
            "provider interface. All STT calls in main.js and scenarioRuntime go through "
            "this module. Provider selected via config/constants.js. "
            "This is approval_required — touches core speech pipeline."
        ),
        "acceptance_criteria": (
            "- src/stt.js exports: start(), stop(), onResult(cb), onError(cb), provider name\n"
            "- webkitSpeechRecognition adapter works identically to current inline usage\n"
            "- OpenAI Whisper adapter present but gated behind STT_PROVIDER constant\n"
            "- All direct webkitSpeechRecognition references removed from main.js\n"
            "- Existing STT behavior unchanged (no regressions in grading)"
        ),
        "affected_files": [
            "main.js", "src/engine/scenarioRuntime.js", "src/engine/grading.js",
            "config/constants.js"
        ],
        "status": "active",
    },
    # ── orchestrator_006 split ──
    {
        "id":      "pbi_orch_env_paths",
        "epic_id": "epic_orch_infrastructure",
        "project": "orchestrator",
        "title":   "Env-based repo paths",
        "description": (
            "Move REPO_PATHS in config.py from hardcoded absolute paths to "
            "os.environ.get() calls with sensible defaults, loading from a "
            "gitignored .env.local file. No new dependencies."
        ),
        "acceptance_criteria": (
            "- Each path (LANG_REPO_PATH, MERIDIAN_REPO_PATH, etc.) reads from env var\n"
            "- .env.local added to .gitignore\n"
            "- .env.example updated with all new vars and example values\n"
            "- Orchestrator starts cleanly with or without .env.local present"
        ),
        "affected_files": ["config.py", ".env.example", ".gitignore"],
        "status": "active",
    },
    # ── orchestrator_008 split ──
    {
        "id":      "pbi_orch_rejection_loop",
        "epic_id": "epic_orch_infrastructure",
        "project": "orchestrator",
        "title":   "Rejection feedback loop",
        "description": (
            "After Discord 'reject <task_id> <reason>', store the reason and inject it "
            "into the retry prompt so MiniMax understands what was wrong. "
            "Closes the quality feedback loop for human-reviewed tasks."
        ),
        "acceptance_criteria": (
            "- reject command accepts optional reason string\n"
            "- Reason stored in tasks.rejection_reason column\n"
            "- On requeue, rejection_reason injected into Ollama prompt revision\n"
            "- Discord shows confirmation with reason echoed back\n"
            "- Works for both single and bulk rejects"
        ),
        "affected_files": [
            "agents/commands.py", "task_queue.py", "executor.py"
        ],
        "status": "active",
    },
]

for p in pbis:
    ok = tq.add_pbi(p)
    print(f"PBI {'added' if ok else 'exists'}: {p['id']}")

# ─────────────────────────────────────────────────────────────────────────────
# SPLIT TASKS
# ─────────────────────────────────────────────────────────────────────────────

now = datetime.now().isoformat()

tasks = [
    # ── pbi_lang_srs_gate (replaces lang_001) ──
    {
        "id":             "lang_010",
        "project":        "lang",
        "pbi_id":         "pbi_lang_srs_gate",
        "description":    "Add SRS gate persistence fields: srs_due[] array and practiced_tier tracking per scene to persistence.js and localStorage.js. Add PRACTICED_THRESHOLD constant to config/constants.js.",
        "complexity":     "low",
        "effort_category":"feature",
        "perspective":    "pedagogy_expert",
        "rationale":      "Data layer first — gate UI and wiring depend on these fields.",
        "depends_on":     [],
        "priority":       1,
    },
    {
        "id":             "lang_011",
        "project":        "lang",
        "pbi_id":         "pbi_lang_srs_gate",
        "description":    "Build srsGateUi.js: display 3 due phrases with furigana overlay, mic prompt per phrase, pass/fail feedback pill. Accepts phrase list and onComplete(results) callback. No scene coupling.",
        "complexity":     "medium",
        "effort_category":"feature",
        "perspective":    "mobile_ux_designer",
        "rationale":      "UI component in isolation — easier to review and retry independently.",
        "depends_on":     ["lang_010"],
        "priority":       2,
    },
    {
        "id":             "lang_012",
        "project":        "lang",
        "pbi_id":         "pbi_lang_srs_gate",
        "description":    "Wire SRS gate into scene_manager.js: before scene HUD loads, check practiced_tier; if practiced+, fetch 3 due phrases from srs_due[], launch srsGateUi, await completion, then unlock main scene. Non-practiced scenes show panel with skip option.",
        "complexity":     "medium",
        "effort_category":"feature",
        "perspective":    "pedagogy_expert",
        "rationale":      "Integration step — depends on persistence fields and UI component.",
        "depends_on":     ["lang_011"],
        "priority":       3,
    },

    # ── pbi_lang_passport_stamp (replaces lang_005) ──
    {
        "id":             "lang_013",
        "project":        "lang",
        "pbi_id":         "pbi_lang_passport_stamp",
        "description":    "Add arc_completion tracking to persistence.js: store per-scene practiced+ status and arc_complete flag. Add helper allScenesAtPracticed(scenes) to scenes/ja/index.js that returns bool + total unique phrases earned.",
        "complexity":     "low",
        "effort_category":"feature",
        "perspective":    "pedagogy_expert",
        "rationale":      "Data layer before UI — stamp screen and ribbon both read from this.",
        "depends_on":     [],
        "priority":       1,
    },
    {
        "id":             "lang_014",
        "project":        "lang",
        "pbi_id":         "pbi_lang_passport_stamp",
        "description":    "Extend src/3d/rewardUi.js: add renderPassportStamp(phrasesEarned) function that shows stamp graphic, phrase count, and dismiss button. Add arc ribbon component showArcRibbon(pct, nextStop) to same file.",
        "complexity":     "medium",
        "effort_category":"feature",
        "perspective":    "mobile_ux_designer",
        "rationale":      "UI components in isolation before wiring to game loop.",
        "depends_on":     ["lang_013"],
        "priority":       2,
    },
    {
        "id":             "lang_015",
        "project":        "lang",
        "pbi_id":         "pbi_lang_passport_stamp",
        "description":    "Wire arc completion into main.js and scene_manager.js: after each scene completion check allScenesAtPracticed(); if true fire renderPassportStamp. Mount showArcRibbon on scene menu with live % and next-stop label.",
        "complexity":     "medium",
        "effort_category":"feature",
        "perspective":    "game_designer",
        "rationale":      "Final integration — depends on persistence and UI components.",
        "depends_on":     ["lang_014"],
        "priority":       3,
    },

    # ── pbi_lang_stt_abstraction (replaces lang_007) ──
    {
        "id":             "lang_016",
        "project":        "lang",
        "pbi_id":         "pbi_lang_stt_abstraction",
        "description":    "Create src/stt.js: export start(lang), stop(), onResult(cb), onError(cb), providerName. Implement webkitSpeechRecognition adapter as default. Read STT_PROVIDER from config/constants.js. Add STT_PROVIDER='webkit' to constants.",
        "complexity":     "medium",
        "effort_category":"scaffold",
        "perspective":    "engineering_architect",
        "rationale":      "Provider module first — main.js migration depends on it existing.",
        "depends_on":     [],
        "priority":       1,
    },
    {
        "id":             "lang_017",
        "project":        "lang",
        "pbi_id":         "pbi_lang_stt_abstraction",
        "description":    "Replace all direct webkitSpeechRecognition usage in main.js and src/engine/scenarioRuntime.js with calls to src/stt.js. No behaviour changes — purely a refactor. Validate with node -e require on all modified files.",
        "complexity":     "medium",
        "effort_category":"refactor",
        "perspective":    "engineering_architect",
        "rationale":      "Migration of existing call sites. approval_required — touches core speech pipeline.",
        "depends_on":     ["lang_016"],
        "priority":       2,
        "approval_required": True,
    },

    # ── pbi_orch_env_paths (replaces orchestrator_006) ──
    {
        "id":             "orchestrator_009",
        "project":        "orchestrator",
        "pbi_id":         "pbi_orch_env_paths",
        "description":    "Move REPO_PATHS in config.py to os.environ.get() with HOME-based defaults. Parse .env.local manually at top of config.py (no python-dotenv). Add .env.local to .gitignore. Update .env.example with LANG_REPO_PATH, MERIDIAN_REPO_PATH, RTS_REPO_PATH, GAMMA_REPO_PATH, NINJA_REPO_PATH, TAX_REPO_PATH.",
        "complexity":     "low",
        "effort_category":"refactor",
        "perspective":    "engineering_architect",
        "rationale":      "Single-file change — simpler than the original high-complexity estimate.",
        "depends_on":     [],
        "priority":       1,
    },

    # ── pbi_orch_rejection_loop (replaces orchestrator_008) ──
    {
        "id":             "orchestrator_010",
        "project":        "orchestrator",
        "pbi_id":         "pbi_orch_rejection_loop",
        "description":    "Update agents/commands.py: extend _handle_reject() to accept optional reason after task ID ('reject lang_001 wrong files'). Store reason via task_queue.mark_rejected(task, reason). Echo reason in Discord confirmation.",
        "complexity":     "low",
        "effort_category":"feature",
        "perspective":    "engineering_architect",
        "rationale":      "Discord interface change — standalone, no executor dependency.",
        "depends_on":     [],
        "priority":       1,
    },
    {
        "id":             "orchestrator_011",
        "project":        "orchestrator",
        "pbi_id":         "pbi_orch_rejection_loop",
        "description":    "Update executor.py revise_execution_prompt(): load task.rejection_reason from DB if present and prepend 'PREVIOUS HUMAN REJECTION: <reason>' block before the failure issues list. Ensures MiniMax sees human feedback on retry.",
        "complexity":     "low",
        "effort_category":"feature",
        "perspective":    "engineering_architect",
        "rationale":      "Executor change — depends on rejection_reason being stored (orchestrator_010).",
        "depends_on":     ["orchestrator_010"],
        "priority":       2,
    },
]

inserted = 0
for t in tasks:
    pbi_id = t.pop("pbi_id", None)
    ok = tq.add_task(t)
    if ok and pbi_id:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tasks SET pbi_id=? WHERE id=?", (pbi_id, t["id"]))
        conn.commit()
        conn.close()
    print(f"Task {'added' if ok else 'exists'}: {t['id']}" + (f" → {pbi_id}" if pbi_id else ""))
    if ok:
        inserted += 1

print(f"\n{inserted}/{len(tasks)} tasks inserted")

# ─────────────────────────────────────────────────────────────────────────────
# CANCEL ORIGINALS
# ─────────────────────────────────────────────────────────────────────────────

originals_to_cancel = ["lang_001", "lang_005", "lang_007", "orchestrator_006", "orchestrator_008"]
import sqlite3
conn = sqlite3.connect(DB_PATH)
for tid in originals_to_cancel:
    row = conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
    if row and row[0] == "queued":
        conn.execute(
            "UPDATE tasks SET status='failed', notes=? WHERE id=?",
            (f"cancelled: split into PBI tasks", tid)
        )
        print(f"Cancelled: {tid}")
    elif row:
        print(f"Skipped cancel {tid} (status={row[0]})")
    else:
        print(f"Not found: {tid}")
conn.commit()
conn.close()

print("\nDone.")
