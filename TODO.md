# Orchestrator — Remaining TODOs

Priority: 🔴 do before first overnight run | 🟡 do before multi-project run | ⚪ polish / low risk

---

## 🔴 Before First Overnight Run

**Verify MiniMax direct API model name**
`MINIMAX_MODEL = "minimax-m3"` in orchestrator_main.py is an assumed value.
Confirm the actual chat/completions model string at platform.minimax.io before first run.
Wrong model name = silent 404 on every task execution.

**Set API key and test Ollama connectivity**
```bash
export MINIMAX_API_KEY="..."
curl http://localhost:11434/api/tags   # verify Ollama is running
ollama list                            # verify qwen3-coder:30b and qwen3:14b present
```

**executor.py `configure()` must be called before any task runs**
`executor.configure(CFG)` is called in orchestrator_main.py on import, but if
any module imports executor directly (e.g. tests, scripts) without going through
main, `_config` will be empty and all `_cfg()` calls will KeyError.
Consider a guard: raise a clear error if `_config` is empty when `_cfg()` is called.

---

## 🟡 Before Multi-Project Run

**Wire lang_pipeline into orchestrator_main scheduler**
`run_lang_nightly` is imported and scheduled — but `lang_pipeline.py` uses its own
hardcoded `OLLAMA_BASE`, `MINIMAX_API_BASE`, `MINIMAX_MODEL`, `OLLAMA_MODEL_CODE`.
These should come from the shared CFG dict (via `configure()`) to avoid drift.

**task_generator.py `_load_context` REPO_PATHS mapping**
`_load_context` builds paths as `BASE_DIR.parent / v` where `v` is a string from
`REPO_PATHS`. But `lang` and `gamma` are under `~/Documents/claude/projects/`, not
`~/projects/`. The parent join logic will produce wrong paths for those two.
Fix: pass the full `REPO_PATHS` dict (with absolute Paths) from CFG.

**Add `python approve.py` to morning routine docs**
`approve.py` is built but not mentioned in `ORCHESTRATOR_CONTEXT.md`'s daily rhythm
section. Jacob needs to know to run it each morning alongside reading the digest.

---

## ⚪ Polish / Low Risk

**DB backup requires `sqlite3` CLI on PATH**
`backup_db()` uses `subprocess.run(["sqlite3", ...])`. If sqlite3 CLI isn't installed
(it usually is on macOS), backup silently fails. Add a Python-native fallback using
`sqlite3` stdlib: `conn.iterdump()`.

**approve.py `--open` uses $PAGER fallback to `less`**
Works on macOS. Consider also trying `delta` or `diff-so-fancy` if installed,
for syntax-highlighted diff viewing.

**git_watcher.py not added to login items yet**
Currently requires manual `python3 git_watcher.py &` each session.
Add to macOS login items or launchd plist for persistence across reboots.
