#!/usr/bin/env python3
"""
Offline data pipeline for the named peaks drawn on the real horizon.

Reads mapsforge POI files (SQLite, https://download.mapsforge.org/pois/, OpenStreetMap data) and
writes one small `.peaks` file per 1 degree tile, named exactly like the elevation tiles
(`tiles/N50/N50E015.peaks`, see `DemTileKey`). The output is meant to be published as a separate
public data repository and downloaded by the app together with the elevation tiles; nothing here
goes into the APK.

    python3 data_pipeline/generate_peaks.py <out_dir> <region.poi> [<region.poi> ...]

The `.poi` files are large (the Czech one is ~165 MB), so the developer downloads them by hand and
this script only ever reads them.

Format of a `.peaks` file (big-endian, the same style as `HorizonProfileCodec`; the Kotlin reader is
`domain/horizon/LandmarkTileCodec.kt`, and the two are held together by a fixture this script
produced, checked in under `app/src/test/resources/landmarks/`):

    int    magic    0x434C4D4B  ("CLMK")
    int    version  1
    int    count
    count x {
        int   lat_e6      degrees * 1e6, rounded
        int   lon_e6
        short ele_m       metres above sea level, 1..8900
        UTF   name        java.io.DataOutput.writeUTF (modified UTF-8, 2-byte length); never empty
        UTF   name_cs     empty = missing
        UTF   name_en     empty = missing
    }

A change to this layout is a new `version` here and in the reader, and a new tag of the data
repository, because the app downloads from a pinned tag.
"""

import math
import os
import re
import sqlite3
import struct
import sys

MAGIC = 0x434C4D4B
VERSION = 1

PEAK_CATEGORIES = ("peak", "volcano")

MIN_ELEVATION_M = 1
MAX_ELEVATION_M = 8900

# One .poi file is a country, and its bounding box reaches into the neighbours. A tile is written as
# "covered" (possibly empty) only when the file holds at least this many POIs of any kind in it, so
# the sliver of Poland that a Czech extract happens to touch is not declared peak-free. The app reads
# an absent file as "this region is not covered", never as "no peaks here", so declaring a tile
# covered when it is not would hide real peaks behind an empty answer.
MIN_POIS_FOR_COVERED_TILE = 20

# The same peak lives in the files of both countries on a border. Two records with the same name
# closer than this are one summit; 0.001 degrees is about 100 m, well inside what two mappers place
# the same cairn apart and well outside two distinct summits sharing a name ("Velký Javorník").
DUPLICATE_GRID_DEGREES = 0.001

_ELE_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_ELE_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_FEET = 0.3048


