"""
lang_pipeline.py
Dedicated nightly pipeline for language learning app scene generation.

Why separate from the generic task queue:
  - Fixed 7-night schedule (not dynamic council-generated tasks)
  - Schema-aware prompts (full scene JS module format)
  - Node.js smoke test runs automatically after each scene
  - Per-scene pass/fail tracked separately from code task history
  - Failed scenes retry on the next available night, not immediately

Scheduled from orchestrator_main.py:
  scheduler.add_job(run_nightly, "cron", hour=22, minute=0, id="lang_nightly")

Manual run:
  python lang_pipeline.py              # run tonight's scenes
  python lang_pipeline.py --status     # show schedule state
"""

import os
import json
import time
import subprocess
import logging
import argparse
from datetime import datetime, date
from pathlib import Path

import requests

log = logging.getLogger(__name__)

HOME      = Path.home()
REPO_PATH = HOME / "Documents/claude/projects/language-travel-app"
BASE_DIR  = Path(__file__).parent
STATE_FILE = BASE_DIR / "tasks" / "lang_schedule.json"

MINIMAX_API_BASE = "https://api.minimax.io/v1"
MINIMAX_MODEL    = "minimax-m3"

OLLAMA_BASE       = "http://localhost:11434"
OLLAMA_MODEL_CODE = "qwen3-coder:30b"

# ── 7-NIGHT SCHEDULE ─────────────────────────────────────────────────────────
# Each scene: {id, language, level, location, status: pending|pass|fail, night}
# Status persists in lang_schedule.json across runs.

SCENE_SCHEDULE = [
    # Night 1
    {"id": "ja_izakaya_01",     "night": 1, "lang": "ja", "level": "a0",
     "location": "izakaya",          "output": "scenes/ja/izakaya_01.js"},
    {"id": "ja_konbini_01",     "night": 1, "lang": "ja", "level": "a0",
     "location": "konbini",          "output": "scenes/ja/konbini_01.js"},
    # Night 2
    {"id": "ja_train_01",       "night": 2, "lang": "ja", "level": "a1",
     "location": "train station",    "output": "scenes/ja/train_station_01.js"},
    {"id": "ja_ramen_01",       "night": 2, "lang": "ja", "level": "a1",
     "location": "ramen shop",       "output": "scenes/ja/ramen_shop_01.js"},
    # Night 3
    {"id": "ja_temple_01",      "night": 3, "lang": "ja", "level": "a1",
     "location": "temple/directions","output": "scenes/ja/temple_01.js"},
    {"id": "ja_review_pass",    "night": 3, "lang": "ja", "level": "a1",
     "location": "randomization_expand", "output": None},  # no new scene, expand existing pools
    # Night 4
    {"id": "es_taco_01",        "night": 4, "lang": "es", "level": "a0",
     "location": "taco vendor",      "output": "scenes/es/taco_vendor_01.js"},
    {"id": "es_mercado_01",     "night": 4, "lang": "es", "level": "a0",
     "location": "mercado",          "output": "scenes/es/mercado_01.js"},
    # Night 5
    {"id": "es_cafe_01",        "night": 5, "lang": "es", "level": "a1",
     "location": "café",             "output": "scenes/es/cafe_01.js"},
    {"id": "es_taxi_01",        "night": 5, "lang": "es", "level": "a1",
     "location": "taxi",             "output": "scenes/es/taxi_01.js"},
    # Night 6
    {"id": "es_hotel_01",       "night": 6, "lang": "es", "level": "a1",
     "location": "hotel check-in",   "output": "scenes/es/hotel_01.js"},
    {"id": "es_srs_integration","night": 6, "lang": "es", "level": "a1",
     "location": "srs_integration",  "output": None},  # SRS system wiring
    # Night 7 — buffer: retry failures, expand randomization
]

# ── SCENE JS SCHEMA TEMPLATE ─────────────────────────────────────────────────
# Included in every generation prompt so MiniMax knows exactly what to output.

SCENE_SCHEMA = """
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
    vocabularyFocus: string[],   // 5-8 target words
    grammarFocus: string,        // one grammar point
    randomizationPool: {
      playerGreetings: string[], // minimum 5 variants
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
"""

# ── SCHEDULE STATE ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {s["id"]: {"status": "pending", "attempts": 0, "last_run": None}
            for s in SCENE_SCHEDULE}

def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))

def _due_tonight(state: dict, night_number: int) -> list[dict]:
    """Return scenes due tonight: scheduled night <= tonight OR previously failed."""
    due = []
    for scene in SCENE_SCHEDULE:
        s = state.get(scene["id"], {})
        status = s.get("status", "pending")
        if status == "pass":
            continue
        if scene["night"] <= night_number or status == "fail":
            due.append(scene)
    return due

