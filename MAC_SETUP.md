# Running Antarctic Sea Ice Explorer on macOS (Apple Silicon M1/M2/M3)

The project's original instructions (`Link.txt`, `docs/testing.md`) are written for Windows / PowerShell (`py -3.13`, `$env:...`, `C:\...` paths, `--single-port`).
This guide lists the steps that were actually verified on a **Mac M1 (arm64) running macOS 15**, using **PostgreSQL** as the database.

---

## 1. Prerequisites

| Tool | Tested version | Purpose | How to install |
|---|---|---|---|
| Homebrew | any | Installs the other tools | https://brew.sh |
| Python | **3.13** (arm64) | Backend (Reflex) | `brew install python@3.13` |
| Node.js | 22.x (22.22+ recommended) | Used by Reflex to build the frontend | `brew install node@22` or nvm |
| PostgreSQL | **18** (14+ should work) | Database | `brew install postgresql@18` |
| Git | any | Clone / push the code | `brew install git` |

> You do **not** need to install bun manually: Reflex downloads the frontend dependencies (`.web/node_modules`) on first run.

Confirm you are running native arm64 tools:

```bash
uname -m
```

```bash
/opt/homebrew/bin/python3.13 -c "import platform; print(platform.machine())"
```

Both commands should print `arm64`.

---

## 2. Start PostgreSQL and create the database

Start the server (it also starts automatically at login from now on):

```bash
brew services start postgresql@18
```

Create the database the app uses by default:

```bash
/opt/homebrew/opt/postgresql@18/bin/createdb antarctic_sea_ice
```

Check that you can connect:

```bash
/opt/homebrew/opt/postgresql@18/bin/psql -d antarctic_sea_ice -c "select version();"
```

A Homebrew install creates a superuser named after your macOS account with no password, so no credentials are needed. The app connects with:

```
postgresql+psycopg://localhost:5432/antarctic_sea_ice
```

To use a different server, user or password, set `REFLEX_DB_URL` before running any `reflex` command (see section 7).

---

## 3. Create a virtual environment and install Python dependencies

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

This installs `reflex==0.10.0`, `xy[reflex]==0.0.9`, `sqlmodel`, `alembic`, `psycopg[binary]` (the PostgreSQL driver), `pillow`, `faker`, etc. All of them ship arm64 wheels, so nothing needs to be compiled.

---

## 4. Create the tables (required on first run)

```bash
.venv/bin/reflex db migrate
```

This applies the 3 Alembic migrations in `db_migrations/versions/` to the `antarctic_sea_ice` database.
The message `Detected database schema changes...` can be ignored: the models map to the existing columns through column aliases, so no new migration is needed.

Check that all three migrations show `[✓]` and that the URL is the PostgreSQL one:

```bash
.venv/bin/reflex db status
```

---

## 5. Import the bundled CSV data (recommended on first run)

The `data/` folder contains 13 CSV files. Load them into the database with the project's own script (about 30 seconds, ~1.6 million rows):

```bash
.venv/bin/python -m scripts.restore_bundled_data
```

On success the last line looks like:

```
Finished: 13 inserted files, 1,597,400 inserted rows, 0 skipped files, 0 failed files.
```

The script is idempotent: running it again prints `SKIP` for datasets that already exist.

---

## 6. Start the app

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

Or use the one-shot script, which performs steps 2–6 and skips anything already done:

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

To also stop PostgreSQL:

```bash
brew services stop postgresql@18
```

### (Optional) Production mode, equivalent to the original Windows command

```bash
.venv/bin/reflex run --env prod --frontend-port 3028 --backend-port 3028 --single-port
```

---

## 7. Choosing a different database

The URL comes from `REFLEX_DB_URL` (see `rxconfig.py`); the default is the local PostgreSQL database above. Examples:

Another PostgreSQL server or user:

```bash
REFLEX_DB_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/antarctic_sea_ice" .venv/bin/reflex run --frontend-port 3000 --backend-port 8000
```

SQLite fallback with no database server (file created in the project root):

```bash
REFLEX_DB_URL="sqlite:///globe_check.db" .venv/bin/reflex run --frontend-port 3000 --backend-port 8000
```

Set the same variable for `reflex db migrate` and `scripts.restore_bundled_data` so they target the same database.

---

## 8. Run the tests (optional)

macOS equivalent of the PowerShell test command in `docs/testing.md`:

```bash
PYTEST_CURRENT_TEST=unittest-discovery .venv/bin/python -c 'import logging,sys,unittest; logging.getLogger().setLevel(logging.CRITICAL); r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover("scripts")); sys.exit(not r.wasSuccessful())'
```

Verified result: `Ran 73 tests ... OK`.

---

## 9. What was verified

- `reflex db status` shows all 3 migrations applied on `postgresql+psycopg://localhost:5432/antarctic_sea_ice`.
- The import created 13 datasets and 1,597,400 rows in PostgreSQL.
- **Map Explorer**: shows the 2023-09 sea-ice-concentration layer with 82,120 valid observations on the Antarctic globe.
- **Compare** and **Model Insights** globes render without the square grid frame or colour spilling onto land; the **Data** page lists the imported CSV layers.
- The browser console shows a single `Point data request failed (404)` while the page loads (a request to the empty path `/_upload/`); the real data request right after returns 200, so it has no effect.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `connection refused` / `could not connect to server` on port 5432 | PostgreSQL isn't running: `brew services start postgresql@18`. |
| `database "antarctic_sea_ice" does not exist` | Run the `createdb` command in section 2. |
| `role "..." does not exist` / password errors | Your server isn't the default Homebrew setup: put the right user and password in `REFLEX_DB_URL` (section 7). |
| `no such table` / `relation "uploaded_dataset" does not exist` | `reflex db migrate` was not run against this database. |
| Map stays on "Loading" / no layers | Section 5 was skipped, so the database is empty. |
| `Warning: Your version (22.21.1) of Node.js is out of date` | Only a warning; the app runs fine. Upgrade to Node 22.22+ to silence it. |
| `Warning: Your version (0.10.0) of reflex is out of date` | Do not upgrade; the project is pinned to 0.10.0. |
| `Address already in use` | A port is taken: stop the old process with the `lsof` / `kill` commands in section 6, or use different ports. |
| Start completely fresh | `dropdb antarctic_sea_ice && createdb antarctic_sea_ice`, delete `.web/`, `.states/` and `uploaded_files/`, then start again from section 4. |

---

## 11. Folder overview

- `antarctic_sea_ice_dashboard/`: the app that actually runs (`app_name` in `rxconfig.py`).
- `app/`: earlier retained implementation, still used by the tests.
- `data/`: bundled CSV data.
- `db_migrations/`: Alembic migration scripts.
- `uploaded_files/`: map-point JSON cache generated at runtime (git-ignored; regenerated automatically).
- `.web/`, `.states/`, `.venv/`, `*.db`: generated locally, not committed to Git.
