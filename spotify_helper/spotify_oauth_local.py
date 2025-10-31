#!/usr/bin/env python3
"""
Simple local Spotify OAuth script:
- Opens browser for login
- Captures redirect with code
- Exchanges code for access & refresh tokens
- Prints all tokens to console (no file writing)
"""

import base64
import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv("./../env/.env")
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
print("Loaded Spotify Client ID: ..." + CLIENT_ID[-4:])
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
print("Loaded Spotify Client ID: ..." + CLIENT_ID[-2:])

REDIRECT_URI = "http://127.0.0.1:8080/callback"

SCOPES  = "user-read-playback-state user-modify-playback-state"
# ---------------------

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def basic_auth_header(client_id, client_secret):
    token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return f"Basic {token}"


class OAuthHandler(BaseHTTPRequestHandler):
    """Handles Spotify redirect and extracts authorization code"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = qs.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<h2>Authorization successful!</h2><p>You can close this tab now.</p>"
        )

    def log_message(self, *args):
        return  # silence logs


def get_authorization_code():
    """Open Spotify login in browser and capture authorization code."""
    state = os.urandom(8).hex()
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    # Start local server
    server = HTTPServer(("127.0.0.1", 8080), OAuthHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("Opening browser for Spotify authorization...")
    webbrowser.open(auth_url)
    print("Waiting for redirect...")

    # Wait for handler to receive code
    start = time.time()
    while not getattr(server, "auth_code", None):
        if time.time() - start > 300:
            raise TimeoutError("Timeout waiting for Spotify authorization.")
        time.sleep(0.2)

    return server.auth_code


def exchange_code_for_tokens(auth_code):
    """Exchange authorization code for access & refresh tokens."""
    headers = {
        "Authorization": basic_auth_header(CLIENT_ID, CLIENT_SECRET),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
    }
    resp = requests.post(TOKEN_URL, headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    print("=== Spotify OAuth Token Generator ===")

    code = get_authorization_code()
    print("✅ Got authorization code!")

    token_data = exchange_code_for_tokens(code)
    access = token_data.get("access_token")
    refresh = token_data.get("refresh_token")
    expires = token_data.get("expires_in")

    print("\n=== ✅ SUCCESS ===")
    print("Copy these variables into a safe place:\n")
    print(f'SPOTIFY_CLIENT_ID="{CLIENT_ID}"')
    print(f'SPOTIFY_CLIENT_SECRET="{CLIENT_SECRET}"')
    print(f'SPOTIFY_ACCESS_TOKEN="{access}"')
    print(f'SPOTIFY_REFRESH_TOKEN="{refresh}"')
    print(f"# Access token expires in {expires} seconds (~{expires/60:.1f} min)")
    print("\nYou can refresh the access token later using the refresh token.")


if __name__ == "__main__":
    main()