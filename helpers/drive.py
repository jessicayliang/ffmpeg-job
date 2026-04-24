import logging
import re
import subprocess

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _get_drive_service(user_token: str):
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


def get_file_meta(file_id: str, user_token: str) -> dict:
    """Return file metadata (name, size, mimeType). Raises PermissionError if inaccessible."""
    service = _get_drive_service(user_token)
    try:
        meta = service.files().get(
            fileId=file_id,
            fields="name,size,mimeType",
            supportsAllDrives=True,
        ).execute()
        logger.info(f"File: name={meta.get('name')!r} size={meta.get('size')} mimeType={meta.get('mimeType')}")
        return meta
    except Exception as e:
        raise PermissionError(
            f"Could not access file_id={file_id}. "
            f"Make sure you have access to this file in your Google Drive. ({e})"
        )


def drive_direct_url(file_id: str) -> str:
    """Return the direct media download URL for a Drive file."""
    return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"


def clip_from_drive(
        file_id: str,
        user_token: str,
        output_path: str,
        start_seconds: float,
        duration_seconds: float,
) -> None:
    """
    Cut a clip directly from a Google Drive file without downloading it first.

    FFmpeg reads the file over HTTPS using the user's Bearer token, seeking to
    the right position. For container formats that support it (MP4, MKV) FFmpeg
    will only fetch the bytes it actually needs.
    """
    url = drive_direct_url(file_id)

    cmd = [
        "ffmpeg", "-y",
        # Pass the auth header so Drive accepts the request
        "-headers", f"Authorization: Bearer {user_token}\r\n",
        # Seek before opening — much faster for large files
        "-ss", str(start_seconds),
        "-i", url,
        "-t", str(duration_seconds),
        "-c", "copy",
        output_path,
    ]

    logger.info(f"Clipping directly from Drive: ss={start_seconds}s t={duration_seconds}s -> {output_path}")
    logger.info(f"Running: {' '.join(cmd[:6])} ... {output_path}")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-3000:]}")

    logger.info(f"Clip complete: {output_path}")