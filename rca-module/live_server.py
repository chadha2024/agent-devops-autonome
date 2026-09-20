"""
Serveur local pour le dashboard live — US 4.1, critère 3 : "interface
web sécurisée".

Sert les fichiers statiques (dashboard HTML, live_status.json), accepte
les clics de boutons Approuver/Rejeter/Choisir (POST /choose), ET exige
une authentification HTTP Basic avant de laisser passer quoi que ce soit.

⚠️ Limite honnête : c'est du HTTP Basic Auth en clair (pas de HTTPS) —
suffisant pour un dashboard interne/démo, mais PAS pour une vraie
exposition sur internet, où il faudrait TLS + un vrai système d'identité.

Identifiants dans dashboard_auth.yaml (jamais en dur dans le code).

Usage :
    python live_server.py --port 8000
"""

import argparse
import base64
import json
import os
import yaml
from http.server import SimpleHTTPRequestHandler, HTTPServer

AUTH_CONFIG_PATH = "dashboard_auth.yaml"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "changeme"  # ⚠️ à changer via dashboard_auth.yaml avant tout usage réel


def _load_credentials() -> tuple[str, str]:
    if os.path.exists(AUTH_CONFIG_PATH):
        with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("username", DEFAULT_USERNAME), data.get("password", DEFAULT_PASSWORD)
    return DEFAULT_USERNAME, DEFAULT_PASSWORD


def _check_auth(auth_header: str | None, username: str, password: str) -> bool:
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[len("Basic "):]).decode("utf-8")
        sent_user, sent_pass = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return sent_user == username and sent_pass == password


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    username, password = _load_credentials()

    def _require_auth(self) -> bool:
        """Retourne True si authentifié ; sinon envoie 401 et retourne False."""
        if _check_auth(self.headers.get("Authorization"), self.username, self.password):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Dashboard Agent DevOps"')
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Authentification requise.")
        return False

    def do_GET(self):
        if not self._require_auth():
            return
        super().do_GET()

    def do_POST(self):
        if not self._require_auth():
            return
        if self.path == "/choose":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
                with open("human_choice.json", "w", encoding="utf-8") as f:
                    json.dump(data, f)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            except (json.JSONDecodeError, OSError) as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        SimpleHTTPRequestHandler.end_headers(self)

    def log_message(self, format, *args):
        pass  # évite de polluer le terminal avec chaque requête GET du polling


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    username, _ = _load_credentials()
    server = HTTPServer(("localhost", args.port), DashboardRequestHandler)
    print(f"Dashboard servi sur http://localhost:{args.port}/live_dashboard.html")
    print(f"Authentification requise — utilisateur : {username}")
    if not os.path.exists(AUTH_CONFIG_PATH):
        print(f"⚠️  Mot de passe par défaut utilisé ('{DEFAULT_PASSWORD}') — "
              f"crée {AUTH_CONFIG_PATH} pour le changer avant un vrai usage.")
    print("Ctrl+C pour arrêter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")


if __name__ == "__main__":
    main()
