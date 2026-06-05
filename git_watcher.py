#!/usr/bin/env python3
"""
git_watcher.py
Runs on your Mac as a background process. Watches for COMMIT_REQUEST.txt
written by Claude (via the sandbox), then commits and pushes on your behalf
— where you have full filesystem permissions to clear git lock files.

Start it once per session (or add to login items):
  python3 git_watcher.py &
  python3 git_watcher.py --daemon   # suppress output

Stop it:
  kill $(cat .git_watcher.pid)
"""

import subprocess
import sys
import time
import os
import argparse
from pathlib import Path
from datetime import datetime

REPO     = Path(__file__).parent
REQUEST  = REPO / "COMMIT_REQUEST.txt"
PID_FILE = REPO / ".git_watcher.pid"
LOG_FILE = REPO / "logs" / "git_watcher.log"
POLL_SEC = 10   # check every 10 seconds


def log(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def clear_locks():
    for lock in REPO.glob(".git/*.lock"):
        try:
            lock.unlink()
            log(f"Cleared lock: {lock.name}")
        except Exception as e:
            log(f"Could not clear {lock.name}: {e}")


def git(*args) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO, capture_output=True, text=True
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def handle_request():
    if not REQUEST.exists():
        return

    message = REQUEST.read_text().strip()
    if not message:
        REQUEST.unlink(missing_ok=True)
        return

    log(f"Commit request: {message[:60]}")
    clear_locks()

    ok, out = git("add", "-A")
    if not ok:
        log(f"git add failed: {out}")
        REQUEST.write_text(f"ERROR: git add failed\n{out}")
        return

    ok, out = git("commit", "-m", message)
    if not ok:
        if "nothing to commit" in out:
            log("Nothing to commit — request satisfied")
            REQUEST.unlink(missing_ok=True)
            return
        log(f"git commit failed: {out}")
        REQUEST.write_text(f"ERROR: git commit failed\n{out}")
        return

    log(f"Committed: {out.splitlines()[0]}")

    ok, out = git("push", "origin", "main")
    if not ok:
        log(f"git push failed: {out}")
        REQUEST.write_text(f"ERROR: git push failed\n{out}")
        return

    log("Pushed to origin/main")
    REQUEST.unlink(missing_ok=True)


def run():
    PID_FILE.write_text(str(os.getpid()))
    log(f"git_watcher started (pid {os.getpid()}) — polling every {POLL_SEC}s")
    log(f"Watching: {REQUEST}")

    try:
        while True:
            handle_request()
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        log("git_watcher stopped")
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true",
                        help="Redirect output to log file only")
    args = parser.parse_args()

    if args.daemon:
        sys.stdout = open(LOG_FILE, "a")
        sys.stderr = sys.stdout

    run()
