# api.py

from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple
import asyncio
import contextlib

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field

import business_logic as bl
import spotify_api as sp
import db_api as db
from song import Song

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler for FastAPI (startup + shutdown replacement).
    Periodically runs business_logic.check_sp_queue() every 10 seconds.
    """
    stop_event = asyncio.Event()

    async def periodic_check():
        while not stop_event.is_set():
            try:
                result = bl.check_sp_queue()
                if result:
                    logger.info("check_sp_queue: added song to Spotify queue ✅")
            except Exception as e:
                logger.error("Error in check_sp_queue: %s", e)
            await asyncio.sleep(10)

    # Start background task
    task = asyncio.create_task(periodic_check())
    logger.info("Background task started (check_sp_queue every 10 s)")
    try:
        yield
    finally:
        # Graceful shutdown
        stop_event.set()
        await task
        logger.info("Background task stopped")

app = FastAPI(title="Spotify Party Queue API", version="1.0.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # oder z. B. ["http://127.0.0.1:5500"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --------------------------- Schemas ---------------------------

class SongOut(BaseModel):
    id: str
    name: str
    artist: str
    vote_sum: int
    client_vote: int = 0

    @classmethod
    def from_item(cls, item: Tuple[Song, int, Optional[int]]) -> "SongOut":
        s, vote_sum, client_vote = item
        return cls(
            id=str(s.song_id),
            name=str(s.name),
            artist=str(s.artist),
            vote_sum=int(vote_sum),
            client_vote=client_vote
        )


class AddSongBody(BaseModel):
    song_link: str = Field(..., description="Spotify track link or URI")


class VoteBody(BaseModel):
    song_id: str = Field(..., description="Spotify track ID")
    vote: int = Field(..., description="Integer vote value")


# --------------------------- Helpers ---------------------------

_SPOTIFY_TRACK_ID_RE = re.compile(
    r"""
    (?:
        spotify:track:(?P<id1>[A-Za-z0-9]+)                 # spotify:track:<id>
        |
        open\.spotify\.com/track/(?P<id2>[A-Za-z0-9]+)      # https://open.spotify.com/track/<id>
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_track_id(link_or_uri: str) -> Optional[str]:
    """Extract Spotify track ID from URI/URL."""
    if not link_or_uri:
        return None
    m = _SPOTIFY_TRACK_ID_RE.search(link_or_uri)
    if not m:
        return None
    return m.group("id1") or m.group("id2")


# --------------------------- Endpoints ---------------------------

@app.get("/queue", response_model=List[SongOut])
def get_queue(x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")) -> List[SongOut]:
    """
    Returns the current application queue (DB), sorted by vote_sum DESC (tie: oldest first),
    including the vote_sum & client_vote for each item.
    """
    items = bl.get_queue(client_id=x_client_id)  # List[Tuple[Song, int, int]]
    return [SongOut.from_item(it) for it in items]

@app.post("/queue", response_model=bool)
def add_song_to_app_queue(body: AddSongBody) -> bool:
    """
    Adds a song (by Spotify link/URI) to the application queue via business logic.
    Steps:
      - Parse track_id
      - Fetch Song details from Spotify
      - business_logic.add_song_to_app_queue(song)
    Returns True if the song was added, otherwise False.
    """
    track_id = extract_track_id(body.song_link)
    if not track_id:
        raise HTTPException(status_code=400, detail="Invalid Spotify track link/URI.")

    try:
        ok = bl.add_song_to_app_queue(track_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return bool(ok)


@app.post("/vote", response_model=bool)
def vote_song(
    body: VoteBody,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id"),
) -> bool:
    """
    Records a vote for a given song ID, restricted by client id (required via header 'X-Client-Id').
    - If the client voted before for that song, the vote is overwritten (db-level logic).
    - Voting is only effective if the song is currently in the app_queue.
    Returns True/False.
    """
    client_id = x_client_id
    if not client_id:
        raise HTTPException(status_code=400, detail="Missing 'X-Client-Id' header.")

    # Try to resolve the Song from DB; if unknown, fetch from Spotify for metadata.
    song = db.get_song(body.song_id)
    if song is None:
        sp_song = sp.search_song(body.song_id)
        if not sp_song:
            raise HTTPException(status_code=404, detail="Song not found on Spotify.")
        song = sp_song

    ok = bl.vote_song(song, client_id, body.vote)
    return bool(ok)


# --------------------------- Run Hint ---------------------------
# Run locally:
#   uvicorn api:app --reload --port 8000
#
# Example calls:
#   GET queue (from DB/application queue):
#     curl http://127.0.0.1:8000/queue
#
#   POST add to app queue:
#     curl -X POST http://127.0.0.1:8000/queue \
#          -H "Content-Type: application/json" \
#          -d '{"song_link":"https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp"}'
#
#   POST vote (with client id restriction):
#     curl -X POST http://127.0.0.1:8000/vote \
#          -H "Content-Type: application/json" \
#          -H "X-Client-Id: your-client-123" \
#          -d '{"song_id":"3n3Ppam7vgaVa1iaRUc9Lp","vote":1}'
