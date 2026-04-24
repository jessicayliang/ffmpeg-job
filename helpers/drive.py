import logging
import os
import re

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

logger = logging.getLogger(__name__)


def _get_drive_service(user_token: str):
    """Build a Drive API client using the user's own OAuth access token."""
    creds = Credentials(token=user_token)
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


def download_video(file_id: str, dest_path: str, user_token: str) -> None:
    """Download a Drive file using the user's own access token."""
    logger.info(f"Downloading file_id={file_id} using user token")
    service = _get_drive_service(user_token)

    try:
        meta = service.files().get(fileId=file_id, fields="name,size,mimeType",supportsAllDrives=True).execute()
        logger.info(f"File: name={meta.get('name')!r} size={meta.get('size')} mimeType={meta.get('mimeType')}")
    except Exception as e:
        raise PermissionError(
            f"Could not access file_id={file_id}. "
            f"Make sure you have access to this file in your Google Drive. ({e})"
        )

    request = service.files().get_media(fileId=file_id,supportsAllDrives=True)
    with open(dest_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request, chunksize=32 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            logger.info(f"Download progress: {int(status.progress() * 100)}%")

    size_mb = os.path.getsize(dest_path) / 1e6
    logger.info(f"Download complete: {dest_path} ({size_mb:.1f} MB)")
