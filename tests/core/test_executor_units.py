"""
tests/core/test_executor_units.py
Unit tests for pure helper functions in core/executor.py.
No Ollama, no MiniMax, no subprocess, no filesystem side effects (except _safe_write).
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# executor.configure() is needed before importing module-level code that calls _cfg().
# We inject a minimal config so the module loads without raising RuntimeError.
import core.executor as executor
executor.configure({
    "OLLAMA_BASE":         "http://localhost:11434",
    "OLLAMA_MODEL_CODE":   "qwen3-coder:30b",
    "OLLAMA_MODEL_DIGEST": "qwen3:14b",
    "MINIMAX_SPEND_CAP":   10.0,
    "PENDING_DIR":         Path("/tmp"),
    "REPO_PATHS":          {},
})

from core.executor import (
    _detect_content_shrinkage,
    _extract_new_files_from_diff,
    _detect_question,
    _parse_file_blocks,
    _safe_write,
)
from core.task_queue import _compute_review_priority


# ── _detect_content_shrinkage ─────────────────────────────────────────────────

def _make_diff(path: str, adds: int, dels: int) -> str:
    """Build a minimal fake git diff with the given add/del line counts."""
    lines = [f"diff --git a/{path} b/{path}"]
    lines += [f"+added line {i}" for i in range(adds)]
    lines += [f"-deleted line {i}" for i in range(dels)]
    return "\n".join(lines)


class TestDetectContentShrinkage(unittest.TestCase):

    def test_no_shrinkage_returns_empty(self):
        diff = _make_diff("core/app.py", adds=50, dels=10)
        self.assertEqual(_detect_content_shrinkage(diff), [])

    def test_flags_file_with_over_60_percent_deletions(self):
        # 5 adds, 80 dels → dels/(5+80) ≈ 94% > 60% threshold, dels >= 20
        diff = _make_diff("core/executor.py", adds=5, dels=80)
        flagged = _detect_content_shrinkage(diff)
        self.assertEqual(len(flagged), 1)
        self.assertIn("core/executor.py", flagged[0])

    def test_ignores_small_files_under_20_deletions(self):
        # 1 add, 19 dels — under the 20-deletion minimum
        diff = _make_diff("tiny.py", adds=1, dels=19)
        self.assertEqual(_detect_content_shrinkage(diff), [])

    def test_exactly_at_threshold_not_flagged(self):
        # dels/(adds+dels) == 0.6 exactly — threshold is >, not >=
        diff = _make_diff("core/app.py", adds=20, dels=30)  # 30/50 = 0.60 exactly
        self.assertEqual(_detect_content_shrinkage(diff), [])

    def test_multiple_files_only_flagged_ones_returned(self):
        diff = (
            _make_diff("clean.py",   adds=50, dels=5) + "\n" +
            _make_diff("shrunk.py",  adds=2,  dels=80)
        )
        flagged = _detect_content_shrinkage(diff)
        self.assertEqual(len(flagged), 1)
        self.assertIn("shrunk.py", flagged[0])

    def test_empty_diff_returns_empty(self):
        self.assertEqual(_detect_content_shrinkage(""), [])

    def test_custom_threshold(self):
        # 10 adds, 25 dels → 25/35 ≈ 71%, should flag at default 0.6 but not at 0.8
        diff = _make_diff("core/app.py", adds=10, dels=25)
        self.assertEqual(len(_detect_content_shrinkage(diff, threshold=0.8)), 0)
        self.assertEqual(len(_detect_content_shrinkage(diff, threshold=0.6)), 1)


# ── _extract_new_files_from_diff ──────────────────────────────────────────────

def _new_file_diff(path: str) -> str:
    """Diff fragment for a newly created file (--- /dev/null)."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"+content\n"
    )


