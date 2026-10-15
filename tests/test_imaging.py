"""Image sniffing, decoding limits, EXIF orientation and the AVIF round trip."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from poster.errors import ServiceError
from poster.imaging import make_thumbnail, open_upright, pack_avif, sniff_image, unpack_avif
from tests.fakes import image_bytes


@pytest.mark.parametrize(("fmt", "mimetype"), [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp"), ("GIF", None)])
def test_sniff_image(fmt: str, mimetype: str | None) -> None:
    assert sniff_image(image_bytes(fmt)) == mimetype


def test_huge_png_is_refused_before_decoding() -> None:
    out = io.BytesIO()
    Image.new("L", (8000, 6000)).save(out, "PNG")  # 48 MP, a few KB on disk
    with pytest.raises(ServiceError) as caught:
        open_upright(out.getvalue(), (400, 400))
    assert caught.value.kind == "validation" and "8000×6000" in caught.value.message


def test_large_jpeg_is_decoded_at_reduced_size() -> None:
    out = io.BytesIO()
    Image.new("RGB", (8000, 6000), (10, 20, 30)).save(out, "JPEG", quality=50)
    assert open_upright(out.getvalue(), (400, 400)).size == (400, 300)


def test_exif_orientation_is_applied() -> None:
    out = io.BytesIO()
    image = Image.new("RGB", (200, 100), (200, 0, 0))
    exif = image.getexif()
    exif[0x0112] = 6  # rotate 90° clockwise to display
    image.save(out, "JPEG", exif=exif)
    assert open_upright(out.getvalue(), (400, 400)).size == (100, 200)


def test_undecodable_data() -> None:
    with pytest.raises(ServiceError) as caught:
        open_upright(b"\x89PNG\r\n\x1a\nnot really", (400, 400))
    assert caught.value.kind == "validation"


def test_avif_round_trip_keeps_size_and_turns_transparency_white() -> None:
    packed = pack_avif(image_bytes("PNG", (640, 480), (0, 0, 255, 0)))
    assert packed[4:12] == b"ftypavif"
    assert unpack_avif(packed, "image/avif") == packed
    with Image.open(io.BytesIO(unpack_avif(packed, "image/jpeg"))) as jpeg:
        assert jpeg.size == (640, 480)
        assert min(jpeg.convert("RGB").getpixel((10, 10))) > 240  # transparent -> white
    with Image.open(io.BytesIO(make_thumbnail(image_bytes("JPEG", (1600, 800))))) as thumb:
        assert thumb.format == "AVIF" and thumb.size == (400, 200)


def test_pack_avif_shrinks_to_the_maximum() -> None:
    with Image.open(io.BytesIO(pack_avif(image_bytes("JPEG", (4000, 1000))))) as image:
        assert image.size == (1920, 480)


def test_unpack_refuses_other_files() -> None:
    with pytest.raises(ServiceError) as caught:
        unpack_avif(image_bytes("PNG"), "image/jpeg")
    assert caught.value.kind == "parse"
