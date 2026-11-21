"""Fallback body-size data for when Wikipedia's text doesn't give a usable mass or length.

Priority (first hit wins, per value):
  1. Curated override             data/overrides.csv (hand-checked, with a citation)
  2. Wikipedia article text       unless it disagrees >4x with a species-level database value
  3. Trait database, species      Amniote DB > AmphiBIO > AnimalTraits (bundled data/traits.json.gz)
  4. Wikidata                     P2067 mass / P2043 length on the taxon's item
  5. Trait database, higher taxon median of the genus, then family, then order
  6. Mass estimated from length   last resort (flagged as an estimate)
"""
from __future__ import annotations

import csv
import gzip
import json
import logging
import math
import zlib
from pathlib import Path

from .cache import resource_dir

DATA = resource_dir() / "data"
INDEX_PATH = DATA / "traits.json.gz"
OVERRIDES_PATH = DATA / "overrides.csv"
OVERRIDE_FIELDS = ["title", "scientific_name", "mass_kg", "length_m", "source"]
MISMATCH_RATIO = 4.0

CITATIONS = {
    "amniote": "Amniote Life History Database — Myhrvold et al. (2015), Ecology 96:3109 (CC0)",
    "amphibio": "AmphiBIO — Oliveira et al. (2017), Scientific Data 4:170123 (CC BY 4.0); "
                "maximum adult size",
    "animaltraits": "AnimalTraits — Herberstein et al. (2022), Scientific Data 9:265 (CC0)",
    "wikidata": "Wikidata",
}
SHORT = {"amniote": "Amniote DB", "amphibio": "AmphiBIO", "animaltraits": "AnimalTraits",
         "wikidata": "Wikidata"}

log = logging.getLogger(__name__)
_LEVELS = ("species", "genus", "family", "order")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")   # spreadsheet formula triggers

_index: dict | None = None
_overrides: dict | None = None


# -- loading -------------------------------------------------------------------------------------
def _empty_index() -> dict:
    return {level: {} for level in _LEVELS} | {"genus_family": {}, "family_order": {}}


def index(path: Path = INDEX_PATH) -> dict:
    """The bundled trait index; an empty one if it's missing or damaged (the app still works)."""
    global _index
    if _index is None:
        data = None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            pass
        except (OSError, EOFError, ValueError, zlib.error) as e:   # truncated/corrupt gzip or JSON
            log.warning("ignoring damaged trait index %s: %s", path, e)
        _index = _empty_index()
        if isinstance(data, dict):
            for key, value in data.items():
                if key in _index and not isinstance(value, dict):
                    continue                               # wrong shape: keep the empty default
                _index[key] = value
    return _index


def _clean_cell(value) -> str:
    """Undo the formula-escaping applied by write_overrides."""
    v = str(value or "").strip()
    if v.startswith("'") and v[1:2] in _FORMULA_START:
        return v[1:]
    return v


def load_overrides(path: Path = OVERRIDES_PATH) -> dict:
    """{lower-case title or scientific name: row}. A damaged file is ignored with a warning."""
    out = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for raw in csv.DictReader(line for line in f if not line.lstrip().startswith("#")):
                row = {k: _clean_cell(v) for k, v in raw.items() if isinstance(k, str)}
                for key in (row.get("title"), row.get("scientific_name")):
                    if key:
                        out[key.lower()] = row
    except FileNotFoundError:
        pass
    except (OSError, ValueError, csv.Error) as e:
        log.warning("ignoring unreadable overrides file %s: %s", path, e)
    return out


def overrides() -> dict:
    global _overrides
    if _overrides is None:
        _overrides = load_overrides()
    return _overrides


def reload() -> None:
    global _index, _overrides
    _index = _overrides = None


def _num(v, upper: float = 1e7) -> float | None:
    """A positive finite number below `upper`, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and 0 < f < upper else None


def _row(idx: dict, level: str, key) -> tuple | None:
    """(mass, mass_src, length, length_src) from the index, or None if missing/malformed."""
    row = idx.get(level, {}).get(key) if isinstance(key, str) else None
    if not isinstance(row, (list, tuple)) or len(row) < 4:
        return None
    return _num(row[0]), str(row[1] or ""), _num(row[2], 1000.0), str(row[3] or "")


# -- lookups -------------------------------------------------------------------------------------
def lookup(sci_name: str | None) -> dict:
    """Best database values for a taxon name.

    Returns {"mass": (kg, label, citation, level) | None, "length": (...) | None}, searching the
    exact name (species, or a genus/family/order article) before the binomial's genus.
    """
    out = {"mass": None, "length": None}
    if not isinstance(sci_name, str) or not sci_name.strip():
        return out
    idx = index()
    name = " ".join(sci_name.lower().split()[:2])       # drop subspecies / authority
    tries = []
    if " " in name:
        tries.append(("species", name))
        tries.append(("genus", name.split()[0]))
    else:
        tries += [("genus", name), ("family", name), ("order", name)]
    for level, key in tries:
        row = _row(idx, level, key)
        if not row:
            continue
        m, msrc, length, lsrc = row
        for field, val, src in (("mass", m, msrc), ("length", length, lsrc)):
            if out[field] is None and val:
                kind = "species value" if level == "species" else f"{level} median"
                label = f"{SHORT.get(src, src)} ({kind})"
                out[field] = (val, label, CITATIONS.get(src, src), level)
    return out


def _species_family_fallback(sci_name: str | None, field: str):
    """Family/order medians for a species whose genus isn't indexed (via the genus->family map)."""
    if not isinstance(sci_name, str) or " " not in sci_name.strip():
        return None
    idx = index()
    fam = idx.get("genus_family", {}).get(sci_name.lower().split()[0])
    order = idx.get("family_order", {}).get(fam) if isinstance(fam, str) else None
    for level, key in (("family", fam), ("order", order)):
        row = _row(idx, level, key)
        if row:
            val, src = (row[0], row[1]) if field == "mass" else (row[2], row[3])
            if val:
                return (val, f"{SHORT.get(src, src)} ({level} median)", CITATIONS.get(src, src), level)
    return None


