# spotify_api.py

from __future__ import annotations

import base64
import json
import logging
import os
import time
from json import JSONDecodeError
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from urllib.parse import urlencode
from pathlib import Path

import urllib.request
import urllib.error

from song import Song

try:
    from dotenv import load_dotenv, find_dotenv
    env_path = Path(__file__).resolve().parent / "env" / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        # Fallback: normales find_dotenv() im Projektverzeichnis
        from dotenv import find_dotenv
        load_dotenv(find_dotenv(), override=False)
except Exception:
    # Wenn python-dotenv nicht installiert ist, läuft es einfach ohne .env-Load weiter
    pass

logger = logging.getLogger(__name__)

# --- Configuration (env-based) ------------------------------------------------
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

# Optional: if you want to seed with a manually obtained access token.
SPOTIFY_ACCESS_TOKEN = os.getenv("SPOTIFY_ACCESS_TOKEN")

# --- Constants ----------------------------------------------------------------
API_BASE = "https://api.spotify.com/v1"
ACCOUNTS_TOKEN_URL = "https://accounts.spotify.com/api/token"

# We only work with tracks (not episodes)
TRACK_URI_PREFIX = "spotify:track:"


# --- Token Manager ------------------------------------------------------------
@dataclass
class _TokenState:
    access_token: Optional[str] = None
    # Epoch seconds when token expires. We'll refresh a bit early (skew).
    expires_at: Optional[float] = None


