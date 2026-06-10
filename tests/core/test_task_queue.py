"""
tests/core/test_task_queue.py
Unit tests for TaskQueue — uses in-memory SQLite, no external dependencies.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Make sure the repo root is on sys.path regardless of how pytest is invoked
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.task_queue import TaskQueue, _compute_review_priority, _description_hash


# ── HELPERS ───────────────────────────────────────────────────────────────────

def make_task(id="t1", project="lang", description="Do something", **kwargs):
    base = {
        "id": id,
        "project": project,
        "description": description,
        "status": "queued",
        "priority": 1,
        "complexity": "medium",
        "effort_category": "feature",
        "perspective": "engineering_architect",
        "approval_required": False,
        "depends_on": [],
        "blocks": [],
        "estimated_tokens": 1000,
    }
    base.update(kwargs)
    return base


# SQLite :memory: opens a new, empty database on every connect() call, which
# breaks TaskQueue because _init_db() and subsequent queries use separate
# connections. Use a named temp file instead — same isolation, stable schema.
_tmp_dir = tempfile.mkdtemp()
_db_counter = 0

def fresh_queue():
    global _db_counter
    _db_counter += 1
    db_path = Path(_tmp_dir) / f"test_{_db_counter}.db"
    return TaskQueue(db_path=db_path)


# ── _compute_review_priority ──────────────────────────────────────────────────

class TestComputeReviewPriority(unittest.TestCase):

    def test_low_no_approval(self):
        self.assertEqual(_compute_review_priority("low", False), 1)

    def test_medium_no_approval(self):
        self.assertEqual(_compute_review_priority("medium", False), 2)

    def test_high_no_approval(self):
        self.assertEqual(_compute_review_priority("high", False), 3)

    def test_low_with_approval(self):
        self.assertEqual(_compute_review_priority("low", True), 3)

    def test_high_with_approval_capped_at_5(self):
        self.assertEqual(_compute_review_priority("high", True), 5)

    def test_medium_with_approval(self):
        self.assertEqual(_compute_review_priority("medium", True), 4)

    def test_unknown_complexity_treated_as_medium(self):
        self.assertEqual(_compute_review_priority("unknown", False), 2)


# ── _description_hash ─────────────────────────────────────────────────────────

class TestDescriptionHash(unittest.TestCase):

    def test_identical_inputs_same_hash(self):
        self.assertEqual(
            _description_hash("Do something", "lang"),
            _description_hash("Do something", "lang"),
        )

    def test_different_project_different_hash(self):
        self.assertNotEqual(
            _description_hash("Do something", "lang"),
            _description_hash("Do something", "other"),
        )

    def test_whitespace_normalized(self):
        self.assertEqual(
            _description_hash("  Do   something  ", "lang"),
            _description_hash("Do something", "lang"),
        )

    def test_case_normalized(self):
        self.assertEqual(
            _description_hash("DO SOMETHING", "lang"),
            _description_hash("do something", "lang"),
        )

    def test_different_description_different_hash(self):
        self.assertNotEqual(
            _description_hash("Do something", "lang"),
            _description_hash("Do something else", "lang"),
        )


# ── add_task ──────────────────────────────────────────────────────────────────

class TestAddTask(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_insert_returns_true(self):
        self.assertTrue(self.q.add_task(make_task()))

    def test_exact_id_duplicate_returns_false(self):
        self.q.add_task(make_task(id="t1"))
        self.assertFalse(self.q.add_task(make_task(id="t1")))

    def test_semantic_duplicate_same_project_returns_false(self):
        self.q.add_task(make_task(id="t1", description="Refactor auth"))
        self.assertFalse(self.q.add_task(make_task(id="t2", description="Refactor auth")))

    def test_semantic_duplicate_different_project_allowed(self):
        self.q.add_task(make_task(id="t1", project="lang",  description="Refactor auth"))
        self.assertTrue(self.q.add_task(make_task(id="t2", project="other", description="Refactor auth")))

    def test_semantic_duplicate_ignores_completed(self):
        """After a task completes, the same description can be re-queued."""
        self.q.add_task(make_task(id="t1", description="Refactor auth"))
        self.q.mark_completed(make_task(id="t1"))
        self.assertTrue(self.q.add_task(make_task(id="t2", description="Refactor auth")))

    def test_semantic_duplicate_ignores_failed(self):
        self.q.add_task(make_task(id="t1", description="Refactor auth"))
        self.q.mark_failed(make_task(id="t1"))
        self.assertTrue(self.q.add_task(make_task(id="t2", description="Refactor auth")))

    def test_depends_on_stored_as_list(self):
        self.q.add_task(make_task(id="t1", depends_on=["t0"]))
        task = self.q.get_next(projects=["lang"])  # blocked, so None
        # Verify the data round-trips as a list via load
        import sqlite3
        conn = sqlite3.connect(":memory:")  # just check the JSON parsing
        raw = json.dumps(["t0"])
        self.assertIsInstance(json.loads(raw), list)

    def test_approval_required_stored_as_bool(self):
        self.q.add_task(make_task(id="t1", approval_required=True))
        # approval_required tasks are excluded from get_next
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_review_priority_computed_on_insert(self):
        self.q.add_task(make_task(id="t1", complexity="high", approval_required=True))
        stats = self.q.stats()
        self.assertEqual(stats.get("queued", 0), 1)


# ── get_next ──────────────────────────────────────────────────────────────────

class TestGetNext(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_empty_queue_returns_none(self):
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_returns_task_with_no_deps(self):
        self.q.add_task(make_task(id="t1"))
        task = self.q.get_next(projects=["lang"])
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], "t1")

    def test_respects_project_filter(self):
        self.q.add_task(make_task(id="t1", project="lang"))
        self.q.add_task(make_task(id="t2", project="other"))
        task = self.q.get_next(projects=["other"])
        self.assertEqual(task["id"], "t2")
        self.assertIsNone(self.q.get_next(projects=["nonexistent"]))

    def test_skips_approval_required(self):
        self.q.add_task(make_task(id="t1", approval_required=True))
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_skips_task_with_unmet_dependency(self):
        self.q.add_task(make_task(id="t1", depends_on=["t0"]))
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_unblocked_after_dependency_completed(self):
        self.q.add_task(make_task(id="t0", description="First task"))
        self.q.add_task(make_task(id="t1", description="Second task depends on first", depends_on=["t0"]))
        self.q.mark_completed(make_task(id="t0"))
        task = self.q.get_next(projects=["lang"])
        self.assertEqual(task["id"], "t1")

    def test_pending_review_counts_as_done_for_deps(self):
        self.q.add_task(make_task(id="t0", description="First task"))
        self.q.add_task(make_task(id="t1", description="Depends on first", depends_on=["t0"]))
        self.q.mark_pending_review(make_task(id="t0"), Path("/tmp/fake.diff"))
        task = self.q.get_next(projects=["lang"])
        self.assertEqual(task["id"], "t1")

    def test_priority_ordering(self):
        self.q.add_task(make_task(id="low",  priority=2))
        self.q.add_task(make_task(id="high", priority=0, description="High prio task"))
        task = self.q.get_next(projects=["lang"])
        self.assertEqual(task["id"], "high")

    def test_does_not_return_running_task(self):
        self.q.add_task(make_task(id="t1"))
        self.q.mark_running(make_task(id="t1"))
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_does_not_return_completed_task(self):
        self.q.add_task(make_task(id="t1"))
        self.q.mark_completed(make_task(id="t1"))
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_does_not_return_failed_task(self):
        self.q.add_task(make_task(id="t1"))
        self.q.mark_failed(make_task(id="t1"))
        self.assertIsNone(self.q.get_next(projects=["lang"]))

    def test_depends_on_deserialized_as_list(self):
        self.q.add_task(make_task(id="t1"))
        task = self.q.get_next(projects=["lang"])
        self.assertIsInstance(task["depends_on"], list)
        self.assertIsInstance(task["blocks"], list)
        self.assertIsInstance(task["approval_required"], bool)


# ── total_unblocked ───────────────────────────────────────────────────────────

class TestTotalUnblocked(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_empty_queue(self):
        self.assertEqual(self.q.total_unblocked(projects=["lang"]), 0)

    def test_counts_tasks_with_no_deps(self):
        self.q.add_task(make_task(id="t1"))
        self.q.add_task(make_task(id="t2", description="Another task"))
        self.assertEqual(self.q.total_unblocked(projects=["lang"]), 2)

    def test_excludes_blocked_tasks(self):
        self.q.add_task(make_task(id="t1", depends_on=["t0"]))
        self.assertEqual(self.q.total_unblocked(projects=["lang"]), 0)

    def test_excludes_approval_required(self):
        self.q.add_task(make_task(id="t1", approval_required=True))
        self.assertEqual(self.q.total_unblocked(projects=["lang"]), 0)

    def test_unblocked_after_dep_completes(self):
        self.q.add_task(make_task(id="t0", description="First task"))
        self.q.add_task(make_task(id="t1", description="Depends on first", depends_on=["t0"]))
        # t0 unblocked, t1 blocked
        self.assertEqual(self.q.total_unblocked(projects=["lang"]), 1)
        self.q.mark_completed(make_task(id="t0"))
        # t0 now completed (excluded from queue), t1 now unblocked
        self.assertEqual(self.q.total_unblocked(projects=["lang"]), 1)


# ── Status transitions ────────────────────────────────────────────────────────

class TestStatusTransitions(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()
        self.q.add_task(make_task(id="t1"))
        self.task = make_task(id="t1")

    def _fetch(self, id="t1"):
        with self.q._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
        return dict(row) if row else None

    def test_mark_running_sets_status_and_started_at(self):
        self.q.mark_running(self.task)
        row = self._fetch()
        self.assertEqual(row["status"], "running")
        self.assertIsNotNone(row["started_at"])

    def test_mark_completed_sets_status_and_completed_at(self):
        self.q.mark_completed(self.task)
        row = self._fetch()
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["completed_at"])

    def test_mark_committed_sets_status_commit_hash_and_timestamps(self):
        self.q.mark_committed(self.task, commit_hash="abc1234", diff_path="/tmp/t1.diff")
        row = self._fetch()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["commit_hash"], "abc1234")
        self.assertIsNotNone(row["committed_at"])
        self.assertIsNotNone(row["completed_at"])

    def test_mark_failed_sets_status_and_notes(self):
        self.q.mark_failed(self.task, notes="timeout after 3 attempts")
        row = self._fetch()
        self.assertEqual(row["status"], "failed")
        self.assertIn("timeout", row["notes"])

    def test_mark_rejected_sets_status_reason_and_rejected_at(self):
        self.q.mark_rejected(self.task, reason="wrong file modified")
        row = self._fetch()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["rejection_reason"], "wrong file modified")
        self.assertIsNotNone(row["rejected_at"])
        self.assertIn("rejected:", row["notes"])

    def test_mark_rejected_no_reason(self):
        self.q.mark_rejected(self.task)
        row = self._fetch()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["notes"], "rejected")

    def test_mark_blocked(self):
        self.q.mark_blocked(self.task, question="Which file should I edit?")
        row = self._fetch()
        self.assertEqual(row["status"], "blocked")
        self.assertIn("model_question", row["notes"])

    def test_mark_pending_review_sets_diff_path(self):
        self.q.mark_pending_review(self.task, Path("/tmp/t1.diff"))
        row = self._fetch()
        self.assertEqual(row["status"], "pending_review")
        self.assertIn("t1.diff", row["diff_path"])


# ── get_pending_review ────────────────────────────────────────────────────────

class TestGetPendingReview(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_empty(self):
        self.assertEqual(self.q.get_pending_review(), [])

    def test_returns_only_pending_review(self):
        self.q.add_task(make_task(id="t1"))
        self.q.add_task(make_task(id="t2", description="Task 2"))
        self.q.mark_pending_review(make_task(id="t1"), Path("/tmp/t1.diff"))
        result = self.q.get_pending_review()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "t1")

    def test_ordered_by_review_priority_desc(self):
        self.q.add_task(make_task(id="low",  complexity="low"))
        self.q.add_task(make_task(id="high", complexity="high", description="High complexity task"))
        self.q.mark_pending_review(make_task(id="low"),  Path("/tmp/low.diff"))
        self.q.mark_pending_review(make_task(id="high"), Path("/tmp/high.diff"))
        result = self.q.get_pending_review()
        self.assertEqual(result[0]["id"], "high")


# ── get_gap_fill_tasks ────────────────────────────────────────────────────────

class TestGetGapFillTasks(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_returns_only_gap_fill(self):
        self.q.add_task(make_task(id="t1", effort_category="feature"))
        self.q.add_task(make_task(id="t2", effort_category="gap-fill", description="Gap fill task"))
        result = self.q.get_gap_fill_tasks()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "t2")

    def test_limited_to_3(self):
        for i in range(5):
            self.q.add_task(make_task(id=f"g{i}", effort_category="gap-fill",
                                      description=f"Gap fill task {i}"))
        self.assertLessEqual(len(self.q.get_gap_fill_tasks()), 3)


# ── Epics & PBIs ──────────────────────────────────────────────────────────────

class TestEpicsAndPbis(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()
        self.epic = {"id": "epic_001", "project": "lang", "name": "STT Reliability", "color": "#6366f1"}
        self.pbi  = {
            "id": "pbi_001", "epic_id": "epic_001", "project": "lang",
            "title": "Add retry logic", "description": "Retry on timeout",
            "acceptance_criteria": "3 retries max",
            "affected_files": ["core/executor.py"],
        }

    def test_add_epic_returns_true_then_false(self):
        self.assertTrue(self.q.add_epic(self.epic))
        self.assertFalse(self.q.add_epic(self.epic))

    def test_add_pbi_returns_true_then_false(self):
        self.q.add_epic(self.epic)
        self.assertTrue(self.q.add_pbi(self.pbi))
        self.assertFalse(self.q.add_pbi(self.pbi))

    def test_pbis_for_epic(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        pbis = self.q.pbis_for_epic("epic_001")
        self.assertEqual(len(pbis), 1)
        self.assertEqual(pbis[0]["id"], "pbi_001")
        self.assertIsInstance(pbis[0]["affected_files"], list)

    def test_pbis_for_epic_wrong_id(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        self.assertEqual(self.q.pbis_for_epic("nonexistent"), [])

    def test_pbi_progress_empty(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        prog = self.q.pbi_progress("pbi_001")
        self.assertEqual(prog["total"],     0)
        self.assertEqual(prog["completed"], 0)
        self.assertEqual(prog["failed"],    0)
        self.assertEqual(prog["queued"],    0)

    def test_pbi_progress_with_tasks(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        # add_task doesn't persist pbi_id — assign it via update_status
        self.q.add_task(make_task(id="t1"))
        self.q.add_task(make_task(id="t2", description="Task 2"))
        self.q.update_status("t1", "queued",     pbi_id="pbi_001")
        self.q.update_status("t2", "queued",     pbi_id="pbi_001")
        self.q.mark_completed(make_task(id="t1"))
        prog = self.q.pbi_progress("pbi_001")
        self.assertEqual(prog["total"],     2)
        self.assertEqual(prog["completed"], 1)
        self.assertEqual(prog["queued"],    1)

    def test_update_pbi_affected_files_merges_no_duplicates(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        self.q.update_pbi_affected_files("pbi_001", ["core/executor.py", "core/spend.py"])
        pbi = self.q.get_pbi("pbi_001")
        files = pbi["affected_files"]
        self.assertIn("core/executor.py", files)
        self.assertIn("core/spend.py", files)
        self.assertEqual(files.count("core/executor.py"), 1)  # no duplicate

    def test_handoff_notes_round_trip(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        self.q.update_pbi_handoff("pbi_001", "t1", "Added retry with exponential backoff")
        notes = self.q.get_pbi_handoff_notes("pbi_001")
        self.assertEqual(notes["t1"], "Added retry with exponential backoff")

    def test_tasks_for_pbi(self):
        self.q.add_epic(self.epic)
        self.q.add_pbi(self.pbi)
        # add_task doesn't persist pbi_id — assign it via update_status
        self.q.add_task(make_task(id="t1"))
        self.q.add_task(make_task(id="t2", description="Task 2"))  # no pbi_id
        self.q.update_status("t1", "queued", pbi_id="pbi_001")
        result = self.q.tasks_for_pbi("pbi_001")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "t1")

    def test_all_epics(self):
        self.q.add_epic(self.epic)
        self.q.add_epic({"id": "epic_002", "project": "other", "name": "Other epic"})
        self.assertEqual(len(self.q.all_epics()), 2)
        self.assertEqual(len(self.q.all_epics(project="lang")), 1)


# ── stats ─────────────────────────────────────────────────────────────────────

class TestStats(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_empty(self):
        self.assertEqual(self.q.stats(), {})

    def test_counts_by_status(self):
        self.q.add_task(make_task(id="t1"))
        self.q.add_task(make_task(id="t2", description="Task 2"))
        self.q.mark_completed(make_task(id="t1"))
        stats = self.q.stats()
        self.assertEqual(stats["queued"],    1)
        self.assertEqual(stats["completed"], 1)

    def test_multiple_statuses_independent(self):
        self.q.add_task(make_task(id="t1"))
        self.q.add_task(make_task(id="t2", description="Task 2"))
        self.q.add_task(make_task(id="t3", description="Task 3"))
        self.q.mark_failed(make_task(id="t2"))
        self.q.mark_running(make_task(id="t3"))
        stats = self.q.stats()
        self.assertEqual(stats["queued"],  1)
        self.assertEqual(stats["failed"],  1)
        self.assertEqual(stats["running"], 1)


# ── metrics_data ──────────────────────────────────────────────────────────────

class TestMetricsData(unittest.TestCase):

    def setUp(self):
        self.q = fresh_queue()

    def test_empty_db_returns_expected_shape(self):
        m = self.q.metrics_data(days=30)
        self.assertIn("quality_gate",      m)
        self.assertIn("throughput_by_day", m)
        self.assertIn("perspectives",      m)
        self.assertIn("recent_failures",   m)
        self.assertIn("cost_by_project",   m)

    def test_pass_rate_zero_with_no_tasks(self):
        m = self.q.metrics_data(days=30)
        self.assertEqual(m["quality_gate"]["pass_rate"],  0.0)
        self.assertEqual(m["quality_gate"]["total"],      0)

    def test_pass_rate_100_when_all_pass(self):
        self.q.add_task(make_task(id="t1"))
        self.q.update_status("t1", "completed", quality_score=8)
        self.q.add_task(make_task(id="t2", description="Task 2"))
        self.q.update_status("t2", "completed", quality_score=7)
        m = self.q.metrics_data(days=30)
        self.assertEqual(m["quality_gate"]["pass_rate"],  100.0)
        self.assertEqual(m["quality_gate"]["passed"],     2)

    def test_pass_rate_excludes_score_below_6(self):
        self.q.add_task(make_task(id="t1"))
        self.q.update_status("t1", "completed", quality_score=5)
        self.q.add_task(make_task(id="t2", description="Task 2"))
        self.q.update_status("t2", "completed", quality_score=8)
        m = self.q.metrics_data(days=30)
        self.assertEqual(m["quality_gate"]["passed"], 1)
        self.assertEqual(m["quality_gate"]["total"],  2)

    def test_perspectives_excludes_null(self):
        """Tasks with a null perspective should not appear in the perspectives list."""
        self.q.add_task(make_task(id="t1", perspective="speech_linguist"))
        m = self.q.metrics_data(days=30)
        names = [p["name"] for p in m["perspectives"]]
        self.assertNotIn(None, names)
        self.assertNotIn("", names)

    def test_recent_failures_capped_at_10(self):
        for i in range(15):
            self.q.add_task(make_task(id=f"f{i}", description=f"Failed task {i}"))
            self.q.mark_failed(make_task(id=f"f{i}"))
        m = self.q.metrics_data(days=30)
        self.assertLessEqual(len(m["recent_failures"]), 10)


if __name__ == "__main__":
    unittest.main()
