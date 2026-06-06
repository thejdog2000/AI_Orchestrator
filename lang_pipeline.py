"""
lang_pipeline.py — MOVED to pipeline/lang_pipeline.py

Backward-compatible shim. The canonical source is pipeline/lang_pipeline.py.

Manual run (preferred):
  python pipeline/lang_pipeline.py
  python pipeline/lang_pipeline.py --status
"""
import sys
from pathlib import Path

# Ensure orchestrator root is on sys.path
_root = str(Path(__file__).parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from pipeline.lang_pipeline import *  # noqa: F401,F403

if __name__ == "__main__":
    import runpy
    runpy.run_path(str(Path(__file__).parent / "pipeline" / "lang_pipeline.py"),
                   run_name="__main__")
