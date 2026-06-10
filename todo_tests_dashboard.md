# Dashboard Test Scenarios

Target files: `tests/test_dashboard_server.py`, `tests/test_dashboard_data.py`
Test strategy: real in-memory SQLite, no mocking except external HTTP calls.

---

## Server (`tests/test_dashboard_server.py`)

Spin up `DashboardHandler` via `HTTPServer` on a random port in `setUpClass`, tear down in `tearDownClass`.

### Static serving
- `GET /` → 200, `Content-Type: text/html`, body contains `<script src="/static/app.js">`
- `GET /static/app.js` → 200, `Content-Type: application/javascript`
- `GET /static/index.html` → 200, `Content-Type: text/html`
- `GET /static/../server.py` → 404 (path traversal blocked)
- `GET /static/nonexistent.js` → 404
- `GET /unknown/route` → 404

### API — happy path
- `GET /api/config` → 200 JSON with keys `colors`, `perspectives`, `effort_categories`
- `GET /api/tasks` → 200 JSON array (empty DB returns `[]`, not error)
- `GET /api/spend` → 200 JSON with keys `monthly`, `daily`, `total`
- `GET /api/epics` → 200 JSON with keys `epics`, `pbis`
- `GET /api/retros` → 200 JSON array
- `GET /api/metrics` → 200 JSON (empty DB returns `{}` or valid shape)

### API — error handling
- All API endpoints return JSON (not HTML) on internal error → status 500, body `{"error": "..."}`
- `GET /api/tasks` when DB file is absent (point server at nonexistent path) → `[]` not 500

### Pipeline + diff routes
- `GET /pipeline/<id>` when log exists → 200 JSON
- `GET /pipeline/nonexistent` → 404 JSON `{"error": "pipeline log not found"}`
- `GET /diff/foo.diff` when file exists → 200 `text/plain`
- `GET /diff/../../etc/passwd` → 404 (traversal + must end in `.diff`)
- `GET /diff/nonexistent.diff` → 404

### Retro catch-up (integration smoke)
- `GET /` with no retros → serves HTML without crashing (catch-up skipped silently)
- `GET /` when `generate_retrospective` raises → still returns 200 HTML (warning logged, not propagated)

---

## Data loading (`tests/test_dashboard_data.py`)

Use a temp SQLite file seeded with known rows. Override `config.DB_PATH` before import.

### `load_tasks()`
- Empty DB → `[]`
- Single queued task → list of length 1 with correct fields
- `depends_on` and `blocks` are lists (not raw JSON strings)
- `approval_required` is a bool
- `duration_sec` computed correctly when both `started_at` and `completed_at` present
- `duration_sec` is `None` when either timestamp is missing
- `diff_stats` defaults to `{files:0, added:0, removed:0}` when `diff_path` empty
- Tasks ordered by `priority ASC, review_priority DESC`

### `load_spend()`
- Missing `spend.json` → `{monthly:0.0, daily:0.0, total:0.0}`
- Corrupted `spend.json` → returns zeros, does not raise
- Sums only current-month daily entries into `monthly`
- `total` comes from `total_usd` field, not sum of daily

### `load_metrics()` / `TaskQueue.metrics_data()`
- Empty DB → returns dict with expected top-level keys (`quality_gate`, `throughput_by_day`, etc.)
- `pass_rate` is 0.0 when no tasks
- `throughput_by_day` only includes days within the `days` window
- `cost_by_project` ratios sum to ≤ 1.0 of `spend_total`

### `load_epics_pbis()`
- Empty DB → `([], [])`
- Epic with two PBIs → both PBIs in result with `epic_name` and `epic_color` populated
- PBI `progress` dict has keys `total`, `completed`, `failed`, `queued`, `running`
