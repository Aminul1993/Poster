"""Image handling: type sniffing, thumbnails, and the AVIF copy stored instead of the original."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

from poster.config import API, LIMITS
from poster.errors import ServiceError

IMAGE_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
# Pillow's names for the formats /api/media/image/ serves.
IMAGE_FORMATS = {"image/avif": "AVIF", "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def sniff_image(data: bytes) -> str | None:
    """Real image type from magic bytes: image/jpeg, image/png, image/webp or None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def open_upright(data: bytes, max_size: tuple[int, int]) -> Image.Image:
    """The image shrunk to fit max_size and turned upright according to the photo's EXIF orientation."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            # JPEG decodes at reduced size (draft); PNG and WEBP decode every pixel first, so cap their size.
            if image.format != "JPEG" and image.width * image.height > LIMITS["max_decode_pixels"]:
                megapixels = LIMITS["max_decode_pixels"] // 1_000_000
                raise ServiceError(f"The image is too large to process ({image.width}×{image.height} pixels). "
                                   f"Use one of at most {megapixels} megapixels.", kind="validation")
            image.thumbnail(max_size, Image.Resampling.LANCZOS)  # decodes large JPEGs at reduced size
            return ImageOps.exif_transpose(image)  # a copy, so it outlives the file
    except (OSError, Image.DecompressionBombError) as exc:
        raise ServiceError("The image could not be decoded.", kind="validation") from exc


def on_white(image: Image.Image) -> Image.Image:
    """The image as RGB, with transparency turned white, as in the thumbnails the browser makes."""
    rgba = image.convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")


def make_thumbnail(data: bytes) -> bytes:
    """A small AVIF preview, turned upright according to the photo's EXIF orientation."""
    thumbnail = on_white(open_upright(data, API["media"]["thumbnail_size"]))
    out = io.BytesIO()
    thumbnail.save(out, "AVIF", quality=API["media"]["thumbnail_quality"], speed=API["media"]["thumbnail_speed"])
    return out.getvalue()


def pack_avif(data: bytes) -> bytes:
    """The image as a compressed AVIF file: shrunk to fit avif_max_size, upright, and without EXIF or XMP
    metadata (the color profile stays)."""
    image = open_upright(data, API["media"]["avif_max_size"])
    image = image.convert("RGBA" if image.has_transparency_data else "RGB")
    out = io.BytesIO()
    image.save(out, "AVIF", quality=API["media"]["avif_quality"], speed=API["media"]["avif_speed"])
    return out.getvalue()


def unpack_avif(data: bytes, mimetype: str) -> bytes:
    """A file written by pack_avif() as `mimetype`: AVIF as stored, or converted to JPEG, PNG or WEBP."""
    if data[4:8] != b"ftyp" or data[8:12] not in (b"avif", b"avis"):
        raise ServiceError("The stored file is not an AVIF image.", kind="parse", service="Media host")
    if mimetype == "image/avif":
        return data
    out = io.BytesIO()
    try:
        with Image.open(io.BytesIO(data), formats=["AVIF"]) as image:
            icc_profile = image.info.get("icc_profile")
            converted = on_white(image) if mimetype == "image/jpeg" else image  # JPEG has no transparency
            converted.save(out, IMAGE_FORMATS[mimetype], quality=API["media"]["served_quality"], icc_profile=icc_profile)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ServiceError("The stored AVIF file could not be read.", kind="parse", service="Media host") from exc
    return out.getvalue()
