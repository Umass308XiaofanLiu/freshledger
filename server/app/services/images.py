from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from ..errors import AppError


MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_LONG_EDGE = 1536
MAX_SHORT_EDGE = 768

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
register_heif_opener()


def _looks_like_supported_image(data: bytes) -> bool:
    is_jpeg = data.startswith(b"\xff\xd8\xff")
    is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    is_webp = len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    is_heif = len(data) >= 12 and data[4:8] == b"ftyp"
    return is_jpeg or is_png or is_webp or is_heif


def prepare_receipt_image(data: bytes) -> bytes:
    if not data:
        raise AppError(400, "EMPTY_IMAGE", "The uploaded image was empty.", "Choose a receipt photo and try again.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(413, "IMAGE_TOO_LARGE", "The uploaded image exceeded 8 MB.", "That photo is too large — choose one under 8 MB.")
    if not _looks_like_supported_image(data):
        raise AppError(415, "UNSUPPORTED_IMAGE", "The upload was not JPEG, PNG, HEIC, or WebP.", "Use a JPEG, PNG, HEIC, or WebP receipt photo.")

    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError(415, "UNSUPPORTED_IMAGE", "The image could not be decoded.", "That photo could not be opened — try JPEG or PNG.") from exc
    except Image.DecompressionBombError as exc:
        raise AppError(413, "IMAGE_TOO_LARGE", "The image pixel count is unsafe.", "That photo is too large — choose a smaller version.") from exc

    width, height = image.size
    long_edge = max(width, height)
    short_edge = min(width, height)
    scale = min(1.0, MAX_LONG_EDGE / long_edge, MAX_SHORT_EDGE / short_edge)
    if scale < 1.0:
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=80, optimize=True)
    return output.getvalue()

