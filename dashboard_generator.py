"""
dashboard_generator.py
Generates dashboard/index.html from the SQLite task database.
Called at each digest (morning / afternoon / evening).
Open the output file directly in any browser — no server needed.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

from task_queue import DB_PATH, PROJECT_COLORS, PERSPECTIVES, EFFORT_CATEGORIES

DASHBOARD_DIR = Path(__file__).parent / "dashboard"
OUTPUT_PATH   = DASHBOARD_DIR / "index.html"


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
        result.append(d)
    return result


def generate() -> Path:
    DASHBOARD_DIR.mkdir(exist_ok=True)
    tasks       = _load_all_tasks()
    projects    = sorted({t["project"] for t in tasks})
    generated   = datetime.now().strftime("%Y-%m-%d %H:%M")

    tasks_json  = json.dumps(tasks, default=str)
    colors_json = json.dumps(PROJECT_COLORS)

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
</style>
</head>
<body>

<div class="header">
  <h1>⚙ Orchestrator</h1>
  <div id="statsBar" class="stats-bar" style="padding:0;border:none;"></div>
  <div class="generated">Generated {generated}</div>
</div>

<div class="filters">
  <span class="filter-label">Project</span>
  <span class="pill active" data-filter="project" data-value="all" onclick="setFilter('project','all',this)">All</span>
  {' '.join(f'<span class="pill" data-filter="project" data-value="{p}" onclick="setFilter(\'project\',\'{p}\',this)" style="border-color:{PROJECT_COLORS.get(p,\'#555\')}">{p}</span>' for p in projects)}
  &nbsp;
  <span class="filter-label">Perspective</span>
  <span class="pill active" data-filter="perspective" data-value="all" onclick="setFilter('perspective','all',this)">All</span>
  {''.join(f'<span class="pill" data-filter="perspective" data-value="{p}" onclick="setFilter(\'perspective\',\'{p}\',this)">{p.replace("_"," ")}</span>' for p in PERSPECTIVES)}
  &nbsp;
  <span class="filter-label">Complexity</span>
  <span class="pill active" data-filter="complexity" data-value="all" onclick="setFilter('complexity','all',this)">All</span>
  <span class="pill" data-filter="complexity" data-value="high" onclick="setFilter('complexity','high',this)" style="color:var(--red)">High</span>
  <span class="pill" data-filter="complexity" data-value="medium" onclick="setFilter('complexity','medium',this)" style="color:var(--yellow)">Medium</span>
  <span class="pill" data-filter="complexity" data-value="low" onclick="setFilter('complexity','low',this)" style="color:var(--green)">Low</span>
</div>

<div class="board" id="board"></div>

<script>
const ALL_TASKS   = {tasks_json};
const COLORS      = {colors_json};
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
  return `
    <div class="card" title="${{t.rationale || ''}}">
      <div class="card-top">
        <span class="project-dot" style="background:${{color}}"></span>
        <span class="card-desc truncated">${{t.description}}</span>
      </div>
      <div class="card-meta">
        ${{badge(t.project, 'project', )}}
        ${{badge((t.perspective||'').replace(/_/g,' '), 'perspective')}}
        ${{badge(t.complexity||'medium', 'complexity-' + (t.complexity||'medium'))}}
        ${{badge(t.effort_category||'feature', 'effort')}}
        ${{approvalBadge}}
      </div>
      ${{t.rationale ? `<div class="card-id" title="Rationale">${{t.rationale}}</div>` : ''}}
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

render();
</script>
</body>
</html>"""

    OUTPUT_PATH.write_text(html)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = generate()
    print(f"Dashboard written → {path}")
