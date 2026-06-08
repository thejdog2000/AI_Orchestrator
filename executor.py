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
# Note: task_generator and dashboard_generator are imported lazily inside run_task()
# because task_generator imports load_context from this module — lazy import breaks the cycle.

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


_NON_CODE_PATTERN = re.compile(
    r'^diff --git a/(.+?) b/\1',
    re.MULTILINE,
)
_NON_CODE_EXTENSIONS = re.compile(
    r'\.(md|txt|rst|adoc|log|lock|gitignore|gitattributes|editorconfig|env\.example)$'
    r'|/(TODO|CHANGELOG|CHANGES|LICENCE|LICENSE|NOTICE|AUTHORS|CONTRIBUTORS|CODEOWNERS)(\..*)?$',
    re.IGNORECASE,
)


def _filter_non_code_hunks(diff_text: str) -> tuple[str, list[str]]:
    """
    Strip doc/config-only file sections from a git diff before quality gate evaluation.
    Returns (filtered_diff, list_of_skipped_paths).

    Prevents markdown/TODO rewrites at the top of a diff from consuming the
    6000-char Ollama excerpt and triggering false 'prose instead of code' failures.
    """
    # Split on 'diff --git' boundaries, keeping the delimiter via a capture group
    parts  = re.split(r'(?=^diff --git )', diff_text, flags=re.MULTILINE)
    kept, skipped = [], []
    for part in parts:
        m = _NON_CODE_PATTERN.match(part)
        if m and _NON_CODE_EXTENSIONS.search(m.group(1)):
            skipped.append(m.group(1))
        else:
            kept.append(part)
    return "".join(kept), skipped


def evaluate_diff(diff_text: str, task: dict) -> dict:
    """
    Ollama quality gate for low/medium complexity diffs.
    High-complexity diffs skip evaluation and go straight to pending_review.
    json_mode=True prevents markdown wrapping breaking the parser.

    Pass criteria are explicit so "pass" means the same thing across all calls.
    The gate is a sanity check (did MiniMax produce real code for this task?),
    NOT a semantic correctness review — it cannot verify logic on truncated diffs.

    Non-code file hunks (*.md, TODO.*, lock files, etc.) are stripped before
    the excerpt is taken so they don't crowd out actual code in the 6000-char window.

    Diff truncated to 6000 chars (~3000-4500 tokens), keeping total prompt
    well within qwen3-coder:30b's 8192-token context window.
    """
    if task.get("complexity") == "high":
        log.info(f"[{task['project']}] High-complexity — skipping Ollama gate")
        return {"score": 7, "pass": True, "issues": [], "reasoning": "High complexity — human review",
                "gate_skipped": True}

    filtered, skipped = _filter_non_code_hunks(diff_text)
    if skipped:
        log.info(f"[{task['project']}] Quality gate skipping non-code files: {skipped}")

    # Fall back to full diff if filtering removed everything (pure-doc task)
    eval_text = filtered if filtered.strip() else diff_text

    shown   = min(len(eval_text), 6000)
    total   = len(diff_text)           # report original total for context
    excerpt = eval_text[:6000]

    skip_note = f" — doc/config files excluded: {skipped}" if skipped else ""
    prompt = (
        f"Evaluate this code diff against the task specification.\n\n"
        f"Task: {task['description']}\n"
        f"Project: {task['project']}\n"
        f"Complexity: {task.get('complexity', 'medium')}\n\n"
        f"Diff ({shown} of {total} chars total{skip_note}):\n{excerpt}\n\n"
        f"Set pass=true only if ALL of the following are true:\n"
        f"- Diff contains actual code changes (not empty, not prose, not placeholder stubs)\n"
        f"- File paths match the project language and task scope\n"
        f"- No obvious syntax errors visible in the diff\n"
        f"- Changes are plausibly related to the task description\n"
        f"- No evidence of model refusal, apology text, or TODO-only output\n\n"
        f"Set pass=false if any of: empty diff, wrong language/extension, obvious syntax errors, "
        f"model hallucinated prose instead of code, changes clearly unrelated to task.\n\n"
        f'Return JSON only: {{"score":0-10,"pass":true/false,"issues":[],"reasoning":""}}'
    )
    raw = ollama_generate(prompt, max_tokens=500, json_mode=True, temperature=0.1)
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


