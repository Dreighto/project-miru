import os
from pathlib import Path

from flask import Flask
from waitress import serve

try:
    from flask_compress import Compress
except ImportError:
    Compress = None

from routes import api_bp, pages_bp

BASE_DIR = Path(__file__).resolve().parent

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
    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    return app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18080"))
    serve(app, host="0.0.0.0", port=port, threads=8)
