"""TikTok Content Posting API client (the official automation path).

Requires a TikTok for Developers app with the Content Posting API product
and `video.upload` / `video.publish` scopes. Two posting modes:

- inbox  — video lands in the user's TikTok drafts; they tap Post in the
           app. Works for unaudited apps.
- direct — publishes without user action. NOTE: TikTok restricts content
           from *unaudited* API clients to SELF_ONLY (private) visibility;
           public direct-posting requires passing TikTok's app audit.

Tokens are stored in data/tiktok_tokens.json and auto-refreshed (access
tokens expire every 24h).
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.parse
from pathlib import Path

import httpx

from ..config import settings

log = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DIRECT_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"

CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


class TikTokError(RuntimeError):
    pass


class TikTokClient:
    def __init__(self, data_dir: Path):
        self.token_path = data_dir / "tiktok_tokens.json"
        self._state = ""

    # ------------------------------------------------------------- OAuth

    def connected(self) -> bool:
        return self.token_path.exists()

    def auth_url(self) -> str:
        self._state = secrets.token_urlsafe(16)
        params = {
            "client_key": settings.tiktok_client_key,
            "response_type": "code",
            "scope": "user.info.basic,video.upload,video.publish",
            "redirect_uri": settings.tiktok_redirect_uri,
            "state": self._state,
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        data = {
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.tiktok_redirect_uri,
        }
        resp = httpx.post(TOKEN_URL, data=data, timeout=30)
        tokens = resp.json()
        if "access_token" not in tokens:
            raise TikTokError(f"Token exchange failed: {tokens}")
        tokens["obtained_at"] = time.time()
        self.token_path.write_text(json.dumps(tokens, indent=2))
        return tokens

    def _tokens(self) -> dict:
        if not self.token_path.exists():
            raise TikTokError("TikTok account not connected")
        tokens = json.loads(self.token_path.read_text())
        # access tokens expire in ~24h; refresh with 10-minute margin
        expires_at = tokens.get("obtained_at", 0) + tokens.get("expires_in", 86400) - 600
        if time.time() >= expires_at:
            tokens = self._refresh(tokens)
        return tokens

    def _refresh(self, tokens: dict) -> dict:
        data = {
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        }
        resp = httpx.post(TOKEN_URL, data=data, timeout=30)
        fresh = resp.json()
        if "access_token" not in fresh:
            raise TikTokError(f"Token refresh failed: {fresh}")
        fresh["obtained_at"] = time.time()
        self.token_path.write_text(json.dumps(fresh, indent=2))
        return fresh

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._tokens()['access_token']}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    # ----------------------------------------------------------- posting

    def post_video(self, video_path: Path, caption: str) -> dict:
        """Upload and post/draft a video. Returns {publish_id, mode}."""
        size = video_path.stat().st_size
        if size > 1024 * 1024 * 1024:
            raise TikTokError("Video exceeds TikTok's 1 GB API limit")

        chunk_size = min(CHUNK_SIZE, size)
        total_chunks = max(1, (size + chunk_size - 1) // chunk_size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        }

        if settings.tiktok_post_mode == "direct":
            body = {
                "post_info": {
                    "title": caption[:2200],
                    "privacy_level": "SELF_ONLY",  # audited apps may use PUBLIC_TO_EVERYONE
                    "disable_comment": False,
                    "disable_duet": False,
                    "disable_stitch": False,
                },
                "source_info": source_info,
            }
            init_url = DIRECT_INIT_URL
        else:
            body = {"source_info": source_info}
            init_url = INBOX_INIT_URL

        resp = httpx.post(init_url, headers=self._headers(), json=body, timeout=60)
        payload = resp.json()
        if payload.get("error", {}).get("code") not in (None, "ok"):
            raise TikTokError(f"Init failed: {payload['error']}")
        data = payload["data"]
        upload_url = data["upload_url"]
        publish_id = data["publish_id"]

        self._upload_chunks(upload_url, video_path, size, chunk_size)
        log.info("TikTok upload complete: %s (%s mode)", publish_id, settings.tiktok_post_mode)
        return {"publish_id": publish_id, "mode": settings.tiktok_post_mode}

    def _upload_chunks(self, upload_url: str, video_path: Path,
                       size: int, chunk_size: int) -> None:
        with video_path.open("rb") as fh, httpx.Client(timeout=300) as client:
            offset = 0
            while offset < size:
                chunk = fh.read(chunk_size)
                end = offset + len(chunk) - 1
                resp = client.put(
                    upload_url,
                    content=chunk,
                    headers={
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                        "Content-Type": "video/mp4",
                    },
                )
                if resp.status_code not in (200, 201, 206):
                    raise TikTokError(f"Chunk upload failed ({resp.status_code}): {resp.text[:300]}")
                offset += len(chunk)

    def publish_status(self, publish_id: str) -> dict:
        resp = httpx.post(
            STATUS_URL, headers=self._headers(),
            json={"publish_id": publish_id}, timeout=30,
        )
        return resp.json().get("data", {})
