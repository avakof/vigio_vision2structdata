"""Bounded source import with native PDF text and original-space geometry."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import threading
import unicodedata
from contextlib import closing
from pathlib import Path

import pypdfium2 as pdf
from pypdfium2 import raw
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import ProcessingError, Settings
from .contracts import Box, Ingested, Issue, Page, Region, Source
from .vision_geometry import box, invert, raster_affine

_MAX_NATIVE_CHARS = 20_000
_MAX_RENDER_EDGE = 8192
_MAX_PAGE_INVENTORY = 100_000
_PDF_LOCK = threading.Lock()


def import_source(path: Path, root: Path, limit: int):
    original = root / "original"
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ProcessingError("unsupported_source", "Source must be a regular file.")
            if info.st_size > limit:
                raise ProcessingError("oversized_input", f"Source exceeds {limit} bytes.")
            header = source.read(64)
            if not header:
                raise ProcessingError("empty_source", "The source is empty.")
            if header.startswith(b"%PDF-"):
                media = "application/pdf"
            elif header.startswith(b"\x89PNG\r\n\x1a\n"):
                media = "image/png"
            elif header.startswith(b"\xff\xd8\xff"):
                media = "image/jpeg"
            elif header[4:8] == b"ftyp" and any(
                brand in header[8:] for brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")
            ):
                raise ProcessingError("unsupported_heic", "HEIC decoding is not configured. Convert the original to PNG or JPEG.")
            else:
                raise ProcessingError("unsupported_format", "Expected a JPEG, PNG or PDF file.")
            with original.open("xb") as output:
                try:
                    block = header
                    while block:
                        total += len(block)
                        if total > limit:
                            raise ProcessingError("oversized_input", f"Source exceeds {limit} bytes.")
                        output.write(block)
                        digest.update(block)
                        block = source.read(1 << 20)
                    output.flush()
                    os.fsync(output.fileno())
                except BaseException:
                    original.unlink(missing_ok=True)
                    raise
            original.chmod(0o400)
    except FileExistsError as exc:
        raise ProcessingError("artifact_exists", "The job already has an original source artifact.") from exc
    except OSError as exc:
        raise ProcessingError("source_io", "Could not read or preserve the source file.") from exc
    return original, media, digest.hexdigest(), total


def _quality(image, number):
    if min(image.size) < 220:
        yield Issue(code="low_resolution", message="The rendered page has fewer than 220 pixels on its short edge.", page=number)
    sample = image.convert("L")
    sample.thumbnail((256, 256))
    low, high = sample.getextrema()
    if high - low < 24:
        yield Issue(code="low_contrast", message="The rendered page has very low luminance contrast.", page=number)


def _raster(original, media, root, settings):
    try:
        with Image.open(original, formats=["JPEG", "PNG"]) as source:
            width, height = source.size
            if width * height > settings.max_pixels:
                raise ProcessingError("source_pixel_limit", f"Image exceeds {settings.max_pixels} source pixels.")
            if getattr(source, "n_frames", 1) > 1:
                raise ProcessingError("unsupported_animation", "Animated PNG is unsupported. Export still images individually.")
            orientation = source.getexif().get(274, 1)
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except Image.DecompressionBombError as exc:
        raise ProcessingError("source_pixel_limit", "Image exceeds Pillow's decoder pixel limit.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ProcessingError("corrupt_image", f"The declared {media} source could not be decoded.") from exc
    white = Image.new("RGB", image.size, "white")
    white.paste(image, mask=image.getchannel("A"))
    w, h = white.size
    budget = min(settings.render_pixels, settings.max_pixels)
    scale = min(1.0, math.sqrt(budget / (w * h)), _MAX_RENDER_EDGE / max(w, h), budget / max(w, h))
    if scale < 1:
        white = white.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    relative = "pages/0001.png"
    white.save(root / relative)
    matrix = raster_affine(orientation, width, height, w / white.width, h / white.height)
    page = Page(number=1, width=width, height=height, unit="px", image_path=relative,
                image_width=white.width, image_height=white.height, image_to_original=matrix)
    return 1, [page], [], list(_quality(white, 1)), []


def _pdf_lines(handle, bounds, original_to_render, render_width, render_height):
    lines, current, previous, hidden, invalid = [], [], None, 0, 0
    left, bottom, right, top = bounds
    pdf_to_original = (1, 0, 0, -1, -left, top)
    with closing(handle.get_textpage()) as text:
        count = text.count_chars()
        for index in range(min(count, _MAX_NATIVE_CHARS)):
            codepoint = raw.FPDFText_GetUnicode(text, index)
            char = chr(codepoint) if codepoint <= 0x10FFFF else "\ufffd"
            if char in "\r\n":
                if current:
                    lines.append(current)
                current, previous = [], None
                continue
            if char.isspace():
                if current:
                    current.append((" ", None))
                continue
            obj = raw.FPDFText_GetTextObject(text, index)
            if obj and raw.FPDFTextObj_GetTextRenderMode(obj) in (
                raw.FPDF_TEXTRENDERMODE_INVISIBLE, raw.FPDF_TEXTRENDERMODE_CLIP
            ):
                hidden += 1
                continue
            if unicodedata.category(char) in ("Cc", "Co", "Cn", "Cs") or char == "\ufffd":
                invalid += 1
                continue
            bounds = box(pdf_to_original, text.get_charbox(index), right - left, top - bottom)
            if bounds is None:
                invalid += 1
                continue
            # Use the displayed (possibly rotated) page for grouping and order.
            display = box(original_to_render, bounds, render_width, render_height)
            if previous and display:
                height = max(previous[3] - previous[1], display[3] - display[1])
                overlap = min(previous[3], display[3]) - max(previous[1], display[1])
                gap = display[0] - previous[2]
                if overlap < -max(2, height * 0.22) or gap > max(12, 1.5 * height) or gap < -2 * height:
                    lines.append(current)
                    current = []
            previous = display
            current.append((char, bounds))
    if current:
        lines.append(current)
    return lines, count, hidden, invalid


def _native_regions(handle, bounds, page):
    matrix = invert(page.image_to_original)
    lines, count, hidden, invalid = _pdf_lines(handle, bounds, matrix, page.image_width, page.image_height)
    left, _, _, top = bounds
    pdf_to_original = (1, 0, 0, -1, -left, top)
    mixed = any(
        (raster := box(pdf_to_original, obj.get_bounds(), page.width, page.height)) is not None
        and (raster[2] - raster[0]) * (raster[3] - raster[1]) >= 0.15 * page.width * page.height
        for obj in handle.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4)
    )
    rows = []
    for line in lines:
        rects = [rect for _, rect in line if rect is not None]
        if not rects:
            continue
        x0, y0, x1, y1 = zip(*rects)
        rect = min(x0), min(y0), max(x1), max(y1)
        display = box(matrix, rect, page.image_width, page.image_height)
        if display:
            rows.append((display, rect, "".join(chars for chars, _ in line).strip()))
    rows.sort(key=lambda row: (round(row[0][3] / 5), row[0][0]))
    regions = [Region(id=f"p{page.number}-pdf-{index}", page=page.number, order=index,
                      kind="native_text", text=content[:16384], writing="print",
                      box=Box(xyxy=rect, precision="native_text"))
               for index, (_, rect, content) in enumerate(rows)]
    if not regions or invalid > max(2, count * 0.08):
        return ("unreliable" if count else "absent"), []
    if hidden or mixed or invalid or count > _MAX_NATIVE_CHARS:
        return "partial", regions
    return "usable", regions


def _pdf_page(handle, number, root, settings):
    left, bottom, right, top = bounds = handle.get_bbox()
    width, height = right - left, top - bottom
    w, h = handle.get_size()
    if not all(math.isfinite(n) and n > 0 for n in (width, height, w, h)):
        raise ValueError("invalid page dimensions")
    budget = min(settings.render_pixels, settings.max_pixels)
    # Leave room for PDFium's rounding to whole pixels.
    scale = min(3.0, math.sqrt(budget / (w * h)), min(budget, _MAX_RENDER_EDGE) / max(w, h))
    while math.ceil(w * scale) * math.ceil(h * scale) > budget:
        scale *= 0.99
    relative = f"pages/{number:04d}.png"
    with closing(handle.render(scale=scale, rev_byteorder=True)) as bitmap:
        image = bitmap.to_pil()
        image.save(root / relative)
        w, h = image.size
        pos = bitmap.get_posconv(handle)
        x0, y0 = pos.to_page(0, 0)
        xx, yx = pos.to_page(w, 0)
        xy, yy = pos.to_page(0, h)
        matrix = ((xx - x0) / w, (y0 - yx) / w, (xy - x0) / h,
                  (y0 - yy) / h, x0 - left, top - y0)
        issues = list(_quality(image, number))
    page = Page(number=number, width=width, height=height, unit="pt", image_path=relative,
                image_width=w, image_height=h, image_to_original=matrix)
    try:
        page.text_layer, regions = _native_regions(handle, bounds, page)
    except (pdf.PdfiumError, ValueError):
        page.text_layer, regions = "unreliable", []
    if page.text_layer in ("partial", "unreliable"):
        issues.append(Issue(code=f"pdf_text_{page.text_layer}", message="The visual page is needed to supplement the PDF text layer.", page=number))
    return page, regions, issues


def _pdf(original, root, settings):
    try:
        with _PDF_LOCK, pdf.PdfDocument(original) as document:
            if raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
                raise ProcessingError("encrypted_pdf", "The PDF is encrypted. Supply an unencrypted copy.")
            count = len(document)
            if count > _MAX_PAGE_INVENTORY:
                raise ProcessingError("source_page_inventory_limit", "The PDF page count exceeds the inventory limit.")
            pages, regions, issues, skipped = [], [], [], []
            for number in range(1, min(count, settings.max_pages) + 1):
                try:
                    with closing(document[number - 1]) as handle:
                        page, local, findings = _pdf_page(handle, number, root, settings)
                except (pdf.PdfiumError, ValueError, OSError, OverflowError):
                    skipped.append(number)
                    issues.append(Issue(code="page_render_failed", message="PDF page could not be rendered.", page=number))
                    continue
                pages.append(page)
                regions.extend(local)
                issues.extend(findings)
            skipped.extend(range(settings.max_pages + 1, count + 1))
            if count > settings.max_pages:
                issues.append(Issue(code="page_limit", message=f"Page budget skipped {count - settings.max_pages} source pages."))
    except pdf.PdfiumError as exc:
        if exc.err_code == raw.FPDF_ERR_PASSWORD:
            raise ProcessingError("encrypted_pdf", "The PDF requires a password. Supply an unencrypted copy.") from exc
        raise ProcessingError("corrupt_pdf", "The PDF could not be opened.") from exc
    if count == 0:
        raise ProcessingError("empty_pdf", "The PDF has no pages.")
    if not pages:
        raise ProcessingError("page_render_failed", f"No page of the {count}-page PDF could be rendered.")
    return count, pages, regions, issues, skipped


def inspect_source(path: Path, artifact_root: Path, settings: Settings) -> Ingested:
    source, media, digest, size = import_source(path, artifact_root, settings.max_bytes)
    (artifact_root / "pages").mkdir(mode=0o700, exist_ok=False)
    if media == "application/pdf":
        count, pages, regions, issues, skipped = _pdf(source, artifact_root, settings)
    else:
        count, pages, regions, issues, skipped = _raster(source, media, artifact_root, settings)
    return Ingested(source=Source(sha256=digest, media_type=media, size_bytes=size, page_count=count,
                                  original_path=source.name), pages=pages, regions=regions,
                    issues=issues, skipped_pages=skipped, source_skipped_pages=skipped)
