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

Manual run (from orchestrator root):
  python pipeline/lang_pipeline.py              # run tonight's scenes
  python pipeline/lang_pipeline.py --status     # show schedule state
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

from config import (MINIMAX_API_BASE, MINIMAX_MODEL, OLLAMA_BASE,
                    OLLAMA_MODEL_CODE, REPO_PATHS, TASKS_DIR, MINIMAX_SPEND_CAP, LOGS_DIR)

BASE_DIR   = Path(__file__).parent.parent   # orchestrator root
REPO_PATH  = REPO_PATHS["lang"]
STATE_FILE = TASKS_DIR / "lang_schedule.json"

# ── 7-NIGHT SCHEDULE ─────────────────────────────────────────────────────────
# Each scene: {id, language, level, location, status: pending|pass|fail, night}
# Status persists in lang_schedule.json across runs.

SCENE_SCHEDULE = [
    # Night 1
    # ja_izakaya_01 skipped — migrated manually to src/scenes/ja/izakaya-morning/
    {"id": "ja_konbini_01",     "night": 1, "lang": "ja", "level": "a0",
     "location": "konbini",          "output_dir": "src/scenes/ja/konbini-morning"},
    # Night 2
    {"id": "ja_train_01",       "night": 2, "lang": "ja", "level": "a1",
     "location": "train station",    "output_dir": "src/scenes/ja/train-station"},
    {"id": "ja_ramen_01",       "night": 2, "lang": "ja", "level": "a1",
     "location": "ramen shop",       "output_dir": "src/scenes/ja/ramen-shop"},
    # Night 3
    {"id": "ja_temple_01",      "night": 3, "lang": "ja", "level": "a1",
     "location": "temple/directions","output_dir": "src/scenes/ja/temple-visit"},
    # Night 4
    # es_taco_01 skipped — migrated manually to src/scenes/es/taco-vendor/
    {"id": "es_mercado_01",     "night": 4, "lang": "es", "level": "a0",
     "location": "mercado",          "output_dir": "src/scenes/es/mercado"},
    # Night 5
    # es_cafe_01 skipped — migrated manually to src/scenes/es/cafe-morning/
    {"id": "es_taxi_01",        "night": 5, "lang": "es", "level": "a1",
     "location": "taxi",             "output_dir": "src/scenes/es/taxi"},
    # Night 6
    {"id": "es_hotel_01",       "night": 6, "lang": "es", "level": "a1",
     "location": "hotel check-in",   "output_dir": "src/scenes/es/hotel-checkin"},
    # Night 7 — buffer: retry failures, expand randomization
]

# ── SCENE FILE SCHEMAS ───────────────────────────────────────────────────────
# Each scene lives in src/scenes/{lang}/{slug}/ with 4 files.
# These schemas are injected into generation prompts.

ENV_SCHEMA = """
// env.js — Three.js environment only. No dialogue content.
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.module.js';

export const CAMERA = {
  ambient:      { pos: new THREE.Vector3(x, y, z), lookAt: new THREE.Vector3(x, y, z) },
  conversation: { pos: new THREE.Vector3(x, y, z), lookAt: new THREE.Vector3(x, y, z) },
};

export const NPC_ANCHOR = { name: string, position: [x, y, z], lookAt: [x, y, z] };

// ENV: Three.js scene factory. Returns { startCinematic, dispose }.
// Rules: Three.js r128 only. No CapsuleGeometry (use CylinderGeometry+SphereGeometry).
// Must include: floor, back wall, counter, NPC body+head, 2-4 scene-specific props,
// at least one practical point light, NPC head-bob in animate loop,
// camera lerp from ambient → conversation on startCinematic(),
// resize handler, dispose() that cancels animId + removes resize listener.
export const ENV = { camera: CAMERA, npcAnchor: NPC_ANCHOR, createScene };
export function createScene({ canvas, getAppState, onConversationReady }) { ... }
"""

DIALOGUE_SCHEMA = """
// dialogue.js — NPC data, all conversation content, randomization pools.
export const DIALOGUE = {
  npc: { name: string, role: string, personality: string },
  opening: { [lang]: string, en: string },
  vocabularyFocus: string[],  // 5-8 target words for this level
  grammarFocus: string,       // one grammar point (e.g. "て-form requests")
  randomizationPool: {
    playerGreetings:    string[],  // ≥5 variants
    orderOptions:       string[],  // ≥5 variants
    correctResponses:   string[],  // ≥5 variants
    incorrectAttempts:  string[],  // ≥5 variants
  },
  branches: [{
    trigger: string,
    npcResponse: { [lang]: string, en: string },
    playerOptions: string[],
  }],
  successEnding:   { [lang]: string, en: string },
  failureRecovery: { [lang]: string, en: string },
};
"""

