from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from auth.utils import (
    get_auth_url, 
    get_creds_from_code,
    save_credentials,
    load_credentials,
    refresh_credentials
)
from urllib.parse import urlencode
import os
import logging

# Setup logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

router = APIRouter()

@router.get("/auth")
async def auth(request: Request):
    try:
        redirect_uri = str(request.url_for("auth_callback"))
        logger.debug(f"Auth endpoint called. Redirect URI: {redirect_uri}")
        
        auth_url = get_auth_url(redirect_uri)
        logger.info(f"Redirecting to Google auth URL: {auth_url}")
        
        return RedirectResponse(auth_url)
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {e}")
        raise HTTPException(status_code=500, detail="Unable to generate auth URL.")

@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(code: str, request: Request):
    try:
        redirect_uri = str(request.url_for("auth_callback"))
        logger.debug(f"Received auth callback with code: {code}")
        
        creds = get_creds_from_code(code, redirect_uri)
        
        # Static user ID for demo; replace in production
        user_id = "user_123"
        save_credentials(user_id, creds)

        logger.info(f"User {user_id} authenticated successfully.")
        
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8501")
        success_url = f"{frontend_url}?auth_success=true"
        logger.debug(f"Redirecting to frontend success URL: {success_url}")
        
        return RedirectResponse(success_url)
    except Exception as e:
        logger.error(f"Authentication callback failed: {e}")
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8501")
        error_url = f"{frontend_url}?auth_error={str(e)}"
        logger.debug(f"Redirecting to frontend error URL: {error_url}")
        return RedirectResponse(error_url)

@router.get("/auth/status")
async def auth_status(user_id: str = "user_123"):
    try:
        logger.debug(f"Checking authentication status for user: {user_id}")
        creds = load_credentials(user_id)
        if not creds:
            logger.info(f"No credentials found for user {user_id}")
            return {"authenticated": False}
        
        refreshed = refresh_credentials(creds)
        if refreshed:
            logger.info(f"Credentials refreshed for user {user_id}")
            save_credentials(user_id, creds)
        else:
            logger.debug(f"No refresh needed for user {user_id}")

        return {
            "authenticated": True,
            "expired": creds.expired,
            "scopes": creds.scopes
        }
    except Exception as e:
        logger.error(f"Error checking auth status for user {user_id}: {e}")
        return {"authenticated": False, "error": str(e)}
