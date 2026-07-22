"""YouTube Data API v3 publishing.

OAuth flow:
1. Create an OAuth Desktop client in Google Cloud (YouTube Data API v3 enabled),
   download the client secret JSON to YOUTUBE_CLIENT_SECRETS_FILE.
2. Call `get_authorization_url()` / `finish_authorization()` (wired to /publish
   routes) once to mint a refresh token stored at YOUTUBE_TOKEN_FILE.
3. `upload_video()` reuses that token forever (auto-refreshes).

Uploads default to PRIVATE. Nothing is uploaded without an APPROVED project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import logger

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _load_credentials():
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from google.auth.transport.requests import Request  # noqa: PLC0415

    token_file = Path(settings.youtube_token_file)
    if not token_file.exists():
        raise RuntimeError(
            "YouTube not authorized. Complete the OAuth flow via /publish/authorize first."
        )
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return creds


def _build_service():
    from googleapiclient.discovery import build  # noqa: PLC0415

    return build("youtube", "v3", credentials=_load_credentials(), cache_discovery=False)


def _allow_insecure_localhost(redirect_uri: str) -> None:
    """Permit the OAuth code exchange over plain HTTP, but only on localhost.

    oauthlib hard-refuses non-HTTPS redirect URIs (InsecureTransportError). Google
    itself allows http://localhost redirects precisely because the traffic never
    leaves the machine, so this opt-in is safe for local development — and it is
    deliberately NOT set for any other host, where plaintext OAuth would expose the
    authorization code in transit.
    """
    host = urlparse(redirect_uri).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    else:
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)


def get_authorization_url(redirect_uri: str) -> tuple[str, str]:
    """Returns (auth_url, state). Persist state to validate the callback."""
    from google_auth_oauthlib.flow import Flow  # noqa: PLC0415

    _allow_insecure_localhost(redirect_uri)

    flow = Flow.from_client_secrets_file(
        settings.youtube_client_secrets_file, scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return auth_url, state


def finish_authorization(redirect_uri: str, authorization_response: str) -> None:
    from google_auth_oauthlib.flow import Flow  # noqa: PLC0415

    _allow_insecure_localhost(redirect_uri)
    flow = Flow.from_client_secrets_file(
        settings.youtube_client_secrets_file, scopes=SCOPES, redirect_uri=redirect_uri
    )
    flow.fetch_token(authorization_response=authorization_response)
    token_file = Path(settings.youtube_token_file)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(flow.credentials.to_json())
    logger.info("YouTube OAuth token stored at {}", token_file)


def is_authorized() -> bool:
    return Path(settings.youtube_token_file).exists()


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy: str | None = None,
    thumbnail_path: str | None = None,
    category_id: str = "22",  # People & Blogs
) -> str:
    """Upload a video, optionally set a thumbnail. Returns the YouTube video id."""
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

    service = _build_service()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:15],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": (privacy or settings.youtube_upload_privacy),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/*")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("upload progress {:.0f}%", status.progress() * 100)

    video_id = response["id"]
    logger.info("uploaded video id={}", video_id)

    if thumbnail_path and Path(thumbnail_path).exists():
        try:
            service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path, mimetype="image/png"),
            ).execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("thumbnail set failed (needs verified channel): {}", e)

    return video_id
