import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def _timestamp_to_seconds(ts: str) -> float:
    """Convert HH:MM:SS or MM:SS or plain seconds to a float."""
    if ":" in ts:
        parts = ts.split(":")
        parts = [float(p) for p in parts]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
    return float(ts)


def run_ffmpeg_clip(input_path: str, output_path: str, start: str, end: str) -> None:
    """Extract a clip using FFmpeg with stream copy (fast, no re-encode).

    Uses -ss (seek) + -t (duration) rather than -ss + -to, because when -ss is
    placed before -i the seek is relative to the file start, but -to is also
    relative to the file start — meaning FFmpeg versions differ on whether -to
    is treated as absolute time or offset from the seek point. Using -t (duration)
    is unambiguous in all versions.
    """
    if not shutil.which("ffmpeg"):
        raise FileNotFoundError("ffmpeg binary not found on PATH")

    start_sec = _timestamp_to_seconds(start)
    end_sec = _timestamp_to_seconds(end)
    duration_sec = end_sec - start_sec

    if duration_sec <= 0:
        raise ValueError(f"end ({end}) must be after start ({start})")

    logger.info(f"Clip: {start} → {end} = {duration_sec:.3f}s")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration_sec),
        "-c", "copy",
        output_path,
    ]
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        logger.error(f"FFmpeg stderr:\n{result.stderr}")
        last_error = next(
            (line for line in reversed(result.stderr.splitlines()) if line.strip()),
            "unknown error",
        )
        raise RuntimeError(f"FFmpeg exited {result.returncode}: {last_error}")