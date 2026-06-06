#!/usr/bin/env python3
"""
o.py — MOVED to agents/o.py

This file forwards to the canonical location for backward compatibility.
Update your zshrc alias:
  alias o="python3 ~/projects/Orchestrator/agents/o.py"
"""
import sys
from pathlib import Path

agents_dir = Path(__file__).parent / "agents"
sys.path.insert(0, str(agents_dir))
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    import runpy
    sys.argv[0] = str(agents_dir / "o.py")
    runpy.run_path(str(agents_dir / "o.py"), run_name="__main__")
