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

from config import DB_PATH, PENDING_DIR, RETROS_DIR, LOGS_DIR, PROJECT_COLORS, PERSPECTIVES, EFFORT_CATEGORIES

try:
    from retro_generator import load_all_retros
except ImportError:
    def load_all_retros():
        return []


def _load_real_spend() -> dict:
    """
    Read spend.json for the true monthly/daily totals.
    The DB cost_usd only covers successful tasks — failed API calls are tracked
    separately in spend.json by SpendTracker.
    """
    spend_file = LOGS_DIR / "spend.json"
    if not spend_file.exists():
        return {"monthly": 0.0, "daily": 0.0, "total": 0.0}
    try:
        data  = json.loads(spend_file.read_text())
        month = datetime.now().strftime("%Y-%m")
        today = datetime.now().strftime("%Y-%m-%d")
        monthly = sum(v["usd"] for k, v in data.get("daily", {}).items() if k.startswith(month))
        daily   = data.get("daily", {}).get(today, {}).get("usd", 0.0)
        return {"monthly": round(monthly, 4), "daily": round(daily, 4), "total": round(data.get("total_usd", 0.0), 4)}
    except Exception:
        return {"monthly": 0.0, "daily": 0.0, "total": 0.0}


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
            if d.get("started_at") and d.get("completed_at"):
                s = datetime.fromisoformat(d["started_at"])
                e = datetime.fromisoformat(d["completed_at"])
                d["duration_sec"] = int((e - s).total_seconds())
            else:
                d["duration_sec"] = None
        except Exception:
            d["duration_sec"] = None

        # For failed tasks, load extra detail from the saved JSON
        d["quality_reasoning"] = ""
        d["quality_issues"]    = []
        d["response_preview"]  = ""
        d["attempts"]          = None
        d["injected_files"]    = []
        if d.get("status") == "failed":
            for prefix in ("QUALITY_FAILED_", "FAILED_"):
                detail_path = PENDING_DIR / f"{prefix}{d['project']}_{d['id']}.json"
                if detail_path.exists():
                    try:
                        detail = json.loads(detail_path.read_text())
                        ev = detail.get("evaluation", {})
                        d["quality_reasoning"] = ev.get("reasoning", "")
                        d["quality_issues"]    = ev.get("issues", [])
                        d["response_preview"]  = detail.get("response_preview", "")
                        d["attempts"]          = detail.get("attempts")
                        d["injected_files"]    = detail.get("injected_files", [])
                    except Exception:
                        pass
                    break

        result.append(d)
    return result


