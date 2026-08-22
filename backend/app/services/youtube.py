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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logging import logger

# Scopes requested when minting a NEW token. yt-analytics.readonly is what makes
# the country breakdown in the weekly report possible — view/like counts are public
# and need no OAuth, but geography is owner-only data the Data API cannot return at
# all. Existing upload-only tokens keep working; they just skip geography until
# re-authorized (see services.performance._analytics_service).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _load_credentials():
    from google.oauth2.credentials import Credentials  # noqa: PLC0415
    from google.auth.transport.requests import Request  # noqa: PLC0415

    token_file = Path(settings.youtube_token_file)
    if not token_file.exists():
        raise RuntimeError(
            "YouTube not authorized. Complete the OAuth flow via /publish/authorize first."
        )
    # Scopes come from the token file rather than SCOPES above: the live token was
    # minted upload-only, and pinning it to a list that now includes analytics would
    # send a scope on refresh that it was never granted. Uploads must not break to
    # add a reporting feature.
    creds = Credentials.from_authorized_user_file(str(token_file))
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


def next_publish_slot(
    slot: str | None = None, offset_minutes: int = 0, now: datetime | None = None
) -> str | None:
    """RFC 3339 timestamp for the next `HH:MM` UTC slot, or None to publish now.

    Returns None — meaning "publish immediately, as before" — in three cases:
    no slot configured, an unparseable slot, or a slot too far away to wait for.

    That last case is the important one. The slot is today's if it is still ahead
    of us and tomorrow's otherwise, so a run that overruns its slot by a minute
    would otherwise sit on a finished same-day news Short for 23 hours. Past
    `youtube_publish_max_lead_hours` we stop scheduling and just publish.

    `offset_minutes` staggers the videos within a single run; two Shorts landing
    in the same second only compete with each other for feed impressions.
    """
    slot = settings.youtube_publish_slot if slot is None else slot
    if not slot.strip():
        return None
    try:
        hour, _, minute = slot.strip().partition(":")
        target_h, target_m = int(hour), int(minute)
        if not (0 <= target_h < 24 and 0 <= target_m < 60):
            raise ValueError(f"out of range: {slot}")
    except ValueError as e:
        logger.warning("YOUTUBE_PUBLISH_SLOT {!r} is not HH:MM ({}) — publishing now", slot, e)
        return None

    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    target += timedelta(minutes=offset_minutes)
    if target <= now:
        target += timedelta(days=1)

    lead = target - now
    if lead > timedelta(hours=settings.youtube_publish_max_lead_hours):
        logger.warning(
            "slot {} is {:.1f}h away (run overran it) — publishing now instead",
            slot,
            lead.total_seconds() / 3600,
        )
        return None
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def check_credentials() -> None:
    """Force a real token refresh, raising if the grant is dead.

    The stored access token is always past its 1-hour expiry by the time a
    scheduled run starts, so this performs the same refresh round-trip the
    upload would. It spends no YouTube API quota, which makes it safe to call
    as a preflight — and it catches the one failure that wastes an entire run:
    a refresh token Google has expired or revoked, which otherwise only
    surfaces after ~9 minutes of LLM, image and render work.
    """
    _load_credentials()


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy: str | None = None,
    thumbnail_path: str | None = None,
    category_id: str = "22",  # People & Blogs
    publish_at: str | None = None,
    language: str | None = None,
) -> str:
    """Upload a video, optionally set a thumbnail. Returns the YouTube video id.

    `publish_at` (RFC 3339, e.g. "2026-08-20T22:30:00Z") hands the publish moment
    to YouTube instead of leaving it to whenever the upload happens to finish. The
    API only honours it on a private video, so passing it forces privacyStatus to
    "private" regardless of `privacy` — YouTube flips the video public itself at
    that instant. This is what decouples the publish slot from GitHub Actions
    queue delay, which had been running as high as 2h39m.

    `language` sets both defaultLanguage (title/description) and
    defaultAudioLanguage (narration). Both were previously unset, leaving YouTube
    to infer the language of every upload.
    """
    from googleapiclient.http import MediaFileUpload  # noqa: PLC0415

    service = _build_service()
    lang = language or settings.youtube_default_language
    snippet = {
        "title": title[:100],
        "description": description[:5000],
        "tags": tags[:15],
        "categoryId": category_id,
    }
    if lang:
        snippet["defaultLanguage"] = lang
        snippet["defaultAudioLanguage"] = lang

    status = {
        "privacyStatus": (privacy or settings.youtube_upload_privacy),
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        # "private" is not a preference here, it is the API's precondition: setting
        # publishAt on an already-public video is rejected, and on an unlisted one
        # it is silently ignored.
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
        logger.info("scheduling publish for {}", publish_at)

    body = {"snippet": snippet, "status": status}
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
