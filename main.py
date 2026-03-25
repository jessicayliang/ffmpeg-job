import io
import logging
import os
import tempfile
import traceback
import uuid
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from helpers.auth import check_allowed
from helpers.cache import cache_stats, clear_cache, get_cached, save_to_cache
from helpers.drive import download_video, extract_file_id
from helpers.ffmpeg import run_ffmpeg_clip
from models import ClipRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Video Clipper API")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url}:\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
    )


@app.post("/clip")
async def clip_video(req: ClipRequest):
    email = check_allowed(req)
    logger.info(f"Received clip request: drive_url={req.drive_url!r}, {len(req.clips)} clip(s)")

    try:
        file_id = extract_file_id(req.drive_url)
        logger.info(f"Extracted Drive file_id: {file_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = uuid.uuid4().hex[:8]
    logger.info(f"Job ID: {job_id}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Use cached source video if available, otherwise download and cache it
        input_path = get_cached(file_id)
        if input_path is None:
            input_path = os.path.join(tmpdir, f"input_{job_id}.mp4")
            try:
                download_video(file_id, input_path)
            except PermissionError as e:
                raise HTTPException(status_code=403, detail=str(e))
            except Exception as e:
                logger.error(f"Download failed:\n{traceback.format_exc()}")
                raise HTTPException(status_code=502, detail=f"Failed to download video: {type(e).__name__}: {e}")

            if os.path.getsize(input_path) == 0:
                raise HTTPException(status_code=502, detail="Downloaded file is empty. Check the Drive link and sharing settings.")

            input_path = save_to_cache(file_id, input_path)

        # Clip and zip
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for i, clip in enumerate(req.clips, start=1):
                label = clip.label or f"clip_{i:02d}"
                output_filename = f"{label}.mp4"
                output_path = os.path.join(tmpdir, output_filename)
                logger.info(f"Clip {i}/{len(req.clips)}: {label!r} {clip.start} → {clip.end}")

                try:
                    run_ffmpeg_clip(input_path, output_path, clip.start, clip.end)
                except FileNotFoundError:
                    raise HTTPException(
                        status_code=500,
                        detail="ffmpeg not found. Install it with: brew install ffmpeg (mac) or apt install ffmpeg (linux)",
                    )
                except Exception as e:
                    logger.error(f"FFmpeg error on clip {i}:\n{traceback.format_exc()}")
                    raise HTTPException(status_code=500, detail=f"FFmpeg error on clip {i} ({label!r}): {type(e).__name__}: {e}")

                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    raise HTTPException(
                        status_code=500,
                        detail=f"FFmpeg produced no output for clip {i} ({label!r}). Check that timestamps are within the video duration.",
                    )

                zf.write(output_path, arcname=output_filename)
                logger.info(f"Clip {i} added to ZIP: {output_filename} ({os.path.getsize(output_path) / 1e6:.1f} MB)")

        zip_buffer.seek(0)
        zip_filename = f"clips_{job_id}.zip"
        logger.info(f"Job {job_id} complete — returning {zip_filename} ({zip_buffer.getbuffer().nbytes / 1e6:.1f} MB)")

        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
        )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/cache")
def get_cache_stats(request: Request):
    email = check_allowed(request)
    return cache_stats()


@app.delete("/cache")
def delete_cache(request: Request):
    email = check_allowed(request)
    return clear_cache()