def _modified_file_diff(path: str) -> str:
    """Diff fragment for a modified file (not new)."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"-old line\n"
        f"+new line\n"
    )


class TestExtractNewFilesFromDiff(unittest.TestCase):

    def test_extracts_new_file(self):
        diff = _new_file_diff("scenes/ja/izakaya_01.js")
        result = _extract_new_files_from_diff(diff, existing_files=[])
        self.assertEqual(result, ["scenes/ja/izakaya_01.js"])

    def test_skips_modified_file(self):
        diff = _modified_file_diff("core/executor.py")
        result = _extract_new_files_from_diff(diff, existing_files=[])
        self.assertEqual(result, [])

    def test_skips_file_already_in_existing(self):
        diff = _new_file_diff("scenes/ja/izakaya_01.js")
        result = _extract_new_files_from_diff(diff, existing_files=["scenes/ja/izakaya_01.js"])
        self.assertEqual(result, [])

    def test_no_duplicates_in_output(self):
        # Two diff hunks for the same new file (edge case)
        diff = _new_file_diff("foo.py") + _new_file_diff("foo.py")
        result = _extract_new_files_from_diff(diff, existing_files=[])
        self.assertEqual(result.count("foo.py"), 1)

    def test_mixed_new_and_modified(self):
        diff = _new_file_diff("new_file.py") + _modified_file_diff("existing.py")
        result = _extract_new_files_from_diff(diff, existing_files=[])
        self.assertEqual(result, ["new_file.py"])

    def test_empty_diff_returns_empty(self):
        self.assertEqual(_extract_new_files_from_diff("", []), [])

    def test_multiple_new_files(self):
        diff = _new_file_diff("a.py") + _new_file_diff("b.py")
        result = _extract_new_files_from_diff(diff, existing_files=[])
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)
        self.assertEqual(len(result), 2)


# ── _detect_question ──────────────────────────────────────────────────────────

CLEAR_QUESTION = (
    "Before I proceed, could you please clarify which file should be modified? "
    "I am not sure whether you want me to edit executor.py or task_queue.py. "
    "Could you confirm the expected output format?"
)

PLAIN_CODE = (
    "Here is the updated executor.py with retry logic added.\n"
    "The function now retries up to 3 times on timeout."
)

class TestDetectQuestion(unittest.TestCase):

    def test_detects_clear_clarification_request(self):
        result = _detect_question(CLEAR_QUESTION, "")
        self.assertNotEqual(result, "")

    def test_plain_code_output_not_flagged(self):
        result = _detect_question(PLAIN_CODE, "")
        self.assertEqual(result, "")

    def test_single_question_mark_not_enough(self):
        # Only one question mark and no phrase match
        result = _detect_question("Is this correct?", "")
        self.assertEqual(result, "")

    def test_checks_thinking_block_too(self):
        # _detect_question requires >= 2 question marks AND a phrase match.
        thinking = (
            "I need more information about this. "
            "Before I proceed, I am not sure which module to edit? "
            "Could you clarify the expected behavior?"
        )
        result = _detect_question("", thinking)
        self.assertNotEqual(result, "")

    def test_empty_inputs_return_empty(self):
        self.assertEqual(_detect_question("", ""), "")

    def test_returns_content_prefix(self):
        result = _detect_question(CLEAR_QUESTION, "")
        self.assertLessEqual(len(result), 400)
        self.assertIn("clarify", result.lower())


# ── _parse_file_blocks ────────────────────────────────────────────────────────

class TestParseFileBlocks(unittest.TestCase):

    def test_parses_single_block(self):
        response = "<<<FILE: core/app.py>>>\nprint('hello')\n<<<END>>>"
        result = _parse_file_blocks(response)
        self.assertIn("core/app.py", result)
        self.assertIn("print('hello')", result["core/app.py"])

    def test_parses_multiple_blocks(self):
        response = (
            "<<<FILE: a.py>>>\nx = 1\n<<<END>>>\n"
            "<<<FILE: b.py>>>\ny = 2\n<<<END>>>"
        )
        result = _parse_file_blocks(response)
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)
        self.assertEqual(len(result), 2)

    def test_no_blocks_returns_empty_dict(self):
        self.assertEqual(_parse_file_blocks("No file blocks here."), {})

    def test_empty_string_returns_empty_dict(self):
        self.assertEqual(_parse_file_blocks(""), {})

    def test_preserves_multiline_content(self):
        content = "def foo():\n    return 42\n"
        response = f"<<<FILE: foo.py>>>\n{content}<<<END>>>"
        result = _parse_file_blocks(response)
        self.assertEqual(result["foo.py"], content)

    def test_strips_path_whitespace(self):
        response = "<<<FILE:   spaced/path.py   >>>\ncode\n<<<END>>>"
        result = _parse_file_blocks(response)
        self.assertIn("spaced/path.py", result)


# ── _safe_write ───────────────────────────────────────────────────────────────

class TestSafeWrite(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_writes_file_inside_repo(self):
        result = _safe_write(self.tmp, "core/app.py", "content")
        self.assertTrue(result)
        self.assertEqual((self.tmp / "core" / "app.py").read_text(), "content")

    def test_creates_parent_directories(self):
        _safe_write(self.tmp, "deep/nested/dir/file.py", "hello")
        self.assertTrue((self.tmp / "deep" / "nested" / "dir" / "file.py").exists())

    def test_blocks_path_traversal(self):
        result = _safe_write(self.tmp, "../../etc/passwd", "evil")
        self.assertFalse(result)
        self.assertFalse(Path("/etc/passwd").exists() and
                         Path("/etc/passwd").read_text() == "evil")

    def test_blocks_absolute_path(self):
        result = _safe_write(self.tmp, "/tmp/evil.py", "evil")
        self.assertFalse(result)

    def test_returns_true_on_success(self):
        self.assertTrue(_safe_write(self.tmp, "ok.py", "data"))

    def test_overwrites_existing_file(self):
        _safe_write(self.tmp, "file.py", "original")
        _safe_write(self.tmp, "file.py", "updated")
        self.assertEqual((self.tmp / "file.py").read_text(), "updated")


if __name__ == "__main__":
    unittest.main()
