#!/usr/bin/env python3
"""
o.py — Orchestrator CLI alias.

Same intent parsing as #orchestrator-chat, usable from terminal.

Setup (add to ~/.zshrc):
  alias o="python3 ~/projects/Orchestrator/agents/o.py"

Usage:
  o "what happened overnight"
  o "approve all lang"
  o "how much have we spent"
  o status
  o "what's queued for tonight"
  o "show me blocked items"
  o help
"""

import sys
import logging
from pathlib import Path

# Silence noisy loggers when running interactively
logging.disable(logging.WARNING)

BASE_DIR     = Path(__file__).parent   # agents/
ORCHESTRATOR = BASE_DIR.parent         # Orchestrator/
sys.path.insert(0, str(ORCHESTRATOR))

from agents.commands import _parse_intent, _dispatch


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: o <command or question>")
        print('Examples: o status  |  o "approve all lang"  |  o help')
        sys.exit(0)

    message = " ".join(sys.argv[1:])

    # Re-enable warnings for Ollama errors during CLI use
    logging.disable(logging.NOTSET)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    intent   = _parse_intent(message)
    response = _dispatch(intent, message)
    print(response)


if __name__ == "__main__":
    main()
