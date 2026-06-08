#!/usr/bin/env python3
"""
approve.py — CLI for approval_required tasks.

With FEAT-AutoCommit, 90-95% of tasks commit automatically.
This script handles only the short approval_required list:
  - JWT auth implementation
  - User data schema migrations
  - Client deliverables (tax)
  - Council-flagged architecture decisions

Usage:
  python approve.py                    list pending approval_required tasks
  python approve.py <task_id>          approve: git commit + mark completed
  python approve.py --reject <task_id> reject: mark failed + delete diff
  python approve.py --open <task_id>   open diff in $PAGER (or less)

What "approve" does:
  - git add -A + git commit -m "[approved] {project}: {description}"
  - Moves diff file to approved/ archive
  - Marks task completed in SQLite
  - Posts Discord notification to #orchestrator-live
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from config import BASE_DIR, PENDING_DIR, APPROVED_DIR, REPO_PATHS

APPROVED_DIR.mkdir(exist_ok=True)
(BASE_DIR / "pending_review").mkdir(exist_ok=True)

from core.task_queue import TaskQueue
import core.notify as notify

task_queue = TaskQueue()


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _find_diff(task_id: str) -> "Path | None":
    matches = list(PENDING_DIR.glob(f"*{task_id}*.diff"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Multiple diffs match '{task_id}':")
        for m in matches:
            print(f"  {m.name}")
        return None
    return None


def _get_pending() -> list[dict]:
    """Return pending approval_required tasks sorted by review_priority desc."""
    tasks = task_queue.get_pending_review()
    # Filter to approval_required only — auto-committed tasks won't appear here
    # but filtering ensures future-safety
    return sorted(
        [t for t in tasks if t.get("approval_required", False)],
        key=lambda t: t.get("review_priority", 3),
        reverse=True,
    )


def _project_from_diff(diff_path: Path) -> str:
    return diff_path.name.split("_")[0]


# ── COMMANDS ──────────────────────────────────────────────────────────────────

def cmd_list():
    pending = _get_pending()
    if not pending:
        print("No approval_required tasks pending.")
        print("Most tasks now auto-commit — check git log or Discord #orchestrator-live.")
        return

    colour = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[92m"}
    reset  = "\033[0m"

    print(f"\n{'─'*72}")
    print(f"  {'PRIORITY':<8} {'PROJ':<10} {'COMPLEXITY':<10} {'PERSPECTIVE':<22} ID")
    print(f"{'─'*72}")
    for t in pending:
        c   = t.get("complexity", "medium")
        col = colour.get(c, "")
        print(
            f"  {t.get('review_priority',3):<8} "
            f"{t['project']:<10} "
            f"{col}{c:<10}{reset} "
            f"{t.get('perspective',''):<22} "
            f"{t['id']}"
        )
        diff = _find_diff(t["id"])
        print(f"    {t['description'][:65]}")
        if diff:
            print(f"    diff → {diff.name}")
        print()
    print(f"  {len(pending)} task(s) require approval")
    print(f"  Approve: python approve.py <task_id>")
    print(f"  Open:    python approve.py --open <task_id>")
    print(f"{'─'*72}\n")


def cmd_approve(task_id: str):
    diff_path = _find_diff(task_id)
    if not diff_path:
        print(f"No diff found for task_id '{task_id}' in pending_review/")
        print("Run: python approve.py   to see all pending")
        sys.exit(1)

    project   = _project_from_diff(diff_path)
    repo_path = REPO_PATHS.get(project)

    if not repo_path or not repo_path.exists():
        print(f"Repo not found for project '{project}': {repo_path}")
        sys.exit(1)

    # Find the task record
    tasks = task_queue.get_pending_review()
    task  = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        print(f"Warning: task {task_id} not found in DB — it may already be processed.")
        task = {"id": task_id, "project": project, "description": "(unknown)", "perspective": ""}

    # Stage and commit
    add_result = subprocess.run(
        ["git", "add", "-A"], cwd=repo_path, capture_output=True, text=True,
    )
    if add_result.returncode != 0:
        print(f"git add failed:\n{add_result.stderr}")
        sys.exit(1)

    description = task.get("description", "")[:60]
    perspective = task.get("perspective", "")
    commit_msg  = f"[approved] {project}: {description} ({perspective})"

    commit_result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=repo_path, capture_output=True, text=True,
    )
    if commit_result.returncode != 0:
        if "nothing to commit" in commit_result.stdout + commit_result.stderr:
            print("Nothing to commit — changes may have already been applied.")
        else:
            print(f"git commit failed:\n{commit_result.stderr}")
            sys.exit(1)

    # Get commit hash
    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
    )
    commit_hash = rev.stdout.strip() if rev.returncode == 0 else ""

    # Mark completed in DB
    task_queue.mark_committed(task, commit_hash=commit_hash, diff_path=str(diff_path))

    # Archive diff
    archive = APPROVED_DIR / diff_path.name
    diff_path.rename(archive)

    # Discord notification
    notify.post(
        "live",
        f"✅  **[{project}]** Approved + committed: {description}\n"
        f"    commit: `{commit_hash}` (approved via approve.py)"
    )

    print(f"\n✓ Approved and committed {task_id}")
    print(f"  Commit: {commit_hash} in {repo_path}")
    print(f"  Diff archived → approved/{diff_path.name}")
    print(f"  Discord notified on #orchestrator-live")


def cmd_reject(task_id: str):
    diff_path = _find_diff(task_id)

    tasks = task_queue.get_pending_review()
    task  = next((t for t in tasks if t["id"] == task_id), None)
    if task:
        task_queue.mark_failed(task, notes="rejected by Jacob via approve.py")
        notify.post(
            "live",
            f"❌  **[{task['project']}]** Rejected: {task.get('description','')[:60]}…\n"
            f"    task `{task_id}` rejected via approve.py"
        )
    else:
        print(f"Warning: task {task_id} not in DB pending_review state.")

    if diff_path:
        diff_path.unlink()
        print(f"✗ Rejected {task_id} — diff deleted")
    else:
        print(f"✗ Rejected {task_id} — no diff file found")

    for f in PENDING_DIR.glob(f"*{task_id}*"):
        f.unlink()
        print(f"  Removed {f.name}")


def cmd_open(task_id: str):
    diff_path = _find_diff(task_id)
    if not diff_path:
        print(f"No diff found for '{task_id}'")
        sys.exit(1)
    pager = os.environ.get("PAGER", "less")
    subprocess.run([pager, str(diff_path)])


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Approve or reject approval_required AI-generated tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("task_id",  nargs="?",  help="Task ID to approve")
    parser.add_argument("--reject", metavar="TASK_ID", help="Reject a task")
    parser.add_argument("--open",   metavar="TASK_ID", help="Open diff in pager")

    args = parser.parse_args()

    if args.reject:
        cmd_reject(args.reject)
    elif args.open:
        cmd_open(args.open)
    elif args.task_id:
        cmd_approve(args.task_id)
    else:
        cmd_list()


if __name__ == "__main__":
    main()
