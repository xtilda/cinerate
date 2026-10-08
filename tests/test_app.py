import csv
import io
import pytest
from cinerate.db import get_db


def post(client, path, data=None):
    client.get("/")
    with client.session_transaction() as s:
        token = s["csrf"]
    return client.post(path, data=dict(data or {}, csrf=token))


def register(client, name="tester"):
    return post(
        client,
        "/register",
        {
            "username": name,
            "email": name + "@example.com",
            "password": "a-safe-password",
        },
    )


def test_catalogue_filters_and_missing_movies(client):
    for path in [
        "/",
        "/films",
        "/films?q=Alien",
        "/films?genre=Drama&year=1999",
        "/films?page=oops",
        "/films?q=%25",
        "/movie/19995",
    ]:
        assert client.get(path).status_code == 200
    assert client.get("/movie/999999999").status_code == 404
    assert b"James Cameron" in client.get("/movie/19995").data


def test_auth_and_csrf(client, app):
    assert client.post("/register", data={}).status_code == 400
    assert register(client).status_code == 302
    with app.app_context():
        assert (
            get_db().execute("SELECT password FROM users").fetchone()[0]
            != "a-safe-password"
        )
    post(client, "/logout")
    assert (
        post(client, "/login", {"username": "tester", "password": "wrong"}).status_code
        == 401
    )
    assert (
        post(
            client, "/login", {"username": "tester", "password": "a-safe-password"}
        ).status_code
        == 302
    )
    post(client, "/logout")
    assert register(client).status_code == 409


def test_shelf_log_rewatch_and_export(client, app):
    register(client)
    assert post(client, "/movie/19995/shelf/watchlist").status_code == 302
    assert b"Remove from Watchlist" in client.get("/movie/19995").data
    data = {"watched_on": "2025-01-01", "score": "4.5", "review": "=SUM(1,2)"}
    assert post(client, "/movie/19995/log", data).status_code == 302
    assert b"Add to Watchlist" in client.get("/movie/19995").data
    post(client, "/movie/19995/log", data)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
    rows = list(csv.reader(io.StringIO(client.get("/export").data.decode("utf-8-sig"))))
    assert rows[1][4].startswith("'=")
    assert b"Avatar" not in client.get("/discover").data
    assert client.get("/journal").status_code == 200


@pytest.mark.parametrize("score", ["nan", "inf", "-1", "5.5", "4.2", "bad"])
def test_invalid_rating(client, score):
    register(client)
    assert (
        post(
            client, "/movie/19995/log", {"watched_on": "2025-01-01", "score": score}
        ).status_code
        == 400
    )


def test_validation_escape_and_ownership(client, app):
    assert (
        post(
            client, "/register", {"username": "x", "email": "bad", "password": "x"}
        ).status_code
        == 400
    )
    register(client)
    assert (
        post(client, "/movie/19995/log", {"watched_on": "2099-01-01"}).status_code
        == 400
    )
    post(
        client,
        "/movie/19995/log",
        {"watched_on": "2025-01-01", "review": "<script>alert(1)</script>"},
    )
    html = client.get("/journal").data
    assert b"&lt;script&gt;" in html and b"<script>alert(1)</script>" not in html
    other = app.test_client()
    register(other, "someoneelse")
    assert post(other, "/entry/1/delete").status_code == 404
    assert b"Avatar" not in other.get("/journal").data
    assert post(client, "/entry/1/delete").status_code == 302


def test_headers_and_private_routes(client):
    assert client.get("/journal").status_code == 302
    assert client.get("/export").status_code == 302
    assert client.get("/").headers["X-Frame-Options"] == "DENY"
    assert client.get("/logout").status_code == 404


def test_edit_entry_ownership_and_validation(client, app):
    register(client)
    post(client, "/movie/19995/log", {"watched_on": "2025-01-01", "score": "0"})
    other = app.test_client()
    register(other, "editor")
    assert post(other, "/entry/1/edit", {"watched_on": "2025-01-01"}).status_code == 404
    assert (
        post(
            client, "/entry/1/edit", {"watched_on": "2025-01-01", "score": "6"}
        ).status_code
        == 400
    )
    assert (
        post(
            client,
            "/entry/1/edit",
            {"watched_on": "2025-01-02", "score": "3.5", "review": "Changed my mind."},
        ).status_code
        == 302
    )
    with app.app_context():
        entry = get_db().execute("SELECT * FROM entries WHERE id=1").fetchone()
        assert entry["score"] == 3.5 and entry["review"] == "Changed my mind."
