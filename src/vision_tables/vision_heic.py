"""Static HEIC decoding through the system ImageIO framework on macOS."""

from __future__ import annotations

import ctypes as c
import json
import os
import resource
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

_HEIC_TYPE = "public.heic"
_CONVERT_MESSAGE = "Export a single still as PNG or JPEG to process this source."


class _DecodeError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _framework(name):
    return c.CDLL(f"/System/Library/Frameworks/{name}.framework/{name}")


def _function(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype = result
    fn.argtypes = args
    return fn


def _native_decode(source_path, output_path, max_pixels):
    io = _framework("ImageIO")
    cf = _framework("CoreFoundation")
    cg = _framework("CoreGraphics")
    ptr, index, size = c.c_void_p, c.c_long, c.c_size_t
    release = _function(cf, "CFRelease", None, ptr)
    make_url = _function(cf, "CFURLCreateFromFileSystemRepresentation", ptr, ptr, c.c_char_p, index, c.c_bool)
    copy_string = _function(cf, "CFStringGetCString", c.c_bool, ptr, c.c_char_p, index, c.c_uint32)
    get_value = _function(cf, "CFDictionaryGetValue", ptr, ptr, ptr)
    get_number = _function(cf, "CFNumberGetValue", c.c_bool, ptr, index, ptr)
    make_string = _function(cf, "CFStringCreateWithCString", ptr, ptr, c.c_char_p, c.c_uint32)
    utf8 = 0x08000100

    with ExitStack() as context:
        def owned(ref):
            if not ref:
                raise _DecodeError("corrupt_heic", "ImageIO could not decode the HEIC source.")
            context.callback(release, ref)
            return ref

        def url(path):
            encoded = os.fsencode(path)
            return owned(make_url(None, encoded, len(encoded), False))

        source = owned(_function(io, "CGImageSourceCreateWithURL", ptr, ptr, ptr)(url(source_path), None))
        type_ref = _function(io, "CGImageSourceGetType", ptr, ptr)(source)
        identifier = c.create_string_buffer(128)
        if not type_ref or not copy_string(type_ref, identifier, len(identifier), utf8):
            raise _DecodeError("corrupt_heic", "ImageIO could not identify the HEIC container.")
        if identifier.value.decode("utf-8") != _HEIC_TYPE:
            raise _DecodeError("unsupported_heic", "The container is not a supported HEIC still. " + _CONVERT_MESSAGE)
        count = _function(io, "CGImageSourceGetCount", size, ptr)(source)
        if count == 0:
            raise _DecodeError("corrupt_heic", "ImageIO found no image in the HEIC container.")
        if count != 1:
            raise _DecodeError("unsupported_heic_sequence", "HEIC sequences and multi-image containers are unsupported. " + _CONVERT_MESSAGE)
        status = _function(io, "CGImageSourceGetStatusAtIndex", c.c_int32, ptr, size)
        if status(source, 0) != 0:
            raise _DecodeError("corrupt_heic", "The HEIC still is incomplete or invalid.")
        properties = owned(_function(io, "CGImageSourceCopyPropertiesAtIndex", ptr, ptr, size, ptr)(source, 0, None))

        def number(key, default=0):
            value = get_value(properties, ptr.in_dll(io, "kCGImageProperty" + key))
            result = c.c_int64()
            return result.value if value and get_number(value, 4, c.byref(result)) else default

        width, height, orientation = number("PixelWidth"), number("PixelHeight"), number("Orientation", 1)
        if width <= 0 or height <= 0 or orientation not in range(1, 9):
            raise _DecodeError("corrupt_heic", "The HEIC dimensions or orientation are invalid.")
        if width * height > max_pixels:
            raise _DecodeError("source_pixel_limit", f"Image exceeds {max_pixels} source pixels.")
        # ImageIO creates the stored raster. Applying orientation separately makes its transform explicit.
        image = owned(_function(io, "CGImageSourceCreateImageAtIndex", ptr, ptr, size, ptr)(source, 0, None))
        native_w = _function(cg, "CGImageGetWidth", size, ptr)(image)
        native_h = _function(cg, "CGImageGetHeight", size, ptr)(image)
        if (native_w, native_h) != (width, height):
            raise _DecodeError("unsupported_heic", "ImageIO returned dimensions that do not match the stored raster. " + _CONVERT_MESSAGE)
        png = owned(make_string(None, b"public.png", utf8))
        destination = owned(_function(io, "CGImageDestinationCreateWithURL", ptr, ptr, ptr, size, ptr)(
            url(output_path), png, 1, None
        ))
        _function(io, "CGImageDestinationAddImage", None, ptr, ptr, ptr)(destination, image, None)
        if not _function(io, "CGImageDestinationFinalize", c.c_bool, ptr)(destination):
            raise _DecodeError("corrupt_heic", "ImageIO could not write the decoded HEIC pixels.")
    return {"width": width, "height": height, "orientation": orientation}


def heic_info():
    """Probe native decoder availability without touching document files."""
    if sys.platform == "darwin":
        try:
            io, cf = _framework("ImageIO"), _framework("CoreFoundation")
            ptr = c.c_void_p
            types = _function(io, "CGImageSourceCopyTypeIdentifiers", ptr)()
            if types:
                try:
                    length = _function(cf, "CFArrayGetCount", c.c_long, ptr)(types)
                    get_type = _function(cf, "CFArrayGetValueAtIndex", ptr, ptr, c.c_long)
                    copy_string = _function(cf, "CFStringGetCString", c.c_bool, ptr, c.c_char_p, c.c_long, c.c_uint32)
                    text = c.create_string_buffer(128)
                    for index in range(length):
                        if copy_string(get_type(types, index), text, len(text), 0x08000100) and text.value == b"public.heic":
                            return {"ready": True, "backend": "macOS ImageIO", "scope": "single-image HEIC stills"}
                finally:
                    _function(cf, "CFRelease", None, ptr)(types)
        except (AttributeError, OSError):
            pass
    return {"ready": False, "reason": "The local macOS ImageIO HEIC decoder is unavailable. " + _CONVERT_MESSAGE}


def decode_heic(original: Path, png: Path, max_pixels: int):
    from .config import ProcessingError

    if not (capability := heic_info())["ready"]:
        raise ProcessingError("unsupported_heic", capability["reason"])
    try:
        native = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__)), str(original), str(png), str(max_pixels)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45)
        result = json.loads(native.stdout)
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError("heic_decode_timeout", "ImageIO exceeded the HEIC decode time limit. " + _CONVERT_MESSAGE) from exc
    except (OSError, ValueError) as exc:
        raise ProcessingError("corrupt_heic", "ImageIO could not decode the HEIC source. " + _CONVERT_MESSAGE) from exc
    if native.returncode:
        raise ProcessingError(result["code"], result["message"])
    return result["width"], result["height"], result["orientation"]


if __name__ == "__main__":
    source, output, pixels = sys.argv[1:]
    max_pixels = int(pixels)
    # Allow 16-bit RGBA PNG plus PNG and compression overhead.
    file_limit = 9 * max_pixels + (1 << 20)
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    try:
        result = _native_decode(source, output, max_pixels)
    except _DecodeError as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}))
        raise SystemExit(1)
    print(json.dumps(result))
