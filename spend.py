"""
spend.py
MiniMax API spend tracking and cap enforcement.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Rates are set here — update when promo expires (verify at platform.minimax.io)
MINIMAX_RATES = {
    "minimax-m3": (0.30, 1.20),   # (input, output) per 1M tokens, promo rate
}
DEFAULT_RATE = (0.30, 1.20)


class SpendTracker:
    def __init__(self, log_file: Path, spend_cap: float):
        self.log_file  = log_file
        self.spend_cap = spend_cap
        self.data      = self._load()

    def _load(self) -> dict:
        if self.log_file.exists():
            try:
                return json.loads(self.log_file.read_text())
            except json.JSONDecodeError:
                log.warning("spend.json corrupted — starting fresh")
        return {"daily": {}, "total_input_tokens": 0, "total_output_tokens": 0, "total_usd": 0.0}

    def _save(self):
        self.log_file.write_text(json.dumps(self.data, indent=2))

    def record(self, project: str, input_tokens: int, output_tokens: int, model: str) -> float:
        input_rate, output_rate = MINIMAX_RATES.get(model, DEFAULT_RATE)
        cost  = (input_tokens / 1_000_000 * input_rate) + (output_tokens / 1_000_000 * output_rate)
        today = datetime.now().strftime("%Y-%m-%d")

        if today not in self.data["daily"]:
            self.data["daily"][today] = {"usd": 0.0, "tasks": 0, "by_project": {}}

        self.data["daily"][today]["usd"]    += cost
        self.data["daily"][today]["tasks"]  += 1
        self.data["daily"][today]["by_project"].setdefault(project, 0.0)
        self.data["daily"][today]["by_project"][project] += cost

        self.data["total_usd"]          += cost
        self.data["total_input_tokens"] += input_tokens
        self.data["total_output_tokens"]+= output_tokens
        self._save()
        return cost

    def monthly_spend(self) -> float:
        month = datetime.now().strftime("%Y-%m")
        return sum(v["usd"] for k, v in self.data["daily"].items() if k.startswith(month))

    def daily_spend(self) -> float:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.data["daily"].get(today, {}).get("usd", 0.0)

    def check_caps(self) -> bool:
        """Returns False and logs if monthly spend is at or near cap."""
        monthly = self.monthly_spend()
        if monthly >= self.spend_cap:
            log.error(f"MiniMax spend ${monthly:.2f} hit ${self.spend_cap:.0f} cap — halting.")
            return False
        if monthly >= self.spend_cap * 0.85:
            log.warning(f"MiniMax spend ${monthly:.2f} — approaching ${self.spend_cap:.0f} cap.")
        return True
