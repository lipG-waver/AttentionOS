# Attention v5.0 — Refactoring Summary

## Overview

The project has been restructured from a flat file layout (all `.py` files in root) into a proper Python package with three logical sub-packages. No business logic was changed.

## New Directory Structure

```
Attention_v5_refactored/
├── run.py                          # Entry point (unchanged role)
├── requirements.txt
├── README.md
├── start_desktop_monitor.sh
├── .env
├── .gitignore
│
├── attention/                      # Main package
│   ├── __init__.py
│   ├── config.py                   # Configuration (BASE_DIR adjusted for new depth)
│   ├── utils.py                    # Shared utilities
│   ├── main.py                     # CLI monitoring loop / AttentionAgent
│   │
│   ├── core/                       # Core infrastructure
│   │   ├── __init__.py
│   │   ├── llm_client.py           # LLM API client
│   │   ├── agents.py               # Agent prompt templates & calls
│   │   ├── database.py             # Work-log JSON database
│   │   ├── screenshot.py           # Screen capture
│   │   ├── analyzer.py             # Vision-model screen analysis
│   │   ├── activity_monitor.py     # Keyboard/mouse activity tracking
│   │   ├── state_fusion.py         # Multi-signal state fusion
│   │   ├── speech_recognition.py   # SenseVoice speech recognition
│   │   └── autostart_manager.py    # OS auto-start registration
│   │
│   ├── features/                   # Feature modules
│   │   ├── __init__.py
│   │   ├── pomodoro.py             # Pomodoro timer
│   │   ├── break_reminder.py       # Break reminder dialogs
│   │   ├── recovery_reminder.py    # Recovery tracking
│   │   ├── hourly_checkin.py       # Hourly check-in dialogs & summaries
│   │   ├── daily_briefing.py       # Morning briefing generation
│   │   ├── daily_report.py         # End-of-day report
│   │   ├── weekly_insight.py       # Weekly insight analysis
│   │   ├── todo_manager.py         # Todo list management
│   │   ├── work_start_tracker.py   # Work-start time tracking
│   │   └── app_database.py         # App/website categorization DB
│   │
│   └── ui/                         # User interface
│       ├── __init__.py
│       ├── web_server.py           # FastAPI web dashboard
│       ├── tray_app.py             # System tray icon
│       ├── desktop_overlay.py      # Desktop pet overlay
│       ├── pomodoro_overlay.py     # Pomodoro floating window manager
│       ├── pomodoro_overlay_process.py  # Pomodoro overlay subprocess
│       └── break_overlay_process.py     # Break overlay subprocess
│
├── tests/
│   ├── __init__.py
│   ├── test_hourly_checkin.py
│   ├── test_todo_manager.py
│   ├── test_pomodoro_break.py
│   └── test_modelscope_api.py
│
├── static/                         # Web dashboard assets
├── data/                           # Runtime data (JSON logs, check-ins)
└── docs/                           # Documentation
```

## Grouping Rationale

| Sub-package | Purpose | Files |
|---|---|---|
| `attention.core` | Infrastructure that other modules depend on: LLM client, database, screen capture, analysis, activity monitoring, state fusion | 9 modules |
| `attention.features` | Self-contained feature modules, each implementing a specific user-facing capability | 10 modules |
| `attention.ui` | Anything that renders UI: web server, system tray, desktop overlays, subprocess GUI processes | 6 modules |

This three-way split mirrors the natural dependency flow: **core → features → ui**, with minimal cross-cutting.

## What Changed

### Import paths

Every local `from X import Y` was rewritten to absolute form. For example:

| Before (flat) | After (packaged) |
|---|---|
| `from config import Config` | `from attention.config import Config` |
| `from database import get_database` | `from attention.core.database import get_database` |
| `from pomodoro import get_pomodoro` | `from attention.features.pomodoro import get_pomodoro` |
| `from desktop_overlay import get_desktop_overlay` | `from attention.ui.desktop_overlay import get_desktop_overlay` |

Both top-level and lazy/dynamic imports inside functions were updated.

### config.py — `BASE_DIR` fix

`config.py` moved from the project root into `attention/`, so `Path(__file__).parent` no longer points to the project root. Fixed to:

```python
BASE_DIR = Path(__file__).resolve().parent.parent  # project root
```

All downstream paths (`DATA_DIR`, `SCREENSHOT_DIR`, etc.) derive from `BASE_DIR` and remain correct.

### web_server.py — `static/` path fix

The web server referenced `Path(__file__).parent / "static"` to find the static assets directory. Since `web_server.py` moved to `attention/ui/`, this was changed to use `Config.BASE_DIR / "static"` instead.

### Test files

Tests were moved to `tests/` and updated:
- `sys.modules["config"]` → `sys.modules["attention.config"]`
- `import todo_manager as tm` → `import attention.features.todo_manager as tm`
- Added `sys.path.insert(0, ...)` pointing to project root

## What Did NOT Change

- All business logic, algorithms, class structures, and function signatures
- The `run.py` entry point remains at the project root with the same CLI interface
- Subprocess scripts (`break_overlay_process.py`, `pomodoro_overlay_process.py`) remain co-located with their launchers in `attention/ui/`
- `static/`, `data/`, `docs/` directories remain at the project root
- `requirements.txt`, `.env`, `.gitignore`, shell scripts unchanged
- All `if __name__ == "__main__"` blocks preserved

## Running the Application

Same as before — from the project root:

```bash
python run.py                # Full mode (tray + web + monitoring)
python run.py --no-tray      # Debug mode (no tray icon)
python run.py --web-only     # Web dashboard only
python run.py --cli          # CLI monitoring only
```

## Running Tests

```bash
# From project root
python -m pytest tests/ -v

# Or individual tests
python -m unittest tests.test_todo_manager -v
python -m unittest tests.test_hourly_checkin -v
```
