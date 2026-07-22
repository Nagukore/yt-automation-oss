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
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Google permits http://localhost redirects; oauthlib still needs this opt-in.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

from app.core.config import settings  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_PATH = "/api/publish/oauth/callback"
PORT = 8000

_result: dict[str, str] = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
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
    auth_url, _state = flow.authorization_url(
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
    server.handle_request()  # serves exactly one request, then returns
    server.server_close()

    if "url" not in _result:
        print("ERROR: no callback received.")
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
