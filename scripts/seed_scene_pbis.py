"""
seed_scene_pbis.py
Seeds one epic, 7 PBIs (one per scene), and 4 tasks per PBI for the
lang scene generation pipeline.

Scenes covered (all net-new, written to src/scenes/{lang}/{slug}/):
  ja: konbini-morning, train-station, ramen-shop, temple-visit
  es: mercado, taxi, hotel-checkin

Each PBI gets 4 tasks:
  1. generate-env      — env.js   (Three.js camera, lights, geometry, asset placements)
  2. generate-dialogue — dialogue.js (NPC, vocab/grammar focus, randomizationPool, branches)
  3. generate-beats    — beats.js  (optional beats, A0/A1 variants, SRS cards)
  4. test              — smoke test all three files, verify index.js exports

Safe to re-run — add_epic/add_pbi/add_task skip existing IDs.
"""

import sys, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.task_queue import TaskQueue
from config import DB_PATH

tq = TaskQueue(DB_PATH)

# ── EPIC ──────────────────────────────────────────────────────────────────────

epic = {
    "id":      "epic_lang_scenes",
    "project": "lang",
    "name":    "Scene Generation (7-Night Schedule)",
    "color":   "#10b981",
}
ok = tq.add_epic(epic)
print(f"Epic {'added' if ok else 'exists'}: {epic['id']}")

# ── SCENE DEFINITIONS ─────────────────────────────────────────────────────────
# Each entry drives one PBI + 4 tasks.
# output_dir: path relative to repo root where env/dialogue/beats/index.js land.

SCENES = [
    # ── Japanese ──────────────────────────────────────────────────────────────
    {
        "slug":        "konbini-morning",
        "lang":        "ja",
        "level":       "a0",
        "location":    "convenience store (コンビニ)",
        "npc":         "young cashier named Yuki",
        "context":     "Player buys snacks and a drink at a 7-Eleven-style konbini in Tokyo. "
                       "Interactions: greeting, asking price, paying, receiving change/receipt.",
        "output_dir":  "src/scenes/ja/konbini-morning",
    },
    {
        "slug":        "train-station",
        "lang":        "ja",
        "level":       "a1",
        "location":    "train station ticket gate (駅)",
        "npc":         "station attendant named Tanaka-san",
        "context":     "Player buys a ticket or charges an IC card at a Tokyo metro station. "
                       "Interactions: destination, ticket type, price, platform direction.",
        "output_dir":  "src/scenes/ja/train-station",
    },
    {
        "slug":        "ramen-shop",
        "lang":        "ja",
        "level":       "a1",
        "location":    "ramen shop counter (ラーメン屋)",
        "npc":         "chef/owner named Yamamoto-san",
        "context":     "Player orders ramen at a small counter shop in Tokyo. "
                       "Interactions: broth choice, toppings, spice level, paying.",
        "output_dir":  "src/scenes/ja/ramen-shop",
    },
    {
        "slug":        "temple-visit",
        "lang":        "ja",
        "level":       "a1",
        "location":    "Buddhist temple entrance (お寺)",
        "npc":         "temple monk named Suzuki-san",
        "context":     "Player asks for directions and learns about visiting etiquette at a Kyoto temple. "
                       "Interactions: asking directions, etiquette questions, buying an omamori charm.",
        "output_dir":  "src/scenes/ja/temple-visit",
    },

    # ── Spanish ───────────────────────────────────────────────────────────────
    {
        "slug":        "mercado",
        "lang":        "es",
        "level":       "a0",
        "location":    "market stall at Mercado de la Merced, Mexico City",
        "npc":         "vendor named Señora Petra",
        "context":     "Player buys produce at a busy Mexico City market. "
                       "Interactions: asking for items, quantity (kilo/half-kilo), price, paying.",
        "output_dir":  "src/scenes/es/mercado",
    },
    {
        "slug":        "taxi",
        "lang":        "es",
        "level":       "a1",
        "location":    "taxi in Mexico City (street hail)",
        "npc":         "driver named Javier",
        "context":     "Player hails a taxi and negotiates the ride. "
                       "Interactions: stating destination, route preference, agreeing on fare, small talk.",
        "output_dir":  "src/scenes/es/taxi",
    },
    {
        "slug":        "hotel-checkin",
        "lang":        "es",
        "level":       "a1",
        "location":    "boutique hotel reception in Roma Norte, Mexico City",
        "npc":         "receptionist named Diego",
        "context":     "Player checks into a hotel. "
                       "Interactions: giving name, confirming nights, room preferences, receiving key card.",
        "output_dir":  "src/scenes/es/hotel-checkin",
    },
]

