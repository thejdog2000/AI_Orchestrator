#!/usr/bin/env bash
# setup_personas_env.sh
# Wires up shared Orchestrator personas for Claude Code, Codex, and the
# MiniMax council pipeline. Run once from any directory.
#
# Usage:
#   bash ~/projects/Orchestrator/scripts/setup_personas_env.sh

set -euo pipefail

ORCHESTRATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PERSONAS_DIR="$ORCHESTRATOR_DIR/agents/personas/domain"
ZSHRC="$HOME/.zshrc"
GLOBAL_CLAUDE_MD="$HOME/.claude/CLAUDE.md"

# ── 1. ENVIRONMENT VARIABLE ────────────────────────────────────────────────────

ENV_LINE="export ORCHESTRATOR_PERSONAS_DIR=\"$PERSONAS_DIR\""
ALIAS_LINE='alias o="python3 ~/projects/Orchestrator/agents/o.py"'

if grep -qF "ORCHESTRATOR_PERSONAS_DIR" "$ZSHRC" 2>/dev/null; then
  echo "✓ ORCHESTRATOR_PERSONAS_DIR already in $ZSHRC"
else
  echo "" >> "$ZSHRC"
  echo "# Orchestrator shared personas (used by task_generator, Claude Code, Codex)" >> "$ZSHRC"
  echo "$ENV_LINE" >> "$ZSHRC"
  echo "✓ Added ORCHESTRATOR_PERSONAS_DIR to $ZSHRC"
fi

if grep -qF "alias o=" "$ZSHRC" 2>/dev/null; then
  echo "✓ alias o already in $ZSHRC"
else
  echo "$ALIAS_LINE" >> "$ZSHRC"
  echo "✓ Added alias o to $ZSHRC"
fi

# ── 2. GLOBAL CLAUDE.MD (~/.claude/CLAUDE.md) ─────────────────────────────────
# Claude Code loads this file in every project automatically.

mkdir -p "$(dirname "$GLOBAL_CLAUDE_MD")"

CLAUDE_BLOCK="# Orchestrator Personas

Expert personas for all projects live in:
  $PERSONAS_DIR

Available domain personas (inject as system prompt for the relevant task type):
$(ls "$PERSONAS_DIR"/*.md 2>/dev/null | xargs -I{} basename {} .md | sed 's/^/  - /')

Available review personas:
$(ls "$ORCHESTRATOR_DIR/agents/personas/review"/*.md 2>/dev/null | xargs -I{} basename {} .md | sed 's/^/  - /')

When working on a task, select the persona whose expertise best matches the work
(e.g. engineering_architect for system design, qa_tester for test coverage,
speech_linguist for language-app features) and load its .md file as context."

if [ ! -f "$GLOBAL_CLAUDE_MD" ]; then
  echo "$CLAUDE_BLOCK" > "$GLOBAL_CLAUDE_MD"
  echo "✓ Created $GLOBAL_CLAUDE_MD"
elif grep -qF "Orchestrator Personas" "$GLOBAL_CLAUDE_MD"; then
  echo "✓ Orchestrator personas block already in $GLOBAL_CLAUDE_MD"
else
  echo "" >> "$GLOBAL_CLAUDE_MD"
  echo "$CLAUDE_BLOCK" >> "$GLOBAL_CLAUDE_MD"
  echo "✓ Appended personas block to $GLOBAL_CLAUDE_MD"
fi

# ── 3. CODEX CONFIG ────────────────────────────────────────────────────────────
# Codex reads ~/.codex/instructions.md as a global system prompt.
# If you use Codex+, uncomment and run this block.

CODEX_INSTRUCTIONS="$HOME/.codex/instructions.md"
# mkdir -p "$(dirname "$CODEX_INSTRUCTIONS")"
# cat >> "$CODEX_INSTRUCTIONS" << EOF
#
# ## Orchestrator Personas
# Expert persona files are at: $PERSONAS_DIR
# Load the relevant .md as system context before starting work on a task.
# EOF
# echo "✓ Added personas reference to $CODEX_INSTRUCTIONS"
echo "  (Codex block is commented out — uncomment in script if you use Codex+)"

# ── DONE ───────────────────────────────────────────────────────────────────────

echo ""
echo "Done. Run: source ~/.zshrc"
echo ""
echo "Verify:"
echo "  echo \$ORCHESTRATOR_PERSONAS_DIR"
echo "  python3 -c \"import os,sys; sys.path.insert(0,'$ORCHESTRATOR_DIR'); import task_generator; print(task_generator.PERSONAS_DIR)\""
