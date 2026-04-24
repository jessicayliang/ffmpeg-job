import datetime
import time
import logging
import os
import tempfile
import traceback
import uuid
import zipfile

import google.auth
import google.auth.transport.requests
from google.cloud import storage

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from helpers.auth import check_allowed
from helpers.drive import extract_file_id, get_file_meta, clip_from_drive
from models import ClipRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Video Clipper API")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

GCS_BUCKET = os.environ.get("GCS_BUCKET")
_gcs = storage.Client() if GCS_BUCKET else None

def _parse_ts(ts: str) -> float:
    """Convert HH:MM:SS or SS.mmm to float seconds."""
    if ":" in ts:
        parts = ts.split(":")
        parts = [float(p) for p in parts]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return parts[0] * 60 + parts[1]
    return float(ts)


progress_store = {}


def upload_and_sign(local_path: str, blob_name: str) -> str:
    if not GCS_BUCKET or not _gcs:
        raise RuntimeError("GCS_BUCKET env var is not set.")

    bucket = _gcs.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path, content_type="application/zip")
    logger.info(f"Uploaded {blob_name} to gs://{GCS_BUCKET}")

    credentials, _ = google.auth.default()
    credentials.refresh(google.auth.transport.requests.Request())

    import urllib.request
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req) as resp:
        sa_email = resp.read().decode()

    url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(hours=1),
        method="GET",
        response_disposition=f'attachment; filename="{blob_name}"',
        service_account_email=sa_email,
        access_token=credentials.token,
    )
    return url


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url}:\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
    )


def _extract_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    return auth.removeprefix("Bearer ")


@app.post("/clip")
async def clip_video(request: Request, req: ClipRequest):
    email = check_allowed(request)
    user_token = _extract_token(request)
    logger.info(f"Clip request from {email}: drive_url={req.drive_url!r}, {len(req.clips)} clip(s)")

    try:
        file_id = extract_file_id(req.drive_url)
        logger.info(f"Extracted Drive file_id: {file_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = req.job_id or uuid.uuid4().hex[:8]
    progress_store[job_id] = "starting"
    logger.info(f"Job ID: {job_id}")

    # Verify access upfront — fast metadata call, no download
    try:
        meta = get_file_meta(file_id, user_token)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    total = len(req.clips)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_filename = f"clips_{job_id}.zip"
        zip_path = os.path.join(tmpdir, zip_filename)

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, clip in enumerate(req.clips, start=1):
                label = clip.label or f"clip_{i:02d}"
                output_filename = f"{label}.mp4"
                output_path = os.path.join(tmpdir, output_filename)

                start_s = _parse_ts(clip.start)
                end_s = _parse_ts(clip.end)
                duration_s = end_s - start_s

                if duration_s <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Clip {i} ({label!r}): end must be after start.",
                    )

                progress_store[job_id] = f"clipping {i}/{total}"
                logger.info(f"Clip {i}/{total}: {label!r} {clip.start} -> {clip.end} ({duration_s:.1f}s)")

                try:
                    clip_from_drive(file_id, user_token, output_path, start_s, duration_s)
                except FileNotFoundError:
                    raise HTTPException(
                        status_code=500,
                        detail="ffmpeg not found. Install it with: apt install ffmpeg",
                    )
                except Exception as e:
                    logger.error(f"Clip error on clip {i}:\n{traceback.format_exc()}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error on clip {i} ({label!r}): {type(e).__name__}: {e}",
                    )

                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    raise HTTPException(
                        status_code=500,
                        detail=f"No output for clip {i} ({label!r}). Check timestamps are within video duration.",
                    )

                zf.write(output_path, arcname=output_filename)
                size_mb = os.path.getsize(output_path) / 1e6
                logger.info(f"Clip {i} added to ZIP: {output_filename} ({size_mb:.1f} MB)")

                # Remove the clip file immediately to keep disk usage low
                os.remove(output_path)

        zip_size_mb = os.path.getsize(zip_path) / 1e6
        progress_store[job_id] = f"uploading ({zip_size_mb:.0f} MB)"
        logger.info(f"Uploading {zip_filename} ({zip_size_mb:.1f} MB) to GCS")

        try:
            signed_url = upload_and_sign(zip_path, zip_filename)
        except Exception as e:
            logger.error(f"GCS upload failed:\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"GCS upload failed: {type(e).__name__}: {e}")

        progress_store[job_id] = "done"
        return JSONResponse({"url": signed_url, "filename": zip_filename})


@app.get("/progress/{job_id}")
def progress_stream(job_id: str):
    def event_stream():
        last = None
        while True:
            status = progress_store.get(job_id, "starting")
            if status != last:
                yield f"data: {status}\n\n"
                last = status
            if status == "done":
                break
            time.sleep(0.5)
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/health")
def health():
    return {"status": "ok"}