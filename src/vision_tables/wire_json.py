"""Strict, bounded JSON decoding for model replies and human correction files."""
import json


def parse_object(data: str | bytes, *, limit: int = 1 << 20) -> dict:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > limit:
        raise ValueError("JSON byte limit exceeded")

    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON key: " + key[:80])
            obj[key] = value
        return obj

    def invalid(value):
        raise ValueError(f"unsupported numeric constant {value}")

    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
    except RecursionError as exc:
        raise ValueError("JSON nesting limit exceeded") from exc
    if not isinstance(obj, dict):
        raise ValueError("expected one JSON object")
    stack = [(obj, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > 32:
            raise ValueError("JSON nesting limit exceeded")
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, float):
            import math
            if not math.isfinite(node):
                raise ValueError("nonfinite JSON number")
    return obj
