#!/usr/bin/env python3
"""
validate.py — Pre-flight checklist for the Orchestrator.

Run before the first overnight session, or any time something seems off.
Loud failures here beat silent 2am debugging.

Usage:
  python validate.py
  python validate.py --fix    # show fix commands for failed checks
"""

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import requests

# Suppress orchestrator logger output during validation
import logging
logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    CFG, DB_PATH, ENABLED_PROJECTS, REPO_PATHS,
    OLLAMA_BASE, OLLAMA_MODEL_CODE, OLLAMA_MODEL_DIGEST,
    MINIMAX_SPEND_CAP, DASHBOARD_PORT,
)

# ── ANSI COLOURS ──────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

_results: list[tuple[bool, str, str]] = []   # (passed, label, fix)


def check(label: str, passed: bool, fix: str = ""):
    icon = f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"
    print(f"  {icon}  {label}")
    _results.append((passed, label, fix))
    return passed


def warn(label: str, fix: str = ""):
    print(f"  {YELLOW}⚠{RESET}  {label}")
    _results.append((True, f"(warn) {label}", fix))


def section(title: str):
    print(f"\n{BOLD}{title}{RESET}")


# ── CHECKS ────────────────────────────────────────────────────────────────────

def check_ollama():
    section("Ollama")
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        resp.raise_for_status()
        check("Ollama reachable at " + OLLAMA_BASE, True)
        loaded = {m["name"] for m in resp.json().get("models", [])}
        check(
            f"Model {OLLAMA_MODEL_CODE} loaded",
            OLLAMA_MODEL_CODE in loaded,
            fix=f"ollama pull {OLLAMA_MODEL_CODE}",
        )
        check(
            f"Model {OLLAMA_MODEL_DIGEST} loaded",
            OLLAMA_MODEL_DIGEST in loaded,
            fix=f"ollama pull {OLLAMA_MODEL_DIGEST}",
        )
    except Exception as e:
        check(f"Ollama reachable at {OLLAMA_BASE}", False,
              fix=f"Start Ollama: open /Applications/Ollama.app  (error: {e})")
        check(f"Model {OLLAMA_MODEL_CODE} loaded",  False, fix=f"ollama pull {OLLAMA_MODEL_CODE}")
        check(f"Model {OLLAMA_MODEL_DIGEST} loaded", False, fix=f"ollama pull {OLLAMA_MODEL_DIGEST}")


def check_env_vars():
    section("Environment Variables")
    vars_required = [
        ("MINIMAX_API_KEY",       "MiniMax API key",    "export MINIMAX_API_KEY='...'"),
        ("DISCORD_BOT_TOKEN",     "Discord bot token",  "export DISCORD_BOT_TOKEN='...'"),
        ("DISCORD_CHANNEL_LIVE",  "#orchestrator-live channel ID",
         "export DISCORD_CHANNEL_LIVE='...'  (right-click channel → Copy ID in Discord Dev Mode)"),
        ("DISCORD_CHANNEL_BLOCKED", "#orchestrator-blocked channel ID",
         "export DISCORD_CHANNEL_BLOCKED='...'"),
        ("DISCORD_CHANNEL_CHAT",  "#orchestrator-chat channel ID",
         "export DISCORD_CHANNEL_CHAT='...'"),
    ]
    vars_optional = [
        ("DISCORD_USER_ID",  "Discord user ID (for DMs)"),
    ]
    for var, label, fix in vars_required:
        check(f"{var} set ({label})", bool(os.environ.get(var)), fix=fix)
    for var, label in vars_optional:
        val = os.environ.get(var, "")
        if not val:
            warn(f"{var} not set ({label}) — optional but recommended")


def check_spend_cap():
    section("Spend Cap")
    print(f"  ℹ  Monthly cap: ${MINIMAX_SPEND_CAP:.0f} — verify matching limit at platform.minimax.io → Billing")
    warn(
        f"Cap is set to ${MINIMAX_SPEND_CAP:.0f}/month in config.py — "
        f"confirm you've also set it at platform.minimax.io",
        fix="platform.minimax.io → Billing → Usage Limits → set to $65"
    )


