# Avala Tracker Pro

A local productivity analytics platform for tracking annotation work on `avala.ai`.

## Features
- Chrome extension task detection (`datasets` / `work_batches` URLs)
- Local FastAPI backend with SQLite persistence
- Activity and idle tracking (mouse/keyboard + extension pings)
- Frame logging and session tracking
- Analytics engine for efficiency and distributions
- Payments and earnings sync support
- Streamlit dashboard with Plotly graphs
- AI-assisted bounding-box suggestion overlay for Avala.ai task pages
- Daily/weekly report scripts
- Earnings estimation and productivity heatmap

## Project Structure

```text
avala-tracker-pro/
  backend/
    main.py
    routes.py
    models.py
    database.py
    schemas.py
    session_manager.py
    audit_service.py  # NEW: Payment audit and reconciliation service
  tracker/
    activity_monitor.py
    idle_detector.py
    frame_tracker.py
  analytics/
    metrics.py
    productivity.py
    predictions.py
  dashboard/
    dashboard.py
    charts.py
    audit_dashboard.py  # NEW: Audit dashboard visualization
  extension/
    manifest.json
    background.js
    content.js
    task_detector.js
  data/
    avala.db
  config/
    settings.json
  scripts/
    daily_report.py
    weekly_summary.py
    repair_task_counts.py
```

## Setup

1. Create venv and install deps:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2. Start backend:

```bash
uvicorn backend.main:app --reload
```

3. Start dashboard:

```bash
streamlit run dashboard/dashboard.py
```

## Chrome Extension Install

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the `extension/` folder

## API Endpoints
- `GET /health`
- `POST /task/start`
- `POST /task/update`
- `POST /task/end`
- `POST /activity/ping`
- `POST /frame/log`
- `POST /contributions/sync`
- `POST /extension/heartbeat`
- `POST /payments/add-batch`
- `POST /payments/add-history`
- `POST /payments/debug`
- `POST /payments/sync`
- `GET /payments/summary`
- `GET /payments/batches`
- `POST /vision/analyze` - **NEW**: Analyze current task frame and suggest bounding-box improvements
- `POST /payments/detect-duplicates` - **NEW**: Detect duplicate payments
- `GET /payments/duplicates/pending` - **NEW**: Get pending duplicates
- `POST /payments/reconcile` - **NEW**: Reconcile duplicate payments
- `POST /payments/audit-log` - **NEW**: Query audit trail
- `GET /payments/audit-stats` - **NEW**: Get audit statistics
- `GET /payments/flagged` - **NEW**: Get flagged payments
- `GET /analytics/overview`
- `GET /analytics/performance`
- `GET /analytics/today`

## Reports

```bash
python scripts/daily_report.py
python scripts/weekly_summary.py
python scripts/repair_task_counts.py
python scripts/cleanup_unknown_entries.py
python scripts/normalize_camera_names.py
```

## Security Notes
- Runs fully local
- Does not automate annotation actions
- Does not alter Avala.ai behavior
- Collects productivity analytics only
