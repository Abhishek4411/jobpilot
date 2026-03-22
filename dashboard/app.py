"""Flask application factory for the JobPilot dashboard."""

import os

import yaml
from flask import Flask, request, session, redirect, url_for, render_template_string

_LOGIN_HTML = """<!doctype html><html><head><title>JobPilot Login</title>
<style>body{background:#0f1117;color:#e0e0e0;font-family:sans-serif;display:flex;
justify-content:center;align-items:center;height:100vh;margin:0}
form{background:#1a1d2e;padding:2rem;border-radius:8px;display:flex;
flex-direction:column;gap:1rem;min-width:280px}
input{padding:.6rem;border-radius:4px;border:1px solid #333;background:#0f1117;color:#e0e0e0}
button{padding:.6rem;background:#4f8ef7;color:#fff;border:none;border-radius:4px;cursor:pointer}
</style></head><body><form method="post">
<h2 style="margin:0 0 .5rem">JobPilot</h2>
<input type="password" name="token" placeholder="Dashboard token" autofocus>
<button type="submit">Sign in</button>
{% if error %}<p style="color:#f87171;margin:0">{{ error }}</p>{% endif %}
</form></body></html>"""


def create_app() -> Flask:
    """Create and configure the Flask application.

    If DASHBOARD_TOKEN is set in .env, all routes require a one-time token login.
    Leave DASHBOARD_TOKEN unset (or empty) to run without auth (default for localhost).

    Returns:
        Configured Flask app instance with all blueprints registered.
    """
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "jobpilot-secret-change-in-production")

    @app.template_filter("toyaml")
    def toyaml_filter(value: dict) -> str:
        """Convert a dict to YAML string for template display."""
        if not isinstance(value, dict):
            return ""
        return yaml.dump(value, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        token_required = os.environ.get("DASHBOARD_TOKEN", "")
        if not token_required:
            session["authenticated"] = True
            return redirect(url_for("main.index"))
        error = None
        if request.method == "POST":
            if request.form.get("token") == token_required:
                session["authenticated"] = True
                return redirect(request.args.get("next") or url_for("main.index"))
            error = "Invalid token"
        return render_template_string(_LOGIN_HTML, error=error)

    @app.before_request
    def _require_auth():
        if not os.environ.get("DASHBOARD_TOKEN", ""):
            return  # Auth disabled when no token is configured
        public_prefixes = ("/login", "/static")
        if any(request.path.startswith(p) for p in public_prefixes):
            return
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))

    from dashboard.routes import bp as main_bp
    from dashboard.cv_routes import cv_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(cv_bp, url_prefix="/cv")

    return app