BEATS_SCHEMA = """
// beats.js — optional beats, skill-level variants, SRS cards.
export const BEATS = {
  optional: [{           // unlocked on repeat visits
    id: string,
    trigger: string,
    npcLine: { [lang]: string, en: string },
    playerOptions: string[],
    unlockTier: 'practiced' | 'confident' | 'mastered',
  }],
  a0Variants: [{         // simpler NPC lines + full scaffolding for A0 players
    beatRef: string,
    npcLine: { [lang]: string, en: string },
    scaffold: string,
  }],
  a1Variants: [{         // natural-speed NPC lines + reduced scaffolding for A1 players
    beatRef: string,
    npcLine: { [lang]: string, en: string },
    scaffold: string,
  }],
  srsCards: [{           // flashcards earned from this scene (min 8)
    front: string,       // target language
    back: string,        // English meaning
    hint: string,        // usage note or example sentence
  }],
};
"""

INDEX_TEMPLATE = """\
// index.js — auto-generated barrel. Do not edit by hand.
import {{ createScene, CAMERA, NPC_ANCHOR }} from './env.js';
import {{ DIALOGUE }} from './dialogue.js';
import {{ BEATS }} from './beats.js';

const SCENE = {{
  id:        '{scene_id}',
  language:  '{lang}',
  level:     '{level}',
  location:  '{location}',
  camera:    CAMERA,
  npcAnchor: NPC_ANCHOR,
  createScene,
  npc:       DIALOGUE.npc,
  dialogue:  DIALOGUE,
  beats:     BEATS,
}};

export default SCENE;
"""

# ── SCHEDULE STATE ────────────────────────────────────────────────────────────

def _load_state() -> dict:
    """
    Load schedule state from lang_schedule.json.
    State structure:
      current_night: int     — which night we're on (1-7, set explicitly)
      scenes: {scene_id: {status, attempts, last_run}}
    """
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        # Migrate old flat format (scene_id → {status, attempts}) to new nested format
        if "scenes" not in data:
            data = {
                "current_night": _infer_night_from_legacy(data),
                "scenes":        data,
            }
        return data
    return {
        "current_night": 1,
        "scenes": {s["id"]: {"status": "pending", "attempts": 0, "last_run": None}
                   for s in SCENE_SCHEDULE},
    }


def _infer_night_from_legacy(flat_state: dict) -> int:
    """
    One-time migration helper: infer night number from old flat state format.
    Only called when upgrading from the old format — not used after migration.
    """
    passed_nights = {
        SCENE_SCHEDULE[[s["id"] for s in SCENE_SCHEDULE].index(sid)]["night"]
        for sid, v in flat_state.items() if v.get("status") == "pass"
        if sid in [s["id"] for s in SCENE_SCHEDULE]
    }
    return (max(passed_nights) + 1) if passed_nights else 1


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _current_night(state: dict) -> int:
    """Read current night from state. Explicit, not inferred from scene status (MED-3 fix)."""
    return state.get("current_night", 1)


def _advance_night_if_complete(state: dict) -> bool:
    """
    After a nightly run, check if all scenes for the current night have passed.
    If so, increment current_night. Returns True if night advanced.
    """
    night = _current_night(state)
    night_scenes = [s for s in SCENE_SCHEDULE if s["night"] == night]
    if not night_scenes:
        # Night 7 = buffer night, always advance
        state["current_night"] = min(night + 1, 7)
        return True

    all_passed = all(
        state["scenes"].get(s["id"], {}).get("status") == "pass"
        for s in night_scenes
    )
    if all_passed:
        state["current_night"] = min(night + 1, 7)
        log.info(f"[lang] Night {night} complete — advancing to night {state['current_night']}")
        return True
    return False


def _due_tonight(state: dict, night_number: int) -> list[dict]:
    """Return scenes due tonight: scheduled night == tonight OR previously failed."""
    due = []
    scenes = state.get("scenes", {})
    for scene in SCENE_SCHEDULE:
        s      = scenes.get(scene["id"], {})
        status = s.get("status", "pending")
        if status == "pass":
            continue
        if scene["night"] == night_number or status == "fail":
            due.append(scene)
    return due