class _TokenManager:
    """
    Minimal refresh-token based token manager.
    Requires SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET / SPOTIFY_REFRESH_TOKEN.
    """

    SKEW_SECONDS = 30  # refresh a bit early

    def __init__(self) -> None:
        self.state = _TokenState()

        # If user seeded an access token, keep it until expiry unknown; we will
        # refresh on first 401 or when explicit refresh is requested.
        if SPOTIFY_ACCESS_TOKEN:
            self.state.access_token = SPOTIFY_ACCESS_TOKEN
            self.state.expires_at = None  # unknown; treat as possibly valid

    def _basic_auth_header(self) -> str:
        assert SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET, (
            "SPOTIFY_CLIENT_ID/SECRET must be set"
        )
        b64 = base64.b64encode(
            f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {b64}"

    def refresh(self) -> Optional[str]:
        """Always attempts a refresh using the refresh token."""
        if not SPOTIFY_REFRESH_TOKEN:
            logger.error(
                "Missing SPOTIFY_REFRESH_TOKEN; cannot refresh Spotify access token."
            )
            return None

        data = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": SPOTIFY_REFRESH_TOKEN,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            ACCOUNTS_TOKEN_URL,
            data=data,
            headers={
                "Authorization": self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.error("Spotify token refresh failed: %s", e.read().decode("utf-8"))
            return None
        except Exception as e:
            logger.exception("Spotify token refresh unexpected error: %s", e)
            return None

        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in")  # seconds
        if not access_token:
            logger.error("Spotify token refresh missing access_token field.")
            return None

        now = time.time()
        expires_at = now + int(expires_in or 3600)  # default 1h if not provided

        self.state.access_token = access_token
        self.state.expires_at = expires_at

        logger.debug("Obtained new Spotify access token; expires in %ss.", expires_in)
        return access_token

    def get_token(self) -> Optional[str]:
        """
        Returns a valid access token, refreshing if needed.
        """
        # If we have an expiry and it's due, refresh.
        if self.state.expires_at is not None and (
            time.time() > (self.state.expires_at - self.SKEW_SECONDS)
        ):
            return self.refresh()

        # If we have no token yet, refresh.
        if not self.state.access_token:
            return self.refresh()

        # Otherwise return what we have.
        return self.state.access_token


_token_mgr = _TokenManager()


# --- HTTP Helpers -------------------------------------------------------------
def _parse_json_response(resp) -> Optional[Dict[str, Any]]:
    status = resp.getcode()
    if status == 204:
        return {}
    raw = resp.read()  # bytes
    if not raw or not raw.strip():
        return {}

    # Nur JSON parsen, wenn Header danach aussieht
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" not in ctype:
        # Für Erfolgsfälle ohne JSON-Body einfach leeres Dict zurückgeben
        return {}

    try:
        return json.loads(raw.decode("utf-8"))
    except JSONDecodeError:
        # Falls doch mal kein gültiges JSON drin ist → einfach {} zurück
        return {}

def _authorized_request(method: str, url: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:

    """
    Makes an authorized request with auto-refresh on 401 and simple 429 handling.
    Returns parsed JSON dict on 2xx with body, or {} if 204, else None.
    """
    token = _token_mgr.get_token()
    if not token:
        logger.error("No Spotify access token available.")
        return None

    full_url = url
    if params:
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        if qs:
            full_url = f"{url}?{qs}"

    data_bytes = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(full_url, data=data_bytes, headers=headers, method=method)

    def _do() -> Optional[Dict[str, Any]]:
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return _parse_json_response(resp)

        except urllib.error.HTTPError as e:
            status = e.code
            if status == 401:
                logger.info("401 from Spotify; attempting token refresh.")
                if _token_mgr.refresh():
                    headers["Authorization"] = f"Bearer {_token_mgr.state.access_token}"
                    req2 = urllib.request.Request(full_url, data=data_bytes, headers=headers, method=method)
                    try:
                        with urllib.request.urlopen(req2, timeout=20) as resp2:
                            return _parse_json_response(resp2)
                    except Exception as e2:
                        logger.error("Retry after refresh failed: %s", e2)
                        return None
                return None

            if status == 429:
                retry_after = e.headers.get("Retry-After")
                wait_s = int(retry_after) if retry_after and retry_after.isdigit() else 1
                logger.warning("Spotify rate limited (429). Waiting %ss then retrying once.", wait_s)
                time.sleep(wait_s)
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp3:
                        return _parse_json_response(resp3)
                except Exception as e3:
                    logger.error("Retry after 429 failed: %s", e3)
                    return None

            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = "<no body>"
            if status == 404 and "NO_ACTIVE_DEVICE" in err_body:
                logger.warning("Failed adding song to spotify queue, because there is no active device. Please start playing music on any device assosiated with the account. URL: %s", full_url)
            else:
                logger.error("Spotify API error %s at %s: %s", status, full_url, err_body)
            return None

        except Exception as e:
            logger.error("Spotify API request error at %s: %s", full_url, e)
            return None

    return _do()


def _item_to_song(item: Dict[str, Any]) -> Optional[Song]:
    """
    Convert a Spotify 'track' object to our Song. Returns None for non-tracks.
    """
    if not item or item.get("type") != "track":
        return None
    track_id = item.get("id")
    name = item.get("name")
    artists = item.get("artists") or []
    artist_names = ", ".join([a.get("name") for a in artists if a and a.get("name")])
    if not track_id or not name or not artist_names:
        return None
    return Song(track_id, name, artist_names)


# --- Public API ---------------------------------------------------------------
def get_queue() -> List[Song]:
    """
    Returns: [curr_song, queue_song_1, queue_song_2, ...]
    Rules:
    - If no current song but there are queued tracks: [queue_songs...]
    - If there is a current song but no queued tracks: [curr_song]
    - If neither: []
    - Episodes are ignored; only 'track' items are converted to Song.
    """
    endpoint = f"{API_BASE}/me/player/queue"
    payload = _authorized_request("GET", endpoint)
    if payload is None:
        return []
    out: List[Song] = []

    curr = _item_to_song(payload.get("currently_playing"))
    if curr:
        out.append(curr)

    for q_item in payload.get("queue") or []:
        s = _item_to_song(q_item)
        if s:
            out.append(s)

    return out


def add_song_to_queue(song: Song) -> bool:
    """
    Adds a track to the current device's playback queue.
    Returns True on success, False on error.
    """
    if not song or not song.song_id:
        logger.error("add_song_to_queue called with invalid song.")
        return False

    endpoint = f"{API_BASE}/me/player/queue"
    params = {"uri": f"{TRACK_URI_PREFIX}{song.song_id}"}
    payload = _authorized_request("POST", endpoint, params=params)
    # On success Spotify returns HTTP 204 (we normalize to {}), so payload=={} is fine.
    return payload is not None


def search_song(song_id: str) -> Optional[Song]:
    """
    Fetch a track by its Spotify ID and return a Song.
    Joins all artist names with ', '.
    """
    if not song_id:
        return None
    endpoint = f"{API_BASE}/tracks/{song_id}"
    payload = _authorized_request("GET", endpoint)
    if not payload:
        return None
    return _item_to_song({**payload, "type": "track"})
