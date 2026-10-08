"""Official TMDB API client used only by the explicitly invoked sync command."""

import json
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.themoviedb.org/3/"


class TMDBError(RuntimeError):
    pass


class TMDBClient:
    def __init__(self, token):
        self.token = token

    def get(self, path, **params):
        url = BASE_URL + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise TMDBError(
                f"TMDB returned HTTP {error.code}. Check token permissions and retry."
            ) from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise TMDBError(
                "Could not read TMDB data. Check your connection and retry."
            ) from None


def normalize_movie(raw, updated_at):
    credits = raw.get("credits", {})
    keywords = raw.get("keywords", {}).get("keywords", [])
    poster = raw.get("poster_path")
    return dict(
        id=raw["id"],
        title=raw["title"],
        year=int(raw["release_date"][:4]) if raw.get("release_date") else None,
        director=", ".join(
            person["name"]
            for person in credits.get("crew", [])
            if person.get("job") == "Director"
        )
        or "Unknown",
        description=raw.get("overview") or "Synopsis unavailable.",
        genres=json.dumps([item["name"] for item in raw.get("genres", [])]),
        cast_names=json.dumps([item["name"] for item in credits.get("cast", [])[:10]]),
        keywords=json.dumps([item["name"] for item in keywords]),
        runtime=raw.get("runtime") or None,
        language=raw.get("original_language", ""),
        rating=raw.get("vote_average") if raw.get("vote_count") else None,
        votes=raw.get("vote_count", 0),
        poster="https://image.tmdb.org/t/p/w342" + poster if poster else "",
        source_url="https://www.themoviedb.org/movie/" + str(raw["id"]),
        updated_at=updated_at,
    )
