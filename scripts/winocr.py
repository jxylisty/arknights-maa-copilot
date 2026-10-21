"""OCR via Windows.Media.Ocr (winrt). No external binaries needed.

Requires: pip install winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging
          winrt-Windows.Storage.Streams winrt-Windows.Globalization
"""
import asyncio
import io

from PIL import Image
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        e = OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
        if e is None:
            e = OcrEngine.try_create_from_user_profile_languages()
        _ENGINE = e
    return _ENGINE


async def _ocr(png_bytes):
    st = InMemoryRandomAccessStream()
    w = DataWriter(st.get_output_stream_at(0))
    w.write_bytes(png_bytes)
    await w.store_async()
    await w.flush_async()
    st.seek(0)
    dec = await BitmapDecoder.create_async(st)
    bmp = await dec.get_software_bitmap_async()
    eng = _engine()
    if eng is None:
        return ""
    return (await eng.recognize_async(bmp)).text


def ocr_image(pil_img, scale=2):
    if scale != 1:
        pil_img = pil_img.resize(
            (pil_img.width * scale, pil_img.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return asyncio.run(_ocr(buf.getvalue()))


def ocr_region(pil_img, box, scale=2):
    return ocr_image(pil_img.crop(box), scale)


async def _ocr_lines(png_bytes):
    st = InMemoryRandomAccessStream()
    w = DataWriter(st.get_output_stream_at(0))
    w.write_bytes(png_bytes)
    await w.store_async()
    await w.flush_async()
    st.seek(0)
    dec = await BitmapDecoder.create_async(st)
    bmp = await dec.get_software_bitmap_async()
    eng = _engine()
    if eng is None:
        return []
    res = await eng.recognize_async(bmp)
    return res.lines


def ocr_text_lines(pil_img, scale=2):
    """OCR returning [(text, cx, cy, x0, y0, x1, y1), ...] in image pixels.

    Coordinates are scaled back to the ORIGINAL image, so callers can tap them
    directly. This is what makes "find the button with the weird name" possible:
    locate the label, tap its centre, then check what screen you landed on.
    """
    if scale != 1:
        pil_img = pil_img.resize(
            (pil_img.width * scale, pil_img.height * scale), Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")

    out = []
    for line in asyncio.run(_ocr_lines(buf.getvalue())):
        words = list(line.words)
        if not words:
            continue
        x0 = min(w.bounding_rect.x for w in words) / scale
        y0 = min(w.bounding_rect.y for w in words) / scale
        x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words) / scale
        y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words) / scale
        out.append((line.text, int((x0 + x1) / 2), int((y0 + y1) / 2),
                    int(x0), int(y0), int(x1), int(y1)))
    return out


def norm(txt):
    """Collapse whitespace: OCR likes to space out CJK and split tokens."""
    return (txt or "").replace(" ", "").replace("\n", "")


if __name__ == "__main__":
    import sys
    im = Image.open(sys.argv[1])
    box = tuple(int(x) for x in sys.argv[2:6]) if len(sys.argv) > 5 else (80, 250, 540, 720)
    print(repr(ocr_region(im, box)))
