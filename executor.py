"""
executor.py
Task execution layer: Ollama prompt writing, MiniMax API calls,
file parsing, CONTEXT.md feedback loop, quality gate, retry logic.
"""

import os
import re
import json
import time
import subprocess
import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# notify is imported lazily to avoid circular-import risk at startup.
# Use _notify() wherever you need it.
def _notify():
    import notify as _n
    return _n

# Imported by orchestrator_main — set there, referenced here via module-level vars
# Injected at init time via configure()
_config: dict = {}

def configure(cfg: dict):
    """Called once from orchestrator_main with shared config."""
    _config.update(cfg)


def check_ollama() -> bool:
    """
    Verify Ollama is reachable and required models are loaded.
    Call at startup before scheduler begins — silent failures overnight are worse
    than a loud startup error.
    Returns True if healthy, False if not.
    """
    import requests as _req
    base = cfg.get("OLLAMA_BASE", "http://localhost:11434") if (cfg := _config) else "http://localhost:11434"
    try:
        resp = _req.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        loaded = {m["name"] for m in resp.json().get("models", [])}
        required = {_config.get("OLLAMA_MODEL_CODE", ""), _config.get("OLLAMA_MODEL_DIGEST", "")}
        missing  = {m for m in required if m and m not in loaded}
        if missing:
            log.error(f"Ollama missing required models: {missing}. Run: ollama pull <model>")
            return False
        log.info(f"Ollama healthy — models loaded: {required}")
        return True
    except Exception as e:
        log.error(f"Ollama unreachable at {base}: {e}")
        log.error("Tasks will proceed with degraded prompts until Ollama is available.")
        return False

def _cfg(key: str):
    if not _config:
        raise RuntimeError(
            "executor.configure(CFG) must be called before any executor functions. "
            "Import orchestrator_main or call executor.configure(CFG) explicitly."
        )
    return _config[key]


# ── OLLAMA ────────────────────────────────────────────────────────────────────

