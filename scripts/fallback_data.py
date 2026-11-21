"""Download the fallback trait databases and build the bundled index data/traits.json.gz.

  python scripts/fallback_data.py               download any missing sources, then build the index
  python scripts/fallback_data.py --download    (re)download every source
  python scripts/fallback_data.py --build       build the index from files already in sources/
  python scripts/fallback_data.py --status      show which sources are present

Raw files go in sources/ (not bundled). If a download fails, fetch the file manually from the
page listed by --status and drop it into sources/ under the expected name.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import wiki  # noqa: E402

RAW = ROOT / "sources"
OUT = ROOT / "data" / "traits.json.gz"
csv.field_size_limit(10_000_000)

SOURCES = {
    "amniote": {
        "file": "Amniote_Database_Aug_2015.csv",
        "page": "https://doi.org/10.6084/m9.figshare.3563457.v1",
        "figshare": 3563457,
        "direct": ["https://esapubs.org/archive/ecol/E096/269/Amniote_Database_Aug_2015.csv"],
        "md5": "10e7c14395d95ed7f53258f96fadb4f7",
        "covers": "~21,300 mammals, birds and reptiles",
    },
    "amphibio": {
        "file": "AmphiBIO_v1.csv",
        "page": "https://doi.org/10.6084/m9.figshare.4644424.v5",
        "figshare": 4644424,
        "direct": [],
        "md5": None,
        "covers": "~6,800 amphibians",
    },
    "animaltraits": {
        "file": "observations.csv",
        "page": "https://doi.org/10.5281/zenodo.6468938",
        "zenodo": 6468938,
        "direct": ["https://zenodo.org/records/6468938/files/observations.csv?download=1"],
        "md5": None,
        "covers": "~2,000 terrestrial animals incl. insects, spiders, molluscs, worms",
    },
}
PRIORITY = ["amniote", "amphibio", "animaltraits"]   # most to least preferred per species


# -- download ------------------------------------------------------------------------------------
MAX_FILE = wiki.MAX_DOWNLOAD_BYTES


def _https_only(urls) -> list[str]:
    out = []
    for u in urls:
        if not isinstance(u, str):
            continue
        try:
            wiki._check_url(u)
        except ValueError:
            print(f"    skipping non-HTTPS link {u[:80]}")
            continue
        out.append(u)
    return out


def _candidate_urls(src: dict) -> list[str]:
    urls = []
    if src.get("figshare"):
        try:
            meta = wiki._get_json(f"https://api.figshare.com/v2/articles/{int(src['figshare'])}",
                                  retries=2)
            files = [f for f in meta.get("files") or [] if isinstance(f, dict)
                     and isinstance(f.get("name"), str) and isinstance(f.get("download_url"), str)]
            files.sort(key=lambda f: (f["name"].lower() != src["file"].lower(),
                                      not f["name"].lower().endswith((".csv", ".zip"))))
            urls += [f["download_url"] for f in files]
        except Exception as e:  # noqa: BLE001 - try the other routes
            print(f"    figshare API: {e}")
    if src.get("zenodo"):
        try:
            meta = wiki._get_json(f"https://zenodo.org/api/records/{int(src['zenodo'])}", retries=2)
            for f in meta.get("files") or []:
                if isinstance(f, dict) and str(f.get("key", "")).lower() == src["file"].lower():
                    link = (f.get("links") or {}).get("self")
                    if isinstance(link, str):
                        urls.insert(0, link)
        except Exception as e:  # noqa: BLE001 - try the other routes
            print(f"    zenodo API: {e}")
    return _https_only(urls + list(src.get("direct", [])))


def _extract(blob: bytes, wanted: str, depth: int = 0) -> bytes | None:
    """Return the wanted file from raw bytes (itself, or a member of a zip).

    Archive members are only read into memory (never written to paths taken from the archive),
    and declared sizes are checked first, so path tricks and zip bombs can't do harm.
    """
    if blob[:2] == b"PK":
        if depth > 2:
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for info in z.infolist():
                    base = info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
                    is_target = base == wanted.lower()
                    if not (is_target or base.endswith(".zip")):
                        continue
                    if info.file_size > MAX_FILE:
                        print(f"    skipping {base}: too large ({info.file_size:,} bytes)")
                        continue
                    data = z.read(info)
                    if is_target:
                        return data
                    inner = _extract(data, wanted, depth + 1)
                    if inner:
                        return inner
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as e:
            print(f"    unreadable archive: {e}")
        return None
    head = blob[:200].lstrip().lower()
    return None if head.startswith((b"<!doctype", b"<html")) else blob


def download(key: str) -> bool:
    src = SOURCES[key]
    dest = RAW / src["file"]
    print(f"  {key}: looking up download links…")
    for url in _candidate_urls(src):
        try:
            print(f"    GET {url}")
            blob = wiki._get(url, retries=2, timeout=120, max_bytes=MAX_FILE)
        except Exception as e:  # noqa: BLE001
            print(f"    failed: {e}")
            continue
        data = _extract(blob, src["file"])
        if not data:
            print("    file not found in that download")
            continue
        RAW.mkdir(exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        md5 = hashlib.md5(data).hexdigest()
        if src["md5"] and md5 != src["md5"]:
            print(f"    note: MD5 {md5} differs from published {src['md5']} (line endings?)")
        print(f"    saved sources/{dest.name} ({len(data) / 1e6:.1f} MB)")
        return True
    print(f"  {key}: could not download. Get '{src['file']}' from {src['page']} and put it in sources/")
    return False


# -- parsing -------------------------------------------------------------------------------------
def _read_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(text)))


def _f(v) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and 0 < x < 1e10 else None   # -999 is the Amniote DB's null


def _binomial(genus: str, species: str) -> str:
    species = (species or "").strip()
    if " " in species:
        return " ".join(species.lower().split()[:2])
    return f"{(genus or '').strip()} {species}".lower().strip()


def parse_amniote(path: Path):
    for r in _read_csv(path):
        yield {"species": _binomial(r.get("genus"), r.get("species")),
               "genus": r.get("genus", ""), "family": r.get("family", ""), "order": r.get("order", ""),
               "mass": (_f(r.get("adult_body_mass_g")) or 0) / 1000 or None,
               "length": (_f(r.get("adult_svl_cm")) or 0) / 100 or None}


def parse_amphibio(path: Path):
    for r in _read_csv(path):
        yield {"species": _binomial(r.get("Genus"), r.get("Species")),
               "genus": r.get("Genus", ""), "family": r.get("Family", ""), "order": r.get("Order", ""),
               "mass": (_f(r.get("Body_mass_g")) or 0) / 1000 or None,
               "length": (_f(r.get("Body_size_mm")) or 0) / 1000 or None}


def parse_animaltraits(path: Path):
    by_species = defaultdict(list)
    meta = {}
    for r in _read_csv(path):
        sp = _binomial(r.get("genus"), r.get("species"))
        m = _f(r.get("body mass"))
        unit = (r.get("body mass - units") or "kg").strip().lower()
        if m and sp:
            by_species[sp].append(m * (1e-3 if unit == "g" else 1.0))
            meta[sp] = r
    for sp, vals in by_species.items():
        r = meta[sp]
        yield {"species": sp, "genus": r.get("genus", ""), "family": r.get("family", ""),
               "order": r.get("order", ""), "mass": statistics.median(vals), "length": None}


PARSERS = {"amniote": parse_amniote, "amphibio": parse_amphibio, "animaltraits": parse_animaltraits}


def _sig(x: float | None) -> float | None:
    return float(f"{x:.4g}") if x else None


def build() -> int:
    species: dict[str, list] = {}               # name -> [mass, msrc, length, lsrc]
    taxa = {"genus": defaultdict(lambda: ([], [], defaultdict(int))),
            "family": defaultdict(lambda: ([], [], defaultdict(int))),
            "order": defaultdict(lambda: ([], [], defaultdict(int)))}
    genus_family, family_order, counts = {}, {}, {}

    for key in PRIORITY:
        path = RAW / SOURCES[key]["file"]
        if not path.exists():
            print(f"  {key}: not downloaded, skipped")
            continue
        n = 0
        try:
            rows = list(PARSERS[key](path))
        except (OSError, ValueError, csv.Error) as e:
            print(f"  {key}: couldn't parse {path.name} ({e}); skipped")
            continue
        for row in rows:
            sp = row["species"]
            if not sp or " " not in sp:
                continue
            entry = species.setdefault(sp, [None, None, None, None])
            if row["mass"] and entry[0] is None:
                entry[0], entry[1] = _sig(row["mass"]), key
            if row["length"] and entry[2] is None:
                entry[2], entry[3] = _sig(row["length"]), key
            g, fam, order = (row["genus"] or sp.split()[0]).lower(), row["family"].lower(), row["order"].lower()
            for level, name in (("genus", g), ("family", fam), ("order", order)):
                if name:
                    masses, lengths, srcs = taxa[level][name]
                    if row["mass"]:
                        masses.append(row["mass"])
                    if row["length"]:
                        lengths.append(row["length"])
                    srcs[key] += 1
            if fam:
                genus_family.setdefault(g, fam)
                if order:
                    family_order.setdefault(fam, order)
            n += 1
        counts[key] = n
        print(f"  {key}: {n:,} rows")

    def summarise(level):
        out = {}
        for name, (masses, lengths, srcs) in taxa[level].items():
            if not masses and not lengths:
                continue
            src = max(srcs, key=srcs.get)
            out[name] = [_sig(statistics.median(masses)) if masses else None, src,
                         _sig(statistics.median(lengths)) if lengths else None, src]
        return out

    index = {"version": 1, "built": time.strftime("%Y-%m-%d"), "counts": counts,
             "species": {k: v for k, v in species.items() if v[0] or v[2]},
             "genus": summarise("genus"), "family": summarise("family"), "order": summarise("order"),
             "genus_family": genus_family, "family_order": family_order}
    OUT.parent.mkdir(exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(index, f, separators=(",", ":"))
    print(f"Index: {len(index['species']):,} species, {len(index['genus']):,} genera, "
          f"{len(index['family']):,} families -> {OUT.name} "
          f"({OUT.stat().st_size / 1024:.0f} KB)")
    return 0 if index["species"] else 1


def status() -> None:
    for key, src in SOURCES.items():
        path = RAW / src["file"]
        state = f"present ({path.stat().st_size / 1e6:.1f} MB)" if path.exists() else "MISSING"
        print(f"  {key:<13} {state:<18} {src['covers']}\n  {'':<13} {src['page']}")
    if OUT.exists():
        with gzip.open(OUT, "rt", encoding="utf-8") as f:
            idx = json.load(f)
        print(f"  index: {len(idx.get('species', {})):,} species, built {idx.get('built')}")
    else:
        print("  index: not built")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true", help="(re)download every source")
    ap.add_argument("--build", action="store_true", help="only build the index")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args(argv)
    if args.status:
        status()
        return 0
    if not args.build:
        wiki.set_request_interval(1.0)
        print("Downloading fallback databases:")
        for key, src in SOURCES.items():
            if args.download or not (RAW / src["file"]).exists():
                download(key)
            else:
                print(f"  {key}: already present")
    print("Building index:")
    return build()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
