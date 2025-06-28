import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from pathlib import Path
import json
from typing import Dict, Optional
import logging

# Configure logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# OAuth Scopes
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_flow(redirect_uri: str = None):
    try:
        client_secret_path = Path(__file__).parent.parent / "client_secret.json"
        logger.debug(f"Initializing OAuth flow with client secret at {client_secret_path}")
        flow = Flow.from_client_secrets_file(
            client_secret_path,
            scopes=SCOPES,
            redirect_uri=redirect_uri
        )
        return flow
    except Exception as e:
        logger.error(f"Error initializing OAuth flow: {e}")
        raise

def get_auth_url(redirect_uri: str) -> str:
    try:
        logger.debug(f"Generating auth URL with redirect_uri: {redirect_uri}")
        flow = get_flow(redirect_uri)
        auth_url, _ = flow.authorization_url(prompt='consent')
        logger.info(f"Generated auth URL: {auth_url}")
        return auth_url
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {e}")
        raise

def get_creds_from_code(code: str, redirect_uri: str) -> Credentials:
    try:
        logger.debug("Fetching credentials from authorization code...")
        flow = get_flow(redirect_uri)
        flow.fetch_token(code=code)
        logger.info("Credentials fetched successfully from code.")
        return flow.credentials
    except Exception as e:
        logger.error(f"Error fetching credentials from code: {e}")
        raise

def save_credentials(user_id: str, creds: Credentials) -> None:
    """Save credentials with better error handling"""
    try:
        project_root = Path(__file__).parent.parent
        tokens_dir = project_root / "tokens"
        tokens_dir.mkdir(exist_ok=True, parents=True)

        if not tokens_dir.exists():
            raise Exception(f"Failed to create tokens directory at {tokens_dir}")

        creds_file = tokens_dir / f"{user_id}.json"
        with open(creds_file, 'w') as f:
            f.write(creds.to_json())

        logger.info(f"Credentials saved successfully for {user_id} at {creds_file}")
        logger.debug(f"Saved credentials content: {creds.to_json()}")

    except Exception as e:
        logger.error(f"Failed to save credentials for {user_id}: {str(e)}")
        raise

def load_credentials(user_id: str) -> Optional[Credentials]:
    try:
        creds_file = Path(__file__).parent.parent / "tokens" / f"{user_id}.json"

        if not creds_file.exists():
            logger.warning(f"No credentials file found for user {user_id}")
            return None

        logger.debug(f"Reading credentials from {creds_file}")
        with open(creds_file, 'r') as f:
            creds_info = json.load(f)

        if not creds_info:
            logger.error(f"Empty credentials file for user {user_id}")
            return None

        logger.info(f"Credentials loaded successfully for user {user_id}")
        return Credentials.from_authorized_user_info(creds_info)

    except Exception as e:
        logger.error(f"Error loading credentials for user {user_id}: {str(e)}")
        return None

def refresh_credentials(creds: Credentials) -> bool:
    try:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired credentials...")
            creds.refresh(Request())
            logger.info("Credentials refreshed successfully.")
            return True
        logger.debug("No need to refresh credentials.")
        return False
    except Exception as e:
        logger.error(f"Failed to refresh credentials: {e}")
        return False
