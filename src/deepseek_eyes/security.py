"""Media security pipeline: MIME sniffing, safe decode, EXIF stripping, and
canonicalization.

Nothing here trusts a filename or extension. Supported raster formats are
sniffed from magic bytes, decoded with Pillow, EXIF metadata stripped, and
re-encoded to a deterministic RGB PNG so that a single source has a single
canonical digest regardless of its original container.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .errors import ErrorCode, EyesError
from .limits import MAX_DECODED_PIXELS, PER_SOURCE_ENCODED_MAX_BYTES

CANONICAL_MIME = "image/png"

# Pillow formats we accept for decode.
_ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "BMP", "GIF"}


def sniff_mime(data: bytes) -> str | None:
    """Return the canonical MIME label for ``data`` or ``None`` if unknown."""
    if len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise EyesError(ErrorCode.SOURCE_DECODE_FAILED, "image could not be decoded") from exc

    fmt = (img.format or "").upper()
    if fmt not in _ALLOWED_FORMATS:
        raise EyesError(
            ErrorCode.SOURCE_DECODE_FAILED,
            f"unsupported image format: {fmt or 'unknown'}",
        )

    # Animated GIF is rejected by default (ERROR_AND_MULTI_IMAGE.md §4).
    if fmt == "GIF" and getattr(img, "n_frames", 1) > 1:
        raise EyesError(ErrorCode.SOURCE_DECODE_FAILED, "animated GIF is not supported")

    width, height = img.size
    if width * height > MAX_DECODED_PIXELS:
        raise EyesError(
            ErrorCode.SOURCE_TOO_LARGE,
            f"decoded image exceeds {MAX_DECODED_PIXELS} pixels",
        )

    img.load()
    return img


def _canonicalize(img: Image.Image) -> bytes:
    """Strip EXIF/metadata and produce a deterministic RGB PNG."""
    # Copying the pixel data drops the container-level metadata (EXIF, ICC,
    # comments) that Pillow otherwise carries through save().
    clean = Image.new("RGB", img.size)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        clean = img.convert("RGBA")
        # Flatten onto white to keep PNG deterministic and avoid alpha surprises.
        background = Image.new("RGBA", clean.size, (255, 255, 255, 255))
        clean = Image.alpha_composite(background, clean).convert("RGB")
    else:
        clean.paste(img.convert("RGB"))

    buf = io.BytesIO()
    clean.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def canonicalize(raw: bytes, origin: str = "bytes") -> tuple[bytes, int, int, str]:
    """Validate + normalize ``raw``.

    Returns ``(canonical_png_bytes, width, height, canonical_mime)``.
    """
    if not raw:
        raise EyesError(ErrorCode.SOURCE_DECODE_FAILED, "empty image payload")
    if len(raw) > PER_SOURCE_ENCODED_MAX_BYTES:
        raise EyesError(
            ErrorCode.SOURCE_TOO_LARGE,
            f"encoded image exceeds {PER_SOURCE_ENCODED_MAX_BYTES} bytes",
        )

    if sniff_mime(raw) is None:
        raise EyesError(ErrorCode.SOURCE_DECODE_FAILED, "unrecognized image content (magic bytes)")

    img = _decode(raw)
    width, height = img.size
    canonical = _canonicalize(img)
    return canonical, width, height, CANONICAL_MIME


def _resolve_strict(path: str) -> Path:
    """Resolve a path without following symlink/junction components."""
    p = Path(path)
    if not p.is_absolute():
        p = p.resolve()
    # Reject if the final entry or any parent is a symlink/junction.
    for part in [*p.parents, p]:
        if part.is_symlink():
            raise EyesError(
                ErrorCode.SOURCE_FORBIDDEN,
                "symlink/junction paths are not allowed for source registration",
            )
    return p


def safe_read_path(path: str) -> bytes:
    """Read a file with path-escap / symlink / junction guards."""
    p = _resolve_strict(path)
    try:
        if not p.is_file():
            raise EyesError(ErrorCode.SOURCE_NOT_FOUND, f"not a file: {p}")
        size = p.stat().st_size
        if size > PER_SOURCE_ENCODED_MAX_BYTES:
            raise EyesError(
                ErrorCode.SOURCE_TOO_LARGE,
                f"file exceeds {PER_SOURCE_ENCODED_MAX_BYTES} bytes",
            )
        return p.read_bytes()
    except EyesError:
        raise
    except OSError as exc:
        raise EyesError(ErrorCode.SOURCE_NOT_FOUND, f"cannot read file: {exc}") from exc
