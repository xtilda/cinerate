"""Request-scoped SQLite connections and non-destructive catalogue migration."""

import hashlib
import json
import sqlite3
from pathlib import Path
from flask import current_app, g

MOVIE_COLUMNS = (
    "id",
    "title",
    "year",
    "director",
    "description",
    "genres",
    "cast_names",
    "keywords",
    "runtime",
    "language",
    "rating",
    "votes",
    "poster",
    "source_url",
    "updated_at",
)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def initialize():
    db = get_db()
    db.executescript(Path(__file__).with_name("schema.sql").read_text())
    columns = {row["name"] for row in db.execute("PRAGMA table_info(movies)")}
    for name in ("source_url", "updated_at"):
        if name not in columns:
            db.execute(f"ALTER TABLE movies ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    raw = Path(current_app.config["CATALOGUE"]).read_bytes()
    version = hashlib.sha256(raw).hexdigest()
    previous = db.execute(
        "SELECT value FROM app_meta WHERE key='catalogue_hash'"
    ).fetchone()
    if previous and previous[0] == version:
        db.commit()
        return
    rows = json.loads(raw)
    for row in rows:
        row.setdefault(
            "source_url", "https://www.themoviedb.org/movie/" + str(row["id"])
        )
        row.setdefault("updated_at", "")
    fields = ",".join(MOVIE_COLUMNS)
    values = ",".join(":" + column for column in MOVIE_COLUMNS)
    updates = ",".join(
        f"{column}=excluded.{column}" for column in MOVIE_COLUMNS if column != "id"
    )
    with db:
        db.executemany(
            f"INSERT INTO movies ({fields}) VALUES ({values}) ON CONFLICT(id) DO UPDATE SET {updates} WHERE date(movies.updated_at) IS NULL OR (date(excluded.updated_at) IS NOT NULL AND date(excluded.updated_at)>=date(movies.updated_at))",
            rows,
        )
        db.execute(
            "INSERT INTO app_meta(key,value) VALUES ('catalogue_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (version,),
        )
        db.execute("PRAGMA user_version = 2")
