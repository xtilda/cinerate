"""Transparent content-based ranking; entirely local, no API or LLM needed."""

import json
from collections import Counter
from .db import get_db
from .sessions import METADATA_KEYWORDS


def recommend(user_id, limit=18):
    db = get_db()
    history = db.execute(
        "SELECT m.*, e.score FROM entries e JOIN movies m ON m.id=e.movie_id WHERE e.user_id=? AND e.id=(SELECT e2.id FROM entries e2 WHERE e2.user_id=e.user_id AND e2.movie_id=e.movie_id ORDER BY e2.watched_on DESC,e2.id DESC LIMIT 1)",
        (user_id,),
    ).fetchall()
    seen = {m["id"] for m in history}
    genres, directors, keywords = Counter(), Counter(), Counter()
    for movie in history:
        weight = (movie["score"] if movie["score"] is not None else 2.5) - 2.5
        for genre in json.loads(movie["genres"]):
            genres[genre] += weight
        directors[movie["director"]] += weight * 2
        for keyword in set(json.loads(movie["keywords"])) - METADATA_KEYWORDS:
            keywords[keyword] += weight * 0.6
    results = []
    for movie in db.execute("SELECT * FROM movies WHERE votes>=50"):
        if movie["id"] in seen:
            continue
        matches = [x for x in json.loads(movie["genres"]) if genres[x] > 0]
        score = (
            sum(genres[x] for x in json.loads(movie["genres"]))
            + directors[movie["director"]]
            + sum(keywords[x] for x in json.loads(movie["keywords"]))
        )
        score += (movie["rating"] or 0) * 0.15
        reason = (
            "Because you enjoy " + ", ".join(matches[:2])
            if matches
            else "Highly rated in the catalogue"
        )
        if directors[movie["director"]] > 0:
            reason = "More from " + movie["director"]
        results.append((score, dict(movie), reason))
    results.sort(key=lambda item: (item[0], item[1]["votes"]), reverse=True)
    return [dict(movie, reason=reason) for _, movie, reason in results[:limit]]
