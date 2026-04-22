import os
from pathlib import Path

from flask import Flask, abort, send_from_directory
from waitress import serve

try:
    from flask_compress import Compress
except ImportError:
    Compress = None

from routes import api_bp, pages_bp

BASE_DIR = Path(__file__).resolve().parent
STOREFRONT_BUILD = BASE_DIR / "storefront" / "build"

# Paths that must never be masked by the SvelteKit SPA fallback.
# If a blueprint didn't match and one of these prefixes falls through,
# return 404 instead of serving index.html.
_RESERVED_PREFIXES = ("api/", "img/", "static/")

def create_app():
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
    )
    if Compress:
        Compress(app)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    # Blueprints register first so /api/*, /img/*, /static/assets/* and
    # any other specific rules match ahead of the SvelteKit catch-all.
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    # ── SvelteKit storefront (Phase 3 — PRO-6) ─────────────────────
    # Served at root (/). Any unknown path that isn't reserved for an
    # API or asset route returns build/index.html so SvelteKit's
    # client-side router can resolve it (SPA fallback).
    @app.route("/__pm_health")
    def storefront_health():
        built = STOREFRONT_BUILD.exists()
        return {"storefront_built": built, "path": str(STOREFRONT_BUILD)}

    @app.route("/")
    def serve_storefront_root():
        return send_from_directory(STOREFRONT_BUILD, "index.html")

    @app.route("/<path:filename>")
    def serve_storefront(filename):
        if filename.startswith(_RESERVED_PREFIXES):
            abort(404)
        if (STOREFRONT_BUILD / filename).is_file():
            return send_from_directory(STOREFRONT_BUILD, filename)
        return send_from_directory(STOREFRONT_BUILD, "index.html")

    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18080"))
    serve(app, host="0.0.0.0", port=port, threads=8)
