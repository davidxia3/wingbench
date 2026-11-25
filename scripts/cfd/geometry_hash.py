"""One lossless identity function for every final SuperWing geometry."""
from __future__ import annotations

import hashlib
import math
import numbers
import struct
from typing import Sequence


DESIGN_DIM = 38
GEOMETRY_HASH_ALGORITHM = "sha256(superwing-geom38-f64le-v1-NUL + 38xf64le)"
_DOMAIN = b"superwing-geom38-f64le-v1\0"


def validated_geometry(geometry_38d: Sequence[float]) -> tuple[float, ...]:
    if isinstance(geometry_38d, (str, bytes)):
        raise ValueError("geometry must be a 38-value numeric sequence")
    try:
        values = list(geometry_38d)
    except TypeError as exc:
        raise ValueError("geometry must be a 38-value numeric sequence") from exc
    if len(values) != DESIGN_DIM:
        raise ValueError(f"geometry must contain exactly {DESIGN_DIM} values; got {len(values)}")
    result = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError(f"geometry[{index}] must be numeric, got {value!r}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"geometry[{index}] must be finite, got {value!r}")
        result.append(number)
    return tuple(result)


def geometry_sha256(geometry_38d: Sequence[float]) -> str:
    geometry = validated_geometry(geometry_38d)
    payload = _DOMAIN + struct.pack("<38d", *geometry)
    return hashlib.sha256(payload).hexdigest()
