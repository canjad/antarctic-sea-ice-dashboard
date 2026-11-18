# Antarctic Sea Ice Explorer

An interactive research dashboard for exploring Antarctic sea ice, sea-surface
temperature and wind observations on a south-polar globe, comparing snapshots
between months, and checking prediction output against recorded sea ice
concentration.

Built with [Reflex](https://reflex.dev) (Python full-stack web framework),
MapLibre GL for the globe, and PostgreSQL for storage.

> Prototype for research and teaching. Colours show uploaded CSV observations;
> they are not validated science.

---

## Features

| Page | What it does |
|---|---|
| **Map Explorer** | Draws the selected Year/Month snapshot on a south-polar globe. Choose the measurement (sea ice concentration, sea-surface temperature, wind), toggle CSV layers, click any point to inspect its value and coordinates. |
| **Compare** | Picks two snapshots and maps the per-cell difference (second minus first) on the globe, with summary means for each snapshot. |
| **Model Insights** | Matches a Prediction dataset with recorded sea ice concentration for the same Year/Month and reports accuracy (100 % minus mean absolute error). |
| **Data** | Upload UTF-8 CSVs (up to 400,000 rows, 10 columns, 20 MB), map their columns to latitude/longitude/measurements, set the snapshot date, and activate them as globe layers. |

Thirteen sample CSVs (NSIDC 25 km polar grid, 2004–2023) are bundled in
[`data/`](data/) and can be imported with one command.

---

## Tech stack

- **Python 3.13**, **Reflex 0.10.0** (backend + generated React frontend)
- **PostgreSQL** (default) via SQLAlchemy / SQLModel and `psycopg`; Alembic migrations in [`db_migrations/`](db_migrations/)
- **MapLibre GL 5** globe projection; observations are blended in the browser into one smooth colour surface drawn beneath the map labels ([`assets/antarctic_globe.js`](assets/antarctic_globe.js))
- Tailwind CSS v4 (Reflex plugin)

---

## Quick start (macOS / Linux)

Prerequisites: Python 3.13, Node.js 22, and a running PostgreSQL server
(on macOS: `brew install python@3.13 node@22 postgresql@18`).

1. Start PostgreSQL and create the database:

   ```bash
   brew services start postgresql@18
   ```

   ```bash
   createdb antarctic_sea_ice
   ```

2. Create a virtual environment and install dependencies:

   ```bash
   python3.13 -m venv .venv
   ```

   ```bash
   .venv/bin/pip install -r requirements.txt
   ```

3. Create the tables and import the bundled sample data:

   ```bash
   .venv/bin/reflex db migrate
   ```

   ```bash
   .venv/bin/python -m scripts.restore_bundled_data
   ```

4. Run the app and open http://localhost:3000/:

   ```bash
   .venv/bin/reflex run --frontend-port 3000 --backend-port 8000
   ```

On macOS, [`start_mac.sh`](start_mac.sh) performs steps 1–4 in one go.
For a detailed Apple Silicon walkthrough, troubleshooting, and how to stop the
app, see **[MAC_SETUP.md](MAC_SETUP.md)**.

### Windows (PowerShell)

Install Python 3.13, Node.js 22 and PostgreSQL, create the
`antarctic_sea_ice` database, then from the project folder:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\reflex db migrate
.venv\Scripts\python -m scripts.restore_bundled_data
.venv\Scripts\reflex run --frontend-port 3000 --backend-port 8000
```

---

## Database configuration

The connection string is read from the `REFLEX_DB_URL` environment variable
(see [`rxconfig.py`](rxconfig.py)). Without it, the app uses:

```
postgresql+psycopg://localhost:5432/antarctic_sea_ice
```

which connects as your OS user with no password — the default for a Homebrew
PostgreSQL install. For any other server, set the variable, for example:

```bash
export REFLEX_DB_URL="postgresql+psycopg://USER:PASSWORD@HOST:5432/antarctic_sea_ice"
```

SQLite still works as a no-install fallback:

```bash
export REFLEX_DB_URL="sqlite:///globe_check.db"
```

`scripts/restore_bundled_data.py` is idempotent: datasets that already exist
are skipped, so it is safe to run again.

---

## Tests

The regression suite uses the standard-library `unittest` runner
(details in [`docs/testing.md`](docs/testing.md)):

```bash
PYTEST_CURRENT_TEST=unittest-discovery .venv/bin/python -c 'import logging,sys,unittest; logging.getLogger().setLevel(logging.CRITICAL); r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover("scripts")); sys.exit(not r.wasSuccessful())'
```

Expected result: `Ran 73 tests ... OK`.

---

## Project structure

```
antarctic_sea_ice_dashboard/   Reflex app that runs (pages, components, states, models)
app/                           Earlier retained implementation, still covered by tests
assets/                        Static files, incl. the globe renderer antarctic_globe.js
data/                          Bundled sample CSVs (NSIDC polar grid)
db_migrations/                 Alembic migrations
docs/                          Design, testing and feature notes
scripts/                       Data import script and unit tests
rxconfig.py                    Reflex config (app name, database URL, plugins)
requirements.txt               Python dependencies
MAC_SETUP.md                   Step-by-step macOS (Apple Silicon) guide
start_mac.sh                   One-shot setup + run script for macOS
```

Generated locally and not committed: `.venv/`, `.web/`, `.states/`,
`uploaded_files/` (globe point cache, rebuilt automatically), `*.db`.