def generate() -> Path:
    DASHBOARD_DIR.mkdir(exist_ok=True)
    tasks       = _load_all_tasks()
    metrics     = _load_metrics()
    projects    = sorted({t["project"] for t in tasks})
    generated   = datetime.now().strftime("%Y-%m-%d %H:%M")

    retros       = load_all_retros()
    real_spend   = _load_real_spend()
    tasks_json   = json.dumps(tasks, default=str)
    colors_json  = json.dumps(PROJECT_COLORS)
    metrics_json = json.dumps(metrics, default=str)
    retros_json  = json.dumps(retros, default=str)
    spend_json   = json.dumps(real_spend)

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

  /* ── ADVANCED FILTERS DROPDOWN ── */
  .adv-filter-wrap {{ position: relative; }}
  .adv-filter-btn {{
    padding: 3px 12px; border-radius: 999px; font-size: 11px; font-weight: 500;
    cursor: pointer; border: 1px solid var(--border); background: var(--surface2);
    color: var(--muted); transition: all .15s; user-select: none;
  }}
  .adv-filter-btn:hover {{ color: var(--text); border-color: #4b5280; }}
  .adv-filter-btn.has-active {{ color: var(--blue); border-color: var(--blue); }}
  .adv-filter-panel {{
    display: none; position: absolute; top: calc(100% + 8px); left: 0; z-index: 50;
    background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px; min-width: 320px; box-shadow: 0 12px 40px rgba(0,0,0,.5);
  }}
  .adv-filter-panel.open {{ display: block; }}
  .adv-section-label {{
    font-size: 10px; text-transform: uppercase; letter-spacing: .5px;
    color: var(--muted); margin-bottom: 8px; font-weight: 600;
  }}
  .adv-pills {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 14px; }}

  /* ── STATS BAR ── */
  .stats-bar {{ padding: 10px 20px; display: flex; gap: 20px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }}
  .stat {{ font-size: 11px; color: var(--muted); }}
  .stat span {{ color: var(--text); font-weight: 600; font-size: 13px; }}

  /* ── SWIMLANE BOARD ── */
  .board {{
    display: grid;
    grid-template-columns: 100px repeat(5, 1fr);
    gap: 0;
    padding: 0 20px 20px;
    min-height: calc(100vh - 160px);
    overflow-x: auto;
  }}
  /* Status header row */
  .board-col-header {{
    position: sticky; top: 0; z-index: 10;
    background: var(--bg); padding: 10px 8px 8px;
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .6px; color: var(--muted);
    display: flex; align-items: center; gap: 6px;
    border-bottom: 1px solid var(--border);
  }}
  .board-col-header.first {{ border-right: 1px solid var(--border); }}
  .board-col-header .col-dot {{
    width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  }}
  /* Project label cells */
  .project-label {{
    position: sticky; left: 0; z-index: 5;
    background: var(--bg); border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    padding: 10px 8px; display: flex; align-items: flex-start;
    padding-top: 12px;
  }}
  .project-label-inner {{
    writing-mode: horizontal-tb;
    font-size: 11px; font-weight: 700; letter-spacing: .4px;
    color: var(--text); display: flex; align-items: center; gap: 6px;
  }}
  .project-dot-label {{
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  }}
  /* Task cells */
  .swim-cell {{
    padding: 6px; border-bottom: 1px solid var(--border);
    border-right: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 5px;
    min-height: 60px; background: var(--bg);
  }}
  .swim-cell.empty {{ background: var(--bg); }}
  /* Column (kept for backward compat with card styles) */
  .column {{
    flex: 1 1 0; min-width: 0; background: var(--surface); border-radius: 10px;
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
  .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; padding: 16px 20px; }}
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

  /* ── RETROS ── */
  .retro-layout {{ display: grid; grid-template-columns: 220px 1fr; gap: 0; min-height: calc(100vh - 120px); }}
  .retro-sidebar {{
    border-right: 1px solid var(--border); padding: 12px 0; overflow-y: auto;
  }}
  .retro-date-item {{
    padding: 10px 16px; font-size: 13px; cursor: pointer; color: var(--muted);
    border-left: 3px solid transparent; transition: all .15s;
  }}
  .retro-date-item:hover {{ color: var(--text); background: var(--surface2); }}
  .retro-date-item.active {{ color: var(--text); border-left-color: var(--blue); background: var(--surface2); }}
  .retro-date-label {{ font-weight: 600; }}
  .retro-date-sub {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  .retro-content {{ padding: 24px 32px; overflow-y: auto; }}
  .retro-header {{ margin-bottom: 24px; }}
  .retro-title {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
  .retro-period {{ font-size: 12px; color: var(--muted); }}
  .retro-stat-row {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 28px; }}
  .retro-stat {{
    background: var(--surface2); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px;
  }}
  .retro-stat-value {{ font-size: 26px; font-weight: 700; }}
  .retro-stat-label {{ font-size: 11px; color: var(--muted); margin-top: 3px; text-transform: uppercase; letter-spacing: .4px; }}
  .retro-section {{ margin-bottom: 28px; }}
  .retro-section-title {{
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); margin-bottom: 12px; padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
  .retro-narrative {{
    background: var(--surface2); border-radius: 10px; padding: 18px 20px;
    font-size: 14px; line-height: 1.75; color: var(--text);
  }}
  .retro-narrative-block {{ margin-bottom: 20px; }}
  .retro-narrative-block:last-child {{ margin-bottom: 0; }}
  .retro-narrative-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px; color: var(--blue); margin-bottom: 6px; }}
  .retro-task-list {{ display: flex; flex-direction: column; gap: 8px; }}
  .retro-task {{
    background: var(--surface2); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 14px; display: flex; gap: 12px; align-items: flex-start;
  }}
  .retro-task-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }}
  .retro-task-body {{ flex: 1; min-width: 0; }}
  .retro-task-desc {{ font-size: 13px; color: var(--text); line-height: 1.5; }}
  .retro-task-meta {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  .retro-task-note {{ font-size: 11px; color: var(--red); margin-top: 3px; font-family: monospace; }}
  .retro-empty {{ color: var(--muted); font-size: 13px; padding: 16px 0; }}

  /* ── MODAL ── */
  .modal-overlay {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.7);
    z-index: 100; align-items: flex-start; justify-content: center; padding: 20px; overflow-y: auto;
  }}
  .modal-overlay.open {{ display: flex; }}
  .modal {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
    width: 100%; max-width: 1400px; padding: 48px; position: relative;
    box-shadow: 0 32px 100px rgba(0,0,0,.7);
  }}
  .modal-close {{
    position: absolute; top: 20px; right: 24px; font-size: 28px; cursor: pointer;
    color: var(--muted); background: none; border: none; line-height: 1;
  }}
  .modal-close:hover {{ color: var(--text); }}
  .modal-id {{ font-size: 14px; color: var(--muted); margin-bottom: 10px; font-family: monospace; }}
  .modal-title {{ font-size: 22px; font-weight: 600; color: var(--text); margin-bottom: 24px; line-height: 1.5; }}
  .modal-section {{ margin-top: 32px; }}
  .modal-section-label {{
    font-size: 13px; text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); margin-bottom: 14px; font-weight: 600;
  }}
  .modal-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }}
  .modal-field {{ background: var(--surface2); border-radius: 10px; padding: 16px 18px; }}
  .modal-field-label {{ font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; margin-bottom: 6px; }}
  .modal-field-value {{ font-size: 16px; color: var(--text); font-weight: 500; word-break: break-all; }}
  .modal-rationale {{
    background: var(--surface2); border-radius: 10px; padding: 18px 20px;
    font-size: 15px; color: var(--text); line-height: 1.7; border-left: 4px solid var(--blue);
  }}
  .diff-stats {{ display: flex; gap: 16px; align-items: center; }}
  .diff-added {{ color: var(--green); font-size: 16px; font-weight: 600; font-family: monospace; }}
  .diff-removed {{ color: var(--red); font-size: 16px; font-weight: 600; font-family: monospace; }}
  .diff-files {{ color: var(--muted); font-size: 15px; }}
  .modal-prompt {{
    background: #0a0c14; border: 1px solid var(--border); border-radius: 10px;
    padding: 18px; font-family: monospace; font-size: 13px; color: #94a3b8;
    white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; line-height: 1.7;
  }}
  .modal-prompt-toggle {{
    font-size: 13px; color: var(--blue); cursor: pointer; margin-bottom: 10px; display: inline-block;
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
  <div class="tab" onclick="switchTab('retros',this)">🔁 Retros</div>
</div>

<div id="tab-kanban" class="tab-content active">
<div class="filters">
  <span class="filter-label">Project</span>
  <span class="pill active" data-filter="project" data-value="all" onclick="setFilter('project','all',this)">All</span>
  {project_pills}

  <div class="adv-filter-wrap" style="margin-left:8px">
    <div class="adv-filter-btn" id="advFilterBtn" onclick="toggleAdvFilters()">⚙ Filters</div>
    <div class="adv-filter-panel" id="advFilterPanel">
      <div class="adv-section-label">Perspective</div>
      <div class="adv-pills">
        <span class="pill active" data-filter="perspective" data-value="all" onclick="setFilter('perspective','all',this)">All</span>
        {perspective_pills}
      </div>
      <div class="adv-section-label">Complexity</div>
      <div class="adv-pills">
        <span class="pill active" data-filter="complexity" data-value="all" onclick="setFilter('complexity','all',this)">All</span>
        <span class="pill" data-filter="complexity" data-value="high" onclick="setFilter('complexity','high',this)" style="color:var(--red)">High</span>
        <span class="pill" data-filter="complexity" data-value="medium" onclick="setFilter('complexity','medium',this)" style="color:var(--yellow)">Medium</span>
        <span class="pill" data-filter="complexity" data-value="low" onclick="setFilter('complexity','low',this)" style="color:var(--green)">Low</span>
      </div>
    </div>
  </div>
</div>

<div class="board" id="board"></div>
</div><!-- end tab-kanban -->

<div id="tab-metrics" class="tab-content">
  <div class="metrics-grid" id="metricsGrid"></div>
</div>

<div id="tab-retros" class="tab-content">
  <div class="retro-layout">
    <div class="retro-sidebar" id="retroSidebar"></div>
    <div class="retro-content" id="retroContent">
      <div class="retro-empty" style="padding:40px">No retrospectives yet — the first one generates at midnight.</div>
    </div>
  </div>
</div>

<script>
const ALL_TASKS   = {tasks_json};
const COLORS      = {colors_json};
const METRICS     = {metrics_json};
const ALL_RETROS  = {retros_json};
const REAL_SPEND  = {spend_json};
const COLUMNS     = [
  {{ key: 'queued',    label: 'Queued',                dot: '#94a3b8' }},
  {{ key: 'running',   label: 'Running',               dot: '#3b82f6' }},
  {{ key: 'completed', label: 'Committed / Completed', dot: '#10b981' }},
  {{ key: 'failed',    label: 'Failed',                dot: '#ef4444' }},
];

const filters = {{ project: 'all', perspective: 'all', complexity: 'all' }};

function toggleAdvFilters() {{
  document.getElementById('advFilterPanel').classList.toggle('open');
}}

// Close dropdown when clicking outside
document.addEventListener('click', e => {{
  const wrap = document.getElementById('advFilterBtn')?.closest('.adv-filter-wrap');
  if (wrap && !wrap.contains(e.target)) {{
    document.getElementById('advFilterPanel').classList.remove('open');
  }}
}});

function setFilter(key, value, el) {{
  filters[key] = value;
  // Pills may live inside .filters or .adv-filter-panel — search both
  document.querySelectorAll(`[data-filter="${{key}}"]`).forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  // Highlight the ⚙ Filters button if any advanced filter is non-default
  const btn = document.getElementById('advFilterBtn');
  if (btn) {{
    const advActive = filters.perspective !== 'all' || filters.complexity !== 'all';
    btn.classList.toggle('has-active', advActive);
    btn.textContent = advActive ? '⚙ Filters ●' : '⚙ Filters';
  }}
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

  // ── SWIMLANE LAYOUT ───────────────────────────────────────────────────────
  // Row per project, column per status. Header row first.

  const projects = [...new Set(ALL_TASKS.map(t => t.project))].sort();

  // Header row: empty corner + status column headers
  const corner = document.createElement('div');
  corner.className = 'board-col-header first';
  corner.textContent = 'Project';
  board.appendChild(corner);

  COLUMNS.forEach(col => {{
    const hdr = document.createElement('div');
    hdr.className = 'board-col-header';
    const colCount = visible.filter(t => t.status === col.key).length;
    hdr.innerHTML = `<span class="col-dot" style="background:${{col.dot}}"></span>${{col.label}} <span class="count" style="margin-left:4px">${{colCount}}</span>`;
    board.appendChild(hdr);
  }});

  // One row per project
  projects.forEach(proj => {{
    const color = COLORS[proj] || '#555';

    // Project label cell
    const lbl = document.createElement('div');
    lbl.className = 'project-label';
    lbl.innerHTML = `<div class="project-label-inner">
      <span class="project-dot-label" style="background:${{color}}"></span>
      ${{proj}}
    </div>`;
    board.appendChild(lbl);

    // One cell per status column
    COLUMNS.forEach(col => {{
      const cell = document.createElement('div');
      cell.className = 'swim-cell';
      const cellTasks = visible.filter(t => t.project === proj && t.status === col.key);

      if (cellTasks.length === 0) {{
        cell.classList.add('empty');
      }} else {{
        cell.innerHTML = cellTasks.map(card).join('');
      }}
      board.appendChild(cell);
    }});
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
  if (name === 'retros')  initRetros();
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
        ${{field('Cost', (t.cost_usd || t.status === 'failed') ? '$' + (t.cost_usd||0).toFixed(5) : null)}}
        ${{field('Tokens (actual)', t.actual_tokens ? t.actual_tokens.toLocaleString() : null)}}
        ${{field('Tokens (est.)', (!t.actual_tokens && t.estimated_tokens) ? t.estimated_tokens.toLocaleString() : null)}}
        ${{field('Model', t.model_used)}}
        ${{field('Commit', t.commit_hash ? t.commit_hash.slice(0,7) : null)}}
      </div>
    </div>

    ${{diffBlock}}

    ${{(() => {{
      if (t.status !== 'failed') return '';
      const errorCode   = (t.notes || '').replace('rejected: ', '');
      const reasoning   = t.quality_reasoning || '';
      const issues      = (t.quality_issues || []).join(', ');
      const rejection   = t.rejection_reason || '';
      const preview     = t.response_preview || '';
      const attempts    = t.attempts;
      const files       = (t.injected_files || []);
      return `<div class="modal-section">
        <div class="modal-section-label">Failure Details</div>
        <div class="modal-grid">
          ${{errorCode ? `<div class="modal-field" style="border-left:4px solid var(--red)">
            <div class="modal-field-label">Error</div>
            <div class="modal-field-value" style="color:var(--red);font-family:monospace">${{errorCode}}</div>
          </div>` : ''}}
          ${{attempts != null ? `<div class="modal-field">
            <div class="modal-field-label">Attempts</div>
            <div class="modal-field-value">${{attempts}} / 3</div>
          </div>` : ''}}
          ${{rejection ? `<div class="modal-field">
            <div class="modal-field-label">Rejected By</div>
            <div class="modal-field-value">${{rejection}}</div>
          </div>` : ''}}
          ${{issues ? `<div class="modal-field">
            <div class="modal-field-label">Quality Issues</div>
            <div class="modal-field-value" style="color:var(--yellow)">${{issues}}</div>
          </div>` : ''}}
        </div>
        ${{reasoning ? `<div class="modal-field" style="margin-top:10px">
          <div class="modal-field-label">Quality Gate Reasoning</div>
          <div class="modal-field-value" style="font-weight:400;line-height:1.6;margin-top:4px">${{reasoning}}</div>
        </div>` : ''}}
        ${{files.length ? `<div class="modal-field" style="margin-top:10px">
          <div class="modal-field-label">Files Injected as Context</div>
          <div class="modal-field-value" style="font-family:monospace;font-size:13px;font-weight:400;line-height:1.8">${{files.join('<br>')}}</div>
        </div>` : ''}}
        ${{preview ? `<div style="margin-top:12px">
          <div class="modal-field-label" style="margin-bottom:6px">MiniMax Response Preview</div>
          <div class="modal-prompt">${{escHtml(preview)}}</div>
        </div>` : ''}}
      </div>`;
    }})()}}

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

// ── RETROS ────────────────────────────────────────────────────────────────────

function renderRetroSidebar() {{
  const sidebar = document.getElementById('retroSidebar');
  if (!ALL_RETROS.length) {{
    sidebar.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--muted)">No retros yet</div>';
    return;
  }}
  sidebar.innerHTML = ALL_RETROS.map((r, i) => {{
    const s = r.stats || {{}};
    const label = r.date || 'Unknown';
    return `<div class="retro-date-item ${{i===0?'active':''}}" onclick="selectRetro(${{i}},this)">
      <div class="retro-date-label">${{label}}</div>
      <div class="retro-date-sub">${{s.completed||0}} done · ${{s.failed||0}} failed · $${{(s.total_cost_usd||0).toFixed(3)}}</div>
    </div>`;
  }}).join('');
}}

function selectRetro(idx, el) {{
  document.querySelectorAll('.retro-date-item').forEach(d => d.classList.remove('active'));
  el.classList.add('active');
  renderRetroContent(ALL_RETROS[idx]);
}}

function retroTaskRow(t, dotColor) {{
  const meta = [t.project, t.perspective?.replace(/_/g,' '), t.complexity].filter(Boolean).join(' · ');
  const note = t.notes ? `<div class="retro-task-note">${{escHtml(t.notes.slice(0,80))}}</div>` : '';
  const score = t.quality_score ? ` · score ${{t.quality_score}}/10` : '';
  return `<div class="retro-task">
    <div class="retro-task-dot" style="background:${{dotColor}}"></div>
    <div class="retro-task-body">
      <div class="retro-task-desc">${{escHtml(t.description?.slice(0,120) || '')}}</div>
      <div class="retro-task-meta">${{meta}}${{score}}</div>
      ${{note}}
    </div>
  </div>`;
}}

function narrativeBlock(label, text) {{
  if (!text) return '';
  return `<div class="retro-narrative-block">
    <div class="retro-narrative-label">${{label}}</div>
    <div>${{escHtml(text)}}</div>
  </div>`;
}}

function renderRetroContent(r) {{
  const s  = r.stats || {{}};
  const n  = r.narrative || {{}};
  const t  = r.tasks || {{}};
  const completed      = t.completed      || [];
  const failed         = t.failed         || [];
  const pending_review = t.pending_review || [];

  const qualityColor = (s.quality_avg || 0) >= 7 ? 'var(--green)' : (s.quality_avg || 0) >= 5 ? 'var(--yellow)' : 'var(--red)';

  const projectBreakdown = Object.entries(s.by_project || {{}})
    .map(([p, v]) => `<span style="font-size:12px;color:var(--muted)">${{p}}: ${{v.completed}}✓ ${{v.failed}}✗ $${{v.cost.toFixed(3)}}</span>`)
    .join('  ·  ');

  document.getElementById('retroContent').innerHTML = `
    <div class="retro-header">
      <div class="retro-title">📅 ${{r.date}} Retrospective</div>
      <div class="retro-period">
        ${{r.window_hours||24}}h window · Generated ${{(r.generated_at||'').slice(0,16)}}
        ${{projectBreakdown ? `<br><span style="margin-top:4px;display:inline-block">${{projectBreakdown}}</span>` : ''}}
      </div>
    </div>

    <div class="retro-stat-row">
      <div class="retro-stat">
        <div class="retro-stat-value" style="color:var(--green)">${{s.completed||0}}</div>
        <div class="retro-stat-label">Completed</div>
      </div>
      <div class="retro-stat">
        <div class="retro-stat-value" style="color:var(--red)">${{s.failed||0}}</div>
        <div class="retro-stat-label">Failed</div>
      </div>
      <div class="retro-stat">
        <div class="retro-stat-value" style="color:var(--yellow)">${{s.pending_review||0}}</div>
        <div class="retro-stat-label">Pending Review</div>
      </div>
      <div class="retro-stat">
        <div class="retro-stat-value" style="color:${{qualityColor}}">${{s.quality_avg != null ? s.quality_avg + '/10' : '—'}}</div>
        <div class="retro-stat-label">Avg Quality</div>
      </div>
      <div class="retro-stat">
        <div class="retro-stat-value">$${{(s.total_cost_usd||0).toFixed(4)}}</div>
        <div class="retro-stat-label">Total Cost</div>
      </div>
    </div>

    ${{Object.keys(r.sprint_features||{{}}).length ? (() => {{
      const sf = r.sprint_features || {{}};
      const rows = Object.entries(sf).map(([proj, cats]) => {{
        const catPills = Object.entries(cats)
          .sort((a,b) => b[1]-a[1])
          .map(([cat,n]) => `<span style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:12px;margin-right:4px">${{n}} ${{cat}}</span>`)
          .join('');
        return `<div class="perspective-row" style="padding:8px 0">
          <span class="persp-name" style="font-size:13px;font-weight:600">${{proj}}</span>
          <div>${{catPills}}</div>
        </div>`;
      }}).join('');
      return `<div class="retro-section">
        <div class="retro-section-title">🚀 Sprint Features Landed (last 7 days)</div>
        <div class="retro-narrative" style="padding:12px 16px">${{rows}}</div>
      </div>`;
    }})() : ''}}

    ${{(n.summary || n.wins || n.failures || n.patterns || n.recommendations || n.sprint_health) ? `
    <div class="retro-section">
      <div class="retro-section-title">🤖 Ollama Analysis</div>
      <div class="retro-narrative">
        ${{narrativeBlock('Summary', n.summary)}}
        ${{narrativeBlock('Sprint Health', n.sprint_health)}}
        ${{narrativeBlock('Wins', n.wins)}}
        ${{narrativeBlock('Failures', n.failures)}}
        ${{narrativeBlock('Patterns', n.patterns)}}
        ${{narrativeBlock('Recommendations', n.recommendations)}}
      </div>
    </div>` : ''}}

    ${{completed.length ? `
    <div class="retro-section">
      <div class="retro-section-title">✅ Completed (${{completed.length}})</div>
      <div class="retro-task-list">${{completed.map(t => retroTaskRow(t,'var(--green)')).join('')}}</div>
    </div>` : ''}}

    ${{failed.length ? `
    <div class="retro-section">
      <div class="retro-section-title">❌ Failed (${{failed.length}})</div>
      <div class="retro-task-list">${{failed.map(t => retroTaskRow(t,'var(--red)')).join('')}}</div>
    </div>` : ''}}

    ${{pending_review.length ? `
    <div class="retro-section">
      <div class="retro-section-title">⏳ Pending Review (${{pending_review.length}})</div>
      <div class="retro-task-list">${{pending_review.map(t => retroTaskRow(t,'var(--yellow)')).join('')}}</div>
    </div>` : ''}}
  `;
}}

function initRetros() {{
  renderRetroSidebar();
  if (ALL_RETROS.length) renderRetroContent(ALL_RETROS[0]);
}}
</script>
</body>
</html>"""

    OUTPUT_PATH.write_text(html)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = generate()
    print(f"Dashboard written → {path}")
