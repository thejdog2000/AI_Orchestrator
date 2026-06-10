"""
tests/core/test_spend.py
Unit tests for SpendTracker — uses a temp directory, no external dependencies.
"""

import json
import sys
import time
import unittest
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.spend import SpendTracker, MINIMAX_RATES, DEFAULT_RATE


# ── HELPERS ───────────────────────────────────────────────────────────────────

def make_tracker(tmp_dir, cap=10.0):
    return SpendTracker(Path(tmp_dir) / "spend.json", spend_cap=cap)


# ── record() ─────────────────────────────────────────────────────────────────

class TestRecord(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_returns_cost_as_float(self):
        t = make_tracker(self.tmp)
        cost = t.record("lang", input_tokens=1_000_000, output_tokens=1_000_000, model="minimax-m3")
        input_rate, output_rate = MINIMAX_RATES["minimax-m3"]
        expected = input_rate + output_rate
        self.assertAlmostEqual(cost, expected, places=6)

    def test_cost_formula_input_only(self):
        t = make_tracker(self.tmp)
        cost = t.record("lang", input_tokens=2_000_000, output_tokens=0, model="minimax-m3")
        input_rate, _ = MINIMAX_RATES["minimax-m3"]
        self.assertAlmostEqual(cost, 2 * input_rate, places=6)

    def test_unknown_model_uses_default_rate(self):
        t = make_tracker(self.tmp)
        cost = t.record("lang", input_tokens=1_000_000, output_tokens=0, model="unknown-model")
        input_rate, _ = DEFAULT_RATE
        self.assertAlmostEqual(cost, input_rate, places=6)

    def test_costs_accumulate_across_calls(self):
        t = make_tracker(self.tmp)
        c1 = t.record("lang", 1_000_000, 0, "minimax-m3")
        c2 = t.record("lang", 1_000_000, 0, "minimax-m3")
        self.assertAlmostEqual(t.daily_spend(), c1 + c2, places=6)

    def test_total_usd_accumulates(self):
        t = make_tracker(self.tmp)
        c1 = t.record("lang",  1_000_000, 0, "minimax-m3")
        c2 = t.record("other", 1_000_000, 0, "minimax-m3")
        self.assertAlmostEqual(t.data["total_usd"], c1 + c2, places=6)

    def test_write_is_atomic_file_exists_after_record(self):
        t = make_tracker(self.tmp)
        t.record("lang", 500_000, 500_000, "minimax-m3")
        spend_file = Path(self.tmp) / "spend.json"
        self.assertTrue(spend_file.exists())
        # No .tmp file left behind
        self.assertFalse(spend_file.with_suffix(".tmp").exists())

    def test_spend_file_is_valid_json(self):
        t = make_tracker(self.tmp)
        t.record("lang", 500_000, 500_000, "minimax-m3")
        data = json.loads((Path(self.tmp) / "spend.json").read_text())
        self.assertIn("daily",   data)
        self.assertIn("total_usd", data)


# ── monthly_spend / daily_spend ───────────────────────────────────────────────

class TestAggregation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_monthly_spend_only_sums_current_month(self):
        t = make_tracker(self.tmp)
        # Inject a prior-month entry directly into data
        t.data["daily"]["2020-01-15"] = {"usd": 99.0, "tasks": 1, "by_project": {}}
        cost = t.record("lang", 1_000_000, 0, "minimax-m3")
        monthly = t.monthly_spend()
        self.assertAlmostEqual(monthly, cost, places=6)
        self.assertLess(monthly, 99.0 + cost)

    def test_daily_spend_only_today(self):
        t = make_tracker(self.tmp)
        t.data["daily"]["2020-01-15"] = {"usd": 99.0, "tasks": 1, "by_project": {}}
        cost = t.record("lang", 1_000_000, 0, "minimax-m3")
        self.assertAlmostEqual(t.daily_spend(), cost, places=6)

    def test_daily_spend_zero_with_no_records(self):
        t = make_tracker(self.tmp)
        self.assertEqual(t.daily_spend(), 0.0)

    def test_monthly_spend_zero_with_no_records(self):
        t = make_tracker(self.tmp)
        self.assertEqual(t.monthly_spend(), 0.0)


# ── check_caps ────────────────────────────────────────────────────────────────

class TestCheckCaps(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_returns_true_below_cap(self):
        t = make_tracker(self.tmp, cap=10.0)
        t.record("lang", 1_000_000, 0, "minimax-m3")
        self.assertTrue(t.check_caps())

    def test_returns_false_at_cap(self):
        t = make_tracker(self.tmp, cap=0.0)  # cap is $0 — any spend hits it
        t.record("lang", 1_000_000, 0, "minimax-m3")
        self.assertFalse(t.check_caps())

    def test_returns_false_above_cap(self):
        t = make_tracker(self.tmp, cap=0.0001)
        t.record("lang", 10_000_000, 0, "minimax-m3")  # well over cap
        self.assertFalse(t.check_caps())

    def test_returns_true_at_85_percent_of_cap(self):
        """85% triggers a warning but still returns True."""
        input_rate, _ = DEFAULT_RATE
        # Spend exactly 90% of cap — above warning threshold, below hard cap
        cap = 1.0
        t = make_tracker(self.tmp, cap=cap)
        # Inject spend directly so we don't have to compute token math precisely
        today = datetime.now().strftime("%Y-%m-%d")
        t.data["daily"][today] = {"usd": cap * 0.90, "tasks": 1, "by_project": {}}
        self.assertTrue(t.check_caps())


# ── record_partial ────────────────────────────────────────────────────────────

class TestRecordPartial(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_adds_to_partial_usd(self):
        t = make_tracker(self.tmp)
        t.record_partial("lang", estimated_input_tokens=1_000_000, model="minimax-m3")
        self.assertGreater(t.data.get("partial_usd", 0), 0)

    def test_also_adds_to_total_for_cap_enforcement(self):
        t = make_tracker(self.tmp)
        before = t.data.get("total_usd", 0.0)
        t.record_partial("lang", 1_000_000, "minimax-m3")
        self.assertGreater(t.data["total_usd"], before)

    def test_partial_events_list_populated(self):
        t = make_tracker(self.tmp)
        t.record_partial("lang", 500_000, "minimax-m3", reason="timeout")
        events = t.data.get("partial_events", [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"],  "timeout")
        self.assertEqual(events[0]["project"], "lang")
        self.assertIn("est_usd",    events[0])
        self.assertIn("est_tokens", events[0])
        self.assertIn("date",       events[0])

    def test_partial_events_capped_at_50(self):
        t = make_tracker(self.tmp)
        for _ in range(60):
            t.record_partial("lang", 1000, "minimax-m3")
        self.assertLessEqual(len(t.data["partial_events"]), 50)


# ── Persistence ───────────────────────────────────────────────────────────────

class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_spend_survives_reload(self):
        t1 = make_tracker(self.tmp)
        cost = t1.record("lang", 1_000_000, 500_000, "minimax-m3")
        # Create a fresh instance pointing at the same file
        t2 = make_tracker(self.tmp)
        self.assertAlmostEqual(t2.daily_spend(), cost, places=6)
        self.assertAlmostEqual(t2.data["total_usd"], cost, places=6)

    def test_missing_file_returns_clean_state(self):
        t = make_tracker(self.tmp)
        self.assertEqual(t.data["total_usd"], 0.0)
        self.assertEqual(t.data["daily"],     {})

    def test_corrupted_file_returns_clean_state(self):
        path = Path(self.tmp) / "spend.json"
        path.write_text("{ not valid json }")
        t = SpendTracker(path, spend_cap=10.0)
        self.assertEqual(t.data["total_usd"], 0.0)
        self.assertEqual(t.data["daily"],     {})


if __name__ == "__main__":
    unittest.main()
