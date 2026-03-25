import logging
import os
import shutil
import time
from typing import Optional


logger = logging.getLogger(__name__)

# Where cached source videos live. Uses /tmp so it's ephemeral across process restarts,
# but persists across requests within the same process (i.e. local dev + Cloud Run instance).
CACHE_DIR = os.environ.get("VIDEO_CACHE_DIR", "/tmp/video_cache")
MAX_CACHE_BYTES = int(os.environ.get("VIDEO_CACHE_MAX_MB", "2048")) * 1024 * 1024  # default 2 GB
MAX_CACHE_AGE_SECONDS = int(os.environ.get("VIDEO_CACHE_MAX_AGE_HOURS", "1")) * 3600


def _cache_path(file_id: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{file_id}.mp4")


def get_cached(file_id: str) -> Optional[str]:
    """Return the cached path for file_id if it exists, otherwise None."""
    path = _cache_path(file_id)
    if os.path.exists(path):
        # Touch the file so LRU eviction knows it was recently used
        os.utime(path, None)
        size_mb = os.path.getsize(path) / 1e6
        logger.info(f"Cache hit: {file_id} ({size_mb:.1f} MB) at {path}")
        return path
    return None


def save_to_cache(file_id: str, source_path: str) -> str:
    """Copy source_path into the cache and return the cached path."""
    _evict_if_needed()
    dest = _cache_path(file_id)
    shutil.copy2(source_path, dest)
    logger.info(f"Cached {file_id} -> {dest} ({os.path.getsize(dest) / 1e6:.1f} MB)")
    return dest


def _evict_if_needed() -> None:
    """Remove expired files, then remove oldest files until under the size cap."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    entries = []
    for fname in os.listdir(CACHE_DIR):
        fpath = os.path.join(CACHE_DIR, fname)
        if not os.path.isfile(fpath):
            continue
        stat = os.stat(fpath)
        entries.append((stat.st_atime, stat.st_size, fpath))

    now = time.time()

    # Evict expired files first
    for atime, size, fpath in entries:
        if now - atime > MAX_CACHE_AGE_SECONDS:
            logger.info(f"Evicting expired cache file: {fpath}")
            os.remove(fpath)

    # Re-scan after expiry eviction
    entries = []
    for fname in os.listdir(CACHE_DIR):
        fpath = os.path.join(CACHE_DIR, fname)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            entries.append((stat.st_atime, stat.st_size, fpath))

    total = sum(s for _, s, _ in entries)
    if total <= MAX_CACHE_BYTES:
        return

    # Evict LRU until under the cap
    for atime, size, fpath in sorted(entries):
        if total <= MAX_CACHE_BYTES:
            break
        logger.info(f"Evicting LRU cache file: {fpath} ({size / 1e6:.1f} MB)")
        os.remove(fpath)
        total -= size


def clear_cache() -> dict:
    """Remove all cached files. Useful for the /cache/clear admin endpoint."""
    if not os.path.exists(CACHE_DIR):
        return {"deleted": 0, "freed_mb": 0}
    deleted, freed = 0, 0
    for fname in os.listdir(CACHE_DIR):
        fpath = os.path.join(CACHE_DIR, fname)
        if os.path.isfile(fpath):
            freed += os.path.getsize(fpath)
            os.remove(fpath)
            deleted += 1
    logger.info(f"Cache cleared: {deleted} file(s), {freed / 1e6:.1f} MB freed")
    return {"deleted": deleted, "freed_mb": round(freed / 1e6, 1)}


def cache_stats() -> dict:
    """Return current cache size and file count."""
    if not os.path.exists(CACHE_DIR):
        return {"files": 0, "total_mb": 0, "max_mb": MAX_CACHE_BYTES // (1024 * 1024)}
    entries = [
        os.path.join(CACHE_DIR, f)
        for f in os.listdir(CACHE_DIR)
        if os.path.isfile(os.path.join(CACHE_DIR, f))
    ]
    total = sum(os.path.getsize(p) for p in entries)
    return {
        "files": len(entries),
        "total_mb": round(total / 1e6, 1),
        "max_mb": MAX_CACHE_BYTES // (1024 * 1024),
    }