# ── PBI FEEDBACK HELPERS ──────────────────────────────────────────────────────

def generate_pbi_handoff(task: dict, diff_text: str, pbi: dict) -> str:
    """
    Ollama writes a compact (~150 word) summary of what this task did,
    scoped to what the next PBI task needs to know.
    Non-blocking — returns "" on failure.
    """
    prompt = (
        f"Write a 100-150 word handoff note for the NEXT developer working on this feature.\n"
        f"Focus on: what was added/changed, any new functions/fields created, "
        f"and what the next task should build on top of.\n"
        f"Be specific and technical. No preamble.\n\n"
        f"PBI: {pbi['title']}\n"
        f"COMPLETED TASK: {task['description']}\n\n"
        f"DIFF:\n{diff_text[:3000]}\n\n"
        f"Handoff note:"
    )
    result = ollama_generate(prompt, max_tokens=250, model=_cfg("OLLAMA_MODEL_DIGEST"), temperature=0.2)
    return result.strip() if result and len(result) > 30 else ""


def _extract_new_files_from_diff(diff_text: str, existing_files: list) -> list:
    """
    Parse a git diff for files that were newly created (not just modified).
    Returns relative paths not already in existing_files.
    New files appear as: diff --git a/path b/path with --- /dev/null
    """
    new_files = []
    lines     = diff_text.splitlines()
    current   = None
    for line in lines:
        if line.startswith("diff --git "):
            # Extract b/ path: "diff --git a/foo b/foo" → "foo"
            parts = line.split(" b/", 1)
            current = parts[1].strip() if len(parts) == 2 else None
        elif line.startswith("--- /dev/null") and current:
            if current not in existing_files and current not in new_files:
                new_files.append(current)
    return new_files


# ── QUESTION DETECTION ────────────────────────────────────────────────────────

_QUESTION_PHRASES = re.compile(
    r"(could you (?:please )?(?:clarify|provide|confirm|specify|share|tell me)|"
    r"(?:i |I )(?:need|would need|require) (?:more |additional )?(?:information|context|clarification|details)|"
    r"before (?:i |I )(?:proceed|implement|make|start|begin)|"
    r"what (?:is|are|should|would) (?:the )?(?:expected|correct|intended|preferred)|"
    r"(?:please )?(?:clarify|confirm|specify) (?:which|what|how|whether)|"
    r"can you (?:clarify|confirm|provide|share|tell)|"
    r"(?:i am|I'm) (?:not sure|unclear|unsure) (?:about|how|what|whether))",
    re.IGNORECASE,
)

def _detect_question(content: str, thinking_block: str) -> str:
    """
    Return the question text if the model asked for clarification instead of
    producing code, otherwise return "".
    Checks both content and thinking block (model sometimes reasons about
    missing context even when content is short).
    """
    # Must have no file blocks (caller already checked) and contain question signals
    combined    = (content + " " + thinking_block)[:3000]
    q_marks     = combined.count("?")
    phrase_hit  = bool(_QUESTION_PHRASES.search(combined))

    if q_marks >= 2 and phrase_hit:
        # Extract the first ~400 chars of actual content as the question text
        question_src = content.strip() or thinking_block.strip()
        return question_src[:400]
    return ""


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


class _MiniMaxTimeout(Exception):
    """Raised on API timeout. Carries estimated input token count for partial spend recording."""
    def __init__(self, estimated_input_tokens: int):
        super().__init__(f"MiniMax API timed out (~{estimated_input_tokens} input tokens)")
        self.estimated_input_tokens = estimated_input_tokens


