"""
task_generator.py
Council-based task generation using sequential single-perspective MiniMax API calls.

ARCHITECTURE (see ORCHESTRATOR_CONTEXT.md):
  Ollama:  digest prose, execution prompt writing, quality gate evaluation
  MiniMax: council task generation (perspective calls + merge), all code generation

WHY MiniMax for task generation (not Ollama):
  - 30B models break multi-persona debate formats (format adherence degrades past ~2 advisors)
  - Ollama default context is 2048 tokens; council prompt alone exceeds this
  - Generation runs ~3-4x/night at ~5k tokens/run = <$0.01/run — negligible cost
  - Quality difference is large; cost difference is not

COUNCIL APPROACH (sequential, not debate):
  1. Select 3 relevant perspectives for this project + sprint phase
  2. One focused MiniMax call per perspective → 3 task proposals each (structured text)
  3. One final MiniMax merge call → deduplicated JSON task array
  Sequential independent proposals then merge > debate format for reliability
"""

import os
import json
import time
import uuid
import logging
import requests
from datetime import datetime
from pathlib import Path

from core.task_queue import TaskQueue
from core.executor   import load_context
from config     import (
    MINIMAX_API_BASE, MINIMAX_MODEL, REPO_PATHS, BASE_DIR, DB_PATH,
    PERSPECTIVE_PROJECT_MAP, COUNCIL_TEMPERATURE,
)

_personas_env = os.environ.get("ORCHESTRATOR_PERSONAS_DIR")
PERSONAS_DIR  = Path(_personas_env) if _personas_env else BASE_DIR / "agents" / "personas" / "domain"

log = logging.getLogger(__name__)

MINIMAX_CHAT_MODEL = os.environ.get("MINIMAX_CHAT_MODEL", MINIMAX_MODEL)

# ── PHASE CONFIG ──────────────────────────────────────────────────────────────

PHASE_WEIGHTS = {
    "architecture": {
        "prioritize":   ["scaffold", "docs"],
        "deprioritize": ["bugfix", "gap-fill"],
        "note": "Focus on system structure and foundations. No user-facing features yet.",
    },
    "feature": {
        "prioritize":   ["feature", "test"],
        "deprioritize": ["docs", "gap-fill"],
        "note": "Build user-facing functionality. Write tests alongside each feature.",
    },
    "polish": {
        "prioritize":   ["test", "bugfix", "docs"],
        "deprioritize": ["scaffold", "refactor"],
        "note": "Stabilize and refine existing features. No new scope.",
    },
    "demo_prep": {
        "prioritize":   ["feature", "bugfix"],
        "deprioritize": ["refactor", "test", "docs"],
        "note": "Only tasks that improve the visible demo. Cut everything that doesn't show.",
    },
    "maintenance": {
        "prioritize":   ["bugfix", "test", "docs"],
        "deprioritize": ["feature", "scaffold"],
        "note": "Keep the system healthy between sprints.",
    },
}

# Perspective priority order per phase — intersected with project's available perspectives
PHASE_PERSPECTIVE_PRIORITY = {
    "architecture": [
        "engineering_architect", "systems_architect", "devops", "security_engineer",
    ],
    "feature": [
        "product_manager", "mobile_ux_designer", "game_designer",
        "speech_linguist", "engineering_architect",
    ],
    "polish": [
        "qa_tester", "game_feel_engineer", "speech_linguist",
        "pedagogy_expert", "product_manager",
    ],
    "demo_prep": [
        "product_manager", "game_designer", "mobile_ux_designer", "client_success",
    ],
    "maintenance": [
        "qa_tester", "devops", "security_engineer", "engineering_architect",
    ],
}


# ── MINIMAX CLIENT ─────────────────────────────────────────────────────────────

