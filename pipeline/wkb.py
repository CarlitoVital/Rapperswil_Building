"""Read swissBUILDINGS3D geometries that shapely/GEOS cannot parse.

Some features of the "Floor" layer are stored as TINs (WKB type 1016, made of
triangles, type 1017). GEOS has no TIN type, but the binary layout of a TIN is
exactly that of a MultiPolygon (and a Triangle that of a Polygon), so patching
the type codes is enough to read them; the triangles are then dissolved into a
footprint polygon by the caller.
"""
from __future__ import annotations

import struct

# ISO WKB base types: TIN / PolyhedralSurface -> MultiPolygon, Triangle -> Polygon
_COLLECTION = {15: 6, 16: 6}      # PolyhedralSurface, TIN
_POLYGONAL = {3: 3, 17: 3}        # Polygon, Triangle


def _dims(t: int) -> tuple[int, int]:
    """(base type, coordinate dimension) of a WKB type code.

    Handles ISO codes (1016 = TIN Z) and the legacy "2.5D" flags GDAL also
    writes (0x80000000 = Z, 0x40000000 = M).
    """
    if t & 0xC0000000:
        base = t & 0x0FFFFFFF
        return base, 2 + bool(t & 0x80000000) + bool(t & 0x40000000)
    base, flag = t % 1000, t // 1000
    return base, {0: 2, 1: 3, 2: 3, 3: 4}[flag]


def tin_to_multipolygon(wkb: bytes) -> bytes:
    """Return ``wkb`` with TIN/PolyhedralSurface rewritten as MultiPolygon.

    Other geometry types are returned unchanged.
    """
    buf = bytearray(wkb)
    order = "<" if buf[0] == 1 else ">"
    (t,) = struct.unpack_from(order + "I", buf, 1)
    base, _ = _dims(t)
    if base not in _COLLECTION:
        return bytes(wkb)
    struct.pack_into(order + "I", buf, 1, t - base + _COLLECTION[base])
    (n,) = struct.unpack_from(order + "I", buf, 5)
    pos = 9
    for _ in range(n):
        o = "<" if buf[pos] == 1 else ">"
        (st,) = struct.unpack_from(o + "I", buf, pos + 1)
        sbase, dim = _dims(st)
        if sbase not in _POLYGONAL:
            raise ValueError(f"unexpected member type {st} in TIN")
        struct.pack_into(o + "I", buf, pos + 1, st - sbase + _POLYGONAL[sbase])
        (nr,) = struct.unpack_from(o + "I", buf, pos + 5)
        pos += 9
        for _ in range(nr):
            (npnt,) = struct.unpack_from(o + "I", buf, pos)
            pos += 4 + npnt * dim * 8
    if pos != len(buf):
        raise ValueError("trailing bytes after TIN")
    return bytes(buf)
