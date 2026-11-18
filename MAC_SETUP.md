# Running Antarctic Sea Ice Explorer on macOS (Apple Silicon M1/M2/M3)

The project's original instructions (`Link.txt`, `docs/testing.md`) are written for Windows / PowerShell (`py -3.13`, `$env:...`, `C:\...` paths, `--single-port`).
This guide lists the steps that were actually verified on a **Mac M1 (arm64) running macOS 15**.

---

## 1. Prerequisites

| Tool | Tested version | Purpose | How to install |
|---|---|---|---|
| Homebrew | any | Installs the other tools | https://brew.sh |
| Python | **3.13** (arm64) | Backend (Reflex) | `brew install python@3.13` |
| Node.js | 22.x (22.22+ recommended) | Used by Reflex to build the frontend | `brew install node@22` or nvm |
| Git | any | Clone / push the code | `brew install git` |
| sqlite3 | bundled with macOS | (optional) inspect the database | preinstalled |

> You do **not** need to install bun manually: Reflex downloads the frontend dependencies (`.web/node_modules`) on first run.
> You do **not** need PostgreSQL: the app defaults to the SQLite file `globe_check.db` in the project root.

Confirm you are running native arm64 tools:

```bash
uname -m
```

```bash
/opt/homebrew/bin/python3.13 -c "import platform; print(platform.machine())"
```

Both commands should print `arm64`.

---

## 2. Create a virtual environment and install Python dependencies

From the project root (the folder containing `rxconfig.py`):

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
```

```bash
.venv/bin/pip install --upgrade pip
```

```bash
.venv/bin/pip install -r requirements.txt
```

This installs `reflex==0.10.0`, `xy[reflex]==0.0.9`, `sqlmodel`, `alembic`, `psycopg[binary]`, `pillow`, `faker`, etc. All of them ship arm64 wheels, so nothing needs to be compiled.

---

## 3. Initialise the database (required on first run)

`*.db` files are git-ignored, so a fresh clone has no database. Create the tables:

```bash
.venv/bin/reflex db migrate
```

This creates `globe_check.db` in the project root and applies the 3 migrations in `db_migrations/versions/`.
The message `Detected database schema changes...` can be ignored: the models map to the existing columns through column aliases, so no new migration is needed.

Check that all three migrations show `[✓]`:

```bash
.venv/bin/reflex db status
```

---

## 4. Import the bundled CSV data (recommended on first run)

The `data/` folder contains 13 CSV files. Load them into the database with the project's own script (about 10 seconds, ~1.6 million rows):

```bash
.venv/bin/python -m scripts.restore_bundled_data
```

On success the last line looks like:

```
Finished: 13 inserted files, 1,597,400 inserted rows, 0 skipped files, 0 failed files.
```

The script is idempotent: running it again prints `SKIP` for datasets that already exist.

---

## 5. Start the app

Development mode (recommended; frontend and backend on separate ports):

```bash
.venv/bin/reflex run --frontend-port 3000 --backend-port 8000
```

It is ready when you see:

```
App running at: http://localhost:3000/
Backend running at: http://0.0.0.0:8000
```

Open http://localhost:3000/ in your browser. The first start downloads frontend dependencies and takes 1–3 minutes; later starts take a few seconds.

Or use the one-shot script, which performs steps 2–5 and skips anything already done:

```bash
./start_mac.sh
```

### Stopping the app

Press **Ctrl+C** in the terminal where it is running. If that terminal is closed, find the process by port:

```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN
```

```bash
kill $(lsof -t -iTCP:3000 -sTCP:LISTEN) $(lsof -t -iTCP:8000 -sTCP:LISTEN)
```

### (Optional) Production mode, equivalent to the original Windows command

```bash
.venv/bin/reflex run --env prod --frontend-port 3028 --backend-port 3028 --single-port
```

To point at a specific database file (the equivalent of `$env:REFLEX_DB_URL` on Windows):

```bash
REFLEX_DB_URL="sqlite:///$PWD/globe_check.db" .venv/bin/reflex run --frontend-port 3000 --backend-port 8000
```

---

## 6. Run the tests (optional)

macOS equivalent of the PowerShell test command in `docs/testing.md`:

```bash
PYTEST_CURRENT_TEST=unittest-discovery .venv/bin/python -c 'import logging,sys,unittest; logging.getLogger().setLevel(logging.CRITICAL); r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover("scripts")); sys.exit(not r.wasSuccessful())'
```

Verified result: `Ran 73 tests ... OK`.

---

## 7. What was verified

- **Map Explorer**: shows the 2023-09 sea-ice-concentration layer with 82,120 valid observations rendered on the Antarctic map.
- **Compare** and **Data** pages load correctly; the Data page lists 10 active CSV layers.
- The browser console shows a single `Point data request failed (404)`. It comes from an initial request to the empty path `/_upload/` while the page loads; the real data request right after returns 200, so it has no effect.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Warning: Your version (22.21.1) of Node.js is out of date` | Only a warning; the app runs fine. Upgrade to Node 22.22+ to silence it. |
| `Warning: Your version (0.10.0) of reflex is out of date` | Do not upgrade; the project is pinned to 0.10.0. |
| `Address already in use` | A port is taken: stop the old process with the `lsof` / `kill` commands in section 5, or use different ports. |
| Map stays on "Loading" / no layers | Steps 3 and 4 were skipped, so the database is empty. |
| `no such table: uploaded_dataset` | `reflex db migrate` was not run. |
| Start completely fresh | Delete `globe_check.db`, `.web/`, `.states/` and `uploaded_files/`, then start again from step 3. |

---

## 9. Folder overview

- `antarctic_sea_ice_dashboard/`: the app that actually runs (`app_name` in `rxconfig.py`).
- `app/`: earlier retained implementation, still used by the tests.
- `data/`: bundled CSV data.
- `db_migrations/`: Alembic migration scripts.
- `uploaded_files/`: map-point JSON cache generated at runtime (git-ignored; regenerated automatically).
- `.web/`, `.states/`, `.venv/`, `globe_check.db`: generated locally, not committed to Git.
