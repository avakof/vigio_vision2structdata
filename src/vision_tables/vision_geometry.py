"""Transforms from render pixels to source coordinates (pixel edges, top-left)."""

import math


def point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def box(matrix, bounds, width, height):
    x0, y0, x1, y1 = bounds
    if not all(math.isfinite(v) for v in bounds) or x0 >= x1 or y0 >= y1:
        return None
    corners = [point(matrix, x, y) for x in (x0, x1) for y in (y0, y1)]
    xs, ys = zip(*corners)
    x0, y0, x1, y1 = max(0, min(xs)), max(0, min(ys)), min(width, max(xs)), min(height, max(ys))
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def invert(matrix):
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    return d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det


def localize(matrix, x, y):
    return (*matrix[:4], *point(matrix, x, y))


def raster_affine(orientation, width, height, sx, sy):
    a, b, c, d, e, f = {
        2: (-1, 0, 0, 1, width, 0),
        3: (-1, 0, 0, -1, width, height),
        4: (1, 0, 0, -1, 0, height),
        5: (0, 1, 1, 0, 0, 0),
        6: (0, -1, 1, 0, 0, height),
        7: (0, -1, -1, 0, width, height),
        8: (0, 1, -1, 0, width, 0),
    }.get(orientation, (1, 0, 0, 1, 0, 0))
    return a * sx, b * sx, c * sy, d * sy, e, f
