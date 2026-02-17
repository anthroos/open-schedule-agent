"""Google OAuth authentication for Calendar API."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_google_credentials(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> Credentials:
    """Get or refresh Google OAuth credentials.

    Args:
        credentials_path: Path to the OAuth client credentials JSON.
        token_path: Path to save/load the token.

    Returns:
        Valid Google Credentials object.
    """
    creds = None
    token_file = Path(token_path)

    if token_file.exists():
        token_data = json.loads(token_file.read_text())
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes", SCOPES),
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google token")
            creds.refresh(Request())
        else:
            if not Path(credentials_path).exists():
                raise FileNotFoundError(
                    f"Google credentials file not found: {credentials_path}\n"
                    "Run 'schedulebot init' or see docs/setup-google.md"
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes or SCOPES),
        }
        token_file.write_text(json.dumps(token_data, indent=2))
        logger.info(f"Token saved to {token_path}")

    return creds