def _minimax_chat(
    messages: list,
    json_mode: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> str:
    """
    Direct MiniMax chat/completions call. Returns content string.
    Raises on HTTP errors — callers handle retries.
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise EnvironmentError("MINIMAX_API_KEY not set")

    payload: dict = {
        "model":       MINIMAX_CHAT_MODEL,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    resp = requests.post(
        f"{MINIMAX_API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=600,
    )
    resp.raise_for_status()
    data = resp.json()
    log.debug(f"MiniMax usage: {data.get('usage', {})}")
    msg = data["choices"][0]["message"]
    # MiniMax-M3 may emit output in reasoning_content when content is empty
    content = msg.get("content") or msg.get("reasoning_content") or ""
    # Strip thinking tags if present
    if "</think>" in content:
        content = content.split("</think>", 1)[-1].strip()
    return content


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _load_context(project: str) -> str:
    """Delegate to executor.load_context using absolute REPO_PATHS from config."""
    return load_context(project, REPO_PATHS, max_chars=2400)


def _load_persona(perspective: str) -> str:
    """
    Load domain expert persona from personas/domain/{perspective}.md.
    Falls back to bare role string if file not found.
    Returns the persona content to inject into the perspective system prompt.
    """
    persona_file = PERSONAS_DIR / f"{perspective}.md"
    if persona_file.exists():
        return persona_file.read_text().strip()
    log.debug(f"No persona file for {perspective} — using bare role string")
    return f"You are a {perspective.replace('_', ' ')}."


def _select_perspectives(project: str, sprint_phase: str, max_count: int = 3) -> list:
    """Return the most relevant perspectives for this project + phase."""
    project_perspectives = {
        p for p, projects in PERSPECTIVE_PROJECT_MAP.items()
        if project in projects
    }
    priority_order = PHASE_PERSPECTIVE_PRIORITY.get(
        sprint_phase, list(PERSPECTIVE_PROJECT_MAP.keys())
    )
    ordered = [p for p in priority_order if p in project_perspectives]
    # Fill remaining slots with any available perspective not yet included
    for p in project_perspectives:
        if p not in ordered:
            ordered.append(p)
    return ordered[:max_count]


def _format_task_list(tasks: list) -> str:
    if not tasks:
        return "None."
    return "\n".join(f"- {t['description']}" for t in tasks[:10])


def _load_active_pbis(project: str) -> list:
    """Load active PBIs for this project with their queued task summaries."""
    try:
        tq = TaskQueue(DB_PATH)
        epics = tq.all_epics(project=project)
        result = []
        for epic in epics:
            for pbi in tq.pbis_for_epic(epic["id"]):
                if pbi["status"] != "active":
                    continue
                prog  = tq.pbi_progress(pbi["id"])
                tasks = tq.tasks_for_pbi(pbi["id"])
                queued_descs = [t["description"] for t in tasks if t["status"] == "queued"]
                result.append({
                    "epic":       epic["name"],
                    "pbi_id":     pbi["id"],
                    "title":      pbi["title"],
                    "queued":     prog.get("queued", 0),
                    "completed":  prog.get("completed", 0),
                    "total":      prog.get("total", 0),
                    "task_descs": queued_descs,
                })
        return result
    except Exception as e:
        log.warning(f"[{project}] Could not load PBIs: {e}")
        return []


def _format_pbi_summary(pbis: list) -> str:
    """Render active PBIs as a concise block for prompt injection."""
    if not pbis:
        return "None — all work is in the flat task queue."
    lines = []
    for p in pbis:
        prog = f"{p['completed']}/{p['total']} tasks done"
        lines.append(f"  [{p['epic']}] {p['title']} ({prog})")
        for desc in p["task_descs"][:3]:
            lines.append(f"    • {desc[:80]}")
    return "\n".join(lines)


# ── PERSPECTIVE CALL ──────────────────────────────────────────────────────────

def _call_perspective(
    perspective:       str,
    project:           str,
    sprint_phase:      str,
    sprint_goal:       str,
    context_summary:   str,
    completed_summary: str,
    pending_summary:   str,
    phase_note:        str,
    pbi_summary:       str = "",
) -> str:
    """
    One MiniMax call per perspective.
    Returns structured text proposals — NOT JSON.
    Structured text is more reliable than asking 30B+ models to produce JSON mid-chain.
    JSON extraction happens in the separate merge pass.
    Persona file content (personas/domain/{perspective}.md) is injected as the system prompt.
    """
    persona_content = _load_persona(perspective)
    system = (
        f"{persona_content}\n\n"
        f"You are advising on a software project. Be direct, specific, and stay in your domain. "
        f"Propose only tasks appropriate for the {sprint_phase} phase."
    )

    user = f"""Project: {project}
Sprint phase: {sprint_phase}
Sprint goal: {sprint_goal or 'Not specified — infer from context.'}
Phase guidance: {phase_note}

Current project state:
{context_summary}

Completed this week:
{completed_summary}

Pending review (already in progress — do NOT re-propose):
{pending_summary}

Active PBIs (multi-task features already planned — do NOT propose tasks covered by these):
{pbi_summary or 'None.'}

---
TASK SCOPING RULES:
- Each task must touch at most 3-8 files. If it needs more, it belongs in a PBI — do not propose it.
- Propose only tasks NOT already covered by an active PBI above.
- Prefer tasks that are independently runnable (no implicit prerequisite not yet done).
- If two tasks are naturally sequential, propose only the first one — the second becomes a depends_on task once the first is done.

As a {perspective.replace('_', ' ')}, propose exactly 3 tasks.
Each task must be a single actionable sentence naming the specific files or components involved.

Use this exact format for each task (no deviations):
TASK: <one sentence naming files/components>
COMPLEXITY: low|medium|high
EFFORT: feature|scaffold|test|docs|bugfix|gap-fill|refactor
APPROVAL_REQUIRED: yes|no
RATIONALE: <one sentence — why this matters from your perspective>

Only output the 3 tasks in the format above. No preamble."""

    return _minimax_chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        json_mode=False,
        temperature=COUNCIL_TEMPERATURE,
        max_tokens=900,
    )


# ── MERGE + EXTRACT ───────────────────────────────────────────────────────────

def _merge_and_extract(
    project:      str,
    proposals:    dict,   # {perspective_name: raw_proposal_text}
    target_count: int = 8,
    pbi_summary:  str = "",
) -> list:
    """
    Final MiniMax call with json_mode=True.
    Deduplicates proposals across all perspectives, returns validated task dicts.
    Low temperature (0.3) — this is extraction, not creative generation.
    """
    proposals_block = "\n\n".join(
        f"=== {p.upper().replace('_', ' ')} ===\n{text}"
        for p, text in proposals.items()
    )

    system = (
        "You are a technical project manager. Extract and deduplicate task proposals "
        "from multiple expert advisors. Output ONLY valid JSON — no prose, no markdown fences."
    )

    user = f"""Project: {project}

Active PBIs (multi-file features already planned with their own task queues — exclude any task covered by these):
{pbi_summary or 'None.'}

Advisor proposals:
{proposals_block}

---
Select the {target_count} most valuable tasks. Rules:
1. Exclude any task whose scope is covered by an active PBI listed above
2. Deduplicate: merge similar tasks, keep the better-scoped description
3. Balance effort categories — do not return all features with no tests
4. Prefer tasks that name specific files or components — reject vague tasks
5. Each task should touch at most 3-8 files — reject anything broader
6. If two tasks are sequential, set depends_on on the second task using the first task's temporary index (0-based)
7. Preserve the perspective field from whichever advisor proposed the best version

Output a JSON object with key "tasks" — an array of objects each containing:
  description       (string)   — one actionable sentence naming specific files/components
  complexity        (string)   — "low", "medium", or "high"
  effort_category   (string)   — "feature", "scaffold", "test", "docs", "bugfix", "gap-fill", or "refactor"
  perspective       (string)   — advisor perspective that proposed this task
  rationale         (string)   — one sentence why this matters
  priority          (integer)  — 0=critical-path, 1=standard, 2=nice-to-have
  approval_required (boolean)  — true only for auth/security/user-data/client-deploy tasks
  estimated_tokens  (integer)  — scaffold:15000, feature:10000, test:6000, docs:4000, bugfix:8000, gap-fill:3000
  depends_on        (array)    — list of task IDs this depends on; use [] if none (IDs assigned after merge)"""

    raw   = _minimax_chat(
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        json_mode=True,
        temperature=0.3,
        max_tokens=3000,
    )
    data  = json.loads(raw)
    tasks = data.get("tasks", [])

    # Stamp required fields — assign IDs first so depends_on can reference them
    ts       = int(time.time())
    id_map   = {}   # temp 0-based index → real task ID
    for i, t in enumerate(tasks):
        t["project"] = project
        t["id"]      = f"{project}_{ts}_{i:03d}"
        t["status"]  = "queued"
        id_map[i]    = t["id"]

    # Resolve depends_on: MiniMax may return 0-based integer indexes or real IDs
    for t in tasks:
        raw_deps = t.get("depends_on", [])
        if not isinstance(raw_deps, list):
            raw_deps = []
        resolved = []
        for dep in raw_deps:
            if isinstance(dep, int) and dep in id_map:
                resolved.append(id_map[dep])   # integer index → real ID
            elif isinstance(dep, str) and dep:
                resolved.append(dep)            # already a string ID — keep as-is
            # drop anything else (None, out-of-range index, etc.)
        t["depends_on"] = resolved
        t.setdefault("blocks", [])

    return tasks


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def generate_tasks_for_project(
    project:            str,
    sprint_phase:       str       = "feature",
    sprint_goal:        str       = "",
    completed_tasks:    list      = None,
    pending_tasks:      list      = None,
    target_count:       int       = 8,
    perspectives_count: int       = 3,
) -> list:
    """
    Full council pipeline for one project.
    Returns a list of task dicts ready to insert into TaskQueue.

    Calls: perspectives_count MiniMax calls (proposals) + 1 merge call.
    Typical total: ~5000 tokens ≈ $0.01 at MiniMax M3 promo rates.
    """
    completed_tasks = completed_tasks or []
    pending_tasks   = pending_tasks   or []

    context_summary   = _load_context(project)
    completed_summary = _format_task_list(completed_tasks[-10:]) if completed_tasks else "Nothing completed yet."

    # Load active PBIs — council must not re-propose tasks already covered by them
    active_pbis  = _load_active_pbis(project)
    pbi_summary  = _format_pbi_summary(active_pbis)

    # Pending summary includes both review items AND queued PBI tasks so council
    # sees the full picture of in-flight work and doesn't duplicate it
    pbi_tasks       = [{"description": d} for p in active_pbis for d in p["task_descs"]]
    all_pending     = (pending_tasks or []) + pbi_tasks
    pending_summary = _format_task_list(all_pending[:15]) if all_pending else "No pending items."

    if active_pbis:
        log.info(f"[{project}] {len(active_pbis)} active PBIs injected into council context")

    phase_config = PHASE_WEIGHTS.get(sprint_phase, PHASE_WEIGHTS["feature"])
    phase_note   = (
        f"{phase_config['note']} "
        f"Prioritize: {', '.join(phase_config['prioritize'])}. "
        f"Avoid: {', '.join(phase_config['deprioritize'])}."
    )

    perspectives = _select_perspectives(project, sprint_phase, max_count=perspectives_count)
    log.info(f"[{project}] Council: {perspectives} | Phase: {sprint_phase}")

    # Sequential perspective calls — each independent, no debate format
    proposals = {}
    for perspective in perspectives:
        log.info(f"[{project}] Perspective: {perspective}")
        try:
            proposals[perspective] = _call_perspective(
                perspective       = perspective,
                project           = project,
                sprint_phase      = sprint_phase,
                sprint_goal       = sprint_goal,
                context_summary   = context_summary,
                completed_summary = completed_summary,
                pending_summary   = pending_summary,
                phase_note        = phase_note,
                pbi_summary       = pbi_summary,
            )
        except Exception as e:
            log.error(f"[{project}] {perspective} call failed: {e}")

    if not proposals:
        log.error(f"[{project}] All perspective calls failed — no tasks generated")
        return []

    log.info(f"[{project}] Merging {len(proposals)} proposals → {target_count} tasks")
    try:
        tasks = _merge_and_extract(project, proposals, target_count=target_count, pbi_summary=pbi_summary)
        log.info(f"[{project}] Generated {len(tasks)} tasks")
        return tasks
    except (json.JSONDecodeError, KeyError) as e:
        log.error(f"[{project}] Merge/extract failed: {e}")
        return []


def generate_tasks_all_projects(
    task_queue:       TaskQueue,
    enabled_projects: list,
    sprint_phases:    dict,   # {project: phase_string}
    sprint_goals:     dict,   # {project: goal_string}
    threshold:        int = 10,
) -> int:
    """
    For each enabled project whose unblocked task count is below threshold,
    run the council pipeline and insert new tasks into the queue.
    Returns total tasks inserted.
    """
    total = 0
    for project in enabled_projects:
        unblocked = task_queue.total_unblocked(projects=[project])
        if unblocked >= threshold:
            log.info(f"[{project}] {unblocked} unblocked tasks — skipping generation")
            continue

        log.info(f"[{project}] Only {unblocked} unblocked tasks — running council")

        completed = [t for t in task_queue.get_completed_today() if t["project"] == project]
        pending   = [t for t in task_queue.get_pending_review()  if t["project"] == project]

        tasks = generate_tasks_for_project(
            project             = project,
            sprint_phase        = sprint_phases.get(project, "feature"),
            sprint_goal         = sprint_goals.get(project, ""),
            completed_tasks     = completed,
            pending_tasks       = pending,
        )

        inserted = sum(1 for t in tasks if task_queue.add_task(t))
        log.info(f"[{project}] Inserted {inserted}/{len(tasks)} new tasks")
        total += inserted

    return total


# ── GAP-FILL ─────────────────────────────────────────────────────────────────

def get_gap_fill_tasks() -> list[dict]:
    """
    Fallback tasks returned when the main queue runs dry.
    These are safe, low-stakes tasks that are always valid to run.
    Add entries here when a project needs a standing fallback.
    """
    import uuid
    return [
        {
            "id":               f"gap_lang_{uuid.uuid4().hex[:8]}",
            "project":          "lang",
            "priority":         2,
            "description":      "Expand randomization pools for any completed language scenes (add 5+ variants to each pool)",
            "approval_required": False,
            "complexity":       "low",
            "rationale":        "More dialogue variety reduces repetition for learners",
            "effort_category":  "gap-fill",
            "perspective":      "speech_linguist",
            "depends_on":       [],
        },
        {
            "id":               f"gap_meridian_{uuid.uuid4().hex[:8]}",
            "project":          "meridian",
            "priority":         2,
            "description":      "Generate JSDoc comments for all undocumented exported functions in meridian-mobile/src",
            "approval_required": False,
            "complexity":       "low",
            "rationale":        "Improves context quality for subsequent tasks",
            "effort_category":  "docs",
            "perspective":      "engineering_architect",
            "depends_on":       [],
        },
        {
            "id":               f"gap_rts_{uuid.uuid4().hex[:8]}",
            "project":          "rts",
            "priority":         2,
            "description":      "Generate XML summary comments for all public C# methods missing documentation",
            "approval_required": False,
            "complexity":       "low",
            "rationale":        "Unity editor tooling uses XML docs for inspector tooltips",
            "effort_category":  "docs",
            "perspective":      "engineering_architect",
            "depends_on":       [],
        },
    ]

    return total