# ── PBIs + TASKS ──────────────────────────────────────────────────────────────

# Task IDs continue from lang_019 (last used in DB).
task_counter = 20

for scene in SCENES:
    slug       = scene["slug"]
    lang       = scene["lang"]
    level      = scene["level"]
    location   = scene["location"]
    npc        = scene["npc"]
    context    = scene["context"]
    out_dir    = scene["output_dir"]
    lang_name  = "Japanese" if lang == "ja" else "Spanish"
    level_name = "absolute beginner (A0)" if level == "a0" else "beginner (A1)"
    pbi_id     = f"pbi_lang_scene_{lang}_{slug.replace('-', '_')}"

    # ── PBI ───────────────────────────────────────────────────────────────────
    pbi = {
        "id":      pbi_id,
        "epic_id": "epic_lang_scenes",
        "project": "lang",
        "title":   f"Scene: {lang.upper()} {level.upper()} — {slug}",
        "description": (
            f"Generate a complete {lang_name} {level_name} scene for: {location}. "
            f"NPC: {npc}. {context} "
            f"Output: {out_dir}/env.js, dialogue.js, beats.js, index.js."
        ),
        "acceptance_criteria": (
            f"- {out_dir}/env.js exports ENV, CAMERA, NPC_ANCHOR\n"
            f"- {out_dir}/dialogue.js exports DIALOGUE with randomizationPool (≥5 variants/key), "
            f"branches, successEnding, failureRecovery, vocabularyFocus (5-8 words), grammarFocus\n"
            f"- {out_dir}/beats.js exports BEATS with optional beats and SRS cards\n"
            f"- {out_dir}/index.js exports default SCENE object matching SCENE_SCHEMA\n"
            f"- Smoke test passes: all exports present, no syntax errors\n"
            f"- Cultural specificity: setting is authentic to {location}"
        ),
        "affected_files": [
            f"{out_dir}/env.js",
            f"{out_dir}/dialogue.js",
            f"{out_dir}/beats.js",
            f"{out_dir}/index.js",
            "src/scenes/index.js",
        ],
        "status": "active",
    }
    ok = tq.add_pbi(pbi)
    print(f"PBI {'added' if ok else 'exists'}: {pbi_id}")

    # ── 4 TASKS ───────────────────────────────────────────────────────────────

    t1_id = f"lang_{task_counter:03d}"; task_counter += 1
    t2_id = f"lang_{task_counter:03d}"; task_counter += 1
    t3_id = f"lang_{task_counter:03d}"; task_counter += 1
    t4_id = f"lang_{task_counter:03d}"; task_counter += 1

    tasks = [
        {
            "id":             t1_id,
            "project":        "lang",
            "description": (
                f"Generate {out_dir}/env.js for the {slug} scene ({lang_name} {level_name}). "
                f"Location: {location}. "
                f"File must export: ENV (Three.js scene setup — camera position, ambient light, "
                f"point lights, floor/walls/counter geometry using BoxGeometry/CylinderGeometry only, "
                f"no CapsuleGeometry), CAMERA (ambient + conversation positions), "
                f"NPC_ANCHOR (name, position, lookAt). "
                f"NPC: {npc}. "
                f"Include head-bob in animate loop. Renderer uses PCFSoftShadowMap + ACESFilmicToneMapping. "
                f"Output format: <<<FILE: {out_dir}/env.js>>> ... <<<END>>>"
            ),
            "complexity":     "medium",
            "effort_category":"generation",
            "perspective":    "game_feel_engineer",
            "rationale":      "Environment first — dialogue and beats are independent of 3D setup.",
            "depends_on":     [],
            "priority":       1,
        },
        {
            "id":             t2_id,
            "project":        "lang",
            "description": (
                f"Generate {out_dir}/dialogue.js for the {slug} scene ({lang_name} {level_name}). "
                f"Location: {location}. NPC: {npc}. Context: {context} "
                f"File must export DIALOGUE object containing: "
                f"npc (name, role, personality), "
                f"opening ({{'{lang}': string, en: string}}), "
                f"vocabularyFocus (5-8 target words for {level_name}), "
                f"grammarFocus (one grammar point), "
                f"randomizationPool (playerGreetings, orderOptions, correctResponses, "
                f"incorrectAttempts — minimum 5 variants each), "
                f"branches (array of {{trigger, npcResponse, playerOptions}}), "
                f"successEnding, failureRecovery (each {{'{lang}': string, en: string}}). "
                f"Output format: <<<FILE: {out_dir}/dialogue.js>>> ... <<<END>>>"
            ),
            "complexity":     "high",
            "effort_category":"generation",
            "perspective":    "speech_linguist",
            "rationale":      "Core dialogue content — highest quality bar, speech_linguist persona.",
            "depends_on":     [],
            "priority":       1,
        },
        {
            "id":             t3_id,
            "project":        "lang",
            "description": (
                f"Generate {out_dir}/beats.js for the {slug} scene ({lang_name} {level_name}). "
                f"Location: {location}. Context: {context} "
                f"File must export BEATS object containing: "
                f"optional (array of optional beats unlocked after first pass, each with "
                f"trigger, npcLine, playerOptions, unlockTier), "
                f"a0Variants (A0-specific NPC lines and player scaffolds for core beats), "
                f"a1Variants (A1-specific — more natural speed, slightly more complexity), "
                f"srsCards (array of {{front, back, hint}} flashcards for vocabulary from this scene, "
                f"minimum 8 cards). "
                f"Output format: <<<FILE: {out_dir}/beats.js>>> ... <<<END>>>"
            ),
            "complexity":     "medium",
            "effort_category":"generation",
            "perspective":    "pedagogy_expert",
            "rationale":      "Optional content and SRS — pedagogy_expert ensures learning value.",
            "depends_on":     [t2_id],
            "priority":       2,
        },
        {
            "id":             t4_id,
            "project":        "lang",
            "description": (
                f"Generate {out_dir}/index.js barrel for the {slug} scene and run smoke test. "
                f"index.js must: import ENV, CAMERA, NPC_ANCHOR from ./env.js; "
                f"import DIALOGUE from ./dialogue.js; import BEATS from ./beats.js; "
                f"export default SCENE object with shape matching SCENE_SCHEMA "
                f"(id: '{lang}/{slug}', language: '{lang}', level: '{level}', "
                f"location: '{location}', three: ENV, camera: CAMERA, npcAnchor: NPC_ANCHOR, "
                f"npc: DIALOGUE.npc, dialogue: DIALOGUE, beats: BEATS). "
                f"Then verify with: node -e \"import('{out_dir}/index.js').then(m => {{ "
                f"const s = m.default; "
                f"if (!s.id || !s.three || !s.dialogue || !s.beats) throw new Error('missing fields'); "
                f"if (!s.dialogue.randomizationPool) throw new Error('missing randomizationPool'); "
                f"const pools = Object.values(s.dialogue.randomizationPool); "
                f"if (pools.some(p => p.length < 5)) throw new Error('pool < 5 variants'); "
                f"console.log('PASS: {slug}'); }})\" "
                f"Output format: <<<FILE: {out_dir}/index.js>>> ... <<<END>>> "
                f"followed by smoke test result."
            ),
            "complexity":     "low",
            "effort_category":"test",
            "perspective":    "qa_tester",
            "rationale":      "Barrel + verification — depends on all three content files existing.",
            "depends_on":     [t1_id, t3_id],
            "priority":       3,
        },
    ]

    for t in tasks:
        pbi_ref = pbi_id
        ok = tq.add_task(t)
        if ok:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE tasks SET pbi_id=? WHERE id=?", (pbi_ref, t["id"]))
            conn.commit()
            conn.close()
        print(f"  Task {'added' if ok else 'exists'}: {t['id']} ({t['effort_category']})")

print(f"\nDone. Next task ID after this batch: lang_{task_counter:03d}")
