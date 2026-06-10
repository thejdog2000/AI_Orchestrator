# Core Orchestrator Test Scenarios

Target files: `tests/test_task_queue.py`, `tests/test_spend.py`, `tests/test_executor_units.py`
Test strategy: real in-memory SQLite for queue tests, temp files for spend, pure unit tests for executor helpers. No Ollama, no MiniMax, no subprocess calls.

---

## TaskQueue (`tests/test_task_queue.py`)

Use `TaskQueue(db_path=":memory:")` for all tests.

### `add_task()`
- Returns `True` on first insert, `False` on exact-id duplicate
- Returns `False` on semantic duplicate (same description+project, status queued/running)
- Semantic duplicate check is project-scoped: same description in different project → both inserted
- Semantic duplicate check ignores completed/failed tasks (re-queuing allowed after failure)
- `depends_on` and `blocks` stored as JSON, deserialized back to lists by `get_next()`
- `review_priority` computed correctly: `low` → 1, `high+approval_required` → 5 (capped)
- `approval_required` stored/retrieved as bool

### `get_next()`
- Returns `None` from empty queue
- Respects `projects` filter — does not return tasks from other projects
- Skips `approval_required=True` tasks
- Skips tasks whose `depends_on` are not in completed/pending_review
- Returns task once dependency is marked `completed`
- Returns task once dependency is marked `pending_review` (counts as done)
- Respects `priority ASC` ordering
- Does not return `running`, `completed`, or `failed` tasks

### `total_unblocked()`
- Returns 0 for empty queue
- Returns correct count respecting dependency resolution
- Does not count `approval_required=True` tasks

### Status transitions
- `mark_running()` → status `running`, `started_at` set
- `mark_completed()` → status `completed`, `completed_at` set
- `mark_committed()` → status `completed`, `committed_at` and `commit_hash` set
- `mark_failed()` → status `failed`, notes stored
- `mark_rejected()` → status `failed`, `rejection_reason` and `rejected_at` set, notes prefixed `rejected:`
- `mark_blocked()` → status `blocked`
- `mark_pending_review()` → status `pending_review`, `diff_path` stored

### `get_pending_review()`
- Returns only `pending_review` tasks
- Ordered by `review_priority DESC`

### `get_gap_fill_tasks()`
- Only returns tasks with `effort_category = 'gap-fill'`
- Limited to 3

### Epics & PBIs
- `add_epic()` returns `True` on insert, `False` on duplicate id
- `add_pbi()` returns `True` on insert, `False` on duplicate id
- `pbis_for_epic()` returns only PBIs for that epic, `affected_files` as list
- `pbi_progress()` counts correctly across statuses
- `update_pbi_affected_files()` merges without duplicating existing paths
- `update_pbi_handoff()` / `get_pbi_handoff_notes()` round-trip correctly
- `tasks_for_pbi()` returns only tasks with matching `pbi_id`

### `stats()`
- Returns correct per-status counts
- Tasks in multiple statuses each counted under their own key

### `metrics_data()`
- `quality_gate.pass_rate` is 0.0 with no tasks, 100.0 when all quality_score >= 6
- `throughput_by_day` excludes tasks outside the `days` window
- `perspectives` list excludes null/empty perspective values
- `recent_failures` capped at 10

---

## SpendTracker (`tests/test_spend.py`)

Use a temp directory for `spend.json`.

### `record()`
- Returns computed cost in USD
- Cost formula: `(input_tokens / 1e6 * input_rate) + (output_tokens / 1e6 * output_rate)`
- Unknown model falls back to `DEFAULT_RATE`
- Accumulates across multiple calls (additive)
- `monthly_spend()` only sums entries for current month (not prior months)
- `daily_spend()` only returns today's total
- Write is atomic: temp file renamed, no partial writes

### `record_partial()`
- Adds to `partial_usd` separately from confirmed spend
- Also adds to total so `check_caps()` sees it
- `partial_events` list capped at 50 entries
- Stores `date`, `project`, `est_tokens`, `est_usd`, `reason`

### `check_caps()`
- Returns `True` when spend is below cap
- Returns `False` (logs error) when spend >= cap
- Logs warning when spend >= 85% of cap but still returns `True`

### Persistence
- `_load()` on missing file returns clean empty structure
- `_load()` on corrupted JSON logs warning and returns clean structure (does not raise)
- Spend written with `record()` is readable by a fresh `SpendTracker` instance on same file

---

## Executor unit helpers (`tests/test_executor_units.py`)

Pure functions only — no Ollama, no MiniMax, no subprocess.

### `_compute_review_priority()`  (imported from task_queue)
- `low` + no approval → 1
- `medium` + no approval → 2
- `high` + no approval → 3
- `low` + approval_required → 3
- `high` + approval_required → 5 (capped, not 6)

### `_description_hash()`
- Same description + project → same hash
- Same description, different project → different hash
- Whitespace normalization: `"  foo  bar  "` → same hash as `"foo bar"`
- Case normalization: `"Foo Bar"` → same hash as `"foo bar"`

### `_detect_content_shrinkage()`
- Returns empty list when no shrinkage
- Returns affected filenames when removed lines > threshold fraction of added lines
- Ignores non-code hunks correctly

### `_extract_new_files_from_diff()`
- Returns only files in diff that aren't in `existing_files`
- Handles `+++ /dev/null` (deletions) — should not appear as new files
- Returns empty list when diff is empty string

### `_detect_question()`
- Returns question text when model output contains a `?`-ended sentence
- Returns empty string when no question detected
- Checks both `content` and `thinking_block` parameters

### `_parse_file_blocks()`
- Parses `## FILE: path/to/file\n<content>` blocks into `{path: content}` dict
- Handles multiple file blocks in one response
- Returns empty dict when no blocks present

### `_safe_write()`
- Creates parent directories if they don't exist
- Returns `True` on success, `False` if path escapes repo root
- Does not write outside `repo_path` (path traversal guard)

---

## What's intentionally excluded

- `run_task()` / `run_minimax_task()` — these are integration tests requiring real API keys and Ollama; not worth mocking the full call chain
- `ollama_generate()` — external HTTP; mock at boundary if ever tested
- `executor.select_relevant_files()` — requires a real git repo; integration test territory
- `pipeline/lang_pipeline.py` — thin wrapper around executor; tested implicitly
- Discord/notify — side-effect only, no logic worth unit testing
