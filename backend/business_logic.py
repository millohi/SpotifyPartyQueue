# business_logic.py

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

# External modules are assumed to exist, as per the specification.
from song import Song
import spotify_api as sp
import db_api as db


def get_queue(client_id: Optional[str] = None) -> List[Tuple[Song, int, int]]:
    return db.get_app_queue(client_id=client_id)


def add_song_to_app_queue(song_id: str) -> bool:
    """
    Add a song to the application queue if allowed.

    Rules:
      1) Ensure the song exists in the DB (db.add_song). This returns the last_played (UTC) or None.
      2) If the song is NOT already in the app queue AND the song was NOT played within the last 30 minutes (UTC),
         then add it to the app queue.

    :param song_id: song_id to add.
    :return: True if the song was added to the app queue, otherwise False.
    """
    # Ensure song is present in song table; get last_played (or None)
    song = sp.search_song(song_id)
    if not song:
        # Could be removed/invalid track or API error.
        raise Exception("Track not found on Spotify.")
    print(song)
    last_played = db.add_song(song)

    # Reject if already in app queue
    if db.check_song_in_app_queue(song):
        return False

    # Reject if played within last 30 minutes (UTC)
    if last_played is not None:
        now_utc = datetime.now(timezone.utc)
        if now_utc - last_played < timedelta(minutes=30):
            return False

    # Otherwise, add to app queue
    return db.add_song_to_app_queue(song)


def vote_song(song: Song, client_id: str, vote: int) -> bool:
    """
    Forward a (possibly overwriting) vote to the DB layer.

    :param song: Target song.
    :param client_id: Unique client identifier.
    :param vote: Integer vote value.
    :return: True if the vote was recorded/overwritten successfully, False otherwise.
    """
    return db.vote_song(song, client_id, vote)


def _pick_next_song() -> Optional[Song]:
    """
    Pick the next song according to the rules:
      - Prefer the song with the highest sum of votes.
      - If there are no votes, choose the oldest song in the app queue.
      - If neither yields a song, return None.

    :return: The selected Song or None.
    """
    # Try by votes first
    most_voted_song_id = db.get_most_voted_song()
    if most_voted_song_id:
        candidate = db.get_song(most_voted_song_id)
        if candidate is not None:
            return candidate

    # Fallback to oldest song in app queue
    oldest = db.get_oldest_song()
    if oldest is not None:
        return oldest

    return None


def check_sp_queue() -> bool:
    """
    Check the Spotify queue and ensure the "next" slot is filled by our app logic.

    Logic:
      - Call sp.get_queue() which returns the currently playing and the next song as a list of Songs.
        If there is ONLY ONE song in the returned list, it means there's no "next" song.
      - If there is no next song:
          * Select the song with the highest vote sum (db.get_most_voted_song());
            if none, select the oldest queued song (db.get_oldest_song()).
          * Add the chosen song to the Spotify queue (sp.add_song_to_queue). If this fails, return False.
          * Attempt to remove it from the app queue (db.remove_song_from_app_queue). If this fails, continue.
          * Clear votes for that song (db.clear_votes).
          * Update last_played to now in UTC (db.set_last_played).
          * Return True.
      - Otherwise (a next song already exists), return False.

    :return: True if a song was successfully added to the Spotify queue by this function; False otherwise.
    """
    queue = sp.get_queue()  # List[Song], length 1 => only "currently playing", length >= 2 => "next" exists
    last_added_song_ids = db.get_last_added_song_ids(2)

    if not isinstance(queue, list):
        # Defensive: if API contract is broken, do nothing.
        return False

    # If only one song -> no "next" song is scheduled
    if len(last_added_song_ids) <= 1 or len([1 for song in queue if song.song_id in last_added_song_ids]) <= 1:
        next_song = _pick_next_song()
        if next_song is None:
            # Nothing to add
            return False

        # Try to add to Spotify queue
        added = sp.add_song_to_queue(next_song)
        if not added:
            return False

        try:
            db.add_last_added_song_id(next_song.song_id)
        except Exception:
            pass
        # Best-effort cleanup/update sequence
        try:
            db.remove_song_from_app_queue(next_song)
        except Exception:
            # Per spec: if removal failed, continue with the rest
            pass

        # Clear votes (ignore result by spec; continue regardless)
        try:
            db.clear_votes(next_song)
        except Exception:
            pass

        # Update last_played to now (UTC)
        now_utc = datetime.now(timezone.utc)
        try:
            db.set_last_played(next_song, now_utc)
        except Exception:
            # Non-fatal for business logic here
            pass

        return True

    # A "next" song already exists
    return False
