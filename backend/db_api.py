# db_api.py

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from pathlib import Path

from song import Song

DEFAULT_DB_DIR = Path(os.getenv("DB_DIR", "sqlite"))
DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = os.getenv("DB_FILE", str(DEFAULT_DB_DIR / "spotify_party_queue.sqlite3"))

_conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=10.0)
_conn.row_factory = sqlite3.Row
# robustere Defaults fuer gleichzeitigen Zugriff
_conn.execute("PRAGMA foreign_keys = ON")
_conn.execute("PRAGMA journal_mode = WAL")
_conn.execute("PRAGMA synchronous = NORMAL")

def reset_db() -> None:
    with _conn:
        _conn.execute("DROP TABLE IF EXISTS last_added_songs;")
        _conn.execute("DROP TABLE IF EXISTS votes;")
        _conn.execute("DROP TABLE IF EXISTS app_queue;")
        _conn.execute("DROP TABLE IF EXISTS song;")


def _init_db() -> None:
    with _conn:
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS song (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                artist TEXT NOT NULL,
                last_played TEXT
            );

            CREATE TABLE IF NOT EXISTS app_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id TEXT NOT NULL UNIQUE,
                FOREIGN KEY (song_id) REFERENCES song(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS votes (
                app_queue_id INTEGER NOT NULL,
                client_id TEXT NOT NULL,
                vote INTEGER NOT NULL,
                PRIMARY KEY (app_queue_id, client_id),
                FOREIGN KEY (app_queue_id) REFERENCES app_queue(id) ON DELETE CASCADE
            );
                
            CREATE TABLE IF NOT EXISTS last_added_songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_id TEXT NOT NULL,
                FOREIGN KEY (song_id) REFERENCES song(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_votes_app_queue_id
                ON votes(app_queue_id);
            """
        )


_init_db()


# -------------- Helpers --------------

def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to UTC ISO-8601 with 'Z'."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _from_utc_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse UTC ISO-8601 that may end with 'Z'."""
    if s is None:
        return None
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _get_app_queue_id(song_id: str) -> Optional[int]:
    cur = _conn.execute("SELECT id FROM app_queue WHERE song_id = ?", (song_id,))
    row = cur.fetchone()
    return int(row["id"]) if row else None


# -------------- API --------------

def add_song(song: Song) -> Optional[datetime]:
    """
    Add song to table 'song' if not already there.
    Return last_played (UTC) or None if not set yet.
    """
    cur = _conn.execute("SELECT last_played FROM song WHERE id = ?", (song.song_id,))
    row = cur.fetchone()
    if row:
        return _from_utc_iso(row["last_played"])

    _conn.execute(
        "INSERT INTO song (id, name, artist, last_played) VALUES (?, ?, ?, ?)",
        (song.song_id, song.name, song.artist, None),
    )
    _conn.commit()
    return None


def get_song(song_id: str) -> Optional[Song]:
    """Return Song by id or None if not found."""
    cur = _conn.execute(
        "SELECT id, name, artist FROM song WHERE id = ?", (song_id,)
    )
    row = cur.fetchone()
    if not row:
        return None
    return Song(row["id"], row["name"], row["artist"])


def set_last_played(song: Song, last_played: datetime) -> datetime:
    """Set last_played (stored in UTC) and return the value set."""
    iso = _to_utc_iso(last_played)
    _conn.execute(
        "UPDATE song SET last_played = ? WHERE id = ?",
        (iso, song.song_id),
    )
    _conn.commit()
    # Return normalized UTC datetime
    return _from_utc_iso(iso)  # type: ignore[arg-type]


def add_song_to_app_queue(song: Song) -> bool:
    """
    Add song to 'app_queue'. Returns True on success, False on duplicate / FK error.
    """
    try:
        _conn.execute(
            "INSERT INTO app_queue (song_id) VALUES (?)",
            (song.song_id,),
        )
        _conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_app_queue(client_id: Optional[str] = None) -> List[Tuple[Song, int, int]]:
    """
    Return list of (Song, vote_sum, client_vote) for all songs currently in 'app_queue',
    ordered by vote_sum DESC, then by queue position (oldest first) as tie-breaker.

    - vote_sum: Sum over all votes for that song (int)
    - client_vote: Sum of votes by the given client_id for that song (usually -1, 0, or 1); None if no vote from client
    """
    cur = _conn.execute(
        """
        SELECT s.id,
               s.name,
               s.artist,
               COALESCE(SUM(v.vote), 0)                       AS vote_sum,
               SUM(CASE WHEN v.client_id = ? THEN v.vote END) AS client_vote,
               MIN(aq.id)                                     AS qid
        FROM app_queue aq
                 JOIN song s ON s.id = aq.song_id
                 LEFT JOIN votes v ON v.app_queue_id = aq.id
        GROUP BY aq.id
        ORDER BY vote_sum DESC, qid ASC
        """,
        (client_id,)  # darf None sein → Bedingung matcht nie → client_vote wird NULL
    )
    rows = cur.fetchall()
    result: List[Tuple[Song, int, int]] = []
    for r in rows:
        s = Song(r["id"], r["name"], r["artist"])
        vote_sum = int(r["vote_sum"])
        client_vote = r["client_vote"]
        client_vote = int(client_vote) if client_vote is not None else 0
        result.append((s, vote_sum, client_vote))
    return result

def check_song_in_app_queue(song: Song) -> bool:
    """Check if the given song is in 'app_queue'."""
    cur = _conn.execute(
        "SELECT 1 AS present FROM app_queue WHERE song_id = ? LIMIT 1",
        (song.song_id,),
    )
    return cur.fetchone() is not None


def vote_song(song: Song, client_id: str, vote: int) -> bool:
    """
    Add or overwrite a vote for the given song by client_id.
    Returns False if the song is not in the app queue.
    """
    aq_id = _get_app_queue_id(song.song_id)
    if aq_id is None:
        return False

    _conn.execute(
        """
        INSERT INTO votes (app_queue_id, client_id, vote)
        VALUES (?, ?, ?)
        ON CONFLICT(app_queue_id, client_id)
        DO UPDATE SET vote = excluded.vote
        """,
        (aq_id, client_id, vote),
    )
    _conn.commit()
    return True


def get_most_voted_song() -> Optional[str]:
    """
    Return song_id of the song in 'app_queue' with highest sum of votes.
    If there are no votes at all, return None.
    If sums tie, return one of them (deterministic: lowest queue id).
    """
    # If absolutely no votes exist, return None (per spec).
    cur = _conn.execute("SELECT COUNT(*) AS c FROM votes")
    if int(cur.fetchone()["c"]) == 0:
        return None

    cur = _conn.execute(
        """
        SELECT aq.song_id, COALESCE(SUM(v.vote), 0) AS total, MIN(aq.id) AS qid
        FROM app_queue aq
        LEFT JOIN votes v ON v.app_queue_id = aq.id
        GROUP BY aq.id
        ORDER BY total DESC, qid ASC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    return row["song_id"] if row else None


def clear_votes(song: Song) -> bool:
    """
    Remove all votes for the given song from 'votes'.
    If the song is no longer in app_queue, this is a no-op and returns True.
    """
    aq_id = _get_app_queue_id(song.song_id)
    if aq_id is None:
        return True
    _conn.execute("DELETE FROM votes WHERE app_queue_id = ?", (aq_id,))
    _conn.commit()
    return True


def get_oldest_song() -> Optional[Song]:
    """
    Return the oldest (lowest id) song from 'app_queue' as a Song, or None if queue is empty.
    """
    cur = _conn.execute(
        """
        SELECT s.id, s.name, s.artist
        FROM app_queue aq
        JOIN song s ON s.id = aq.song_id
        ORDER BY aq.id ASC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    return Song(row["id"], row["name"], row["artist"])


def remove_song_from_app_queue(song: Song) -> Optional[Song]:
    """
    Remove the given song from 'app_queue'. Return the removed song on success, otherwise None.
    """
    cur = _conn.execute("DELETE FROM app_queue WHERE song_id = ?", (song.song_id,))
    _conn.commit()
    return song if cur.rowcount and cur.rowcount > 0 else None

def get_last_added_song_ids(num_songs: int) -> List[str]:
    """
    Returns a list of the ids of the last num_songs added songs to the Spotify queue.
    Order: most recently added first.
    """
    if num_songs <= 0:
        return []
    cur = _conn.execute(
        """
        SELECT song_id
        FROM last_added_songs
        ORDER BY id DESC
        LIMIT ?
        """,
        (num_songs,),
    )
    return [row["song_id"] for row in cur.fetchall()]


def add_last_added_song_id(song_id: str) -> bool:
    """
    Adds a song_id to the last_added_songs table.
    Respects the FOREIGN KEY to song(id); will fail if the song does not exist in 'song'.
    Returns True on success, False on constraint/other errors.
    """
    try:
        _conn.execute(
            "INSERT INTO last_added_songs (song_id) VALUES (?)",
            (song_id,),
        )
        _conn.commit()
        return True
    except sqlite3.IntegrityError:
        # e.g., foreign key violation if song_id isn't present in 'song'
        return False
    except Exception:
        return False