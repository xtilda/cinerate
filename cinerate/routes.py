"""Catalogue, account and private journal routes."""

import csv
import io
import json
import math
import re
import sqlite3
from datetime import date
from functools import wraps
from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
    Response,
)
from werkzeug.security import check_password_hash, generate_password_hash
from .db import get_db
from .recommendations import recommend

bp = Blueprint("main", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.auth", mode="login"))
        return view(*args, **kwargs)

    return wrapped


def movie_or_404(movie_id):
    row = get_db().execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@bp.route("/")
def index():
    db = get_db()
    films = db.execute(
        "SELECT * FROM movies WHERE votes>1000 ORDER BY votes DESC LIMIT 12"
    ).fetchall()
    recent = db.execute(
        "SELECT * FROM movies WHERE year>=2020 AND poster!='' ORDER BY year DESC,votes DESC LIMIT 6"
    ).fetchall()
    stats = None
    watchlist = []
    watchlist_count = 0
    if g.user:
        uid = g.user["id"]
        stats = db.execute(
            "SELECT COUNT(*) logs,COUNT(DISTINCT movie_id) films,ROUND(AVG(score),1) average FROM entries WHERE user_id=?",
            (uid,),
        ).fetchone()
        watchlist = db.execute(
            "SELECT m.* FROM shelves s JOIN movies m ON m.id=s.movie_id WHERE s.user_id=? AND s.kind='watchlist' ORDER BY m.title LIMIT 6",
            (uid,),
        ).fetchall()
        watchlist_count = db.execute(
            "SELECT COUNT(*) FROM shelves WHERE user_id=? AND kind='watchlist'", (uid,)
        ).fetchone()[0]
    return render_template(
        "index.html",
        films=films,
        recent=recent,
        stats=stats,
        watchlist=watchlist,
        watchlist_count=watchlist_count,
        count=db.execute("SELECT COUNT(*) FROM movies").fetchone()[0],
        recent_count=db.execute(
            "SELECT COUNT(*) FROM movies WHERE year>=2020"
        ).fetchone()[0],
    )


@bp.route("/films")
@bp.route("/search")
@bp.route("/top-rated")
def films():
    q = request.args.get("q", request.args.get("query", "")).strip()[:200]
    genre = request.args.get("genre", "")
    year = request.args.get("year", "")
    max_runtime = request.args.get("max_runtime", "")
    sort = request.args.get("sort", "rating")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    clauses = ["1=1"]
    params = []
    if q:
        clauses.append('(title LIKE ? ESCAPE "\\" OR director LIKE ? ESCAPE "\\")')
        term = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.extend([f"%{term}%"] * 2)
    if genre:
        clauses.append("EXISTS (SELECT 1 FROM json_each(movies.genres) WHERE value=?)")
        params.append(genre)
    if year.isdigit():
        clauses.append("year=?")
        params.append(int(year))
    if max_runtime.isdigit():
        clauses.append("runtime<=?")
        params.append(min(int(max_runtime), 600))
    order = {
        "rating": "rating DESC,votes DESC",
        "popular": "votes DESC",
        "newest": "year DESC,votes DESC",
        "title": "title COLLATE NOCASE",
    }.get(sort, "rating DESC,votes DESC")
    where = " AND ".join(clauses)
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM movies WHERE " + where, params).fetchone()[
        0
    ]
    pages = max(1, math.ceil(count / 24))
    page = min(page, pages)
    rows = db.execute(
        "SELECT * FROM movies WHERE "
        + where
        + " ORDER BY "
        + order
        + " LIMIT 24 OFFSET ?",
        params + [(page - 1) * 24],
    ).fetchall()
    genres = [
        r[0]
        for r in db.execute(
            "SELECT DISTINCT value FROM movies,json_each(movies.genres) ORDER BY value"
        )
    ]
    return render_template(
        "films.html",
        films=rows,
        q=q,
        genre=genre,
        year=year,
        max_runtime=max_runtime,
        sort=sort,
        page=page,
        pages=pages,
        count=count,
        genres=genres,
    )


@bp.route("/movie/<int(signed=True):movie_id>")
def movie(movie_id):
    row = movie_or_404(movie_id)
    db = get_db()
    shelves = set()
    entries = []
    if g.user:
        shelves = {
            r[0]
            for r in db.execute(
                "SELECT kind FROM shelves WHERE user_id=? AND movie_id=?",
                (g.user["id"], movie_id),
            )
        }
        entries = db.execute(
            "SELECT * FROM entries WHERE user_id=? AND movie_id=? ORDER BY watched_on DESC,id DESC",
            (g.user["id"], movie_id),
        ).fetchall()
    return render_template(
        "movie.html",
        movie=row,
        genres=json.loads(row["genres"]),
        cast=json.loads(row["cast_names"]),
        shelves=shelves,
        entries=entries,
        today=date.today().isoformat(),
    )


