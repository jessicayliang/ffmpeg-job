import logging
import os
import re

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/secrets/sa.json")


def _get_drive_service():
    """Build an authenticated Drive API client from the service account key."""
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Service account key not found at {SERVICE_ACCOUNT_FILE}. "
            "Set GOOGLE_APPLICATION_CREDENTIALS to the path of your SA key JSON."
        )
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)

def extract_file_id(drive_url: str) -> str:
    """Pull the file ID from various Google Drive URL formats."""
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, drive_url)
        if m:
            return m.group(1)
    raise ValueError(f"Could not extract file ID from URL: {drive_url}")


def download_video(file_id: str, dest_path: str) -> None:
    """Download a Drive file using the service account (bypasses anonymous viewer limits)."""
    logger.info(f"Downloading file_id={file_id} via Drive API")
    service = _get_drive_service()

    # Verify the file is accessible and log its name/size for debugging
    try:
        meta = service.files().get(fileId=file_id, fields="name,size,mimeType").execute()
        logger.info(f"File metadata: name={meta.get('name')!r} size={meta.get('size')} mimeType={meta.get('mimeType')}")
    except Exception as e:
        raise PermissionError(
            f"Could not access file_id={file_id} with the service account. "
            f"Share the file with your service account email and try again. ({e})"
        )

    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            logger.info(f"Download progress: {int(status.progress() * 100)}%")

    size_mb = os.path.getsize(dest_path) / 1e6
    logger.info(f"Download complete: {dest_path} ({size_mb:.1f} MB)")
