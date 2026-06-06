"""
digests.py
Digest generation and scheduling — morning, afternoon, evening reports.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from executor import ollama_generate

log = logging.getLogger(__name__)

from config import CFG as _DEFAULT_CFG

_config: dict = {}

def configure(cfg: dict):
    _config.update(cfg)

def _cfg(key: str):
    # Fall back to config.py defaults if configure() hasn't been called
    return _config.get(key) or _DEFAULT_CFG[key]


def generate_digest(period: str, task_queue, spend_tracker) -> str:
    """qwen3:14b writes prose digest from structured data — lighter model, faster."""
    completed   = task_queue.get_completed_today()
    pending_ct  = len(list(_cfg("PENDING_DIR").glob("*.diff")))
    monthly     = spend_tracker.monthly_spend()
    stats       = task_queue.stats()

    prompt = (
        f"Write a brief {period} digest for Jacob's multi-project AI orchestrator.\n"
        f"Tone: direct, no fluff. Plain text, short bullets. 5-8 points max.\n\n"
        f"DATA:\n"
        f"- Tasks completed today: {len(completed)}\n"
        f"- Descriptions: {json.dumps([t['description'] for t in completed[-10:]])}\n"
        f"- Diffs awaiting review: {pending_ct}\n"
        f"- Monthly spend: ${monthly:.2f} / ${_cfg('MINIMAX_SPEND_CAP'):.0f} cap\n"
        f"- Queue stats: {stats}\n"
        f"- Enabled projects: {_cfg('ENABLED_PROJECTS')}\n\n"
        f"Write the {period} digest:"
    )
    return ollama_generate(prompt, max_tokens=600, model=_cfg("OLLAMA_MODEL_DIGEST"), temperature=0.5)


def write_digest(period: str, task_queue, spend_tracker):
    from dashboard_generator import generate as generate_dashboard

    digest      = generate_digest(period, task_queue, spend_tracker)
    pending     = list(_cfg("PENDING_DIR").glob("*.diff"))
    monthly     = spend_tracker.monthly_spend()
    stats       = task_queue.stats()
    dashboard   = _cfg("DASHBOARD_DIR")

    report = (
        f"\n{'='*60}\n"
        f"{period.upper()} DIGEST — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'='*60}\n"
        f"{digest}\n\n"
        f"PENDING REVIEW : {len(pending)} diffs\n"
        f"MONTHLY SPEND  : ${monthly:.2f} / ${_cfg('MINIMAX_SPEND_CAP'):.0f} cap\n"
        f"TASK STATS     : {stats}\n"
        f"{'='*60}\n"
    )
    print(report)
    (dashboard / "latest_digest.txt").write_text(report)
    generate_dashboard()
    log.info(f"{period.capitalize()} digest written.")
