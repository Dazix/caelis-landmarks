# caelis-landmarks

Named summits for the Caelis app's terrain skyline, split into 1° × 1° tiles.

The app downloads only the tile it needs, using the same tile names as its elevation data
(`tiles/N50/N50E015.peaks`), from a pinned tag:

```
https://raw.githubusercontent.com/Dazix/caelis-landmarks/v1/tiles/<N50>/<N50E015>.peaks
```

A request carries nothing but the tile name. A tag stands for a file format: a change to the format
gets a new tag and a matching change in the app. New coverage in the same format moves `v1` forward:
an installed app treats a 404 as "not covered yet" and asks again later, so it picks up new tiles
without an update.

## Coverage

| Tag | Countries | Tiles | Peaks |
| --- | --- | --- | --- |
| `v1` | Europe: every top-level country file of mapsforge `pois/europe/` (49 files, downloaded 2026-11-13), incl. Iceland, the Azores, Madeira, Cyprus, Turkey and Georgia; not Russia | 1 273 | 274 795 |

Every tile that the source data covers has a file, so an empty tile means there are no named peaks
there. A missing file (HTTP 404) means the region is not covered. Tiles on the edge of the coverage
(e.g. along the Russian border) only include the peaks from the countries listed above.

## Format

Big-endian, the `.peaks` format read by `LandmarkTileCodec` in the app:

| Field | Type |
| --- | --- |
| magic | 4 bytes, `CLMK` |
| version | int |
| count | int |
| per peak: `lat_e6`, `lon_e6` | int, int (degrees × 10⁶) |
| per peak: `ele_m` | short (metres) |
| per peak: `name`, `name_cs`, `name_en` | Java modified UTF-8 (`writeUTF`), empty = missing |

## Regenerating

Tiles are generated from [mapsforge POI files](https://download.mapsforge.org/pois/), which are
built from OpenStreetMap. The script keeps `natural=peak` and `natural=volcano` entries that have
both a name and an elevation:

```bash
python3 generate_peaks.py out/ europe/*.poi
```

Use the country files only (`europe/*.poi`), not the regional subdirectories of the larger
countries as well: the same POIs counted twice would lower the threshold that decides whether a tile
is covered.

## Licence

Data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). These tiles are
a derived database and are distributed under the same licence.
