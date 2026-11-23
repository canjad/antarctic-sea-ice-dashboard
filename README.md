# Antarctic Sea Ice Dashboard



## 1. Local Production Mode

To run the compiled frontend and backend together:

```bash
python3 scripts/validate.py reflex run \
  --env prod \
  --single-port \
  --frontend-port 3039 \
  --backend-port 3039 \
  --backend-host 127.0.0.1
```

This runs locally and does not deploy the application.

---

## 2. Stop the Application

### 2.1 Stop the Development Server

Press **Ctrl+C** in the application terminal.

### 2.2 Stop PostgreSQL

```bash
/opt/homebrew/opt/postgresql@18/bin/pg_ctl \
  -D "$PWD/.scratch/integration-validation/postgres" stop
```

> Stopping the services preserves saved datasets and products.

---

## 3  Set Up a New Local Environment

Use this section for a new checkout.

> **Note:** Skip database initialisation if the existing local cluster is already present.

### 4 Create the Python Virtual Environment

```bash
python3 -m venv .scratch/integration-validation/venv
```

Install the required dependencies:

```bash
.scratch/integration-validation/venv/bin/python -m pip install \
  --cache-dir .scratch/integration-validation/pip-cache \
  -r requirements-test.lock
```

### 4.2 Initialise PostgreSQL

Create a dedicated local PostgreSQL cluster:

```bash
/opt/homebrew/opt/postgresql@18/bin/initdb \
  -D .scratch/integration-validation/postgres \
  -A trust \
  -U validation \
  --locale=C \
  --encoding=UTF8
```

### 4.3 Start PostgreSQL

```bash
/opt/homebrew/opt/postgresql@18/bin/pg_ctl \
  -D "$PWD/.scratch/integration-validation/postgres" \
  -l "$PWD/.scratch/integration-validation/postgres.log" \
  -o "-h 127.0.0.1 -p 55439 -k /tmp" start
```

### 4.4 Create the Application Database

```bash
/opt/homebrew/opt/postgresql@18/bin/createdb \
  -h 127.0.0.1 \
  -p 55439 \
  -U validation \
  validation
```

### 4.5 Apply Database Migrations

```bash
python3 scripts/validate.py alembic upgrade head
```

> **Security Note:** The trust-authenticated PostgreSQL cluster is intended for isolated local use only.

---

## 5. Configuration and Storage

The local runner configures the database, scientific resources, and storage before importing the application.

### 5.1 Environment Variables

| Setting | Purpose |
|---------|---------|
| `REFLEX_DB_URL` | Application database connection |
| `REFLEX_MIGRATION_DB_URL` | Database connection used by migrations |
| `REFLEX_MIGRATION_MODULE` | Application package containing the models |
| `SEA_ICE_PRODUCT_STORAGE_ROOT` | Managed immutable product storage |
| `SEA_ICE_REFERENCE_GRID_CSV` | Read-only reference grid |
| `SEA_ICE_CHECKING_DATA_ROOT` | Read-only historical wind resources |

The supplied runner deliberately uses the dedicated `validation` database on loopback port `55439`.

### 5.2 Local Storage

The following resources are stored under:

```text
.scratch/integration-validation/
```

This directory contains:

- Application data
- Published products
- Frontend build
- Browser installation
- Session cache
- Validation evidence

### 5.3 Required Workspace Structure

The scientific resource paths expect the following workspace layout:

```text
sea ice /
├── checking data/
│   └── 10m v wind speed/
└── UG-S2-12/
    └── antarctic_sea_ice_dashboard(v1)/
```

> **Important:** Missing scientific resources must produce unavailable or blocked results. They must not be replaced with synthetic observations.

---

## 6. CSV Workflow

### 6.1 Import and Validate CSV Data

Follow these steps to import, validate, and publish scientific datasets:

1. Open **Data**.
2. Choose a CSV file and select **Import CSV**.
3. Review the saved records and declare known scientific metadata.
4. Run a quality check on the working revision.
5. Inspect findings and use **Locate and edit record** to correct an affected record.
6. Save changes and explicitly run another check.
7. Publish the current revision when its complete scientific check passes.
8. Select the published product in **Map Explorer**, **Compare**, or **Overview**.

### 6.2 CSV Import Limits

| Constraint | Limit |
|------------|-------|
| File size | 5 MiB |
| Data records | 100,000 |
| Columns | 10 |
| Characters per header or cell | 32,768 |
| Preview page size | 25 records |

### 6.3 Supported CSV Formats

The application supports:

- UTF-8 encoded CSV files
- UTF-8 BOM encoded CSV files
- Preservation of original header order
- Preservation of original cell strings
- Importing header-only CSV files

> **Note:** Header-only files can be imported, but empty datasets cannot pass scientific validation.

### 6.4 Dataset Revision Rules

Content and scientific metadata changes advance the data revision.

The following actions do not advance the revision:

- Changing only the display name
- Saving without making changes

### 6.5 Published Products

Published products remain independent of later edits or deletion of their working datasets.

This ensures that previously published scientific results remain available and unchanged.

---


## 7. Build the Frontend

To build and export the frontend:

```bash
python3 scripts/validate.py reflex export \
  --frontend-only \
  --no-zip
```

The validation runner places generated frontend files under its isolated web directory.

---

## 8. Quick Start

For an existing local environment, follow these steps.

### Step 1: Navigate to the Project

```bash
cd "/Users/canai/code/R/sea ice /UG-S2-12/antarctic_sea_ice_dashboard(v1)"
```

### Step 2: Start PostgreSQL

```bash
/opt/homebrew/opt/postgresql@18/bin/pg_ctl \
  -D "$PWD/.scratch/integration-validation/postgres" \
  -l "$PWD/.scratch/integration-validation/postgres.log" \
  -o "-h 127.0.0.1 -p 55439 -k /tmp" start
```

### Step 3: Start the Application

```bash
python3 scripts/validate.py reflex run \
  --frontend-port 3039 \
  --backend-port 8039 \
  --backend-host 127.0.0.1
```

### Step 4: Open the Dashboard

Navigate to:

**http://localhost:3039**

### Step 5: Stop the Application

Press **Ctrl+C**, then run:

```bash
/opt/homebrew/opt/postgresql@18/bin/pg_ctl \
  -D "$PWD/.scratch/integration-validation/postgres" stop
```

---

## 9. Important Notes

- Always quote the application path because `sea ice ` contains a trailing space.
- Use the existing local environment when available.
- Keep PostgreSQL running while using the dashboard.
- Keep the development server running while accessing the application.
- Development mode supports automatic application reloading.
- Local production mode does not deploy the application.
- Published products remain independent of working dataset changes.
- Missing scientific resources must not be replaced with synthetic observations.
- All validation resources are isolated under `.scratch/integration-validation/`.

---

## 10. Technology Stack

The application uses the following technologies:

| Component | Technology |
|-----------|------------|
| Application framework | Reflex |
| Programming language | Python |
| Database | PostgreSQL 18 |
| Database migrations | Alembic |
| Browser testing | Playwright |
| Data format | CSV |
| Frontend development port | 3039 |
| Backend development port | 8039 |
| PostgreSQL port | 55439 |

---