@bp.route("/<mode>", methods=["GET", "POST"])
def auth(mode):
    if mode not in ("login", "register"):
        abort(404)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        if mode == "register":
            email = request.form.get("email", "").strip()
            if (
                not re.fullmatch(r"[\w.-]{3,30}", username)
                or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
                or len(email) > 254
                or not 10 <= len(password) <= 128
            ):
                flash(
                    "Use a 3–30 character username, valid email and a 10–128 character password.",
                    "error",
                )
                return render_template("auth.html", mode=mode), 400
            try:
                with db:
                    user_id = db.execute(
                        "INSERT INTO users(username,email,password) VALUES (?,?,?)",
                        (username, email, generate_password_hash(password)),
                    ).lastrowid
            except sqlite3.IntegrityError:
                flash("That username or email is already registered.", "error")
                return render_template("auth.html", mode=mode), 409
        else:
            user = db.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            if not user or not check_password_hash(user["password"], password):
                flash("Username or password is incorrect.", "error")
                return render_template("auth.html", mode=mode), 401
            user_id = user["id"]
        session.clear()
        session["user_id"] = user_id
        return redirect(url_for("main.journal"))
    return render_template("auth.html", mode=mode)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.index"))


def parse_entry():
    try:
        watched = date.fromisoformat(request.form.get("watched_on", ""))
        raw = request.form.get("score", "")
        score = float(raw) if raw else None
        if watched > date.today() or (
            score is not None
            and (
                not math.isfinite(score)
                or score < 0
                or score > 5
                or score * 2 != int(score * 2)
            )
        ):
            raise ValueError
        review = request.form.get("review", "").strip()
        if len(review) > 5000:
            raise ValueError
    except ValueError:
        abort(
            400,
            description="Use a past or present date, 0–5 stars in half-star steps, and at most 5,000 review characters.",
        )
    return watched, score, review


@bp.post("/movie/<int(signed=True):movie_id>/log")
@login_required
def log(movie_id):
    movie_or_404(movie_id)
    watched, score, review = parse_entry()
    db = get_db()
    with db:
        db.execute(
            "INSERT INTO entries(user_id,movie_id,watched_on,score,review,spoiler) VALUES (?,?,?,?,?,?)",
            (
                g.user["id"],
                movie_id,
                watched.isoformat(),
                score,
                review,
                int("spoiler" in request.form),
            ),
        )
        db.execute(
            "DELETE FROM shelves WHERE user_id=? AND movie_id=? AND kind='watchlist'",
            (g.user["id"], movie_id),
        )
    flash("Added to your film diary.", "success")
    return redirect(url_for("main.movie", movie_id=movie_id))


@bp.post("/entry/<int:entry_id>/delete")
@login_required
def delete_entry(entry_id):
    db = get_db()
    with db:
        result = db.execute(
            "DELETE FROM entries WHERE id=? AND user_id=?", (entry_id, g.user["id"])
        )
        if result.rowcount == 0:
            abort(404)
    flash("Diary entry deleted.", "success")
    return redirect(url_for("main.journal"))


@bp.post("/movie/<int(signed=True):movie_id>/shelf/<kind>")
@login_required
def shelf(movie_id, kind):
    movie_or_404(movie_id)
    if kind not in ("favorite", "watchlist"):
        abort(404)
    db = get_db()
    key = (g.user["id"], movie_id, kind)
    with db:
        if db.execute(
            "SELECT 1 FROM shelves WHERE user_id=? AND movie_id=? AND kind=?", key
        ).fetchone():
            db.execute(
                "DELETE FROM shelves WHERE user_id=? AND movie_id=? AND kind=?", key
            )
        else:
            db.execute("INSERT INTO shelves VALUES (?,?,?)", key)
    return redirect(url_for("main.movie", movie_id=movie_id))


@bp.get("/journal")
@bp.get("/profile")
@login_required
def journal():
    db = get_db()
    uid = g.user["id"]
    entries = db.execute(
        "SELECT e.*,m.title,m.year,m.poster,m.director FROM entries e JOIN movies m ON m.id=e.movie_id WHERE e.user_id=? ORDER BY e.watched_on DESC,e.id DESC",
        (uid,),
    ).fetchall()
    shelves = {
        kind: db.execute(
            "SELECT m.* FROM shelves s JOIN movies m ON m.id=s.movie_id WHERE s.user_id=? AND s.kind=? ORDER BY m.title",
            (uid, kind),
        ).fetchall()
        for kind in ("favorite", "watchlist")
    }
    counts = {}
    for r in db.execute(
        "SELECT m.genres FROM entries e JOIN movies m ON m.id=e.movie_id WHERE e.user_id=?",
        (uid,),
    ):
        for genre in json.loads(r[0]):
            counts[genre] = counts.get(genre, 0) + 1
    stats = db.execute(
        "SELECT COUNT(*) logs,COUNT(DISTINCT movie_id) films,ROUND(AVG(score),1) average FROM entries WHERE user_id=?",
        (uid,),
    ).fetchone()
    return render_template(
        "journal.html",
        entries=entries,
        shelves=shelves,
        stats=stats,
        top_genres=sorted(counts.items(), key=lambda x: x[1], reverse=True)[:5],
    )


