"""Time-budgeted double features with explicit shared metadata explanations."""

import json
from .db import get_db

BREAK_MINUTES = 10
METADATA_KEYWORDS = {
    "aftercreditsstinger",
    "duringcreditsstinger",
    "3d",
    "imax",
    "sequel",
    "prequel",
    "franchise",
    "reboot",
    "remake",
}


def build_pairs(minutes=240, genre="", user_id=None, limit=6):
    db = get_db()
    params = [minutes - BREAK_MINUTES - 40]
    where = "runtime BETWEEN 40 AND ?"
    if genre:
        where += " AND EXISTS (SELECT 1 FROM json_each(movies.genres) WHERE value=?)"
        params.append(genre)
    if user_id:
        where += " AND id NOT IN (SELECT movie_id FROM entries WHERE user_id=?)"
        params.append(user_id)
    rows = db.execute(
        "SELECT * FROM movies WHERE "
        + where
        + ' ORDER BY (poster != "") DESC,COALESCE(rating,0) DESC,votes DESC LIMIT 150',
        params,
    ).fetchall()
    ranked = []
    for i, first in enumerate(rows):
        first_genres = set(json.loads(first["genres"]))
        first_keywords = set(json.loads(first["keywords"])) - METADATA_KEYWORDS
        for second in rows[i + 1 :]:
            duration = first["runtime"] + second["runtime"] + BREAK_MINUTES
            if duration > minutes:
                continue
            shared_genres = first_genres & set(json.loads(second["genres"]))
            shared_keywords = first_keywords & (
                set(json.loads(second["keywords"])) - METADATA_KEYWORDS
            )
            same_director = (
                first["director"] == second["director"]
                and first["director"] != "Unknown"
            )
            if not (shared_genres or shared_keywords or same_director):
                continue
            connection = (
                f"Directed by {first['director']}"
                if same_director
                else (
                    "Shared themes: " + ", ".join(sorted(shared_keywords)[:2])
                    if shared_keywords
                    else "Shared genre: " + ", ".join(sorted(shared_genres)[:2])
                )
            )
            score = (
                4 * same_director
                + min(len(shared_keywords), 4) * 2
                + len(shared_genres) * 0.4
                + ((first["rating"] or 0) + (second["rating"] or 0)) * 0.25
            )
            ranked.append(
                (
                    score,
                    {
                        "films": [dict(first), dict(second)],
                        "minutes": duration,
                        "reason": connection,
                    },
                )
            )
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    results = []
    used = set()
    for _, pair in ranked:
        ids = {movie["id"] for movie in pair["films"]}
        if used & ids:
            continue
        used.update(ids)
        results.append(pair)
        if len(results) >= limit:
            break
    return results
