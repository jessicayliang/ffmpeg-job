import logging
import os

import requests
from fastapi import HTTPException, Request
from typing import Optional

logger = logging.getLogger(__name__)

ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if e.strip()
}
if ALLOWED_EMAILS:
    logger.info(f"Email allowlist active: {ALLOWED_EMAILS}")
else:
    logger.warning("ALLOWED_EMAILS is not set — no email restrictions will be enforced")


def _get_caller_email(request: Request) -> Optional[str]:
    """Verify the Bearer token with Google and return the associated email."""
    auth = request.headers.get("Authorization") or request.headers.get("X-Forwarded-Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ")

    # Verify with Google's tokeninfo endpoint
    try:
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            params={"access_token": token},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning(f"Token verification failed: {resp.status_code} {resp.text}")
            return None
        data = resp.json()
        email = data.get("email", "").lower()
        logger.info(f"Verified token for email: {email}")
        return email or None
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        return None


def check_allowed(request: Request) -> str:
    """Raise 403 if the caller's email is not in the allowlist. Returns the email."""
    if not ALLOWED_EMAILS:
        return "unknown (no allowlist set)"
    email = _get_caller_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Could not determine caller identity.")
    if email not in ALLOWED_EMAILS:
        logger.warning(f"Rejected request from unlisted email: {email}")
        raise HTTPException(status_code=403, detail=f"{email} is not authorised to use this service.")
    return email