@bp.get("/discover")
@login_required
def discover():
    return render_template("discover.html", films=recommend(g.user["id"]))


@bp.get("/export")
@login_required
def export():
    rows = get_db().execute(
        "SELECT m.title,m.year,e.watched_on,e.score,e.review,e.spoiler FROM entries e JOIN movies m ON m.id=e.movie_id WHERE e.user_id=? ORDER BY e.watched_on DESC",
        (g.user["id"],),
    )
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        ["Title", "Year", "Watched on", "Rating (0–5)", "Review", "Spoiler"]
    )
    for row in rows:
        writer.writerow(
            [
                (
                    "'" + value
                    if isinstance(value, str)
                    and value.lstrip().startswith(("=", "+", "-", "@"))
                    else value
                )
                for value in row
            ]
        )
    return Response(
        "\ufeff" + stream.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=cinerate-diary.csv"},
    )


@bp.post("/entry/<int:entry_id>/edit")
@login_required
def edit_entry(entry_id):
    db = get_db()
    entry = db.execute(
        "SELECT * FROM entries WHERE id=? AND user_id=?", (entry_id, g.user["id"])
    ).fetchone()
    if entry is None:
        abort(404)
    watched, score, review = parse_entry()
    with db:
        db.execute(
            "UPDATE entries SET watched_on=?,score=?,review=?,spoiler=? WHERE id=? AND user_id=?",
            (
                watched.isoformat(),
                score,
                review,
                int("spoiler" in request.form),
                entry_id,
                g.user["id"],
            ),
        )
    flash("Diary entry updated.", "success")
    return redirect(url_for("main.movie", movie_id=entry["movie_id"]))


@bp.get("/sessions")
def sessions():
    from .sessions import build_pairs

    try:
        minutes = int(request.args.get("minutes", 240))
    except ValueError:
        minutes = 240
    minutes = max(100, min(minutes, 480))
    genre = request.args.get("genre", "")
    db = get_db()
    genres = [
        row[0]
        for row in db.execute(
            "SELECT DISTINCT value FROM movies,json_each(movies.genres) ORDER BY value"
        )
    ]
    pairs = build_pairs(minutes, genre, g.user["id"] if g.user else None)
    saved = []
    if g.user:
        for row in db.execute(
            "SELECT * FROM watch_sessions WHERE user_id=? ORDER BY created_at DESC,id DESC",
            (g.user["id"],),
        ):
            films = [
                dict(movie_or_404(row["movie_a"])),
                dict(movie_or_404(row["movie_b"])),
            ]
            saved.append(
                dict(
                    row,
                    films=films,
                    minutes=sum(film["runtime"] or 0 for film in films) + 10,
                )
            )
    return render_template(
        "sessions.html",
        pairs=pairs,
        genres=genres,
        genre=genre,
        minutes=minutes,
        saved=saved,
    )


@bp.post("/sessions/save")
@login_required
def save_session():
    try:
        movie_a = int(request.form.get("movie_a", ""))
        movie_b = int(request.form.get("movie_b", ""))
    except ValueError:
        abort(400)
    movie_or_404(movie_a)
    movie_or_404(movie_b)
    title = request.form.get("title", "").strip()
    if movie_a == movie_b or not 1 <= len(title) <= 100:
        abort(
            400,
            description="Choose two different films and a session name of 1–100 characters.",
        )
    db = get_db()
    with db:
        db.execute(
            "INSERT INTO watch_sessions(user_id,title,movie_a,movie_b) VALUES (?,?,?,?)",
            (g.user["id"], title, movie_a, movie_b),
        )
    flash("Session saved.", "success")
    return redirect(url_for("main.sessions") + "#saved")


@bp.post("/sessions/<int:session_id>/delete")
@login_required
def delete_session(session_id):
    db = get_db()
    with db:
        result = db.execute(
            "DELETE FROM watch_sessions WHERE id=? AND user_id=?",
            (session_id, g.user["id"]),
        )
        if not result.rowcount:
            abort(404)
    flash("Session deleted.", "success")
    return redirect(url_for("main.sessions") + "#saved")