def check_repos():
    section("Project Repos")
    if not ENABLED_PROJECTS:
        check("At least one project enabled in ENABLED_PROJECTS", False,
              fix="Edit config.py → ENABLED_PROJECTS = ['lang']")
        return

    check(f"ENABLED_PROJECTS non-empty: {ENABLED_PROJECTS}", True)

    for proj in ENABLED_PROJECTS:
        repo = REPO_PATHS.get(proj)
        if not repo:
            check(f"{proj} has a REPO_PATH configured", False,
                  fix=f"Add '{proj}' to REPO_PATHS in config.py")
            continue
        exists = Path(repo).exists()
        check(f"{proj} repo exists at {repo}", exists,
              fix=f"mkdir -p {repo} && cd {repo} && git init")
        if exists:
            # Check git remote
            result = subprocess.run(
                ["git", "remote", "-v"], cwd=repo, capture_output=True, text=True,
            )
            has_remote = "origin" in result.stdout
            check(
                f"{proj} repo has git remote configured",
                has_remote,
                fix=f"cd {repo} && git remote add origin <url>",
            )


def check_database():
    section("Database")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS _validate_test (x INTEGER)")
        conn.execute("DROP TABLE _validate_test")
        conn.close()
        check(f"orchestrator.db writable at {DB_PATH}", True)
    except Exception as e:
        check(f"orchestrator.db writable at {DB_PATH}", False,
              fix=f"chmod 664 {DB_PATH}  (error: {e})")


def check_git_watcher():
    section("git_watcher")
    pid_file = Path(__file__).parent / ".git_watcher.pid"
    if pid_file.exists():
        pid_str = pid_file.read_text().strip()
        try:
            pid = int(pid_str)
            os.kill(pid, 0)   # signal 0 = just check if alive
            check(f"git_watcher.py running (pid {pid})", True)
        except ProcessLookupError:
            check(
                f"git_watcher.py running (pid {pid_str} — stale PID file)", False,
                fix="rm .git_watcher.pid && python3 git_watcher.py &"
            )
        except ValueError:
            check(f"git_watcher PID file readable", False,
                  fix="python3 git_watcher.py &")
    else:
        check(
            "git_watcher.py running (no PID file found)", False,
            fix="python3 git_watcher.py &   (or add to login items)"
        )


def check_dashboard():
    section("Dashboard Server")
    print(f"  ℹ  Dashboard auto-starts via orchestrator_main on port {DASHBOARD_PORT}")
    print(f"  ℹ  Standalone: python dashboard_server.py {DASHBOARD_PORT}")
    # Can't check if it's running without the orchestrator being up, just inform


def check_discord_connectivity():
    section("Discord API")
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        check("Discord API reachable (skipped — no token)", True)
        return
    try:
        resp = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}"},
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            check(f"Discord bot token valid — logged in as {data.get('username', '?')}", True)
        elif resp.status_code == 401:
            check("Discord bot token valid", False,
                  fix="Regenerate token at discord.com/developers → your app → Bot → Reset Token")
        else:
            check(f"Discord API reachable (HTTP {resp.status_code})", False,
                  fix=f"Check Discord status at discordstatus.com")
    except Exception as e:
        check(f"Discord API reachable", False, fix=f"Check internet connection (error: {e})")


# ── SUMMARY ───────────────────────────────────────────────────────────────────

def print_summary(show_fixes: bool):
    failures = [(label, fix) for passed, label, fix in _results if not passed and fix]

    print(f"\n{'─'*60}")
    passed_count = sum(1 for p, _, _ in _results if p)
    total        = len(_results)
    failed_count = total - passed_count

    if failed_count == 0:
        print(f"{GREEN}{BOLD}All checks passed ({passed_count}/{total}) — ready to run.{RESET}")
    else:
        print(f"{RED}{BOLD}{failed_count} check(s) failed ({passed_count}/{total} passed){RESET}")
        if show_fixes and failures:
            print(f"\n{BOLD}Fix commands:{RESET}")
            for label, fix in failures:
                print(f"\n  # {label}")
                print(f"  {fix}")

    print(f"{'─'*60}\n")
    return failed_count


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pre-flight validation for the Orchestrator.",
    )
    parser.add_argument("--fix", action="store_true",
                        help="Show fix commands for failed checks")
    args = parser.parse_args()

    print(f"\n{BOLD}Orchestrator Pre-Flight Check{RESET}")
    print("=" * 60)

    check_ollama()
    check_env_vars()
    check_spend_cap()
    check_repos()
    check_database()
    check_git_watcher()
    check_dashboard()
    check_discord_connectivity()

    failed = print_summary(show_fixes=args.fix)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
