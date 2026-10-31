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
import tempfile

import structlog

log = structlog.get_logger()

# Long-edge cap. Claude Vision resamples anything over ~1568px, so analysis
# quality and token cost are identical from ~1600 up. 2400 is chosen for
# the item CROPS cut from this payload — the zoom-and-pan modal wants more
# than 1600 to work with. ~500–700 KB per photo at q85 vs 2–3 MB originals.
MAX_EDGE = 2400
JPEG_QUALITY = 85


def encode_jpeg(img, **save_kwargs) -> bytes:
    """JPEG-encode a PIL image and return the bytes — through a real temp
    file, deliberately not a BytesIO.

    Pillow only releases the GIL when the encoder writes to a file
    descriptor. Into a BytesIO it drives the encoder from Python a chunk at
    a time, and with optimize/progressive set (which make libjpeg buffer the
    whole image) the first chunk is the entire encode as one GIL-holding C
    call — every other thread, the API's event loop included, stops for its
    duration. Measured on the API container (2026-10-31), four concurrent
    12 MP encodes at q92 progressive+optimize: 903 ms wall and a 422 ms
    event-loop stall into BytesIO, vs 302 ms wall and a 24 ms stall into a
    temp file. The file is unlinked on creation and lives in the page
    cache, so there is no disk cost.
    """
    with tempfile.TemporaryFile() as f:
        img.save(f, format="JPEG", **save_kwargs)
        f.seek(0)
        return f.read()


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
        out = encode_jpeg(img.convert("RGB"), quality=JPEG_QUALITY, optimize=True)
        log.debug("vision_payload_staged", src=ft, in_bytes=len(raw), out_bytes=len(out))
        return out, "jpeg"
    except Exception as exc:  # noqa: BLE001 — never block an upload on transcode
        log.warning("vision_payload_transcode_failed", file_type=ft, error=str(exc))
        return raw, ft or "jpeg"