# -- the decision --------------------------------------------------------------------------------
def enrich(rec: dict) -> dict:
    """Fill rec's final mass_kg / length_m (+ origin and source text) from all available data.

    Reads: wiki_mass_kg, wiki_length_m, mass_source/length_source (Wikipedia sentences),
           sci_name, wd_mass_kg, wd_length_m, title.
    """
    # Older records only had mass_kg/length_m (from Wikipedia).
    rec.setdefault("wiki_mass_kg", None if rec.get("mass_estimated") else rec.get("mass_kg"))
    rec.setdefault("wiki_length_m", rec.get("length_m"))
    rec.setdefault("wiki_mass_source", rec.get("mass_source", ""))
    rec.setdefault("wiki_length_source", rec.get("length_source", ""))

    for key, upper in (("wiki_mass_kg", 1e7), ("wd_mass_kg", 1e7),
                       ("wiki_length_m", 1000.0), ("wd_length_m", 1000.0)):
        rec[key] = _num(rec.get(key), upper)
    title = rec.get("title") if isinstance(rec.get("title"), str) else ""
    sci = rec.get("sci_name") if isinstance(rec.get("sci_name"), str) else ""
    ov = overrides().get(title.lower()) or overrides().get(sci.lower())
    db = lookup(rec.get("sci_name"))
    notes = []

    def decide(field: str, wiki_val, wiki_src, wd_val):
        ov_val = _num(ov.get("mass_kg" if field == "mass" else "length_m"),
                      1e6 if field == "mass" else 100.0) if ov else None
        if ov_val:
            return ov_val, "Override", ov.get("source") or "data/overrides.csv"
        species = db[field] if db[field] and db[field][3] == "species" else None
        if wiki_val:
            if species and max(wiki_val, species[0]) / min(wiki_val, species[0]) > MISMATCH_RATIO:
                notes.append(f"Wikipedia {field} ({wiki_val:.4g}) disagreed >{MISMATCH_RATIO:g}x with "
                             f"{species[1]}; using the database value.")
            else:
                return wiki_val, "Wikipedia", wiki_src
        if species:
            return species[0], species[1], species[2]
        if wd_val:
            prop = "P2067" if field == "mass" else "P2043"
            return wd_val, "Wikidata", f"item {rec.get('qid') or '?'}, property {prop}"
        higher = db[field] or _species_family_fallback(rec.get("sci_name"), field)
        if higher:
            return higher[0], higher[1], higher[2]
        return None, "", ""

    mass, rec["mass_origin"], rec["mass_source"] = decide(
        "mass", rec.get("wiki_mass_kg"), rec.get("wiki_mass_source", ""), rec.get("wd_mass_kg"))
    length, rec["length_origin"], rec["length_source"] = decide(
        "length", rec.get("wiki_length_m"), rec.get("wiki_length_source", ""), rec.get("wd_length_m"))

    rec["mass_estimated"] = False
    if mass is None and length is not None:
        mass = 25.0 * length ** 3          # typical animal: ~25 kg/m^3 * L^3
        rec["mass_estimated"] = True
        rec["mass_origin"] = "Estimated from length"
        rec["mass_source"] = "No mass found in any source; estimated from body length."
    rec["mass_kg"], rec["length_m"] = mass, length
    rec["data_notes"] = notes
    return rec


# -- overrides file editing (used by the manager UI) ---------------------------------------------
def write_overrides(rows: list[dict], path: Path = OVERRIDES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write("# Hand-checked values used before any other source. mass in kg, length in m.\n"
                "# Cite where each value came from in the source column.\n")
        w = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _escape_cell(r.get(k)) for k in OVERRIDE_FIELDS})
    reload()


def _escape_cell(value) -> str:
    """Neutralise spreadsheet formulas (CSV injection) in text cells; numbers pass unchanged."""
    v = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if v.startswith(_FORMULA_START) and _num(v) is None:
        return "'" + v
    return v


def read_override_rows(path: Path = OVERRIDES_PATH) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [{k: _clean_cell(v) for k, v in row.items() if isinstance(k, str)}
                    for row in csv.DictReader(line for line in f if not line.lstrip().startswith("#"))]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, csv.Error) as e:
        log.warning("couldn't read overrides file %s: %s", path, e)
        return []
