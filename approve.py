#!/usr/bin/env python3
"""
approve.py — CLI for reviewing and acting on pending diffs.

Usage:
  python approve.py                    list all pending diffs by review priority
  python approve.py <task_id>          approve: git add + mark completed + archive diff
  python approve.py --reject <task_id> reject: mark failed + delete diff
  python approve.py --open <task_id>   open diff in $PAGER (or less)

What "approve" does:
  - Stages changes in the project repo (git add) — does NOT commit
  - You review staged changes, then commit yourself when ready
  - Moves diff file to approved/ archive
  - Marks task completed in SQLite

What "approve" does NOT do:
  - git commit (you batch-review then commit yourself)
  - Push to remote
  - Auto-merge anything
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Resolve project root relative to this script
from config import BASE_DIR, PENDING_DIR, APPROVED_DIR, REPO_PATHS

# Ensure dirs exist regardless of whether orchestrator_main has run
APPROVED_DIR.mkdir(exist_ok=True)
(BASE_DIR / "pending_review").mkdir(exist_ok=True)


from task_queue import TaskQueue
task_queue = TaskQueue()


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _find_diff(task_id: str) -> Path | None:
    """Find a .diff file in pending_review/ matching a task_id."""
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
    """Return pending tasks sorted by review_priority desc."""
    return sorted(
        task_queue.get_pending_review(),
        key=lambda t: t.get("review_priority", 3),
        reverse=True,
    )


def _project_from_diff(diff_path: Path) -> str:
    return diff_path.name.split("_")[0]


# ── COMMANDS ──────────────────────────────────────────────────────────────────

def cmd_list():
    pending = _get_pending()
    if not pending:
        print("No pending diffs. Run the orchestrator to generate work.")
        return

    # Complexity → colour via ANSI (terminal only)
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
        # Show diff filename if present
        diff = _find_diff(t["id"])
        print(f"    {t['description'][:65]}")
        if diff:
            print(f"    diff → {diff.name}")
        print()
    print(f"  {len(pending)} diffs pending review")
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

    # Stage changes — do NOT commit (Jacob reviews staged diff, commits himself)
    result = subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"git add failed:\n{result.stderr}")
        sys.exit(1)

    # Show what's staged
    staged = subprocess.run(
        ["git", "diff", "--staged", "--stat"],
        cwd=repo_path, capture_output=True, text=True,
    )
    print(f"\nStaged in {project}:\n{staged.stdout}")

    # Mark completed in DB
    tasks = task_queue.get_pending_review()
    match = next((t for t in tasks if t["id"] == task_id), None)
    if match:
        task_queue.mark_completed(match)
    else:
        print(f"Warning: task {task_id} not found in DB — may already be marked.")

    # Archive diff
    archive = APPROVED_DIR / diff_path.name
    diff_path.rename(archive)

    print(f"✓ Approved {task_id}")
    print(f"  Changes staged in {repo_path}")
    print(f"  Diff archived → approved/{diff_path.name}")
    print(f"  Review staged changes: git -C {repo_path} diff --staged")
    print(f"  Commit when ready:     git -C {repo_path} commit -m \"feat: <description>\"")


def cmd_reject(task_id: str):
    diff_path = _find_diff(task_id)

    tasks = task_queue.get_pending_review()
    match = next((t for t in tasks if t["id"] == task_id), None)
    if match:
        task_queue.mark_failed(match, notes="rejected by Jacob")
    else:
        print(f"Warning: task {task_id} not in DB pending_review state.")

    if diff_path:
        diff_path.unlink()
        print(f"✗ Rejected {task_id} — diff deleted")
    else:
        print(f"✗ Rejected {task_id} — no diff file found (already gone?)")

    # Clean up associated failure JSON files if present
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
        description="Approve, reject, or inspect pending AI-generated diffs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("task_id",  nargs="?",  help="Task ID to approve")
    parser.add_argument("--reject", metavar="TASK_ID", help="Reject a diff")
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
