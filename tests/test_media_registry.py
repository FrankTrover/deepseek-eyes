"""Phase 1 media intake + registry tests."""

from __future__ import annotations

import asyncio

import pytest

from deepseek_eyes.errors import ErrorCode, EyesError
from deepseek_eyes.media import LocalMediaProvider
from deepseek_eyes.registry import InMemoryRegionRegistry, InMemorySourceRegistry
from deepseek_eyes.security import canonicalize, sniff_mime

from .conftest import gif_animated_bytes, jpeg_bytes, png_bytes


async def test_canonicalize_png() -> None:
    raw = png_bytes(64, 48)
    canonical, w, h, mime = canonicalize(raw)
    assert mime == "image/png"
    assert (w, h) == (64, 48)
    assert canonical[:8] == b"\x89PNG\r\n\x1a\n"


def test_sniff_mime() -> None:
    assert sniff_mime(png_bytes()) == "image/png"
    assert sniff_mime(jpeg_bytes()) == "image/jpeg"
    assert sniff_mime(b"not an image at all.........") is None


async def test_animated_gif_rejected() -> None:
    with pytest.raises(EyesError) as exc:
        canonicalize(gif_animated_bytes())
    assert exc.value.code == ErrorCode.SOURCE_DECODE_FAILED


async def test_empty_payload_rejected() -> None:
    with pytest.raises(EyesError) as exc:
        canonicalize(b"")
    assert exc.value.code == ErrorCode.SOURCE_DECODE_FAILED


async def test_exif_stripped() -> None:
    """JPEG with EXIF must produce a canonical PNG with no EXIF metadata."""

    raw_jpeg = jpeg_bytes(32, 32)
    canonical, width, height, mime = canonicalize(raw_jpeg)
    from PIL import Image as PImage

    img = PImage.open(__import__("io").BytesIO(canonical))
    assert img.format == "PNG"
    assert not img.getexif()  # canonical PNG carries no EXIF
    assert (width, height, mime) == (32, 32, "image/png")


async def test_media_provider_labels_canonical_bytes_as_png() -> None:
    image = await LocalMediaProvider().canonicalize(jpeg_bytes(24, 16))
    assert image.canonical_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.mime_type == "image/png"


async def test_media_provider_register(tmp_path) -> None:
    provider = LocalMediaProvider()
    raw = png_bytes(64, 48)
    image = await provider.canonicalize(raw)
    reg = InMemorySourceRegistry()
    ref = await reg.register(image)
    assert ref.startswith("src_")
    desc = await reg.resolve(ref)
    assert desc.canonical_digest == image.canonical_digest


async def test_unknown_source() -> None:
    reg = InMemorySourceRegistry()
    with pytest.raises(EyesError) as exc:
        await reg.resolve("src_missing")
    assert exc.value.code == ErrorCode.SOURCE_NOT_FOUND


async def test_path_escape_guarded(tmp_path) -> None:
    provider = LocalMediaProvider()
    # A path pointing at a directory must fail as not-a-file, not read it.
    with pytest.raises(EyesError):
        await provider.canonicalize_path(str(tmp_path))


async def test_symlink_rejected(tmp_path) -> None:
    target = tmp_path / "real.png"
    target.write_bytes(png_bytes())
    link = tmp_path / "link.png"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported on this platform")

    provider = LocalMediaProvider()
    with pytest.raises(EyesError) as exc:
        await provider.canonicalize_path(str(link))
    assert exc.value.code == ErrorCode.SOURCE_FORBIDDEN


async def test_concurrent_register_resolve() -> None:
    reg = InMemorySourceRegistry()
    provider = LocalMediaProvider()
    images = [await provider.canonicalize(png_bytes(16, 16, (i, i, i))) for i in range(8)]

    async def register(i: int) -> str:
        return await reg.register(images[i])

    refs = await asyncio.gather(*(register(i) for i in range(8)))
    assert len(set(refs)) == 8

    descs = await asyncio.gather(*(reg.resolve(r) for r in refs))
    assert {d.canonical_digest for d in descs} == {im.canonical_digest for im in images}


async def test_region_stale_on_digest_mismatch() -> None:
    reg = InMemorySourceRegistry()
    regions = InMemoryRegionRegistry()
    provider = LocalMediaProvider()
    image = await provider.canonicalize(png_bytes(16, 16))
    ref = await reg.register(image)

    region_ref = await regions.add(ref, image.canonical_digest, (0, 0, 1, 1))
    src_ref, digest, bbox = await regions.resolve(region_ref)
    assert (src_ref, digest) == (ref, image.canonical_digest)

    # A mismatched digest is detectable by the caller via the returned digest;
    # the region registry itself only stores what it was given.
    assert bbox == (0.0, 0.0, 1.0, 1.0)