def _minimax_chat(
    system:     str,
    user:       str,
    max_tokens: int   = 65536,
    temperature: float = 0.2,
) -> tuple:
    """
    Direct MiniMax chat/completions. Key from env only — never CLI args.
    Returns (content, input_tokens, output_tokens, cached_tokens).
    temperature=0.2 default — deterministic code output.
    max_tokens=65536: MiniMax-M3 reasons inside <think>...</think> before writing.
    On complex multi-file tasks, reasoning alone can be 8k-15k tokens. We want
    the model to reason fully and still have room for complete file output.
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise EnvironmentError("MINIMAX_API_KEY not set")

    # Estimate input tokens before the call — used for partial spend on timeout.
    # MiniMax benchmark: ~750 words = 1000 tokens ≈ chars/4.
    estimated_input_tokens = (len(system) + len(user)) // 4

    try:
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
            timeout=900,
        )
    except requests.exceptions.Timeout:
        raise _MiniMaxTimeout(estimated_input_tokens)

    resp.raise_for_status()
    data   = resp.json()
    usage  = data.get("usage", {})
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    msg = data["choices"][0]["message"]
    # MiniMax-M3 (and some other reasoning models) emit extended thinking in
    # `reasoning_content` while leaving `content` empty.  Fall back to it so
    # the file-block parser has something to work with.
    content = msg.get("content") or msg.get("reasoning_content") or ""
    return (
        content,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        cached,
    )


def select_relevant_files(task: dict, repo_path: Path, context_md: str, max_files: int = 20) -> list[str]:
    """
    Ask Ollama which files in the repo are most relevant to this task.
    Uses git ls-files for an accurate, .gitignore-respecting file list.
    Returns a list of relative paths to inject into the MiniMax user message.
    """
    try:
        tree_result = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_path, capture_output=True, text=True,
        )
        file_tree = tree_result.stdout.strip() if tree_result.returncode == 0 else ""
    except Exception:
        file_tree = ""

    if not file_tree:
        return []

    # Ask for {"files": [...]} explicitly — json_mode forces a JSON object as root,
    # so asking for a bare array conflicts with how Ollama implements json_mode.
    # Controlling the key means we always know exactly where to find the list.
    prompt = (
        f"List the {max_files} source files most relevant to this coding task.\n"
        f"Only include files that exist in the file tree below.\n"
        f"Prioritise files that will need to be read or modified to complete the task.\n\n"
        f"TASK: {task['description']}\n"
        f"CONTEXT:\n{context_md}\n\n"
        f"FILE TREE:\n{file_tree[:3000]}\n\n"
        f'Return ONLY this JSON object: {{"files": ["relative/path/to/file.js", "..."]}}'
    )
    raw = ollama_generate(prompt, max_tokens=300, json_mode=True, temperature=0.1)
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[-1].strip()

    paths = []
    try:
        parsed = json.loads(raw)
        paths  = parsed.get("files", []) if isinstance(parsed, dict) else []
    except Exception:
        pass

    if not isinstance(paths, list):
        paths = []

    if not paths:
        log.warning(f"[{task['project']}] select_relevant_files returned nothing — no file context injected")
        return []

    # Validate each path actually exists in the repo
    valid = [p for p in paths if isinstance(p, str) and (repo_path / p).exists()][:max_files]
    if not valid:
        log.warning(f"[{task['project']}] select_relevant_files returned no valid paths: {list(paths)[:5]}")
    return valid


def _load_file_context(repo_path: Path, rel_paths: list[str], max_chars: int = 100_000) -> str:
    """
    Load content of the given files up to max_chars total.
    Reads each file fully; truncates only the last file if the budget is hit.
    """
    parts = []
    total = 0
    for rel in rel_paths:
        path = repo_path / rel
        if not path.exists():
            continue
        content = path.read_text(errors="ignore")
        remaining = max_chars - total
        if len(content) > remaining:
            content = content[:remaining]
        parts.append(f"// {rel}\n{content}")
        total += len(content)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


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

    context_md  = load_context(project, _cfg("REPO_PATHS"))
    task_prompt = system_prompt or write_execution_prompt(task, context_md)

    # ── PBI CONTEXT ───────────────────────────────────────────────────────────
    # If this task belongs to a PBI, inject the PBI spec as a header block and
    # use the PBI's pre-specified affected_files instead of asking Ollama to
    # guess from the file tree — faster, cheaper, more accurate.
    pbi_header  = ""
    pbi_files   = []
    pbi_id      = task.get("pbi_id")
    if pbi_id:
        try:
            from task_queue import TaskQueue
            _tq  = TaskQueue(_cfg("DB_PATH"))
            pbi  = _tq.get_pbi(pbi_id)
            if pbi:
                # Build handoff notes block from previous PBI tasks
                handoff_notes = _tq.get_pbi_handoff_notes(pbi_id)
                handoff_block = ""
                if handoff_notes:
                    notes_text = "\n".join(
                        f"- [{tid}]: {summary}"
                        for tid, summary in list(handoff_notes.items())[-3:]  # last 3 tasks
                    )
                    handoff_block = f"### What Previous Tasks Built\n{notes_text}\n\n"

                pbi_header = (
                    f"## PARENT PBI: {pbi['title']}\n"
                    f"{pbi['description']}\n\n"
                    f"### Acceptance Criteria\n{pbi['acceptance_criteria']}\n\n"
                    f"{handoff_block}"
                )
                pbi_files = pbi.get("affected_files", [])
                log.info(f"[{project}] PBI {pbi_id} injected — {len(pbi_files)} files, "
                         f"{len(handoff_notes)} handoff note(s)")
        except Exception as _e:
            log.warning(f"[{project}] PBI load failed ({_e}) — falling back to file discovery")

    # Use PBI file list if available, otherwise ask Ollama to select relevant files
    if pbi_files:
        relevant = [p for p in pbi_files if (repo_path / p).exists()]
    else:
        try:
            relevant = select_relevant_files(task, repo_path, context_md)
        except Exception as _e:
            log.warning(f"[{project}] select_relevant_files failed ({_e}) — proceeding without file context")
            relevant = []

    file_ctx = _load_file_context(repo_path, relevant) if relevant else ""
    if relevant:
        log.info(f"[{project}] Injecting {len(relevant)} files into prompt: {relevant}")

    # File context goes first in the system prompt so MiniMax's automatic prefix
    # caching kicks in — same files across tasks for a project = cache hits at $0.06/M.
    system = (
        (f"EXISTING FILES (read carefully before modifying):\n\n{file_ctx}\n\n" if file_ctx else "")
        + (pbi_header if pbi_header else "")
        + task_prompt
    )

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
        content, input_tokens, output_tokens, cached_tokens = _minimax_chat(system, user_msg)
    except EnvironmentError as e:
        return {"success": False, "error": str(e), "ollama_prompt": task_prompt,
                "injected_files": relevant}
    except requests.HTTPError as e:
        return {"success": False, "error": f"api_http_{e.response.status_code}",
                "ollama_prompt": task_prompt, "injected_files": relevant}
    except _MiniMaxTimeout as e:
        log.warning(f"[{project}] MiniMax timeout — {e}")
        return {"success": False, "error": str(e), "estimated_input_tokens": e.estimated_input_tokens,
                "ollama_prompt": task_prompt, "injected_files": relevant}
    except Exception as e:
        log.error(f"[{project}] MiniMax call failed: {e}")
        return {"success": False, "error": str(e), "ollama_prompt": task_prompt,
                "injected_files": relevant}

    raw_response = content  # full response before any stripping — saved to pipeline log

    # If content still empty, log the raw message keys to help diagnose future API changes.
    if not content.strip():
        msg_keys = list(data["choices"][0]["message"].keys()) if data.get("choices") else []
        log.warning(f"[{project}] Empty content from MiniMax — message keys: {msg_keys}")

    # Strip thinking blocks — M2.7/M3 output <think>...</think> before file content.
    # Always capture the full thinking block — it's valuable for debugging failures.
    thinking_block = ""
    if "</think>" in content:
        parts          = content.split("</think>", 1)
        thinking_block = parts[0].replace("<think>", "").strip()
        content        = parts[1].strip()
        log.info(f"[{project}] Think block: {len(thinking_block)} chars | "
                 f"output: {len(content)} chars")

    file_blocks = _parse_file_blocks(content)
    if not file_blocks:
        preview = content[:500]
        if not preview and thinking_block:
            log.warning(
                f"[{project}] {task['id']} — model reasoning produced no output.\n"
                f"  Thinking ({len(thinking_block)} chars): {thinking_block[:800]}"
            )
            error = "empty_after_think"
        else:
            # Check if model asked a clarifying question instead of producing code
            question = _detect_question(content, thinking_block)
            if question:
                log.warning(f"[{project}] {task['id']} — model asked a question: {question[:200]}")
                return {
                    "success":          False,
                    "error":            "model_asked_question",
                    "question":         question,
                    "ollama_prompt":    task_prompt,
                    "injected_files":   relevant,
                    "thinking_block":   thinking_block,
                    "output_content":   content,
                    "response_preview": preview,
                    "input_tokens":     input_tokens,
                    "output_tokens":    output_tokens,
                    "cached_tokens":    cached_tokens,
                }
            log.warning(f"[{project}] No file blocks for {task['id']}. "
                        f"Output preview: {preview[:400]}")
            error = "no_file_blocks"
        return {
            "success":          False,
            "error":            error,
            "ollama_prompt":    task_prompt,
            "injected_files":   relevant,
            "thinking_block":   thinking_block,
            "output_content":   content,
            "response_preview": preview or thinking_block[:500],
            "thinking_preview": thinking_block[:800],
            "input_tokens":     input_tokens,
            "output_tokens":    output_tokens,
            "cached_tokens":    cached_tokens,
        }

    written = []
    for rel_path, file_content in file_blocks.items():
        if _safe_write(repo_path, rel_path, file_content):
            log.info(f"[{project}] Wrote {rel_path}")
            written.append(rel_path)
        else:
            log.warning(f"[{project}] Skipped unsafe path: {rel_path}")

    if not written:
        log.error(f"[{project}] All file writes blocked for {task['id']} — possible path traversal")
        return {"success": False, "error": "all_writes_blocked", "ollama_prompt": task_prompt,
                "injected_files": relevant, "thinking_block": thinking_block}

    subprocess.run(["git", "add", "--intent-to-add", "."],
                   cwd=repo_path, capture_output=True)
    diff = subprocess.run(["git", "diff"], cwd=repo_path, capture_output=True, text=True)
    diff_text = diff.stdout

    pending_dir = _cfg("PENDING_DIR")
    diff_path   = pending_dir / f"{project}_{task['id']}_{int(time.time())}.diff"
    diff_path.write_text(diff_text or f"# No diff\n# Files: {list(file_blocks)}")

    cache_note = f" | {cached_tokens} cached" if cached_tokens else ""
    log.info(f"[{project}] {input_tokens} in / {output_tokens} out{cache_note}")
    return {
        "success":        True,
        "diff_path":      diff_path,
        "diff_text":      diff_text,
        "files_written":  list(file_blocks.keys()),
        "injected_files": relevant,
        "ollama_prompt":  task_prompt,
        "thinking_block": thinking_block,
        "output_content": content,
        "input_tokens":   input_tokens,
        "output_tokens":  output_tokens,
        "cached_tokens":  cached_tokens,
        "system_prompt":  task_prompt,   # pass back for retry revision
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

    # Clear any stale git lock files before committing
    git_dir = repo_path / ".git"
    for lock in git_dir.glob("*.lock"):
        try:
            lock.unlink()
            log.debug(f"[{task['project']}] Cleared stale git lock: {lock.name}")
        except Exception:
            pass

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

    result        = None
    evaluation    = {}
    system_prompt = ""
    attempts_made = 0
    attempt_logs  = []   # full per-attempt data written to pipeline_logs/{task_id}.json
    context_md    = load_context(project, _cfg("REPO_PATHS"))

    # Notify Discord that this task is starting
    _notify().task_started(task)

    for attempt in range(max_retries):
        attempts_made = attempt + 1

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

        # Record per-attempt data for pipeline log
        attempt_logs.append({
            "attempt":        attempts_made,
            "ollama_prompt":  result.get("ollama_prompt", system_prompt),
            "injected_files": result.get("injected_files", []),
            "thinking_block": result.get("thinking_block", ""),
            "output_content": result.get("output_content", ""),
            "files_written":  result.get("files_written", []),
            "input_tokens":   result.get("input_tokens", 0),
            "output_tokens":  result.get("output_tokens", 0),
            "cached_tokens":  result.get("cached_tokens", 0),
            "error":          result.get("error") if not result["success"] else None,
        })

        if result["success"]:
            break

        # Record spend for this failed attempt — MiniMax bills all API calls,
        # including ones that returned no file blocks or produced bad output.
        if result.get("input_tokens") or result.get("output_tokens"):
            spend_tracker.record(
                project,
                result.get("input_tokens", 0),
                result.get("output_tokens", 0),
                _cfg("MINIMAX_MODEL"),
            )

        error = result.get("error", "")

        # Don't retry timeouts — the prompt is fine, the API is just slow.
        if "timed out" in error:
            log.warning(f"[{project}] Timeout on attempt {attempt + 1} — skipping retries to save tokens")
            break

        # Don't retry questions — retrying with the same context won't answer them.
        # Surface to human via Discord instead.
        if error == "model_asked_question":
            log.warning(f"[{project}] Model asked a question on attempt {attempt + 1} — blocking task")
            break

        wait = backoff[min(attempt, len(backoff) - 1)]
        log.warning(f"[{project}] Attempt {attempt + 1} failed: {error}. "
                    f"Retrying in {wait}s...")

        # Run quality gate on failed diff to get failure reasons for next revision
        if result.get("diff_text"):
            evaluation = evaluate_diff(result["diff_text"], task)
        time.sleep(wait)

    def _write_pipeline_log(final_status: str, diff_path_str: str = ""):
        """Write full pipeline log for this task to pipeline_logs/{task_id}.json."""
        try:
            log_dir  = _cfg("PIPELINE_LOGS_DIR")
            log_path = log_dir / f"{task['id']}.json"
            log_path.write_text(json.dumps({
                "task_id":     task["id"],
                "project":     project,
                "description": task.get("description", ""),
                "created_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
                "final_status": final_status,
                "attempts":    attempt_logs,
                "diff_path":   diff_path_str,
            }, indent=2, default=str))
        except Exception as _e:
            log.warning(f"[{project}] Pipeline log write failed: {_e}")

    # All attempts exhausted (or broke out early)
    if not result or not result["success"]:
        error = str(result.get("error", "unknown")) if result else "no_result"

        # ── QUESTION BLOCK ────────────────────────────────────────────────────
        if error == "model_asked_question":
            question = result.get("question", "") if result else ""
            log.warning(f"[{project}] {task['id']} blocked — model needs clarification")
            _write_pipeline_log("blocked")
            task_queue.mark_blocked(task, question=question)
            _notify().task_blocked_on_question(task, question)
            return

        log.error(f"[{project}] {task['id']} failed after {attempts_made} attempts")

        # Record partial spend for any timeout attempts — MiniMax bills input tokens
        # processed before timeout even though we never got a response.
        if result and result.get("estimated_input_tokens"):
            spend_tracker.record_partial(
                project,
                result["estimated_input_tokens"],
                _cfg("MINIMAX_MODEL"),
                reason=f"timeout after {attempts_made} attempts",
            )

        fail_path = pending_dir / f"FAILED_{project}_{task['id']}.json"
        fail_path.write_text(json.dumps({
            "task":              task,
            "result":            result,
            "attempts":          attempts_made,
            "system_prompt":     system_prompt,
            "injected_files":    result.get("injected_files", []) if result else [],
            "response_preview":  result.get("response_preview", "") if result else "",
            "thinking_preview":  result.get("thinking_preview", "") if result else "",
        }, indent=2))
        _write_pipeline_log("failed")
        task_queue.mark_failed(task, notes=error)
        if system_prompt:
            task_queue.update_status(task["id"], "failed",
                                     system_prompt=system_prompt[:2000])
        _notify().task_failed(task, error, attempts_made)
        return

    # Quality gate
    evaluation = evaluate_diff(result.get("diff_text", ""), task)
    log.info(f"[{project}] Quality gate: score={evaluation.get('score')} pass={evaluation.get('pass')}")

    if not evaluation.get("pass", True):
        reasoning = evaluation.get("reasoning", "")
        log.warning(f"[{project}] Quality gate failed — flagging {task['id']} for review")
        fail_path = pending_dir / f"QUALITY_FAILED_{project}_{task['id']}.json"
        fail_path.write_text(json.dumps({
            "task":          task,
            "evaluation":    evaluation,
            "system_prompt": system_prompt,
            "injected_files": result.get("injected_files", []),
        }, indent=2))
        _write_pipeline_log("quality_failed", str(result.get("diff_path", "")))
        task_queue.mark_failed(task, notes=f"quality: {reasoning}")
        task_queue.update_status(task["id"], "failed",
                                 quality_score=evaluation.get("score", 0),
                                 system_prompt=system_prompt[:2000])
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

    # ── AUTO-COMMIT ───────────────────────────────────────────────────────────
    # approval_required tasks land in failed so they surface for review on the
    # dashboard — the diff is saved and the system prompt is stored for context.
    _write_pipeline_log(
        "approval_required" if task.get("approval_required") else "committed",
        str(result.get("diff_path", "")),
    )

    if task.get("approval_required"):
        task_queue.mark_failed(task, notes="needs approval — review diff before committing")
        task_queue.update_status(task["id"], "failed",
                                 diff_path=str(result["diff_path"]),
                                 quality_score=evaluation.get("score", 0),
                                 system_prompt=result.get("system_prompt", "")[:2000])
        log.info(f"[{project}] {task['id']} → failed (approval_required — diff saved for review)")
        _notify().task_pending_review(task)
    else:
        commit_hash = _auto_commit(task, repo_path)
        task_queue.mark_committed(
            task,
            commit_hash=commit_hash,
            diff_path=str(result["diff_path"]),
            actual_tokens=result.get("output_tokens", 0),
            cost_usd=cost,
            model_used=_cfg("MINIMAX_MODEL"),
            system_prompt=result.get("system_prompt", "")[:2000],
            quality_score=evaluation.get("score", 0),
        )
        result["commit_hash"] = commit_hash
        _notify().task_committed(task, result, monthly, cap)
        log.info(f"[{project}] {task['id']} auto-committed ({commit_hash})")

        # ── PBI POST-COMMIT HOOKS (Gaps 1 + 2) ───────────────────────────────
        pbi_id = task.get("pbi_id")
        if pbi_id and result.get("diff_text"):
            try:
                from task_queue import TaskQueue as _TQ
                _tq2 = _TQ(_cfg("DB_PATH"))
                _pbi = _tq2.get_pbi(pbi_id)
                if _pbi:
                    # Gap 1: generate and store handoff note for next PBI task
                    handoff = generate_pbi_handoff(task, result["diff_text"], _pbi)
                    if handoff:
                        _tq2.update_pbi_handoff(pbi_id, task["id"], handoff)
                        log.info(f"[{project}] PBI handoff note stored ({len(handoff)} chars)")

                    # Gap 2: discover new files created by this task
                    new_files = _extract_new_files_from_diff(
                        result["diff_text"], _pbi.get("affected_files", [])
                    )
                    if new_files:
                        _tq2.update_pbi_affected_files(pbi_id, new_files)
            except Exception as _e:
                log.warning(f"[{project}] PBI post-commit hooks failed ({_e}) — non-fatal")

    # Refill task queue if running low
    if task_queue.total_unblocked(projects=enabled_projects) < refill_threshold:
        from task_generator import generate_tasks_all_projects       # lazy: circular dep
        from dashboard_generator import generate as generate_dashboard  # lazy: convenience
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
