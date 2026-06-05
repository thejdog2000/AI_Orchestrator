"""
task_generator.py
Council-based task generation using sequential single-perspective MiniMax API calls.

ARCHITECTURE (revised — see ORCHESTRATOR_CONTEXT.md):
  Ollama:  digest prose, Aider prompt writing only
  MiniMax: council task generation + high-complexity quality gates

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

from task_queue import TaskQueue, PERSPECTIVE_PROJECT_MAP
from executor   import load_context
from config     import MINIMAX_API_BASE, MINIMAX_MODEL, REPO_PATHS

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
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    log.debug(f"MiniMax usage: {data.get('usage', {})}")
    return data["choices"][0]["message"]["content"]


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _load_context(project: str) -> str:
    """Delegate to executor.load_context using absolute REPO_PATHS from config."""
    return load_context(project, REPO_PATHS, max_chars=2400)


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
) -> str:
    """
    One MiniMax call per perspective.
    Returns structured text proposals — NOT JSON.
    Structured text is more reliable than asking 30B+ models to produce JSON mid-chain.
    JSON extraction happens in the separate merge pass.
    """
    system = (
        f"You are a {perspective.replace('_', ' ')} advising on a software project. "
        f"Be direct, specific, and stay in your domain. "
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

---
As a {perspective.replace('_', ' ')}, propose exactly 3 tasks.
Each task must be a single actionable sentence scoped to the current sprint phase.

Use this exact format for each task (no deviations):
TASK: <one sentence>
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
        temperature=0.75,
        max_tokens=900,
    )


# ── MERGE + EXTRACT ───────────────────────────────────────────────────────────

def _merge_and_extract(
    project:      str,
    proposals:    dict,   # {perspective_name: raw_proposal_text}
    target_count: int = 8,
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

Advisor proposals:
{proposals_block}

---
Select the {target_count} most valuable tasks. Rules:
1. Deduplicate: merge similar tasks, keep the better-scoped description
2. Balance effort categories — do not return all features with no tests
3. Prefer tasks with clear, measurable scope over vague ones
4. Preserve the perspective field from whichever advisor proposed the best version

Output a JSON object with key "tasks" — an array of objects each containing:
  description       (string)  — one actionable sentence
  complexity        (string)  — "low", "medium", or "high"
  effort_category   (string)  — "feature", "scaffold", "test", "docs", "bugfix", "gap-fill", or "refactor"
  perspective       (string)  — advisor perspective that proposed this task
  rationale         (string)  — one sentence why this matters
  priority          (integer) — 0=critical-path, 1=standard, 2=nice-to-have
  approval_required (boolean) — true only for auth/security/user-data/client-deploy tasks
  estimated_tokens  (integer) — scaffold:15000, feature:10000, test:6000, docs:4000, bugfix:8000, gap-fill:3000"""

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

    # Stamp required fields
    ts = int(time.time())
    for i, t in enumerate(tasks):
        t["project"]    = project
        t["id"]         = f"{project}_{ts}_{i:03d}"
        t["status"]     = "queued"
        t.setdefault("depends_on", [])
        t.setdefault("blocks",     [])

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

    context_summary    = _load_context(project)
    completed_summary  = _format_task_list(completed_tasks[-10:]) if completed_tasks else "Nothing completed yet."
    pending_summary    = _format_task_list(pending_tasks[:10])    if pending_tasks   else "No pending review items."

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
            )
        except Exception as e:
            log.error(f"[{project}] {perspective} call failed: {e}")

    if not proposals:
        log.error(f"[{project}] All perspective calls failed — no tasks generated")
        return []

    log.info(f"[{project}] Merging {len(proposals)} proposals → {target_count} tasks")
    try:
        tasks = _merge_and_extract(project, proposals, target_count=target_count)
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
