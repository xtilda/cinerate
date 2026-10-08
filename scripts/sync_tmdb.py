"""Update the installed database from TMDB without deleting user data.

Usage: TMDB_READ_TOKEN=... python scripts/sync_tmdb.py --pages 2
The secret is read only from the environment and is never written or printed.
"""

import argparse
from datetime import date
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cinerate import create_app
from cinerate.db import MOVIE_COLUMNS, get_db
from cinerate.tmdb import TMDBClient, TMDBError, normalize_movie


def main():
    parser = argparse.ArgumentParser(
        description="Update movie catalogue from the official TMDB API."
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="1–10 pages from each of popular and now-playing",
    )
    args = parser.parse_args()
    if not 1 <= args.pages <= 10:
        parser.error("--pages must be between 1 and 10")
    token = os.environ.get("TMDB_READ_TOKEN")
    if not token:
        parser.exit(
            2,
            "Set TMDB_READ_TOKEN to your TMDB API Read Access Token first. No database changes made.\n",
        )
    client = TMDBClient(token)
    try:
        ids = set()
        for feed in ("movie/popular", "movie/now_playing"):
            for page in range(1, args.pages + 1):
                ids.update(
                    item["id"]
                    for item in client.get(feed, page=page, language="en-US")["results"]
                )
        records = []
        for movie_id in sorted(ids):
            raw = client.get(
                f"movie/{movie_id}",
                append_to_response="credits,keywords",
                language="en-US",
            )
            records.append(normalize_movie(raw, date.today().isoformat()))
    except TMDBError as error:
        parser.exit(1, str(error) + " No partial database updates were committed.\n")
    app = create_app()
    with app.app_context():
        db = get_db()
        fields = ",".join(MOVIE_COLUMNS)
        values = ",".join(":" + name for name in MOVIE_COLUMNS)
        updates = ",".join(
            f"{name}=excluded.{name}" for name in MOVIE_COLUMNS if name != "id"
        )
        with db:
            db.executemany(
                f"INSERT INTO movies ({fields}) VALUES ({values}) ON CONFLICT(id) DO UPDATE SET {updates}",
                records,
            )
    print(
        f"Updated {len(records)} films. Accounts, diary entries and saved sessions were preserved."
    )


if __name__ == "__main__":
    main()
