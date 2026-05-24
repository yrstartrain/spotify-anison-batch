"""
初回認証スクリプト - ローカルで1回だけ実行する

Usage:
    SPOTIFY_CLIENT_ID=xxx SPOTIFY_CLIENT_SECRET=yyy python scripts/get_refresh_token.py

実行するとブラウザが開くので Spotify でログイン・認可する。
完了後、コンソールに Refresh Token が表示される。
その値を GitHub Secrets の SPOTIFY_REFRESH_TOKEN に登録すること。
"""

import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import requests

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID") or input("SPOTIFY_CLIENT_ID: ").strip()
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET") or input("SPOTIFY_CLIENT_SECRET: ").strip()
REDIRECT_URI = "http://localhost:8888/callback"
SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

auth_code: str = ""


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>Authentication successful! You can close this tab.</h1>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Error: no code returned.</h1>")

    def log_message(self, *args):
        pass


def main():
    auth_params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    }
    from urllib.parse import urlencode
    url = f"{AUTH_URL}?{urlencode(auth_params)}"
    print(f"\nOpening browser for Spotify authorization...\nURL: {url}\n")
    webbrowser.open(url)

    server = HTTPServer(("localhost", 8888), CallbackHandler)
    print("Waiting for callback on http://localhost:8888/callback ...")
    server.handle_request()

    if not auth_code:
        print("ERROR: Failed to get authorization code.")
        return

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
    )
    resp.raise_for_status()
    token_data = resp.json()

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh_token in response:", token_data)
        return

    print("\n" + "=" * 60)
    print("SUCCESS! Copy the value below to GitHub Secrets.")
    print("Secret name : SPOTIFY_REFRESH_TOKEN")
    print(f"Secret value: {refresh_token}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
