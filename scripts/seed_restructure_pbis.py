"""Seed restructure PBIs for lang and orchestrator repos."""
import sys, sqlite3
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from task_queue import TaskQueue
from config import DB_PATH

tq = TaskQueue(DB_PATH)

# ── EPICS ──────────────────────────────────────────────────────────────────────
epics = [
    {"id": "epic_lang_infra",  "project": "lang",        "name": "Project Infrastructure",  "color": "#8b5cf6"},
    {"id": "epic_orch_infra",  "project": "orchestrator","name": "Orchestrator Infrastructure","color": "#6366f1"},
]
for e in epics:
    ok = tq.add_epic(e)
    print(f"Epic {'added' if ok else 'exists'}: {e['id']}")

# ── PBIs ───────────────────────────────────────────────────────────────────────
pbis = [
    {
        "id":      "pbi_lang_restructure",
        "epic_id": "epic_lang_infra",
        "project": "lang",
        "title":   "File/folder restructure",
        "description": (
            "Reorganise the lang repo into a clean, navigable structure. "
            "Separate pure logic (testable in Node without a browser) from UI/WebGL code. "
            "Move scene data into content/, consolidate tests/ into a single root. "
            "Keep main.js as a thin bootstrapper only."
        ),
        "acceptance_criteria": (
            "- src/core/ contains engine/, stt/, persistence/ with zero UI imports\n"
            "- src/ui/ contains 3d/ and components/\n"
            "- content/ja/ holds all scene data files (moved from scenes/ja/)\n"
            "- tests/ is the single test root with unit/ and scenes/ subdirs\n"
            "- All imports updated — node -e require passes on every module\n"
            "- No files remain in repo root except main.js, index.html, package.json, config files"
        ),
        "affected_files": [
            "main.js", "scene_manager.js", "localStorage.js", "persistence.js",
            "reward_screen.js", "components/beat_renderer.js", "components/feedback.js",
            "src/engine/scenarioEngine.js", "src/engine/grading.js",
            "src/engine/scenarioRuntime.js", "src/engine/scenarioValidation.js",
            "src/engine/loadJsonScene.js", "src/3d/srsUi.js", "src/3d/rewardUi.js",
            "src/3d/scenes.js", "src/stt.js", "src/scenarios/index.js",
            "scenes/ja/index.js", "scenes/ja/_session.js", "config/constants.js",
            "utils/vocab_overlap.js", "package.json"
        ],
        "status": "active",
    },
    {
        "id":      "pbi_orch_restructure",
        "epic_id": "epic_orch_infra",
        "project": "orchestrator",
        "title":   "File/folder restructure",
        "description": (
            "Reorganise the Orchestrator repo: group the 15 root Python files into "
            "core/, analytics/, dashboard/, scripts/. Move runtime data dirs under data/. "
            "Consolidate duplicate root shims (o.py, orchestrator_bot.py) and personas/. "
            "Single config.py path update moves all runtime data."
        ),
        "acceptance_criteria": (
            "- core/ contains executor, task_queue, task_generator, notify, spend\n"
            "- analytics/ contains metrics, retro_generator, digests, sprint_manager\n"
            "- dashboard/ contains generator.py, server.py, output/ (gitignored)\n"
            "- scripts/ contains approve, validate, git_watcher + existing scripts\n"
            "- docs/ contains ARCHITECTURE.md, AGENT_TASK_GEN.md, ORCHESTRATOR_CONTEXT.md\n"
            "- data/ is the single runtime data root (config.py updated)\n"
            "- Root shim files removed; agents/ is the canonical location\n"
            "- personas/ at root merged into agents/personas/review/\n"
            "- All imports updated; orchestrator_main.py starts cleanly\n"
            "- .gitignore updated for .fuse_hidden*, data/ runtime dirs, __pycache__"
        ),
        "affected_files": [
            "config.py", "orchestrator_main.py",
            "executor.py", "task_queue.py", "task_generator.py", "notify.py", "spend.py",
            "dashboard_generator.py", "dashboard_server.py",
            "metrics.py", "retro_generator.py", "digests.py", "sprint_manager.py",
            "approve.py", "validate.py", "git_watcher.py",
            "o.py", "orchestrator_bot.py",
            "agents/orchestrator_bot.py", "agents/o.py", "agents/commands.py",
            ".gitignore"
        ],
        "status": "active",
    },
]
for p in pbis:
    ok = tq.add_pbi(p)
    print(f"PBI {'added' if ok else 'exists'}: {p['id']}")

