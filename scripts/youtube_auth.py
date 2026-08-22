"""One-shot YouTube OAuth authorization — standalone, no uvicorn required.

Runs a minimal local HTTP server on the exact redirect URI registered in Google
Cloud, opens your browser, completes the code exchange and writes the token file.

Using this instead of the API endpoint avoids a whole class of confusion: the app
server has to be restarted to pick up code changes, and a stale process silently
reproduces already-fixed errors.

Usage:
    python scripts/youtube_auth.py

Stop uvicorn first — this needs port 8000.
"""

from __future__ import annotations

import json
import os
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Google permits http://localhost redirects; oauthlib still needs this opt-in.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from app.core.config import settings  # noqa: E402

# Shared with the API's /publish/authorize flow so both mint identical tokens.
# Includes yt-analytics.readonly, which the weekly report needs for the country
# breakdown — re-run this script and refresh YOUTUBE_TOKEN_JSON to pick it up.
from app.services.youtube import SCOPES  # noqa: E402

REDIRECT_PATH = "/api/publish/oauth/callback"
PORT = 8000

_result: dict[str, str] = {}
_expected_state = ""

# How long to wait for the human to finish the consent screen. Generous on
# purpose: the unverified-app interstitial has a hidden "Advanced" step that is
# easy to miss, and expiring mid-consent means starting the whole flow over.
CALLBACK_TIMEOUT_S = 900


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        state = (params.get("state") or [""])[0]
        if state != _expected_state:
            # A stale callback from an earlier authorization attempt: browsers
            # replay these from history, tab restore and address-bar prefetch,
            # and one used to land before the real one and abort the run with
            # MismatchingStateError. Reject it and keep listening.
            print("  (ignoring a stale callback from an earlier attempt)")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<h2>Stale authorization link.</h2><p>This tab is left over from "
                b"an earlier attempt. Close it and use the newly opened tab.</p>"
            )
            return

        _result["url"] = f"http://localhost:{PORT}{self.path}"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<h2>YouTube authorized.</h2><p>You can close this tab and "
            b"return to the terminal.</p>"
        )

    def log_message(self, *args):  # silence per-request stderr noise
        pass


def main() -> int:
    from google_auth_oauthlib.flow import Flow

    secrets_file = Path(settings.youtube_client_secrets_file)
    if not secrets_file.exists():
        print(f"ERROR: client secrets not found at {secrets_file}")
        return 1

    redirect_uri = f"http://localhost:{PORT}{REDIRECT_PATH}"
    flow = Flow.from_client_secrets_file(
        str(secrets_file), scopes=SCOPES, redirect_uri=redirect_uri
    )
    global _expected_state
    auth_url, _expected_state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )

    try:
        server = HTTPServer(("localhost", PORT), _Handler)
    except OSError as e:
        print(f"ERROR: cannot bind port {PORT} ({e}).")
        print("Stop the uvicorn server first, then re-run this script.")
        return 1

    print("\nOpening your browser to authorize YouTube access.")
    print("If it doesn't open, paste this URL manually:\n")
    print(auth_url + "\n")
    print("(You'll see an 'unverified app' warning -> Advanced -> Go to ...)\n")
    webbrowser.open(auth_url)

    print(f"Waiting for the callback on {redirect_uri} ...")
    # Keep serving until the callback carrying OUR state arrives: favicon hits,
    # prefetches and stale replays each consume a request, so a single
    # handle_request() would lose the race to any of them.
    server.timeout = 5  # makes handle_request() return so the deadline is checked
    deadline = time.monotonic() + CALLBACK_TIMEOUT_S
    while "url" not in _result and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if "url" not in _result:
        print(f"ERROR: no callback received within {CALLBACK_TIMEOUT_S}s.")
        return 1

    flow.fetch_token(authorization_response=_result["url"])
    creds = flow.credentials

    token_file = Path(settings.youtube_token_file)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")

    data = json.loads(creds.to_json())
    print(f"\nToken saved to {token_file}")
    print(f"  refresh_token present : {bool(data.get('refresh_token'))}")
    print(f"  scopes                : {data.get('scopes')}")
    print(f"  expiry                : {data.get('expiry')}")
    if not data.get("refresh_token"):
        print(
            "\nWARNING: no refresh token. Uploads will stop working in ~1 hour.\n"
            "Revoke access at https://myaccount.google.com/permissions and re-run."
        )
        return 1
    print("\nYouTube authorization complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