def parse_elevation(raw):
    """Elevation in whole metres from an OSM `ele` value, or None when it is not a usable one.

    OSM says metres with a dot, but the field is free text: "1602 m", "1 602", "1,602", "1602,5",
    "5262 ft". A value that cannot be read, or lies outside what a peak can be, is dropped rather than
    guessed: a wrong height moves the label off the ridge it names.
    """
    if raw is None:
        return None
    text = raw.strip().lower().replace(" ", " ").replace(" ", " ")
    factor = 1.0
    for suffix in ("meters", "metres", "meter", "metre", "m"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    else:
        for suffix in ("feet", "foot", "ft", "'"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
                factor = _FEET
                break
    text = text.replace(" ", "")
    if _ELE_THOUSANDS_COMMA.match(text):
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    if not _ELE_NUMBER.match(text):
        return None
    metres = int(round(float(text) * factor))
    if metres < MIN_ELEVATION_M or metres > MAX_ELEVATION_M:
        return None
    return metres


def parse_tags(blob):
    """The `key=value` pairs of a mapsforge POI data blob (separated by CR, tolerating LF)."""
    if blob is None:
        return {}
    if isinstance(blob, (bytes, bytearray, memoryview)):
        text = bytes(blob).decode("utf-8", errors="replace")
    else:
        text = str(blob)
    tags = {}
    for part in re.split(r"[\r\n]+", text):
        key, sep, value = part.partition("=")
        if sep and key and key not in tags:
            tags[key] = value.strip()
    return tags


def tile_key(lat_e6, lon_e6):
    """(latSouth, lonWest) of the 1 degree tile holding a point, integer arithmetic so a peak at
    exactly 15.000000 degrees cannot land in the wrong tile through float noise."""
    return lat_e6 // 1_000_000, lon_e6 // 1_000_000


def tile_name(lat_south, lon_west):
    """`N50E015`, matching `DemTileKey.name`."""
    lat = ("N%02d" % lat_south) if lat_south >= 0 else ("S%02d" % -lat_south)
    lon = ("E%03d" % lon_west) if lon_west >= 0 else ("W%03d" % -lon_west)
    return lat + lon


def tile_path(out_dir, lat_south, lon_west):
    name = tile_name(lat_south, lon_west)
    return os.path.join(out_dir, "tiles", name[:3], name + ".peaks")


def java_utf(text):
    """`DataOutputStream.writeUTF`: length-prefixed modified UTF-8.

    It differs from UTF-8 in two ways that matter for place names: NUL is two bytes, and a character
    outside the BMP is written as its two UTF-16 surrogates, three bytes each. The reader is
    `DataInputStream.readUTF`, which would reject plain UTF-8 for those.
    """
    out = bytearray()
    utf16 = text.encode("utf-16-be", errors="surrogatepass")
    for unit in struct.unpack(">%dH" % (len(utf16) // 2), utf16):
        if 0x0001 <= unit <= 0x007F:
            out.append(unit)
        elif unit <= 0x07FF:
            out.append(0xC0 | (unit >> 6))
            out.append(0x80 | (unit & 0x3F))
        else:
            out.append(0xE0 | (unit >> 12))
            out.append(0x80 | ((unit >> 6) & 0x3F))
            out.append(0x80 | (unit & 0x3F))
    if len(out) > 0xFFFF:
        raise ValueError("name too long for writeUTF: %r" % text[:40])
    return struct.pack(">H", len(out)) + bytes(out)


def encode_tile(records):
    """Bytes of one `.peaks` file. `records` are dicts with lat_e6, lon_e6, ele, name, name_cs, name_en."""
    ordered = sorted(records, key=lambda r: (r["lat_e6"], r["lon_e6"], r["name"]))
    out = bytearray(struct.pack(">iii", MAGIC, VERSION, len(ordered)))
    for r in ordered:
        out += struct.pack(">iih", r["lat_e6"], r["lon_e6"], r["ele"])
        out += java_utf(r["name"])
        out += java_utf(r["name_cs"])
        out += java_utf(r["name_en"])
    return bytes(out)


def _signed_int32(value):
    return value if -(2**31) <= value < 2**31 else None


def peak_records(db_path):
    """Yields the usable peak records of one mapsforge POI file, and fills nothing else.

    A record needs a name and a height: a nameless peak has nothing to print, and one without a
    height cannot be placed on the ridge. The local name is always filled (falling back to the
    English or Czech one) so a reader that finds no translation still has something to show.
    """
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        ids = [
            row[0]
            for row in conn.execute("SELECT id, name FROM poi_categories")
            if row[1] and row[1].lower() in PEAK_CATEGORIES
        ]
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        query = (
            "SELECT i.lat, i.lon, d.data FROM poi_data d "
            "JOIN poi_index i ON i.id = d.id "
            "WHERE d.id IN (SELECT id FROM poi_category_map WHERE category IN (%s))" % marks
        )
        for lat, lon, blob in conn.execute(query, ids):
            tags = parse_tags(blob)
            ele = parse_elevation(tags.get("ele"))
            if ele is None:
                continue
            name_cs = tags.get("name:cs", "")
            name_en = tags.get("name:en", "")
            name = tags.get("name", "") or name_en or name_cs
            if not name:
                continue
            lat_e6 = _signed_int32(int(round(lat * 1_000_000)))
            lon_e6 = _signed_int32(int(round(lon * 1_000_000)))
            if lat_e6 is None or lon_e6 is None:
                continue
            if not (-90_000_000 <= lat_e6 <= 90_000_000 and -180_000_000 <= lon_e6 <= 180_000_000):
                continue
            yield {
                "lat_e6": lat_e6,
                "lon_e6": lon_e6,
                "ele": ele,
                "name": name,
                "name_cs": name_cs,
                "name_en": name_en,
            }
    finally:
        conn.close()


def poi_counts(db_path):
    """POIs of any kind per tile of one file, as {(lat, lon): count}.

    Counts are summed over all input files before anything is judged covered: a tile on a border is
    split between two countries' extracts, and each half alone can look like a sliver.
    """
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        # SQLite has no floor(); CAST truncates toward zero, so subtract one for negatives with a fraction.
        rows = conn.execute(
            "SELECT CAST(lat AS INTEGER) - (lat < CAST(lat AS INTEGER)) AS la, "
            "CAST(lon AS INTEGER) - (lon < CAST(lon AS INTEGER)) AS lo, COUNT(*) "
            "FROM poi_index GROUP BY la, lo"
        )
        return {(la, lo): n for la, lo, n in rows}
    finally:
        conn.close()


class Deduper:
    """Drops a record when one with the same name already sits within DUPLICATE_GRID_DEGREES."""

    def __init__(self):
        self._cells = {}

    @staticmethod
    def _cell(rec):
        scale = 1_000_000 * DUPLICATE_GRID_DEGREES
        return int(math.floor(rec["lat_e6"] / scale)), int(math.floor(rec["lon_e6"] / scale))

    def add(self, rec):
        """True when the record is new and was kept."""
        la, lo = self._cell(rec)
        key = rec["name"].casefold()
        limit = 1_000_000 * DUPLICATE_GRID_DEGREES
        for dla in (-1, 0, 1):
            for dlo in (-1, 0, 1):
                for other in self._cells.get((la + dla, lo + dlo), ()):
                    if (
                        other["name"].casefold() == key
                        and abs(other["lat_e6"] - rec["lat_e6"]) <= limit
                        and abs(other["lon_e6"] - rec["lon_e6"]) <= limit
                    ):
                        # Keep whichever carries more of the translations.
                        if not other["name_cs"] and rec["name_cs"]:
                            other["name_cs"] = rec["name_cs"]
                        if not other["name_en"] and rec["name_en"]:
                            other["name_en"] = rec["name_en"]
                        return False
        self._cells.setdefault((la, lo), []).append(rec)
        return True


def generate(out_dir, poi_paths):
    """Writes every tile and returns (peak_count, tile_count, byte_count)."""
    tiles = {}
    counts = {}
    dedupe = Deduper()
    for path in poi_paths:
        for key, n in poi_counts(path).items():
            counts[key] = counts.get(key, 0) + n
        for rec in peak_records(path):
            if dedupe.add(rec):
                tiles.setdefault(tile_key(rec["lat_e6"], rec["lon_e6"]), []).append(rec)

    # Coverage is decided first, from every input together, and only covered tiles are written. A file
    # is a country whose bounding box reaches into its neighbours: a tile it merely touches holds a few
    # POIs and maybe one peak, and writing that as a tile would answer "these are all the peaks here"
    # for ground the extract never surveyed, instead of the 404 that means "not covered". The peaks of
    # such a tile are dropped with it; the extract that does cover it carries them.
    covered = {key for key, n in counts.items() if n >= MIN_POIS_FOR_COVERED_TILE}
    tiles = {key: records for key, records in tiles.items() if key in covered}
    for key in covered:
        tiles.setdefault(key, [])

    peaks = 0
    total_bytes = 0
    for (lat_south, lon_west), records in tiles.items():
        if not (-90 <= lat_south <= 89 and -180 <= lon_west <= 179):
            continue
        data = encode_tile(records)
        target = tile_path(out_dir, lat_south, lon_west)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        part = target + ".part"
        with open(part, "wb") as fh:
            fh.write(data)
        os.replace(part, target)
        peaks += len(records)
        total_bytes += len(data)
    return peaks, len(tiles), total_bytes


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    out_dir, poi_paths = argv[1], argv[2:]
    for path in poi_paths:
        if not os.path.isfile(path):
            sys.stderr.write("generate_peaks: no such file: %s\n" % path)
            return 1
    peaks, tile_count, total_bytes = generate(out_dir, poi_paths)
    print("peaks=%d tiles=%d bytes=%d" % (peaks, tile_count, total_bytes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