# ── MINIMAX + OLLAMA ──────────────────────────────────────────────────────────

def _ollama_prompt(scene: dict) -> str:
    lang_name  = "Japanese" if scene["lang"] == "ja" else "Spanish"
    level_name = "absolute beginner (A0)" if scene["level"] == "a0" else "beginner (A1)"
    out_dir    = scene.get("output_dir", f"src/scenes/{scene['lang']}/unknown")
    prompt = (
        f"Write a system prompt for generating a language learning scene split across 3 files.\n"
        f"Language: {lang_name} ({level_name})\n"
        f"Location: {scene['location']}\n"
        f"Output directory: {out_dir}/\n"
        f"The output must be 3 files: env.js, dialogue.js, beats.js.\n"
        f"Each file must follow its schema exactly.\n"
        f"Randomization pools must have at least 5 variants each.\n"
        f"Vocabulary: 5-8 target words appropriate for the level.\n"
        f"SRS cards: minimum 8.\n\n"
        f"env.js schema:\n{ENV_SCHEMA}\n\n"
        f"dialogue.js schema:\n{DIALOGUE_SCHEMA}\n\n"
        f"beats.js schema:\n{BEATS_SCHEMA}\n\n"
        f"Write a concise system prompt (no preamble):"
    )
    try:
        resp = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": OLLAMA_MODEL_CODE, "prompt": prompt, "stream": False,
                  "options": {"num_ctx": 8192, "num_predict": 600}},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        log.error(f"Ollama prompt generation failed: {e}")
        return (
            f"Generate a {lang_name} {level_name} language learning scene "
            f"for: {scene['location']}. Output 3 files: env.js, dialogue.js, beats.js."
        )


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