# ── TASKS ──────────────────────────────────────────────────────────────────────
tasks = [
    # ── lang restructure ──
    {
        "id": "lang_018", "project": "lang", "pbi_id": "pbi_lang_restructure",
        "description": "Create new directory structure: src/core/engine/, src/core/persistence/, src/core/stt/, src/ui/3d/, src/ui/components/, content/ja/, tests/unit/, tests/scenes/. Move all files to new locations. Update package.json test glob to tests/**/*.test.js.",
        "complexity": "medium", "effort_category": "refactor", "perspective": "engineering_architect",
        "rationale": "Physical move first — all imports will break intentionally, fixed in next task.",
        "depends_on": [], "priority": 1, "approval_required": True,
    },
    {
        "id": "lang_019", "project": "lang", "pbi_id": "pbi_lang_restructure",
        "description": "Update all require/import paths throughout codebase to reflect new directory structure. Validate every module with node -e require. Update CONTEXT.md with new structure map.",
        "complexity": "medium", "effort_category": "refactor", "perspective": "engineering_architect",
        "rationale": "Import fix pass — depends on files being in new locations.",
        "depends_on": ["lang_018"], "priority": 2, "approval_required": True,
    },

    # ── orchestrator restructure ──
    {
        "id": "orchestrator_012", "project": "orchestrator", "pbi_id": "pbi_orch_restructure",
        "description": "Update config.py: change TASKS_DIR, PENDING_DIR, APPROVED_DIR, LOGS_DIR, BACKUPS_DIR, RETROS_DIR, PIPELINE_LOGS_DIR to all resolve under BASE_DIR / 'data' / subdir. Add DASHBOARD_OUTPUT_DIR = BASE_DIR / 'dashboard' / 'output'. Update .gitignore: add data/, dashboard/output/, .fuse_hidden*, __pycache__/, *.pid.",
        "complexity": "low", "effort_category": "refactor", "perspective": "engineering_architect",
        "rationale": "Config change first — all physical moves depend on paths being correct.",
        "depends_on": [], "priority": 1, "approval_required": True,
    },
    {
        "id": "orchestrator_013", "project": "orchestrator", "pbi_id": "pbi_orch_restructure",
        "description": "Move Python files to packages: executor.py, task_queue.py, task_generator.py, notify.py, spend.py → core/. metrics.py, retro_generator.py, digests.py, sprint_manager.py → analytics/. dashboard_generator.py → dashboard/generator.py. dashboard_server.py → dashboard/server.py. approve.py, validate.py, git_watcher.py → scripts/. Add __init__.py to each new package. Move ARCHITECTURE.md, AGENT_TASK_GEN.md, ORCHESTRATOR_CONTEXT.md → docs/. Merge root personas/review/ into agents/personas/review/.",
        "complexity": "medium", "effort_category": "refactor", "perspective": "engineering_architect",
        "rationale": "Physical reorganisation — imports will break, fixed in next task.",
        "depends_on": ["orchestrator_012"], "priority": 2, "approval_required": True,
    },
    {
        "id": "orchestrator_014", "project": "orchestrator", "pbi_id": "pbi_orch_restructure",
        "description": "Update all imports across the codebase to use new package paths (from core.executor import ..., from analytics.metrics import ..., etc.). Update orchestrator_main.py. Remove root shim files o.py and orchestrator_bot.py. Verify orchestrator starts cleanly.",
        "complexity": "medium", "effort_category": "refactor", "perspective": "engineering_architect",
        "rationale": "Import update pass — final step, depends on all files being in new locations.",
        "depends_on": ["orchestrator_013"], "priority": 3, "approval_required": True,
    },
]

inserted = 0
for t in tasks:
    pbi_id = t.pop("pbi_id", None)
    ok = tq.add_task(t)
    if ok and pbi_id:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE tasks SET pbi_id=? WHERE id=?", (pbi_id, t["id"]))
        conn.commit()
        conn.close()
    print(f"Task {'added' if ok else 'exists'}: {t['id']}" + (f" → {pbi_id}" if pbi_id else ""))
    if ok: inserted += 1

print(f"\n{inserted}/{len(tasks)} tasks inserted")
