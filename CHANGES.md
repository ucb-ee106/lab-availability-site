# Changes

## Shared Utilities Module (`lab_utils.py`) - NEW

Extracted ~250 lines of duplicated code from `app.py`, `check_notifications.py`, and `update_db.py` into a single shared module.

**What it contains:**
- Station groupings (`TURTLEBOT_STATIONS`, `UR7E_STATIONS`)
- All file/CSV path constants (single source of truth)
- Pre-compiled `DESK_REGEX` patterns for SVG desk color replacement
- `file_lock()` context manager using `fcntl.flock` for advisory file locking
- `get_current_lab_event()` with 30s TTL cache + mtime-based invalidation
- Calendar ICS parsing (`_parse_calendar()`) - consolidated from 3 separate implementations
- Helper functions: `is_lab_oh_time()`, `is_lab_section_time()`, `is_maintenance_time()`, `is_lab_active_time()`
- `get_manual_overrides()` for reading station override CSV

---

## Backend (`app.py`)

### SVG Caching
- SVG is now generated once and cached with a 5-second TTL
- Responds with `Cache-Control: public, max-age=5` and `ETag` headers
- Uses pre-compiled regex from `lab_utils` instead of recompiling 11 patterns per request

### About Page Caching
- `website_about.md` content is read once and cached in memory

### Admin User Caching
- Admin user list cached with 60s TTL + file mtime check for auto-invalidation

### File Locking on CSV Writes
- All CSV mutations (queue add/remove/reorder, overrides, claims) are wrapped in `file_lock()`
- Prevents race conditions from concurrent requests and the systemd daemons
- `add_to_queue()` does duplicate check + write under a single lock (fixes TOCTOU race)

### New Auto-Refresh Endpoint
- Added `/api/lab-data` returning JSON with lab status, availability counts, and queue data
- Frontend polls this every 10 seconds instead of requiring manual page refresh

### Imports from `lab_utils`
- Removed ~170 lines of duplicated calendar parsing, override reading, and path definitions

---

## Notification Daemon (`check_notifications.py`)

### Reduced CSV I/O
- `has_pending_claim()` and `person_has_active_claim()` now take a `claims` list as parameter instead of re-reading the CSV each call
- `main()` reads all shared state once at the start of each cycle
- Reduced from ~5 CSV reads per 10s cycle to ~2

### File Locking
- All queue and claim CSV writes use `file_lock()`

### Shared Module
- Removed ~130 lines of duplicated calendar parsing and manual overrides code
- Uses `lab_utils` for paths, station groups, and calendar helpers

---

## Station Checker (`update_db.py`)

### Parallel SSH
- Station checks now use `ThreadPoolExecutor(max_workers=11)` - all 11 stations checked concurrently
- Reduces best-case check time from ~3s (sequential) to ~0.3s (parallel)

### Shared Module
- Uses `lab_utils` for station groupings, paths, and calendar state helpers
- `ALL_STATIONS = sorted(TURTLEBOT_STATIONS | UR7E_STATIONS)` replaces hardcoded `range(1, 12)`

---

## Frontend (`static/auth.js`)

### Auto-Refresh
- `refreshLabData()` polls `/api/lab-data` every 10 seconds
- Updates status banner color, state text, availability counts, and SVG image without full page reload
- SVG refreshed via cache-buster query parameter (`?timestamp`)

### Google Sign-In
- Uses `onload` attribute on the Google script tag instead of a 100ms polling loop
- DOMContentLoaded has a simplified one-shot fallback

### Override Actions
- `setOverride()` and `clearOverride()` now call `await refreshLabData()` instead of `setTimeout(() => location.reload(), 1000)` - no more full page reload for admin overrides

---

## Templates

### `index.html` & `admin.html`
- Added `<link rel="preconnect" href="https://accounts.google.com">` for faster auth loading
- External links have `rel="noopener noreferrer"`
- Calendar iframe has a `title` attribute; removed deprecated `frameborder` and `scrolling` attributes
- Sign-in modal has `role="dialog"`, `aria-modal="true"`, `aria-labelledby`
- Close button changed from `<span>` to `<button>` with `aria-label`
- SVG and status elements have IDs for JavaScript auto-refresh targeting

### `claim.html`
- Countdown `setInterval` now stored in a variable and cleared with `clearInterval()` when timer expires
- `alert()` calls replaced with in-page error display

---

## Tests - NEW

Added 74 unit tests across 3 test files:

- **`tests/test_lab_utils.py`** (24 tests) - Station groupings, regex patterns, file locking (including concurrent thread safety), manual overrides, calendar caching
- **`tests/test_app.py`** (35 tests) - Flask routes, API endpoints, auth, queue operations, station overrides, admin page, lab state logic, SVG caching
- **`tests/test_check_notifications.py`** (15 tests) - Station state reading, previous state persistence, queue operations, claim expiry logic

Run with: `venv/bin/python -m pytest tests/ -v`

---

## TODO

(None remaining)