def _write_file_blocks(content: str, repo_path: Path) -> list[str]:
    """
    Parse <<<FILE: path>>> ... <<<END>>> blocks from LLM output and write each to disk.
    Returns list of relative paths written. Blocks outside the repo are rejected.
    """
    import re
    blocks  = re.findall(r"<<<FILE:\s*(.+?)>>>\s*\n(.*?)<<<END>>>", content, re.DOTALL)
    written = []
    for rel_path, file_content in blocks:
        rel  = rel_path.strip()
        dest = (repo_path / rel).resolve()
        if not dest.is_relative_to(repo_path.resolve()):
            log.error(f"[lang] BLOCKED path traversal attempt: {rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(file_content.rstrip() + "\n")
        log.info(f"[lang] Wrote {rel} ({len(file_content)} chars)")
        written.append(rel)
    return written


def _generate_scene(scene: dict) -> dict:
    """
    Generate one scene as 3 files (env.js, dialogue.js, beats.js) + auto-written index.js.
    Returns result dict with success, tokens, smoke_passed, files_written.
    """
    scene_id = scene["id"]
    out_dir  = scene.get("output_dir")

    if not out_dir:
        log.warning(f"[lang] {scene_id}: no output_dir — skipping")
        return {"success": True, "smoke_passed": True, "input_tokens": 0, "output_tokens": 0}

    lang_name  = "Japanese" if scene["lang"] == "ja" else "Spanish"
    level_name = "absolute beginner (A0)" if scene["level"] == "a0" else "beginner (A1)"

    system = _ollama_prompt(scene)
    user   = (
        f"Generate a complete {lang_name} {level_name} language learning scene.\n"
        f"Location: {scene['location']}\n"
        f"Output directory: {out_dir}/\n\n"
        f"Output exactly 3 files using this format for each:\n"
        f"<<<FILE: {out_dir}/env.js>>>\n"
        f"[full env.js content]\n"
        f"<<<END>>>\n"
        f"<<<FILE: {out_dir}/dialogue.js>>>\n"
        f"[full dialogue.js content]\n"
        f"<<<END>>>\n"
        f"<<<FILE: {out_dir}/beats.js>>>\n"
        f"[full beats.js content]\n"
        f"<<<END>>>\n\n"
        f"Requirements:\n"
        f"- env.js: Three.js r128 only, no CapsuleGeometry, NPC head-bob, camera lerp, dispose()\n"
        f"- dialogue.js: randomizationPool minimum 5 variants per key, "
        f"  vocabularyFocus 5-8 words, one grammarFocus point\n"
        f"- beats.js: minimum 8 SRS cards, A0 and A1 variants for core beats, "
        f"  at least 2 optional beats\n"
        f"Cultural specificity: setting must be authentic to {scene['location']}."
    )

    try:
        content, in_tok, out_tok = _minimax_generate(system, user)
    except Exception as e:
        return {"success": False, "error": str(e), "input_tokens": 0, "output_tokens": 0}

    # Write the 3 generated files
    written = _write_file_blocks(content, REPO_PATH)

    # Validate expected files are present
    expected = [f"{out_dir}/env.js", f"{out_dir}/dialogue.js", f"{out_dir}/beats.js"]
    missing  = [f for f in expected if f not in written]
    if missing:
        log.warning(f"[lang] {scene_id}: missing files in response: {missing}")
        return {"success": False, "error": f"missing_files:{','.join(missing)}",
                "input_tokens": in_tok, "output_tokens": out_tok}

    # Auto-generate index.js barrel (no LLM needed — always the same shape)
    scene_slug = out_dir.split("/")[-1]          # e.g. "konbini-morning"
    index_content = INDEX_TEMPLATE.format(
        scene_id = f"{scene['lang']}/{scene_slug}",
        lang     = scene["lang"],
        level    = scene["level"],
        location = scene["location"],
    )
    index_path = REPO_PATH / out_dir / "index.js"
    index_path.write_text(index_content)
    written.append(f"{out_dir}/index.js")
    log.info(f"[lang] Wrote {out_dir}/index.js (auto-generated barrel)")

    # Smoke test against index.js
    smoke_passed, smoke_output = _run_smoke_test(index_path)
    if not smoke_passed:
        log.warning(f"[lang] Smoke test FAILED for {scene_id}:\n{smoke_output}")
    else:
        log.info(f"[lang] Smoke test passed for {scene_id}")

    return {
        "success":       True,
        "smoke_passed":  smoke_passed,
        "smoke_output":  smoke_output,
        "files_written": written,
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

    # Spend cap check before burning tokens on a full nightly run
    from core.spend import SpendTracker
    st = SpendTracker(LOGS_DIR / "spend.json", MINIMAX_SPEND_CAP)
    if not st.check_caps():
        log.error("[lang] Spend cap reached — skipping nightly run")
        return

    state        = _load_state()
    night_number = _current_night(state)   # MED-3: explicit, not inferred from scene status
    due          = _due_tonight(state, night_number)

    if not due:
        log.info(f"[lang] Night {night_number}: no scenes due tonight")
        return

    log.info(f"[lang] Night {night_number}: generating {len(due)} scene(s)")
    total_in = total_out = passed = failed = 0

    for scene in due:
        sid = scene["id"]
        log.info(f"[lang] Generating: {sid}")
        s = state["scenes"].setdefault(sid, {"status": "pending", "attempts": 0, "last_run": None})
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
            s.pop("error", None)
            passed += 1

        _save_state(state)
        time.sleep(2)   # brief pause between scenes

    # Advance to next night if all scenes for tonight passed
    _advance_night_if_complete(state)
    _save_state(state)

    log.info(
        f"[lang] Night {night_number} complete: "
        f"{passed} passed / {failed} failed | "
        f"{total_in} in / {total_out} out tokens"
    )


# ── STATUS REPORT ─────────────────────────────────────────────────────────────

def show_status():
    state       = _load_state()
    scenes_data = state.get("scenes", {})
    current     = _current_night(state)
    print(f"\nLanguage Pipeline — Scene Schedule (current night: {current})\n{'─'*60}")
    for night in range(1, 8):
        night_scenes = [s for s in SCENE_SCHEDULE if s["night"] == night]
        marker = " ◀ tonight" if night == current else ""
        if not night_scenes:
            print(f"  Night {night}: buffer (retries + gap fill){marker}")
            continue
        print(f"  Night {night}:{marker}")
        for s in night_scenes:
            st     = scenes_data.get(s["id"], {})
            status = st.get("status", "pending")
            icon   = {"pass": "✓", "fail": "✗", "pending": "○"}.get(status, "○")
            att    = st.get("attempts", 0)
            err    = f" [{st.get('error','')}]" if status == "fail" else ""
            dest = s.get("output_dir", "—")
            print(f"    {icon} {s['id']:<28} {status:<8} attempts={att}  → {dest}{err}")
    print()


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    # Ensure orchestrator root is on sys.path when run directly
    _root = str(Path(__file__).parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Language scene pipeline")
    parser.add_argument("--status", action="store_true", help="Show schedule state")
    args = parser.parse_args()
    if args.status:
        show_status()
    else:
        run_nightly()
