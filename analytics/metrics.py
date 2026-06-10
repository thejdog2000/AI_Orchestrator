"""
metrics.py
FEAT-4: Evaluation metrics for the orchestrator.

Computes and posts metrics snapshots to #orchestrator-metrics Discord channel.
Scheduled every 10 hours from orchestrator_main.py.

Metrics tracked:
  - Quality gate pass rate (last 30 days)
  - Cost per project / day / week
  - Throughput: committed tasks per night
  - Perspective acceptance rate (committed / attempted)
  - Queue health: queued / running / failed counts
  - Recent failures

NOTE — quality_score reliability:
  quality_score (0-10 from Ollama) is stored per task but should not be used for
  threshold decisions or trend analysis until ~100+ scored tasks have accumulated.
  Until then, the gate boolean (pass/fail) is the operative signal — it tells you
  whether a task was committed or blocked, not whether the output was actually good.

  TODO: Once 100+ tasks have quality_score > 0, revisit metrics_data() to:
    - Compute score distribution (p25/p50/p75) per project and perspective
    - Correlate low scores (<5) with subsequent git reverts or manual fixes
    - Use score trend (rolling 30-task avg) to detect silent model/prompt degradation
    - Consider making the pass threshold dynamic (e.g. flag tasks scoring below
      the project's rolling average by >2 points) rather than the current boolean

Usage:
  python metrics.py              # print snapshot to stdout
  python metrics.py --discord    # post to Discord #metrics channel

env vars:
  DISCORD_CHANNEL_METRICS    — channel ID for #orchestrator-metrics
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config import CFG, MINIMAX_SPEND_CAP
from core.task_queue import TaskQueue
from core.spend import SpendTracker
import core.notify as notify

LOGS_DIR = CFG["LOGS_DIR"] if isinstance(CFG.get("LOGS_DIR"), Path) else BASE_DIR / "logs"


class MetricsTracker:
    """Compute and format orchestrator performance metrics."""

    def __init__(self, days: int = 30):
        self.tq   = TaskQueue()
        self.st   = SpendTracker(LOGS_DIR / "spend.json", MINIMAX_SPEND_CAP)
        self.days = days

    def compute(self) -> dict:
        """Fetch all metrics from SQLite + spend.json."""
        data = self.tq.metrics_data(days=self.days)
        data["monthly_spend"] = round(self.st.monthly_spend(), 2)
        data["daily_spend"]   = round(self.st.daily_spend(), 4)
        data["spend_cap"]     = MINIMAX_SPEND_CAP
        data["spend_pct"]     = round(data["monthly_spend"] / MINIMAX_SPEND_CAP * 100, 1)
        data["generated_at"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
        return data

    # ── FORMATTERS ────────────────────────────────────────────────────────────

    def format_discord(self, data: dict) -> str:
        """Format a metrics snapshot for Discord posting."""
        qg   = data["quality_gate"]
        cost = data["cost_by_project"]

        # Throughput: sum last night (most recent day)
        throughput = data["throughput_by_day"]
        last_night = max(throughput.values()) if throughput else 0

        # Perspective leaderboard (top 5 by tasks attempted)
        persp = sorted(data["perspectives"], key=lambda p: p["attempted"], reverse=True)[:5]
        persp_lines = "\n".join(
            f"  {p['name'].replace('_', ' '):<24} {p['attempted']:>3} tasks · {p['rate']:>5.0f}% accepted"
            for p in persp
        ) or "  (no data yet)"

        # Cost by project
        cost_lines = "\n".join(
            f"  {proj:<10} {v['n']:>3} committed · ${v['total']:.2f}"
            for proj, v in sorted(cost.items())
        ) or "  (no completed tasks yet)"

        # Recent failures
        fail_lines = ""
        if data["recent_failures"]:
            fail_lines = "\n**Recent failures:**\n" + "\n".join(
                f"  `{r['id']}` [{r['project']}] {(r['description'] or '')[:50]}…"
                for r in data["recent_failures"][:3]
            )

        # Queue health
        queued  = sum(r["n"] for r in data["queue_health"] if r["status"] == "queued")
        running = sum(r["n"] for r in data["queue_health"] if r["status"] == "running")
        pending = sum(r["n"] for r in data["queue_health"] if r["status"] == "pending_review")
        failed  = sum(r["n"] for r in data["queue_health"] if r["status"] == "failed")

        bar = "━" * 38

        msg = (
            f"**📊 Metrics Snapshot** — {data['generated_at']}\n"
            f"{bar}\n"
            f"**Quality gate:** {qg['pass_rate']}% pass ({qg['passed']}/{qg['total']} tasks, last {self.days}d)\n"
            f"**Last night:**   {last_night} tasks committed\n"
            f"**Monthly spend:** ${data['monthly_spend']:.2f} / ${data['spend_cap']:.0f} ({data['spend_pct']}%)\n"
            f"\n**By project (last {self.days}d):**\n{cost_lines}\n"
            f"\n**Top perspectives:**\n{persp_lines}\n"
            f"\n**Queue:** {queued} queued · {running} running · {pending} pending review · {failed} failed"
            f"{fail_lines}\n"
            f"{bar}\n"
            f"Dashboard: http://localhost:{os.environ.get('DASHBOARD_PORT', '8080')}"
        )
        return msg

    def format_terminal(self, data: dict) -> str:
        """Format metrics for terminal output."""
        qg = data["quality_gate"]
        lines = [
            f"\n{'='*55}",
            f"ORCHESTRATOR METRICS — {data['generated_at']}",
            f"{'='*55}",
            f"Quality gate:    {qg['pass_rate']}% pass ({qg['passed']}/{qg['total']})",
            f"Monthly spend:   ${data['monthly_spend']:.2f} / ${data['spend_cap']:.0f} ({data['spend_pct']}%)",
            f"Daily spend:     ${data['daily_spend']:.4f}",
            "",
            "Cost by project:",
        ]
        for proj, v in sorted(data["cost_by_project"].items()):
            lines.append(f"  {proj:<12} {v['n']:>3} tasks · ${v['total']:.4f}")

        lines += ["", "Perspective acceptance (last 30d):"]
        persp = sorted(data["perspectives"], key=lambda p: p["attempted"], reverse=True)
        for p in persp[:8]:
            lines.append(
                f"  {p['name'].replace('_', ' '):<26} "
                f"{p['committed']:>2}/{p['attempted']:<3} = {p['rate']:>5.1f}%"
            )

        lines += ["", "Throughput (tasks committed per day):"]
        for day, n in sorted(data["throughput_by_day"].items())[-7:]:
            lines.append(f"  {day}  {'█' * n} {n}")

        qh_queued  = sum(r["n"] for r in data["queue_health"] if r["status"] == "queued")
        qh_failed  = sum(r["n"] for r in data["queue_health"] if r["status"] == "failed")
        qh_pending = sum(r["n"] for r in data["queue_health"] if r["status"] == "pending_review")
        lines += [
            "",
            f"Queue now: {qh_queued} queued / {qh_pending} pending review / {qh_failed} failed",
            f"{'='*55}",
        ]
        return "\n".join(lines)


# ── DISCORD POSTING ───────────────────────────────────────────────────────────

_LAST_POSTED_FILE = LOGS_DIR / "metrics_last_posted.txt"


def _seconds_since_last_post() -> float:
    """Return seconds since last successful metrics post, or infinity if never."""
    try:
        ts = datetime.fromisoformat(_LAST_POSTED_FILE.read_text().strip())
        return (datetime.now() - ts).total_seconds()
    except Exception:
        return float("inf")


def post_metrics_snapshot(days: int = 30, min_interval_hours: float = None) -> bool:
    """
    Compute metrics and post to #orchestrator-metrics.
    Called by scheduler in orchestrator_main.py every N hours.

    min_interval_hours: if set, skip posting if a successful post occurred more
    recently than this many hours ago (guards against restart-loop double-posts).
    Defaults to METRICS_INTERVAL_HOURS from config.
    Returns True if Discord post succeeded.
    """
    if min_interval_hours is None:
        from config import METRICS_INTERVAL_HOURS
        min_interval_hours = METRICS_INTERVAL_HOURS

    if _seconds_since_last_post() < min_interval_hours * 3600:
        log.info("Metrics snapshot skipped — posted less than %sh ago", min_interval_hours)
        return False

    try:
        tracker = MetricsTracker(days=days)
        data    = tracker.compute()
        msg     = tracker.format_discord(data)
        ok      = notify.post("metrics", msg)
        if ok:
            log.info("Metrics snapshot posted to #orchestrator-metrics")
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            _LAST_POSTED_FILE.write_text(datetime.now().isoformat())
        else:
            log.warning("Metrics post failed — Discord not configured?")
        return ok
    except Exception as e:
        log.error(f"Metrics computation failed: {e}")
        return False


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Orchestrator metrics")
    parser.add_argument("--discord", action="store_true", help="Post to Discord #metrics")
    parser.add_argument("--days",    type=int, default=30,  help="Rolling window in days")
    args = parser.parse_args()

    tracker = MetricsTracker(days=args.days)
    data    = tracker.compute()

    print(tracker.format_terminal(data))

    if args.discord:
        ok = post_metrics_snapshot(days=args.days)
        print(f"\nDiscord post: {'✓ sent' if ok else '✗ failed (check DISCORD_CHANNEL_METRICS)'}")
