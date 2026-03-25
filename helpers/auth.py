import base64
import json
import logging
import os

from fastapi import HTTPException, Request

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


def _get_caller_email(request: Request) -> str | None:
    """Extract the caller's email from the Cloud Run-injected identity token.

    Cloud Run forwards the verified token in the X-Forwarded-Authorization header.
    The token is a JWT — we just base64-decode the payload, no signature verification
    needed since Cloud Run already verified it before the request reached us.
    """
    auth = request.headers.get("X-Forwarded-Authorization") or request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ")
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("email", "").lower()
    except Exception:
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