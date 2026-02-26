"""
Render / gunicorn run:app için giriş noktası.
Flask uygulaması app.py'de tanımlı.
"""
from app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(__import__("os").environ.get("PORT", 5000)))
