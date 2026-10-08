"""CineRate application factory. No shared connections or import-time server."""

import os
import secrets
from pathlib import Path
from flask import Flask, render_template, session, request, abort, g
from .db import get_db, close_db, initialize


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        secret_path = Path(app.instance_path) / "secret.key"
        if not secret_path.exists():
            try:
                with secret_path.open("x") as f:
                    f.write(secrets.token_hex(32))
                secret_path.chmod(0o600)
            except FileExistsError:
                pass
        secret = secret_path.read_text()
    app.config.from_mapping(
        SECRET_KEY=secret,
        DATABASE=os.environ.get(
            "CINERATE_DATABASE", str(Path(app.instance_path) / "cinerate.sqlite")
        ),
        CATALOGUE=str(Path(__file__).parent.parent / "data" / "catalogue.json"),
        MAX_CONTENT_LENGTH=128 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)
    with app.app_context():
        initialize()

    @app.before_request
    def load_user_and_check_csrf():
        g.user = (
            get_db()
            .execute(
                "SELECT id,username FROM users WHERE id=?", (session.get("user_id"),)
            )
            .fetchone()
        )
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST" and not secrets.compare_digest(
            session["csrf"], request.form.get("csrf", "")
        ):
            abort(400, description="Form expired. Refresh the page and try again.")

    @app.context_processor
    def helpers():
        return {"csrf": lambda: session["csrf"]}

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https://image.tmdb.org https://media.themoviedb.org https://upload.wikimedia.org; style-src 'self'; script-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    for status in (400, 403, 404, 413, 500):
        app.register_error_handler(
            status,
            lambda error: (render_template("error.html", error=error), error.code),
        )
    from .routes import bp

    app.register_blueprint(bp)
    return app