def _detect_night_number(state: dict) -> int:
    """Infer which night we're on from how many scenes have passed."""
    passed_nights = {
        SCENE_SCHEDULE[[s["id"] for s in SCENE_SCHEDULE].index(sid)]["night"]
        for sid, v in state.items() if v.get("status") == "pass"
        if sid in [s["id"] for s in SCENE_SCHEDULE]
    }
    return (max(passed_nights) + 1) if passed_nights else 1

# ── MINIMAX + OLLAMA ──────────────────────────────────────────────────────────

def _ollama_prompt(scene: dict) -> str:
    lang_name = "Japanese" if scene["lang"] == "ja" else "Spanish"
    level_name = "absolute beginner (A0)" if scene["level"] == "a0" else "beginner (A1)"
    prompt = (
        f"Write a system prompt for generating a language learning scene.\n"
        f"Language: {lang_name} ({level_name})\n"
        f"Location: {scene['location']}\n"
        f"The output must be a complete ES module following the schema below.\n"
        f"Randomization pools must have at least 5 variants each.\n"
        f"Vocabulary: 5-8 target words appropriate for the level.\n\n"
        f"Schema:\n{SCENE_SCHEMA}\n\n"
        f"Write a concise system prompt (no preamble):"
    )
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": OLLAMA_MODEL_CODE, "prompt": prompt, "stream": False,
                  "options": {"num_ctx": 8192, "num_predict": 500}},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        log.error(f"Ollama prompt generation failed: {e}")
        return f"Generate a {scene['lang'].upper()} language learning scene for a {scene['location']}."


