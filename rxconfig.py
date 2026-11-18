"""Configure the Reflex application, database URL, and UI plugins."""

import os

import reflex as rx
import reflex_xy

# PostgreSQL is the default database. Override with REFLEX_DB_URL, e.g.
#   postgresql+psycopg://user:password@host:5432/dbname
#   sqlite:///globe_check.db   (local fallback without PostgreSQL)
# Without a user/password, psycopg connects as the current OS user, which is
# how a Homebrew PostgreSQL install is set up by default.
DEFAULT_DATABASE_URL = "postgresql+psycopg://localhost:5432/antarctic_sea_ice"
database_url = os.getenv("REFLEX_DB_URL", DEFAULT_DATABASE_URL)

config = rx.Config(
    app_name="antarctic_sea_ice_dashboard",
    db_url=database_url,
    default_color_mode="light",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        reflex_xy.XYPlugin(),
    ],
)
