import json
import sqlite3
from pathlib import Path
from cinerate import create_app
from cinerate.db import get_db
from cinerate.sessions import build_pairs
from test_app import post, register


def test_database_upgrade_keeps_accounts_and_viewings(tmp_path):
    database = tmp_path / "old.sqlite"
    db = sqlite3.connect(database)
    db.executescript(
        (Path(__file__).parents[1] / "cinerate/schema.sql")
        .read_text()
        .split("CREATE TABLE IF NOT EXISTS app_meta")[0]
    )
    db.execute(
        "INSERT INTO movies VALUES (19995,'Avatar',2009,'Old Director','Old synopsis','[]','[]','[]',162,'en',7.2,100,'')"
    )
    db.execute("INSERT INTO users VALUES (1,'olduser','old@example.com','hash')")
    db.execute(
        "INSERT INTO entries(user_id,movie_id,watched_on,score,review) VALUES (1,19995,'2025-01-01',4.5,'Keep me')"
    )
    db.commit()
    db.close()
    app = create_app({"TESTING": True, "DATABASE": str(database), "SECRET_KEY": "test"})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT review FROM entries").fetchone()[0] == "Keep me"
        assert db.execute("SELECT username FROM users").fetchone()[0] == "olduser"
        assert (
            db.execute("SELECT director FROM movies WHERE id=19995").fetchone()[0]
            == "James Cameron"
        )
        assert "source_url" in {
            r["name"] for r in db.execute("PRAGMA table_info(movies)")
        }


def test_catalogue_changes_upsert_without_resetting_diary(tmp_path):
    catalogue = tmp_path / "catalogue.json"
    base = dict(
        id=1,
        title="Film A",
        year=2024,
        director="Director A",
        description="Synopsis",
        genres='["Drama"]',
        cast_names="[]",
        keywords="[]",
        runtime=90,
        language="en",
        rating=8,
        votes=100,
        poster="",
    )
    catalogue.write_text(json.dumps([base]))
    config = {
        "TESTING": True,
        "DATABASE": str(tmp_path / "app.sqlite"),
        "SECRET_KEY": "test",
        "CATALOGUE": str(catalogue),
    }
    app = create_app(config)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO users VALUES (1,'user','user@example.com','hash')")
        db.execute(
            "INSERT INTO entries(user_id,movie_id,watched_on) VALUES (1,1,'2025-01-01')"
        )
        db.commit()
    base["title"] = "Film A updated"
    catalogue.write_text(json.dumps([base, dict(base, id=2, title="Film B")]))
    app = create_app(config)
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        assert (
            db.execute("SELECT title FROM movies WHERE id=1").fetchone()[0]
            == "Film A updated"
        )


def test_time_budget_genre_and_watched_exclusion(app):
    with app.app_context():
        pairs = build_pairs(240, "Drama")
        assert pairs
        for pair in pairs:
            assert pair["minutes"] <= 240
            assert all("Drama" in json.loads(film["genres"]) for film in pair["films"])
        ids = [film["id"] for pair in pairs for film in pair["films"]]
        assert len(ids) == len(set(ids))
        db = get_db()
        db.execute("INSERT INTO users VALUES (1,'user','user@example.com','hash')")
        db.execute(
            "INSERT INTO entries(user_id,movie_id,watched_on) VALUES (1,?,'2025-01-01')",
            (ids[0],),
        )
        db.commit()
        assert ids[0] not in [
            film["id"]
            for pair in build_pairs(240, "Drama", 1)
            for film in pair["films"]
        ]


def test_save_session_private_ownership(client, app):
    assert client.get("/sessions").status_code == 200
    register(client)
    assert (
        post(
            client,
            "/sessions/save",
            {"movie_a": 19995, "movie_b": 680, "title": "Friday films"},
        ).status_code
        == 302
    )
    assert b"Friday films" in client.get("/sessions").data
    other = app.test_client()
    register(other, "otheruser")
    assert b"Friday films" not in other.get("/sessions").data
    assert post(other, "/sessions/1/delete").status_code == 404
    assert post(client, "/sessions/1/delete").status_code == 302
    assert (
        post(
            client,
            "/sessions/save",
            {"movie_a": 19995, "movie_b": 19995, "title": "Invalid"},
        ).status_code
        == 400
    )


def test_runtime_filter(client):
    response = client.get("/films?max_runtime=90")
    assert response.status_code == 200
    assert b'name="max_runtime"' in response.data


def test_tmdb_normalization_does_not_invent_ratings():
    from cinerate.tmdb import normalize_movie

    movie = normalize_movie(
        {
            "id": 99,
            "title": "Example",
            "release_date": "2026-01-01",
            "vote_count": 0,
            "vote_average": 0,
            "poster_path": None,
        },
        "2026-10-08",
    )
    assert movie["rating"] is None and movie["year"] == 2026
    assert movie["director"] == "Unknown"
    assert movie["poster"] == ""


def test_supplemental_negative_ids_are_accessible(client, app):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO movies(id,title,director,description,genres,cast_names,keywords,source_url,updated_at) VALUES (-98765,'Supplemental film','Unknown','Synopsis','[]','[]','[]','https://en.wikipedia.org/wiki/Example','2026-10-08')"
        )
        db.commit()
    assert client.get("/movie/-98765").status_code == 200
    register(client)
    assert post(client, "/movie/-98765/shelf/watchlist").status_code == 302
    assert (
        post(
            client, "/movie/-98765/log", {"watched_on": "2025-01-01", "score": "4"}
        ).status_code
        == 302
    )


def test_bundled_snapshot_does_not_roll_back_newer_live_metadata(tmp_path):
    catalogue = tmp_path / "catalogue.json"
    base = dict(
        id=1,
        title="Film",
        year=2024,
        director="Director",
        description="Old",
        genres="[]",
        cast_names="[]",
        keywords="[]",
        runtime=90,
        language="en",
        rating=8,
        votes=100,
        poster="",
        updated_at="2026-01-01",
    )
    catalogue.write_text(json.dumps([base]))
    config = {
        "TESTING": True,
        "DATABASE": str(tmp_path / "app.sqlite"),
        "SECRET_KEY": "test",
        "CATALOGUE": str(catalogue),
    }
    app = create_app(config)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE movies SET description='Live data',updated_at='2026-10-08'")
        db.commit()
    base["description"] = "Bundled older data"
    catalogue.write_text(json.dumps([base]))
    app = create_app(config)
    with app.app_context():
        assert (
            get_db().execute("SELECT description FROM movies").fetchone()[0]
            == "Live data"
        )


def test_recent_films_have_corrected_years_and_no_stale_duplicates(app):
    with app.app_context():
        db = get_db()
        for title in ("Dune: Part Two", "Challengers"):
            rows = db.execute(
                "SELECT year FROM movies WHERE title=?", (title,)
            ).fetchall()
            assert len(rows) == 1 and rows[0]["year"] == 2024
