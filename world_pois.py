#!/usr/bin/env python3
"""The mapsforge POI files that cover the world once, for generate_peaks.py: list them or download them.

    python3 data_pipeline/world_pois.py list
    python3 data_pipeline/world_pois.py download <dir> [--jobs N]

`download` keeps the server's layout (`<dir>/europe/czech-republic.poi`), resumes a file that was cut
off, skips one that is complete and current, fetches again one the server has updated since (its
Last-Modified is newer than the local file's mtime, which is set to it after each download), checks
every file's length against the server's, tries failed files once more at the end, and prints
progress every 5 seconds. Run it again after an interruption, or to update: it carries on where it
stopped and fetches only what changed.
The server limits each connection to about 1 MB/s, so a few files are fetched at once (default 3,
kept low to be fair to a free mirror).

Which files: every country-level file of each continent; a country's regional subdirectory only when
no country file exists, except SKIP_DIRS (covered by other country-level files), plus EXTRA_FILES,
regional files the country file leaves out (France's overseas departments). The US is taken by
state (us/), not by the four us-*.poi regions, so Alaska, Hawaii and the territories are certainly in.
A country never goes in twice: generate_peaks.py would count its POIs twice when it judges coverage.
Some land has no file at all (the Lesser Antilles, Bermuda, the Falklands, Antarctica); its tiles
stay 404, "not covered".
"""
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

BASE = "https://download.mapsforge.org/pois/"
CONTINENTS = ["africa", "asia", "australia-oceania", "central-america", "europe",
              "north-america", "russia", "south-america"]
SKIP_DIRS = {"europe/united-kingdom/"}          # great-britain.poi + ireland-and-northern-ireland.poi
SKIP_FILES = {"north-america/us-midwest.poi", "north-america/us-northeast.poi",
              "north-america/us-south.poi", "north-america/us-west.poi"}  # us/ by state instead
# Regional files outside their country file: france.poi is metropolitan France only.
EXTRA_FILES = ["europe/france/guadeloupe.poi", "europe/france/guyane.poi",
               "europe/france/martinique.poi", "europe/france/mayotte.poi",
               "europe/france/reunion.poi"]

TIMEOUT_S = 60
CHUNK = 1 << 20
REPORT_EVERY_S = 5.0
ATTEMPTS = 5


def _listing(url):
    html = urllib.request.urlopen(url, timeout=TIMEOUT_S).read().decode()
    hrefs = re.findall(r'href="([a-z0-9-]+(?:\.poi|/))"', html)
    return [h for h in hrefs if h.endswith(".poi")], [h for h in hrefs if h.endswith("/")]


def world_paths():
    """Paths relative to BASE, e.g. `europe/czech-republic.poi`."""
    paths = []
    for c in CONTINENTS:
        files, dirs = _listing(BASE + c + "/")
        paths += [c + "/" + f for f in files if c + "/" + f not in SKIP_FILES]
        for d in dirs:
            if c + "/" + d in SKIP_DIRS or (d[:-1] + ".poi") in files:
                continue
            paths += [c + "/" + d + f for f in _listing(BASE + c + "/" + d)[0]]
    return paths + EXTRA_FILES


class Remote:
    """What the server says about one file: its length, when it last changed, and its ETag."""

    def __init__(self, path):
        req = urllib.request.Request(BASE + path, method="HEAD")
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            self.size = int(r.headers["Content-Length"])
            modified = r.headers.get("Last-Modified")
            self.mtime = parsedate_to_datetime(modified).timestamp() if modified else None
            self.etag = r.headers.get("ETag")


def is_outdated(target, remote):
    """True when a local copy is older than the server's file: it is fetched again from scratch.

    The local mtime is the server's Last-Modified once a download completes (set here, and by wget),
    and the time of writing while one is under way, so a partial file is never mistaken for an old one.
    """
    return remote.mtime is not None and os.path.exists(target) and os.path.getmtime(target) < remote.mtime


def _mb(n):
    return "%.0f MB" % (n / 1e6)


def _duration(seconds):
    seconds = int(round(seconds))
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


