"""Google OAuth authentication for Calendar API."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _load_json_from_env_or_file(env_var: str, file_path: str) -> dict | None:
    """Load JSON from a base64-encoded env var, falling back to a file."""
    env_value = os.environ.get(env_var)
    if env_value:
        try:
            return json.loads(base64.b64decode(env_value))
        except Exception:
            # Try as plain JSON
            try:
                return json.loads(env_value)
            except Exception:
                logger.warning(f"Failed to parse {env_var} env var")
    path = Path(file_path)
    if path.exists():
        return json.loads(path.read_text())
    return None


def get_google_credentials(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> Credentials:
    """Get or refresh Google OAuth credentials.

    Supports loading credentials/token from env vars (GOOGLE_CREDENTIALS_JSON,
    GOOGLE_TOKEN_JSON) as base64-encoded JSON for containerized deployments.

    Args:
        credentials_path: Path to the OAuth client credentials JSON.
        token_path: Path to save/load the token.

    Returns:
        Valid Google Credentials object.
    """
    creds = None

    token_data = _load_json_from_env_or_file("GOOGLE_TOKEN_JSON", token_path)
    if token_data:
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
            # Save refreshed token back to file if possible
            _save_token(creds, token_path)
        else:
            creds_data = _load_json_from_env_or_file("GOOGLE_CREDENTIALS_JSON", credentials_path)
            if not creds_data:
                raise FileNotFoundError(
                    f"Google credentials not found in GOOGLE_CREDENTIALS_JSON env var or {credentials_path}\n"
                    "Run 'schedulebot init' or see docs/setup-google.md"
                )
            # Write temp file for InstalledAppFlow (it requires a file path)
            tmp_creds = Path(credentials_path)
            if not tmp_creds.exists():
                tmp_creds.write_text(json.dumps(creds_data))
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
            _save_token(creds, token_path)

    return creds


def _save_token(creds: Credentials, token_path: str) -> None:
    """Save token to file."""
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }
    try:
        Path(token_path).write_text(json.dumps(token_data, indent=2))
        logger.info(f"Token saved to {token_path}")
    except OSError:
        logger.warning(f"Could not save token to {token_path}")
