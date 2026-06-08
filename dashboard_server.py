"""
dashboard_server.py
Serves dashboard/index.html on a local HTTP port.

The #orchestrator-blocked Discord embed includes a link to this server
so Jacob can click through for full Kanban context when approving tasks.

Usage:
  python dashboard_server.py          # default port 8080
  python dashboard_server.py 9090     # custom port

Or run in background alongside the orchestrator:
  nohup python dashboard_server.py &

Port can also be set via the DASHBOARD_PORT env var.
The server auto-regenerates the dashboard on each request so it's always
showing current data without a manual refresh cycle.
"""

import os
import sys
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE_DIR       = Path(__file__).parent
DASHBOARD_DIR  = BASE_DIR / "dashboard"
DASHBOARD_FILE = DASHBOARD_DIR / "index.html"
PIPELINE_DIR   = BASE_DIR / "pipeline_logs"
PENDING_DIR    = BASE_DIR / "pending_review"

log = logging.getLogger("dashboard_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class DashboardHandler(BaseHTTPRequestHandler):
    """
    Minimal HTTP handler that serves the dashboard HTML.
    Regenerates the dashboard on each GET / request so data is always fresh.
    """

    def log_message(self, fmt, *args):
        # Quiet the default per-request logging — it's noisy overnight
        log.debug(fmt % args)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path in ("", "/", "/index.html"):
            self._serve_dashboard()
        elif path.startswith("/pipeline/"):
            self._serve_pipeline(path[len("/pipeline/"):])
        elif path.startswith("/diff/"):
            self._serve_diff(path[len("/diff/"):])
        else:
            self._not_found()

    def _serve_dashboard(self):
        # Regenerate on request so data is always current.
        # importlib.reload() ensures edits to dashboard_generator.py are picked up
        # without restarting the server — critical during development.
        try:
            import importlib
            import dashboard_generator
            importlib.reload(dashboard_generator)
            dashboard_generator.generate()
        except Exception as e:
            log.warning(f"Dashboard regeneration failed: {e} — serving cached version")

        if not DASHBOARD_FILE.exists():
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"Dashboard not generated yet. Run the orchestrator first.")
            return

        content = DASHBOARD_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _serve_pipeline(self, task_id: str):
        """Serve pipeline_logs/{task_id}.json for the dashboard modal."""
        task_id  = task_id.strip().split("/")[0]   # sanitize
        log_path = PIPELINE_DIR / f"{task_id}.json"
        if not log_path.exists():
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"pipeline log not found"}')
            return
        content = log_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _serve_diff(self, diff_name: str):
        """Serve a diff file from pending_review/ by filename."""
        diff_name = diff_name.strip().split("/")[0]   # sanitize — no path traversal
        diff_path = PENDING_DIR / diff_name
        if not diff_path.exists() or not diff_name.endswith(".diff"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Diff not found")
            return
        content = diff_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def _not_found(self):
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not found")


def run(port: int = 8080, bind: str = "0.0.0.0"):
    """Start the HTTP server. Blocks until interrupted."""
    server = HTTPServer((bind, port), DashboardHandler)
    log.info(f"Dashboard server listening on http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Dashboard server stopped.")
    finally:
        server.server_close()


def start_background(port: int = 8080) -> threading.Thread:
    """
    Start the server in a daemon thread.
    Call this from orchestrator_main.py to co-locate the server with the scheduler.

    Example:
        from dashboard_server import start_background
        start_background(port=CFG.get("DASHBOARD_PORT", 8080))
    """
    t = threading.Thread(target=run, args=(port,), daemon=True, name="dashboard_server")
    t.start()
    log.info(f"Dashboard server started in background on port {port}")
    return t


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("DASHBOARD_PORT", "8080"))
    run(port=port)
