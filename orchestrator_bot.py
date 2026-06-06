"""
orchestrator_bot.py — MOVED to agents/orchestrator_bot.py

This file forwards to the canonical location for backward compatibility.
Update any aliases or launch scripts to point to agents/orchestrator_bot.py.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "agents"))
from orchestrator_bot import *  # noqa: F401,F403

if __name__ == "__main__":
    import importlib, runpy
    runpy.run_path(str(Path(__file__).parent / "agents" / "orchestrator_bot.py"), run_name="__main__")
