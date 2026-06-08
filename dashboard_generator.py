"""
dashboard_generator.py
Generates dashboard/index.html from the SQLite task database.
Called at each digest (morning / afternoon / evening).
Open the output file directly in any browser — no server needed.
Includes two tabs: Kanban (task board) and Metrics (FEAT-4).
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

from task_queue import DB_PATH, PROJECT_COLORS, PERSPECTIVES, EFFORT_CATEGORIES


def _load_metrics() -> dict:
    """Load metrics data for the dashboard Metrics tab. Returns empty dict if DB missing."""
    if not DB_PATH.exists():
        return {}
    try:
        from task_queue import TaskQueue
        tq = TaskQueue()
        return tq.metrics_data(days=30)
    except Exception:
        return {}

DASHBOARD_DIR = Path(__file__).parent / "dashboard"
OUTPUT_PATH   = DASHBOARD_DIR / "index.html"


def _parse_diff_stats(diff_text: str) -> dict:
    """Parse a unified diff into file count + line addition/deletion counts."""
    if not diff_text:
        return {"files": 0, "added": 0, "removed": 0}
    files, added, removed = set(), 0, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            files.add(line[4:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"files": len(files), "added": added, "removed": removed}


def _load_all_tasks() -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY priority ASC, review_priority DESC").fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["depends_on"]        = json.loads(d.get("depends_on") or "[]")
        d["blocks"]            = json.loads(d.get("blocks") or "[]")
        d["approval_required"] = bool(d.get("approval_required"))

        # Parse diff stats from diff file if available
        diff_path = d.get("diff_path")
        diff_text = ""
        if diff_path:
            try:
                diff_text = Path(diff_path).read_text()
            except Exception:
                pass
        d["diff_stats"] = _parse_diff_stats(diff_text)

        # Compute duration in seconds if start + end both recorded
        try:
            from datetime import datetime as dt
            if d.get("started_at") and d.get("completed_at"):
                s = dt.fromisoformat(d["started_at"])
                e = dt.fromisoformat(d["completed_at"])
                d["duration_sec"] = int((e - s).total_seconds())
            else:
                d["duration_sec"] = None
        except Exception:
            d["duration_sec"] = None

        result.append(d)
    return result


def generate() -> Path:
    DASHBOARD_DIR.mkdir(exist_ok=True)
    tasks       = _load_all_tasks()
    metrics     = _load_metrics()
    projects    = sorted({t["project"] for t in tasks})
    generated   = datetime.now().strftime("%Y-%m-%d %H:%M")

    tasks_json   = json.dumps(tasks, default=str)
    colors_json  = json.dumps(PROJECT_COLORS)
    metrics_json = json.dumps(metrics, default=str)

    # Pre-computed to avoid backslashes inside f-string expressions (Python 3.9 compat)
    project_pills = " ".join(
        '<span class="pill" data-filter="project" data-value="{p}" '
        'onclick="setFilter(\'project\',\'{p}\',this)" '
        'style="border-color:{c}">{p}</span>'.format(p=p, c=PROJECT_COLORS.get(p, "#555"))
        for p in projects
    )
    perspective_pills = "".join(
        '<span class="pill" data-filter="perspective" data-value="{p}" '
        'onclick="setFilter(\'perspective\',\'{p}\',this)">{label}</span>'.format(
            p=p, label=p.replace("_", " ")
        )
        for p in PERSPECTIVES
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orchestrator Dashboard</title>
<style>
  :root {{
    --bg: #0f1117; --surface: #1a1d27; --surface2: #252836;
    --border: #2e3146; --text: #e2e8f0; --muted: #94a3b8;
    --green: #10b981; --yellow: #f59e0b; --red: #ef4444; --blue: #3b82f6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 13px; }}

  /* ── HEADER ── */
  .header {{ padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
  .header h1 {{ font-size: 15px; font-weight: 600; letter-spacing: .5px; }}
  .generated {{ color: var(--muted); font-size: 11px; margin-left: auto; }}

  /* ── FILTERS ── */
  .filters {{ padding: 10px 20px; border-bottom: 1px solid var(--border); display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  .filter-label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-right: 4px; }}
  .pill {{
    padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 500;
    cursor: pointer; border: 1px solid var(--border); background: var(--surface2);
    color: var(--muted); transition: all .15s;
  }}
  .pill:hover {{ color: var(--text); border-color: #4b5280; }}
  .pill.active {{ color: #fff; border-color: transparent; }}

  /* ── STATS BAR ── */
  .stats-bar {{ padding: 10px 20px; display: flex; gap: 20px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }}
  .stat {{ font-size: 11px; color: var(--muted); }}
  .stat span {{ color: var(--text); font-weight: 600; font-size: 13px; }}

  /* ── BOARD ── */
  .board {{ display: flex; gap: 12px; padding: 16px 20px; overflow-x: auto; min-height: calc(100vh - 160px); }}
  .column {{
    flex: 0 0 280px; background: var(--surface); border-radius: 10px;
    border: 1px solid var(--border); display: flex; flex-direction: column;
  }}
  .col-header {{
    padding: 10px 14px; font-weight: 600; font-size: 12px; letter-spacing: .4px;
    border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px;
    text-transform: uppercase; color: var(--muted);
  }}
  .col-header .count {{
    background: var(--surface2); border-radius: 999px;
    padding: 1px 7px; font-size: 11px; color: var(--text);
  }}
  .col-body {{ padding: 8px; flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }}

  /* ── CARD ── */
  .card {{
    background: var(--surface2); border-radius: 8px; padding: 10px 12px;
    border: 1px solid var(--border); cursor: default;
    transition: border-color .15s;
  }}
  .card:hover {{ border-color: #4b5280; }}
  .card-top {{ display: flex; align-items: flex-start; gap: 6px; margin-bottom: 6px; }}
  .project-dot {{
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 4px;
  }}
  .card-desc {{ font-size: 12px; line-height: 1.5; color: var(--text); flex: 1; }}
  .card-desc.truncated {{ display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
  .card-meta {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }}
  .badge {{
    padding: 2px 7px; border-radius: 4px; font-size: 10px; font-weight: 500;
    letter-spacing: .3px; background: var(--surface); border: 1px solid var(--border);
  }}
  .badge.project  {{ color: #fff; border: none; }}
  .badge.complexity-low    {{ color: var(--green); }}
  .badge.complexity-medium {{ color: var(--yellow); }}
  .badge.complexity-high   {{ color: var(--red); }}
  .badge.approval {{ color: var(--red); border-color: var(--red); }}
  .review-bar {{
    height: 3px; border-radius: 2px; margin-top: 8px;
    background: linear-gradient(90deg, var(--green), var(--yellow), var(--red));
    clip-path: inset(0 calc(100% - (var(--rp) / 5 * 100%)) 0 0 round 2px);
  }}
  .card-id {{ font-size: 10px; color: var(--muted); margin-top: 4px; }}

  /* ── EMPTY ── */
  .empty {{ text-align: center; padding: 40px 20px; color: var(--muted); font-size: 12px; }}

  /* ── COMPLETED TOGGLE ── */
  .completed-toggle {{
    padding: 6px 14px; font-size: 11px; color: var(--muted); cursor: pointer;
    border-bottom: 1px solid var(--border); user-select: none;
  }}
  .completed-toggle:hover {{ color: var(--text); }}
  .hidden {{ display: none; }}

  /* ── TABS ── */
  .tabs {{ display: flex; gap: 0; border-bottom: 1px solid var(--border); padding: 0 20px; }}
  .tab {{
    padding: 8px 16px; font-size: 12px; font-weight: 500; cursor: pointer;
    color: var(--muted); border-bottom: 2px solid transparent; margin-bottom: -1px;
    transition: color .15s, border-color .15s;
  }}
  .tab:hover {{ color: var(--text); }}
  .tab.active {{ color: var(--text); border-bottom-color: var(--blue); }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}

  /* ── METRICS ── */
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; padding: 16px 20px; }}
  .metric-card {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px;
  }}
  .metric-title {{ font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 8px; }}
  .metric-value {{ font-size: 28px; font-weight: 700; color: var(--text); }}
  .metric-sub {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  .perspective-row {{ display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--border); }}
  .perspective-row:last-child {{ border: none; }}
  .persp-name {{ font-size: 11px; color: var(--text); flex: 1; }}
  .persp-bar-wrap {{ width: 80px; height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; }}
  .persp-bar {{ height: 100%; background: var(--blue); border-radius: 3px; }}
  .persp-pct {{ font-size: 11px; color: var(--muted); width: 35px; text-align: right; }}

  /* ── MODAL ── */
  .modal-overlay {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.65);
    z-index: 100; align-items: flex-start; justify-content: center; padding: 40px 20px; overflow-y: auto;
  }}
  .modal-overlay.open {{ display: flex; }}
  .modal {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    width: 100%; max-width: 780px; padding: 28px; position: relative;
    box-shadow: 0 24px 80px rgba(0,0,0,.6);
  }}
  .modal-close {{
    position: absolute; top: 16px; right: 18px; font-size: 20px; cursor: pointer;
    color: var(--muted); background: none; border: none; line-height: 1;
  }}
  .modal-close:hover {{ color: var(--text); }}
  .modal-id {{ font-size: 11px; color: var(--muted); margin-bottom: 6px; font-family: monospace; }}
  .modal-title {{ font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 16px; line-height: 1.5; }}
  .modal-section {{ margin-top: 20px; }}
  .modal-section-label {{
    font-size: 10px; text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); margin-bottom: 8px; font-weight: 600;
  }}
  .modal-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }}
  .modal-field {{ background: var(--surface2); border-radius: 8px; padding: 10px 12px; }}
  .modal-field-label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; margin-bottom: 4px; }}
  .modal-field-value {{ font-size: 12px; color: var(--text); font-weight: 500; word-break: break-all; }}
  .modal-rationale {{
    background: var(--surface2); border-radius: 8px; padding: 12px 14px;
    font-size: 12px; color: var(--text); line-height: 1.6; border-left: 3px solid var(--blue);
  }}
  .diff-stats {{ display: flex; gap: 12px; align-items: center; }}
  .diff-added {{ color: var(--green); font-size: 13px; font-weight: 600; font-family: monospace; }}
  .diff-removed {{ color: var(--red); font-size: 13px; font-weight: 600; font-family: monospace; }}
  .diff-files {{ color: var(--muted); font-size: 12px; }}
  .modal-prompt {{
    background: #0a0c14; border: 1px solid var(--border); border-radius: 8px;
    padding: 14px; font-family: monospace; font-size: 11px; color: #94a3b8;
    white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; line-height: 1.6;
  }}
  .modal-prompt-toggle {{
    font-size: 11px; color: var(--blue); cursor: pointer; margin-bottom: 8px; display: inline-block;
  }}
  .card {{ cursor: pointer; }}
  .card:hover {{ border-color: var(--blue); }}
</style>
</head>
<body>

<!-- ── MODAL ─────────────────────────────────────────────────────────────── -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <button class="modal-close" onclick="closeModal()">✕</button>
    <div id="modalContent"></div>
  </div>
</div>

<div class="header">
  <h1>⚙ Orchestrator</h1>
  <div id="statsBar" class="stats-bar" style="padding:0;border:none;"></div>
  <div class="generated">Generated {generated}</div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('kanban',this)">📋 Kanban</div>
  <div class="tab" onclick="switchTab('metrics',this)">📊 Metrics</div>
</div>

<div id="tab-kanban" class="tab-content active">
<div class="filters">
  <span class="filter-label">Project</span>
  <span class="pill active" data-filter="project" data-value="all" onclick="setFilter('project','all',this)">All</span>
  {project_pills}
  &nbsp;
  <span class="filter-label">Perspective</span>
  <span class="pill active" data-filter="perspective" data-value="all" onclick="setFilter('perspective','all',this)">All</span>
  {perspective_pills}
  &nbsp;
  <span class="filter-label">Complexity</span>
  <span class="pill active" data-filter="complexity" data-value="all" onclick="setFilter('complexity','all',this)">All</span>
  <span class="pill" data-filter="complexity" data-value="high" onclick="setFilter('complexity','high',this)" style="color:var(--red)">High</span>
  <span class="pill" data-filter="complexity" data-value="medium" onclick="setFilter('complexity','medium',this)" style="color:var(--yellow)">Medium</span>
  <span class="pill" data-filter="complexity" data-value="low" onclick="setFilter('complexity','low',this)" style="color:var(--green)">Low</span>
</div>

<div class="board" id="board"></div>
</div><!-- end tab-kanban -->

<div id="tab-metrics" class="tab-content">
  <div class="metrics-grid" id="metricsGrid"></div>
</div>

<script>
const ALL_TASKS   = {tasks_json};
const COLORS      = {colors_json};
const METRICS     = {metrics_json};
const COLUMNS     = [
  {{ key: 'queued',         label: 'Queued',                dot: '#94a3b8' }},
  {{ key: 'running',        label: 'Running',               dot: '#3b82f6' }},
  {{ key: 'pending_review', label: 'Needs Approval',        dot: '#f59e0b' }},
  {{ key: 'completed',      label: 'Committed / Completed', dot: '#10b981' }},
  {{ key: 'failed',         label: 'Failed',                dot: '#ef4444' }},
];

const filters = {{ project: 'all', perspective: 'all', complexity: 'all' }};

function setFilter(key, value, el) {{
  filters[key] = value;
  el.closest('.filters').querySelectorAll(`[data-filter="${{key}}"]`).forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  render();
}}

function applyFilters(tasks) {{
  return tasks.filter(t => {{
    if (filters.project     !== 'all' && t.project     !== filters.project)     return false;
    if (filters.perspective !== 'all' && t.perspective !== filters.perspective) return false;
    if (filters.complexity  !== 'all' && t.complexity  !== filters.complexity)  return false;
    return true;
  }});
}}

function badge(text, cls) {{
  return `<span class="badge ${{cls}}">${{text}}</span>`;
}}

function card(t) {{
  const color = COLORS[t.project] || '#555';
  const rp    = t.review_priority || 3;
  const approvalBadge = t.approval_required ? badge('needs review', 'approval') : '';
  const ds = t.diff_stats || {{}};
  const diffHint = ds.files ? `${{ds.files}} file${{ds.files!==1?'s':''}} · +${{ds.added}} -${{ds.removed}}` : '';
  return `
    <div class="card" onclick="openModal('${{t.id}}')" title="Click for details">
      <div class="card-top">
        <span class="project-dot" style="background:${{color}}"></span>
        <span class="card-desc truncated">${{t.description}}</span>
      </div>
      <div class="card-meta">
        ${{badge(t.project, 'project')}}
        ${{badge((t.perspective||'').replace(/_/g,' '), 'perspective')}}
        ${{badge(t.complexity||'medium', 'complexity-' + (t.complexity||'medium'))}}
        ${{badge(t.effort_category||'feature', 'effort')}}
        ${{approvalBadge}}
        ${{diffHint ? badge(diffHint, '') : ''}}
      </div>
      <div class="review-bar" style="--rp:${{rp}}"></div>
      <div class="card-id">${{t.id}}</div>
    </div>`;
}}

function render() {{
  const visible = applyFilters(ALL_TASKS);
  const board   = document.getElementById('board');
  board.innerHTML = '';

  const statsEl = document.getElementById('statsBar');
  const counts  = {{}};
  ALL_TASKS.forEach(t => counts[t.status] = (counts[t.status]||0) + 1);
  statsEl.innerHTML = Object.entries(counts)
    .map(([s,n]) => `<div class="stat"><span>${{n}}</span> ${{s.replace('_',' ')}}</div>`)
    .join('');

  COLUMNS.forEach(col => {{
    const colTasks = visible.filter(t => t.status === col.key);
    if (col.key !== 'queued' && col.key !== 'pending_review' && colTasks.length === 0) return;

    const colEl = document.createElement('div');
    colEl.className = 'column';

    const isCompleted = col.key === 'completed';
    const bodyId      = `col-${{col.key}}`;

    colEl.innerHTML = `
      <div class="col-header">
        <span style="width:8px;height:8px;border-radius:50%;background:${{col.dot}};display:inline-block"></span>
        ${{col.label}}
        <span class="count">${{colTasks.length}}</span>
      </div>
      ${{isCompleted ? `<div class="completed-toggle" onclick="toggleCompleted()">▸ Show completed</div>` : ''}}
      <div class="col-body ${{isCompleted ? 'hidden' : ''}}" id="${{bodyId}}">
        ${{colTasks.length ? colTasks.map(card).join('') : '<div class="empty">No tasks</div>'}}
      </div>`;

    board.appendChild(colEl);
  }});
}}

function toggleCompleted() {{
  const body   = document.getElementById('col-completed');
  const toggle = body.previousElementSibling;
  const hidden = body.classList.toggle('hidden');
  toggle.textContent = hidden ? '▸ Show completed' : '▾ Hide completed';
}}

// ── TAB SWITCHING ────────────────────────────────────────────────────────────

function switchTab(name, el) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'metrics') renderMetrics();
}}

// ── METRICS RENDERING ────────────────────────────────────────────────────────

function renderMetrics() {{
  const grid = document.getElementById('metricsGrid');
  if (!METRICS || Object.keys(METRICS).length === 0) {{
    grid.innerHTML = '<div class="metric-card"><div class="metric-title">No data yet</div><div class="metric-sub">Run the orchestrator to collect metrics.</div></div>';
    return;
  }}

  const qg         = METRICS.quality_gate || {{}};
  const costByProj = METRICS.cost_by_project || {{}};
  const throughput = METRICS.throughput_by_day || {{}};
  const persp      = (METRICS.perspectives || []).sort((a,b) => b.attempted - a.attempted);
  const failures   = METRICS.recent_failures || [];

  // Throughput sparkline (last 14 days)
  const tpDays = Object.entries(throughput).sort().slice(-14);
  const tpMax  = Math.max(...tpDays.map(([,n]) => n), 1);
  const spark  = tpDays.map(([d, n]) => {{
    const h = Math.max(4, Math.round(n / tpMax * 40));
    return `<span title="${{d}}: ${{n}} tasks" style="display:inline-block;width:10px;height:${{h}}px;background:var(--blue);border-radius:2px;vertical-align:bottom;margin:0 1px"></span>`;
  }}).join('');

  // Cost table
  const costRows = Object.entries(costByProj)
    .sort((a,b) => b[1].total - a[1].total)
    .map(([proj, v]) => `<div class="perspective-row"><span class="persp-name">${{proj}}</span><span style="font-size:11px;color:var(--muted)">${{v.n}} tasks</span><span class="persp-pct">$${{v.total.toFixed(3)}}</span></div>`)
    .join('') || '<div style="color:var(--muted);font-size:11px">No data yet</div>';

  // Perspective bars
  const perspMax = Math.max(...persp.map(p => p.attempted), 1);
  const perspRows = persp.slice(0,10).map(p => {{
    const w = Math.round(p.attempted / perspMax * 100);
    const color = p.rate >= 80 ? 'var(--green)' : p.rate >= 50 ? 'var(--yellow)' : 'var(--red)';
    return `<div class="perspective-row">
      <span class="persp-name">${{p.name.replace(/_/g,' ')}}</span>
      <div class="persp-bar-wrap"><div class="persp-bar" style="width:${{w}}%;background:${{color}}"></div></div>
      <span class="persp-pct">${{p.rate.toFixed(0)}}%</span>
    </div>`;
  }}).join('') || '<div style="color:var(--muted);font-size:11px">No data yet</div>';

  // Recent failures
  const failRows = failures.slice(0,5).map(f =>
    `<div style="font-size:11px;padding:4px 0;border-bottom:1px solid var(--border)">
      <span style="color:var(--red)">\`${{f.id}}\`</span>
      <span style="color:var(--muted)"> [${{f.project}}] ${{(f.description||'').slice(0,55)}}…</span>
    </div>`
  ).join('') || '<div style="color:var(--muted);font-size:11px">No recent failures ✓</div>';

  const gateColor = (qg.pass_rate||0) >= 80 ? 'var(--green)' : (qg.pass_rate||0) >= 60 ? 'var(--yellow)' : 'var(--red)';

  grid.innerHTML = `
    <div class="metric-card">
      <div class="metric-title">Quality Gate Pass Rate</div>
      <div class="metric-value" style="color:${{gateColor}}">${{(qg.pass_rate||0).toFixed(1)}}%</div>
      <div class="metric-sub">${{qg.passed||0}} passed / ${{qg.total||0}} total (last 30d)</div>
    </div>

    <div class="metric-card">
      <div class="metric-title">Nightly Throughput</div>
      <div style="padding:8px 0">${{spark || '<span style="color:var(--muted);font-size:11px">No data yet</span>'}}</div>
      <div class="metric-sub">Tasks committed per night (last 14 days)</div>
    </div>

    <div class="metric-card">
      <div class="metric-title">Cost by Project (last 30d)</div>
      ${{costRows}}
    </div>

    <div class="metric-card">
      <div class="metric-title">Perspective Acceptance Rate</div>
      ${{perspRows}}
    </div>

    <div class="metric-card">
      <div class="metric-title">Recent Failures</div>
      ${{failRows}}
    </div>
  `;
}}

render();

// ── MODAL ─────────────────────────────────────────────────────────────────────

const TASK_MAP = {{}};
ALL_TASKS.forEach(t => TASK_MAP[t.id] = t);

function field(label, value, wide) {{
  if (value === null || value === undefined || value === '' || value === 0 && label !== 'Cost') return '';
  return `<div class="modal-field${{wide ? '" style="grid-column:1/-1' : ''}}">
    <div class="modal-field-label">${{label}}</div>
    <div class="modal-field-value">${{value}}</div>
  </div>`;
}}

function openModal(id) {{
  const t = TASK_MAP[id];
  if (!t) return;

  const ds = t.diff_stats || {{}};
  const diffBlock = ds.files ? `
    <div class="modal-section">
      <div class="modal-section-label">Diff</div>
      <div class="diff-stats">
        <span class="diff-files">${{ds.files}} file${{ds.files!==1?'s':''}} changed</span>
        <span class="diff-added">+${{ds.added}}</span>
        <span class="diff-removed">-${{ds.removed}}</span>
      </div>
    </div>` : '';

  const dur = t.duration_sec != null
    ? (t.duration_sec >= 60 ? `${{Math.floor(t.duration_sec/60)}}m ${{t.duration_sec%60}}s` : `${{t.duration_sec}}s`)
    : null;

  const statusColor = {{
    completed:'var(--green)', failed:'var(--red)', running:'var(--blue)',
    pending_review:'var(--yellow)', queued:'var(--muted)'
  }}[t.status] || 'var(--muted)';

  const promptBlock = t.system_prompt ? `
    <div class="modal-section">
      <div class="modal-section-label">
        Execution Prompt
        <span class="modal-prompt-toggle" onclick="togglePrompt(this)"> ▸ show</span>
      </div>
      <div class="modal-prompt" style="display:none">${{escHtml(t.system_prompt)}}</div>
    </div>` : '';

  const depBlock = (t.depends_on && t.depends_on.length) ? `
    <div class="modal-section">
      <div class="modal-section-label">Dependencies</div>
      <div style="font-size:12px;color:var(--muted)">${{t.depends_on.join(', ')}}</div>
    </div>` : '';

  document.getElementById('modalContent').innerHTML = `
    <div class="modal-id">${{t.id}}</div>
    <div class="modal-title">${{t.description}}</div>

    ${{t.rationale ? `<div class="modal-rationale">${{t.rationale}}</div>` : ''}}

    <div class="modal-section">
      <div class="modal-section-label">Details</div>
      <div class="modal-grid">
        ${{field('Status', `<span style="color:${{statusColor}};font-weight:600">${{t.status.replace(/_/g,' ')}}</span>`)}}
        ${{field('Project', t.project)}}
        ${{field('Perspective', (t.perspective||'').replace(/_/g,' '))}}
        ${{field('Complexity', t.complexity)}}
        ${{field('Category', t.effort_category)}}
        ${{field('Priority', t.priority === 0 ? '🔴 P0' : t.priority === 1 ? '🟡 P1' : '⚪ P2')}}
        ${{field('Review Priority', t.review_priority + '/5')}}
        ${{field('Approval Required', t.approval_required ? '⚠️ Yes' : null)}}
        ${{field('Quality Score', t.quality_score != null ? t.quality_score + '/10' : null)}}
        ${{field('Gate Skipped', t.quality_gate_skipped ? '⚠️ Yes' : null)}}
      </div>
    </div>

    <div class="modal-section">
      <div class="modal-section-label">Timing & Cost</div>
      <div class="modal-grid">
        ${{field('Created', (t.created_at||'').slice(0,16))}}
        ${{field('Started', (t.started_at||'').slice(0,16))}}
        ${{field('Completed', (t.completed_at||'').slice(0,16))}}
        ${{field('Duration', dur)}}
        ${{field('Cost', t.cost_usd ? '$' + t.cost_usd.toFixed(5) : null)}}
        ${{field('Tokens In', t.actual_tokens ? t.actual_tokens.toLocaleString() : null)}}
        ${{field('Model', t.model_used)}}
        ${{field('Commit', t.commit_hash ? t.commit_hash.slice(0,7) : null)}}
      </div>
    </div>

    ${{diffBlock}}

    ${{t.rejection_reason ? `
    <div class="modal-section">
      <div class="modal-section-label">Rejection / Notes</div>
      <div style="font-size:12px;color:var(--red);background:var(--surface2);padding:10px;border-radius:8px">${{t.rejection_reason}}</div>
    </div>` : ''}}

    ${{depBlock}}
    ${{promptBlock}}
  `;

  document.getElementById('modalOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal() {{
  document.getElementById('modalOverlay').classList.remove('open');
  document.body.style.overflow = '';
}}

function togglePrompt(el) {{
  const pre = el.parentElement.nextElementSibling;
  const hidden = pre.style.display === 'none';
  pre.style.display = hidden ? 'block' : 'none';
  el.textContent = hidden ? ' ▾ hide' : ' ▸ show';
}}

function escHtml(s) {{
  return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});

render();
</script>
</body>
</html>"""

    OUTPUT_PATH.write_text(html)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = generate()
    print(f"Dashboard written → {path}")
