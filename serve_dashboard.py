#!/usr/bin/env python3
"""
serve_dashboard.py — Mini-serveur web pour le dashboard, protégé par mot de passe.

Lancé comme service (systemd). Sert dashboard.html sur un port, avec
authentification HTTP Basic. Identifiants et port lus depuis .env :
  DASHBOARD_USER, DASHBOARD_PASSWORD, DASHBOARD_PORT
"""

import base64
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

USER = os.getenv("DASHBOARD_USER", "admin")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_FILE = SCRIPT_DIR / "dashboard.html"

EXPECTED = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()


class Handler(BaseHTTPRequestHandler):
    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Dashboard Planning"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Authentification requise".encode("utf-8"))

    def do_GET(self):
        # Auth
        if not PASSWORD:
            self.send_error(500, "DASHBOARD_PASSWORD non configuré")
            return
        auth = self.headers.get("Authorization", "")
        if auth != EXPECTED:
            self._deny()
            return
        # Sert le dashboard
        if not DASHBOARD_FILE.exists():
            self.send_error(404, "Dashboard pas encore généré")
            return
        content = DASHBOARD_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass  # silence


if __name__ == "__main__":
    if not PASSWORD:
        print("⚠️ DASHBOARD_PASSWORD manquant dans .env — refuse de démarrer sans mot de passe.")
        raise SystemExit(1)
    print(f"Dashboard servi sur http://0.0.0.0:{PORT} (user: {USER})")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