class Progress:
    def __init__(self, total_files, total_bytes, already):
        self.lock = threading.Lock()
        self.total_files, self.total_bytes = total_files, total_bytes
        self.files_done = 0
        self.bytes_done = already          # what was on disk before this run
        self.fetched = 0                   # what this run transferred, for the speed
        self.started = time.monotonic()

    def add(self, n):
        with self.lock:
            self.bytes_done += n
            self.fetched += n

    def file_done(self):
        with self.lock:
            self.files_done += 1

    def line(self):
        with self.lock:
            elapsed = time.monotonic() - self.started
            speed = self.fetched / elapsed if elapsed > 0 else 0
            left = (self.total_bytes - self.bytes_done) / speed if speed > 0 else 0
            return "%d/%d files, %s of %s (%.0f %%), %.1f MB/s, %s elapsed, ~%s left" % (
                self.files_done, self.total_files, _mb(self.bytes_done), _mb(self.total_bytes),
                100.0 * self.bytes_done / self.total_bytes if self.total_bytes else 100.0,
                speed / 1e6, _duration(elapsed), _duration(left))


def _finish(target, remote, progress):
    if remote.mtime is not None:
        os.utime(target, (remote.mtime, remote.mtime))
    progress.file_done()
    return True


def fetch(path, remote, target, progress):
    """Downloads one file to `target`, resuming what is there; True when its length matches."""
    size = remote.size
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for attempt in range(1, ATTEMPTS + 1):
        have = os.path.getsize(target) if os.path.exists(target) else 0
        if have == size:
            return _finish(target, remote, progress)
        if have > size:                    # the server's file was replaced by a shorter one
            print("  %s: %s on disk, longer than the server's %s, starting over" % (path, _mb(have), _mb(size)), flush=True)
            progress.add(-have)
            os.remove(target)
            have = 0
        headers = {}
        if have:
            headers["Range"] = "bytes=%d-" % have
            if remote.etag:
                # Should the file change between two attempts, the server sends all of it (200), not
                # the rest of a different file to glue onto the start of the old one.
                headers["If-Range"] = remote.etag
        try:
            req = urllib.request.Request(BASE + path, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                if have and r.status != 206:   # changed or no range support: start over, never append
                    print("  %s: server sent %d to a resume at %s, starting over" % (path, r.status, _mb(have)), flush=True)
                    progress.add(-have)
                    have = 0
                with open(target, "ab" if have else "wb") as out:
                    while True:
                        chunk = r.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        progress.add(len(chunk))
        except OSError as e:
            print("  %s: attempt %d failed: %s" % (path, attempt, e), flush=True)
            time.sleep(2 * attempt)
    if os.path.exists(target) and os.path.getsize(target) == size:
        return _finish(target, remote, progress)
    return False


def download(out_dir, jobs):
    print("listing %s ..." % BASE, flush=True)
    paths = world_paths()
    with ThreadPoolExecutor(jobs) as pool:
        remotes = dict(zip(paths, pool.map(Remote, paths)))
    targets = {p: os.path.join(out_dir, p) for p in paths}
    outdated = [p for p in paths if is_outdated(targets[p], remotes[p])]
    for p in outdated:
        os.remove(targets[p])
    already = sum(
        min(os.path.getsize(t), remotes[p].size) for p, t in targets.items() if os.path.exists(t))
    progress = Progress(len(paths), sum(r.size for r in remotes.values()), already)
    print("%d files, %s, %s already on disk; %d updated on the server, fetched again; %d at a time" % (
        len(paths), _mb(progress.total_bytes), _mb(already), len(outdated), jobs), flush=True)
    for p in outdated:
        print("  updated: %s" % p, flush=True)

    stop = threading.Event()

    def report():
        while not stop.wait(REPORT_EVERY_S):
            print(progress.line(), flush=True)

    reporter = threading.Thread(target=report, daemon=True)
    reporter.start()
    with ThreadPoolExecutor(jobs) as pool:
        results = dict(zip(paths, pool.map(lambda p: fetch(p, remotes[p], targets[p], progress), paths)))
        # One more round for what failed: a mirror that dropped connections for a minute has usually
        # recovered by the time the rest is in.
        retry = [p for p, ok in results.items() if not ok]
        if retry:
            print("retrying %d failed files" % len(retry), flush=True)
            results.update(zip(retry, pool.map(lambda p: fetch(p, remotes[p], targets[p], progress), retry)))
    stop.set()
    print(progress.line(), flush=True)
    failed = [p for p, ok in results.items() if not ok]
    for p in failed:
        print("FAILED %s" % p, flush=True)
    print("done: %d files complete, %d failed" % (len(paths) - len(failed), len(failed)), flush=True)
    return 1 if failed else 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "list":
        for p in world_paths():
            print(p)
        return 0
    if len(argv) >= 3 and argv[1] == "download":
        jobs = 3
        if len(argv) == 5 and argv[3] == "--jobs":
            jobs = int(argv[4])
        elif len(argv) != 3:
            sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
            return 2
        return download(argv[2], jobs)
    sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
