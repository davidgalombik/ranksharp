"""Vision payload staging for in-store catalogue images.

Why this exists (2026-09-28): the API and the Celery worker are separate
Railway services, and the upload volume is attached to the API only —
the worker cannot read originals from disk. The previous design therefore
shipped every full-resolution photo (2–3 MB HEIC, ~3 MB as base64)
INSIDE the Celery message. On a 1,400-photo day that pushed ~4 GB
through Redis; at the 2.9 GB peak, Redis's once-a-minute background
save (`save 60 1`) was forking a 3 GB process and stalling long enough
that three uploads' publishes timed out AFTER their DB rows had
committed — 71 images stranded as 'pending' with nothing in the queue.

Now the API downsizes each photo to a JPEG and stores it on the image
row. The Celery message carries only the image id. Both services can
read the row, so retries and the stuck-image sweeper work from either
side, and Redis holds kilobytes instead of gigabytes.
"""
import io

import structlog

log = structlog.get_logger()

# Long-edge cap. Claude Vision resamples anything over ~1568px, so analysis
# quality and token cost are identical from ~1600 up. 2400 is chosen for
# the item CROPS cut from this payload — the zoom-and-pan modal wants more
# than 1600 to work with. ~500–700 KB per photo at q85 vs 2–3 MB originals.
MAX_EDGE = 2400
JPEG_QUALITY = 85


def prepare_vision_payload(raw: bytes, file_type: str) -> tuple[bytes, str]:
    """Return (payload_bytes, payload_type) to store on the image row.

    jpeg / jpg / png / heic / heif → RGB JPEG, EXIF-rotated (phone photos
    carry their rotation as metadata), long edge ≤ MAX_EDGE, never upscaled.
    pdf → passed through unchanged; the worker rasterises PDFs itself.

    Never raises: on any failure the original bytes come back with their
    original type, so a transcode problem can never block an upload.
    """
    ft = (file_type or "").lower().lstrip(".")
    if ft == "pdf":
        return raw, "pdf"
    try:
        from PIL import Image, ImageOps
        if ft in ("heic", "heif"):
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img) or img
        img.thumbnail((MAX_EDGE, MAX_EDGE))  # in place; keeps aspect; no upscale
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        out = buf.getvalue()
        log.debug("vision_payload_staged", src=ft, in_bytes=len(raw), out_bytes=len(out))
        return out, "jpeg"
    except Exception as exc:  # noqa: BLE001 — never block an upload on transcode
        log.warning("vision_payload_transcode_failed", file_type=ft, error=str(exc))
        return raw, ft or "jpeg"