def ollama_generate(
    prompt:      str,
    max_tokens:  int   = 1000,
    json_mode:   bool  = False,
    model:       str   = None,
    temperature: float = None,
) -> str:
    """
    Call local Ollama.
      - digest prose      → qwen3:14b  (faster, lighter)
      - execution prompts → qwen3-coder:30b
    num_ctx always 8192 — default 2048 silently truncates.

    temperature guidelines (explicit > Ollama default which varies by model):
      0.1  — evaluation/quality gate (deterministic, not creative)
      0.2  — CONTEXT.md update (factual summarization)
      0.3  — prompt writing (focused, slight variation acceptable)
      0.5  — digest prose (readable variety acceptable)
    """
    model = model or _cfg("OLLAMA_MODEL_CODE")
    try:
        options: dict = {"num_ctx": 8192, "num_predict": max_tokens}
        if temperature is not None:
            options["temperature"] = temperature

        payload: dict = {
            "model":   model,
            "prompt":  prompt,
            "stream":  False,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"
        resp = requests.post(
            f"{_cfg('OLLAMA_BASE')}/api/generate",
            json=payload,
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")
    except Exception as e:
        log.error(f"Ollama error ({model}): {e}")
        return ""


def write_execution_prompt(task: dict, context_md: str) -> str:
    """Ollama writes a focused MiniMax system prompt from task + CONTEXT.md."""
    prompt = (
        f"Write a precise system prompt for an AI code generator.\n"
        f"Specify which files to create or modify. Be concrete. No preamble.\n\n"
        f"TASK: {task['description']}\n"
        f"PROJECT: {task['project']}\n"
        f"CONTEXT:\n{context_md[:2000]}\n\n"
        f"System prompt:"
    )
    result = ollama_generate(prompt, max_tokens=600, temperature=0.3)
    return result.strip() if result else task["description"]


def revise_execution_prompt(task: dict, context_md: str, evaluation: dict) -> str:
    """
    Ollama rewrites the prompt based on quality gate failure reasons.
    Called on attempt 2+ — addresses specific issues from the previous attempt
    rather than blindly repeating the same failed instruction.
    """
    issues    = evaluation.get("issues", [])
    reasoning = evaluation.get("reasoning", "")
    failure   = ", ".join(issues) if issues else reasoning or "unspecified failure"

    prompt = (
        f"The previous attempt at this coding task failed quality review.\n"
        f"Rewrite the system prompt to avoid the specific issues listed below.\n\n"
        f"TASK: {task['description']}\n"
        f"PROJECT: {task['project']}\n"
        f"FAILURE ISSUES: {failure}\n"
        f"CONTEXT:\n{context_md[:1500]}\n\n"
        f"Write an improved system prompt that specifically avoids these issues:"
    )
    result = ollama_generate(prompt, max_tokens=600, temperature=0.3)
    return result.strip() if result else write_execution_prompt(task, context_md)


def evaluate_diff(diff_text: str, task: dict) -> dict:
    """
    Ollama quality gate for low/medium complexity diffs.
    High-complexity diffs skip evaluation and go straight to pending_review.
    json_mode=True prevents markdown wrapping breaking the parser.
    """
    if task.get("complexity") == "high":
        log.info(f"[{task['project']}] High-complexity — skipping Ollama gate")
        return {"score": 7, "pass": True, "issues": [], "reasoning": "High complexity — human review"}

    prompt = (
        f"Evaluate this code diff.\n"
        f"Task: {task['description']}\nProject: {task['project']}\n\n"
        f"Diff (first 2000 chars):\n{diff_text[:2000]}\n\n"
        f'Return JSON: {{"score":0-10,"pass":true/false,"issues":[],"reasoning":""}}'
    )
    raw = ollama_generate(prompt, max_tokens=300, json_mode=True, temperature=0.1)
    try:
        result = json.loads(raw)
        # Validate required keys are present and types are correct
        if not isinstance(result.get("pass"), bool):
            raise ValueError("missing or non-bool 'pass' field")
        return result
    except Exception as e:
        # Fail CLOSED — broken gate must not auto-approve bad output
        log.warning(f"[{task['project']}] Quality gate parse failed ({e}) — failing closed for human review")
        return {"score": 0, "pass": False, "issues": ["quality_gate_parse_failed"],
                "reasoning": f"Ollama returned unparseable response — human review required. Raw: {raw[:200]}"}


# ── CONTEXT.MD FEEDBACK LOOP ──────────────────────────────────────────────────

def update_context_md(task: dict, diff_text: str, repo_path: Path) -> bool:
    """
    After a successful task, Ollama reads diff + old CONTEXT.md and writes
    an updated summary. Closes the feedback loop:

      MiniMax output → Ollama summary → better future prompts

    Non-blocking: logs warning on failure, never fails the task.
    Only writes if Ollama returns something meaningful (>100 chars).
    """
    context_path = repo_path / "CONTEXT.md"
    old_context  = context_path.read_text()[:2000] if context_path.exists() else "(no existing CONTEXT.md)"

    prompt = (
        f"Update this project's CONTEXT.md to reflect completed work.\n"
        f"Keep the total under 2000 tokens. Be factual, no fluff.\n\n"
        f"OLD CONTEXT.md:\n{old_context}\n\n"
        f"COMPLETED TASK: {task['description']}\n\n"
        f"DIFF (what changed):\n{diff_text[:2500]}\n\n"
        f"Update these sections if relevant: Current state, Architecture, "
        f"Completed this week, Known issues, Next tasks.\n"
        f"Output the complete updated CONTEXT.md — nothing else:"
    )

    # Use digest model — CONTEXT.md update is prose summarization, not code generation
    updated = ollama_generate(prompt, max_tokens=1500, model=_cfg("OLLAMA_MODEL_DIGEST"), temperature=0.2)
    if updated and len(updated) > 100:
        context_path.write_text(updated)
        log.info(f"[{task['project']}] CONTEXT.md updated ({len(updated)} chars)")
        return True

    log.warning(f"[{task['project']}] CONTEXT.md update skipped — Ollama returned nothing useful")
    return False


# ── MINIMAX EXECUTION ─────────────────────────────────────────────────────────

_FILE_PATTERN = re.compile(r"<<<FILE:\s*(.+?)>>>\s*\n(.*?)<<<END>>>", re.DOTALL)


def _parse_file_blocks(response: str) -> dict:
    """
    Parse <<<FILE: path>>> ... <<<END>>> blocks from MiniMax response.
    Returns {relative_path: content}.
    """
    return {m.group(1).strip(): m.group(2) for m in _FILE_PATTERN.finditer(response)}


def _safe_write(repo_path: Path, rel_path: str, content: str) -> bool:
    """
    Write a file only if its resolved path is inside repo_path.
    Blocks path traversal attacks (e.g. ../../.env from a hallucinating model).
    Returns True if written, False if blocked.
    """
    try:
        dest = (repo_path / rel_path).resolve()
    except Exception as e:
        log.error(f"Path resolution failed for '{rel_path}': {e}")
        return False

    if not dest.is_relative_to(repo_path.resolve()):
        log.error(f"BLOCKED path traversal attempt: '{rel_path}' → {dest}")
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    return True


def _minimax_chat(
    system:     str,
    user:       str,
    max_tokens: int   = 8000,
    temperature: float = 0.2,
) -> tuple:
    """
    Direct MiniMax chat/completions. Key from env only — never CLI args.
    Returns (content, input_tokens, output_tokens).
    temperature=0.2 default — deterministic code output.
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise EnvironmentError("MINIMAX_API_KEY not set")

    resp = requests.post(
        f"{_cfg('MINIMAX_API_BASE')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model":       _cfg("MINIMAX_MODEL"),
            "messages":    [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": temperature,
            "max_tokens":  max_tokens,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data  = resp.json()
    usage = data.get("usage", {})
    return (
        data["choices"][0]["message"]["content"],
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
    )


def run_minimax_task(task: dict, system_prompt: str = None) -> dict:
    """
    Execute a task via direct MiniMax API.

    system_prompt: if provided (e.g. revised prompt on retry), uses it directly.
                   If None, Ollama generates one from task + CONTEXT.md.

    Returns: {success, diff_path, diff_text, files_written, input_tokens, output_tokens}
    """
    project   = task["project"]
    repo_path = _cfg("REPO_PATHS").get(project)

    if not repo_path or not repo_path.exists():
        return {"success": False, "error": f"repo_not_found:{repo_path}"}
    if not os.environ.get("MINIMAX_API_KEY"):
        return {"success": False, "error": "no_api_key"}

    context_md = load_context(project, _cfg("REPO_PATHS"))
    prompt     = system_prompt or write_execution_prompt(task, context_md)

    user_msg = (
        f"Task: {task['description']}\n\n"
        f"Output every file you create or modify in this exact format:\n"
        f"<<<FILE: relative/path/to/file.ext>>>\n"
        f"<complete file content>\n"
        f"<<<END>>>\n\n"
        f"One block per file. ALL changed files. No prose outside the blocks."
    )

    log.info(f"[{project}] Executing {task['id']} via MiniMax")
    try:
        content, input_tokens, output_tokens = _minimax_chat(prompt, user_msg)
    except EnvironmentError as e:
        return {"success": False, "error": str(e)}
    except requests.HTTPError as e:
        return {"success": False, "error": f"api_http_{e.response.status_code}"}
    except Exception as e:
        log.error(f"[{project}] MiniMax call failed: {e}")
        return {"success": False, "error": str(e)}

    file_blocks = _parse_file_blocks(content)
    if not file_blocks:
        log.warning(f"[{project}] No file blocks for {task['id']}. Preview: {content[:300]}")
        return {"success": False, "error": "no_file_blocks"}

    written = []
    for rel_path, file_content in file_blocks.items():
        if _safe_write(repo_path, rel_path, file_content):
            log.info(f"[{project}] Wrote {rel_path}")
            written.append(rel_path)
        else:
            log.warning(f"[{project}] Skipped unsafe path: {rel_path}")

    if not written:
        log.error(f"[{project}] All file writes blocked for {task['id']} — possible path traversal")
        return {"success": False, "error": "all_writes_blocked"}

    subprocess.run(["git", "add", "--intent-to-add", "."],
                   cwd=repo_path, capture_output=True)
    diff = subprocess.run(["git", "diff"], cwd=repo_path, capture_output=True, text=True)
    diff_text = diff.stdout

    pending_dir = _cfg("PENDING_DIR")
    diff_path   = pending_dir / f"{project}_{task['id']}_{int(time.time())}.diff"
    diff_path.write_text(diff_text or f"# No diff\n# Files: {list(file_blocks)}")

    log.info(f"[{project}] {input_tokens} in / {output_tokens} out")
    return {
        "success":       True,
        "diff_path":     diff_path,
        "diff_text":     diff_text,
        "files_written": list(file_blocks.keys()),
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "system_prompt": prompt,   # pass back for retry revision
    }


# ── AUTO-COMMIT ───────────────────────────────────────────────────────────────

def _auto_commit(task: dict, repo_path: Path) -> str:
    """
    Auto-commit staged changes for a task that passed the quality gate.
    Commit message format: [orchestrator] {project}: {description[:60]} ({perspective})

    Returns the short commit hash, or "" if commit failed.
    This replaces the pending_review accumulation flow for non-approval_required tasks.
    approval_required=True tasks still go to mark_pending_review for Jacob to approve.
    """
    if not repo_path or not repo_path.exists():
        log.error(f"[{task['project']}] Auto-commit skipped — repo_path invalid: {repo_path}")
        return ""

    project     = task["project"]
    description = task.get("description", "")[:60]
    perspective = task.get("perspective", "")
    msg         = f"[orchestrator] {project}: {description} ({perspective})"

    try:
        subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, check=True)
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=repo_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            if "nothing to commit" in result.stdout + result.stderr:
                log.warning(f"[{project}] Auto-commit: nothing to commit for {task['id']}")
                return ""
            log.error(f"[{project}] Auto-commit failed: {result.stderr.strip()}")
            return ""

        # Get short commit hash
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path, capture_output=True, text=True,
        )
        return rev.stdout.strip() if rev.returncode == 0 else ""

    except Exception as e:
        log.error(f"[{project}] Auto-commit exception: {e}")
        return ""


# ── RUN TASK (with retries + feedback) ───────────────────────────────────────

def run_task(task: dict, spend_tracker, task_queue):
    """
    Full task lifecycle: retry loop → quality gate → CONTEXT.md update → queue.
    Called inside per-project lock from orchestrator_main.execute_next_task().
    """
    project          = task["project"]
    repo_path        = _cfg("REPO_PATHS").get(project)
    max_retries      = _cfg("MAX_RETRIES")
    backoff          = _cfg("RETRY_BACKOFF_SECONDS")
    pending_dir      = _cfg("PENDING_DIR")
    enabled_projects = _cfg("ENABLED_PROJECTS")
    refill_threshold = _cfg("QUEUE_REFILL_THRESHOLD")

    result     = None
    evaluation = {}
    context_md = load_context(project, _cfg("REPO_PATHS"))

    # Notify Discord that this task is starting
    _notify().task_started(task)

    for attempt in range(max_retries):
        # Clean working tree before every attempt
        if repo_path and repo_path.exists():
            subprocess.run(["git", "checkout", "--", "."], cwd=repo_path, capture_output=True)

        # First attempt: fresh prompt. Subsequent: revised prompt targeting failure reasons.
        if attempt == 0:
            system_prompt = write_execution_prompt(task, context_md)
        else:
            log.info(f"[{project}] Attempt {attempt + 1}: revising prompt based on failure")
            system_prompt = revise_execution_prompt(task, context_md, evaluation)

        result = run_minimax_task(task, system_prompt=system_prompt)

        if result["success"]:
            break

        wait = backoff[min(attempt, len(backoff) - 1)]
        log.warning(f"[{project}] Attempt {attempt + 1} failed: {result.get('error')}. "
                    f"Retrying in {wait}s...")

        # Run quality gate on failed diff to get failure reasons for next revision
        if result.get("diff_text"):
            evaluation = evaluate_diff(result["diff_text"], task)
        time.sleep(wait)

    # All attempts exhausted
    if not result or not result["success"]:
        error = str(result.get("error", "unknown")) if result else "no_result"
        log.error(f"[{project}] {task['id']} failed after {max_retries} attempts")
        fail_path = pending_dir / f"FAILED_{project}_{task['id']}.json"
        fail_path.write_text(json.dumps({"task": task, "result": result}, indent=2))
        task_queue.mark_failed(task, notes=error)
        _notify().task_failed(task, error, max_retries)
        return

    # Quality gate
    evaluation = evaluate_diff(result.get("diff_text", ""), task)
    log.info(f"[{project}] Quality gate: score={evaluation.get('score')} pass={evaluation.get('pass')}")

    if not evaluation.get("pass", True):
        reasoning = evaluation.get("reasoning", "")
        log.warning(f"[{project}] Quality gate failed — flagging {task['id']} for review")
        fail_path = pending_dir / f"QUALITY_FAILED_{project}_{task['id']}.json"
        fail_path.write_text(json.dumps({"task": task, "evaluation": evaluation}, indent=2))
        task_queue.mark_failed(task, notes=f"quality: {reasoning}")
        _notify().quality_gate_failed(task, reasoning)
        return

    # Record spend
    cost = spend_tracker.record(
        project,
        result.get("input_tokens", 0),
        result.get("output_tokens", 0),
        _cfg("MINIMAX_MODEL"),
    )
    monthly = spend_tracker.monthly_spend()
    cap     = _cfg("MINIMAX_SPEND_CAP")
    log.info(f"[{project}] Done. Cost ${cost:.4f} | Monthly ${monthly:.2f}")

    # Spend milestone notifications (50 / 75 / 85 / 100 %)
    pct = monthly / cap * 100
    _prev_monthly = monthly - cost
    _prev_pct     = _prev_monthly / cap * 100
    for milestone in (50, 75, 85, 100):
        if _prev_pct < milestone <= pct:
            _notify().spend_milestone(monthly, cap)
            break

    # Update CONTEXT.md — closes the feedback loop
    if result.get("diff_text") and repo_path:
        update_context_md(task, result["diff_text"], repo_path)

    # ── AUTO-COMMIT or APPROVAL QUEUE ────────────────────────────────────────
    if task.get("approval_required"):
        # Send to pending_review — Jacob must approve before commit
        task_queue.mark_pending_review(task, result["diff_path"])
        log.info(f"[{project}] {task['id']} → pending_review (approval_required)")
        _notify().task_pending_review(task)
    else:
        # Auto-commit: git add -A + git commit
        commit_hash = _auto_commit(task, repo_path)
        task_queue.mark_committed(
            task,
            commit_hash=commit_hash,
            diff_path=str(result["diff_path"]),
            actual_tokens=result.get("output_tokens", 0),
            cost_usd=cost,
            model_used=_cfg("MINIMAX_MODEL"),
        )
        result["commit_hash"] = commit_hash
        _notify().task_committed(task, result, monthly, cap)
        log.info(f"[{project}] {task['id']} auto-committed ({commit_hash})")

    # Refill task queue if running low
    if task_queue.total_unblocked(projects=enabled_projects) < refill_threshold:
        from task_generator import generate_tasks_all_projects
        from dashboard_generator import generate as generate_dashboard
        generate_tasks_all_projects(
            task_queue       = task_queue,
            enabled_projects = enabled_projects,
            sprint_phases    = _cfg("SPRINT_PHASES"),
            sprint_goals     = _cfg("SPRINT_GOALS"),
            threshold        = refill_threshold,
        )
        generate_dashboard()


# ── GIT COMMIT IPC ───────────────────────────────────────────────────────────

def request_commit(message: str):
    """
    Write COMMIT_REQUEST.txt so git_watcher.py (running on your Mac) can
    git add -A + commit + push without sandbox permission restrictions.

    Usage: request_commit("feat: add izakaya scene")
    git_watcher.py polls every 10s, handles it, deletes the request file.
    """
    base = _cfg("BASE_DIR")
    req  = base / "COMMIT_REQUEST.txt"
    if req.exists():
        log.warning("COMMIT_REQUEST.txt already exists — watcher may be behind")
    req.write_text(message)
    log.info(f"Commit requested: {message[:60]}")


# ── SHARED UTILITY ────────────────────────────────────────────────────────────

def load_context(project: str, repo_paths: dict, max_chars: int = 3000) -> str:
    """
    Load project CONTEXT.md. Single source of truth — imported by both
    executor.py and task_generator.py (fixes #14 duplication).
    """
    repo = repo_paths.get(project)
    if repo:
        path = Path(repo) / "CONTEXT.md"
        if path.exists():
            return path.read_text()[:max_chars]
    log.warning(f"No CONTEXT.md for {project} at {repo}")
    return f"No CONTEXT.md found for {project}."