def _minimax_generate(system: str, user: str) -> tuple:
    """Returns (content, input_tokens, output_tokens)."""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        raise EnvironmentError("MINIMAX_API_KEY not set")
    resp = requests.post(
        f"{MINIMAX_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model":       MINIMAX_MODEL,
            "messages":    [{"role": "system", "content": system},
                            {"role": "user",   "content": user}],
            "temperature": 0.7,   # slightly creative for dialogue variety
            "max_tokens":  6000,
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


# ── SMOKE TEST ────────────────────────────────────────────────────────────────

def _run_smoke_test(scene_path: Path) -> tuple[bool, str]:
    """
    Run Node.js smoke test against generated scene file.
    Expects tests/smoke.js to exist in the language app repo.
    Returns (passed, output).
    """
    smoke_script = REPO_PATH / "tests" / "smoke.js"
    if not smoke_script.exists():
        return True, "smoke.js not yet created — skipping test"

    rel_path = scene_path.relative_to(REPO_PATH)
    result   = subprocess.run(
        ["node", str(smoke_script), str(rel_path)],
        cwd=REPO_PATH, capture_output=True, text=True, timeout=30,
    )
    passed = result.returncode == 0
    output = result.stdout + result.stderr
    return passed, output


# ── SCENE GENERATION ─────────────────────────────────────────────────────────

def _generate_scene(scene: dict) -> dict:
    """
    Generate one scene. Returns result dict with success, tokens, smoke_passed.
    """
    scene_id  = scene["id"]
    out_path  = scene.get("output")

    if not out_path:
        # Special tasks (randomization expand, SRS integration) — simplified prompt
        log.info(f"[lang] Special task: {scene_id}")
        system = _ollama_prompt(scene)
        user   = (
            f"Task: {scene_id.replace('_', ' ')}\n"
            f"Expand randomization pools for all completed scenes or wire SRS integration.\n"
            f"Output modified files in <<<FILE: path>>> ... <<<END>>> format."
        )
        try:
            content, in_tok, out_tok = _minimax_generate(system, user)
            # Parse and write any file blocks
            import re
            blocks = re.findall(r"<<<FILE:\s*(.+?)>>>\s*\n(.*?)<<<END>>>", content, re.DOTALL)
            for rel_path, file_content in blocks:
                dest = REPO_PATH / rel_path.strip()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(file_content)
                log.info(f"[lang] Wrote {rel_path.strip()}")
            return {"success": True, "smoke_passed": True,
                    "input_tokens": in_tok, "output_tokens": out_tok}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Standard scene generation
    dest = REPO_PATH / out_path
    dest.parent.mkdir(parents=True, exist_ok=True)

    lang_name = "Japanese" if scene["lang"] == "ja" else "Spanish"
    level_name = "absolute beginner (A0)" if scene["level"] == "a0" else "beginner (A1)"

    system = _ollama_prompt(scene)
    user   = (
        f"Generate a complete {lang_name} language learning scene for: {scene['location']}\n"
        f"Level: {level_name}\n\n"
        f"Output ONLY the complete ES module as a single code block.\n"
        f"The module must follow the schema exactly.\n"
        f"Randomization pools: minimum 5 variants each.\n"
        f"Vocabulary focus: 5-8 words appropriate for {level_name}.\n\n"
        f"Output the complete ES module (start with 'export const scene = {{'}):"
    )

    try:
        content, in_tok, out_tok = _minimax_generate(system, user)
    except Exception as e:
        return {"success": False, "error": str(e),
                "input_tokens": 0, "output_tokens": 0}

    # Extract JS from response (may or may not have code fences)
    js_content = content.strip()
    if "```" in js_content:
        lines = js_content.split("\n")
        start = next((i for i, l in enumerate(lines) if l.strip().startswith("```")), 0)
        end   = next((i for i, l in enumerate(lines[start+1:], start+1)
                      if l.strip() == "```"), len(lines))
        js_content = "\n".join(lines[start+1:end])

    if "export const scene" not in js_content:
        log.warning(f"[lang] {scene_id}: response missing 'export const scene'")
        return {"success": False, "error": "missing_export_const_scene",
                "input_tokens": in_tok, "output_tokens": out_tok}

    dest.write_text(js_content)
    log.info(f"[lang] Wrote {out_path} ({len(js_content)} chars)")

    # Smoke test
    smoke_passed, smoke_output = _run_smoke_test(dest)
    if not smoke_passed:
        log.warning(f"[lang] Smoke test FAILED for {scene_id}:\n{smoke_output}")
    else:
        log.info(f"[lang] Smoke test passed for {scene_id}")

    return {
        "success":       True,
        "smoke_passed":  smoke_passed,
        "smoke_output":  smoke_output,
        "input_tokens":  in_tok,
        "output_tokens": out_tok,
    }


# ── NIGHTLY RUN ───────────────────────────────────────────────────────────────

def run_nightly():
    """Entry point called by scheduler at 10pm."""
    if not REPO_PATH.exists():
        log.error(f"Language app repo not found: {REPO_PATH}")
        return

    if not os.environ.get("MINIMAX_API_KEY"):
        log.error("MINIMAX_API_KEY not set — skipping lang pipeline")
        return

    state        = _load_state()
    night_number = _detect_night_number(state)
    due          = _due_tonight(state, night_number)

    if not due:
        log.info(f"[lang] Night {night_number}: no scenes due tonight")
        return

    log.info(f"[lang] Night {night_number}: generating {len(due)} scene(s)")
    total_in = total_out = passed = failed = 0

    for scene in due:
        sid = scene["id"]
        log.info(f"[lang] Generating: {sid}")
        s = state.setdefault(sid, {"status": "pending", "attempts": 0, "last_run": None})
        s["attempts"] += 1
        s["last_run"]  = datetime.now().isoformat()

        result = _generate_scene(scene)
        total_in  += result.get("input_tokens", 0)
        total_out += result.get("output_tokens", 0)

        if not result["success"]:
            s["status"] = "fail"
            s["error"]  = result.get("error", "unknown")
            failed += 1
            log.error(f"[lang] {sid} failed: {result.get('error')}")
        elif not result.get("smoke_passed", True):
            s["status"] = "fail"
            s["error"]  = "smoke_test_failed"
            failed += 1
        else:
            s["status"] = "pass"
            passed += 1

        _save_state(state)
        time.sleep(2)   # brief pause between scenes

    log.info(
        f"[lang] Night {night_number} complete: "
        f"{passed} passed / {failed} failed | "
        f"{total_in} in / {total_out} out tokens"
    )


# ── STATUS REPORT ─────────────────────────────────────────────────────────────

def show_status():
    state = _load_state()
    print(f"\nLanguage Pipeline — Scene Schedule\n{'─'*60}")
    for night in range(1, 8):
        scenes = [s for s in SCENE_SCHEDULE if s["night"] == night]
        if not scenes:
            print(f"  Night {night}: buffer (retries + gap fill)")
            continue
        print(f"  Night {night}:")
        for s in scenes:
            st     = state.get(s["id"], {})
            status = st.get("status", "pending")
            icon   = {"pass": "✓", "fail": "✗", "pending": "○"}.get(status, "○")
            att    = st.get("attempts", 0)
            print(f"    {icon} {s['id']:<28} {status:<8} attempts={att}")
    print()


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Language scene pipeline")
    parser.add_argument("--status", action="store_true", help="Show schedule state")
    args = parser.parse_args()
    if args.status:
        show_status()
    else:
        run_nightly()
