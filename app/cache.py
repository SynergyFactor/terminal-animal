"""Bundled (read-only) + user (read-write) animal cache. Images are stored base64-encoded."""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)
MAX_USER_ENTRIES = 500          # live lookups kept locally (oldest dropped first)
MAX_CACHE_FILE_BYTES = 200 * 1024 * 1024


def resource_dir() -> Path:
    """Folder holding bundled data, both from source and inside a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent.parent


BUNDLED = resource_dir() / "data" / "animals.json"


def user_cache_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "TerminalVelocity" / "animals.json"


def encode(record: dict) -> dict:
    r = dict(record)
    if isinstance(r.get("image"), (bytes, bytearray)):
        r["image"] = base64.b64encode(r["image"]).decode("ascii")
    return r


def decode(record: dict) -> dict:
    r = dict(record)
    if isinstance(r.get("image"), str):
        try:
            r["image"] = base64.b64decode(r["image"], validate=True)
        except (binascii.Error, ValueError):
            r["image"] = None                      # corrupt image data: show the record without it
    elif not isinstance(r.get("image"), (bytes, bytearray)):
        r["image"] = None
    return r


def valid_record(r) -> bool:
    """A cache entry must be a dict with a non-empty string title; other fields are checked on use."""
    return isinstance(r, dict) and isinstance(r.get("title"), str) and bool(r["title"].strip())


def load_json(path: Path) -> dict:
    """Read a cache file, returning {} if it's missing, unreadable, oversized, or not an object.

    Entries that aren't valid records are dropped, so one bad entry can't break the app.
    """
    try:
        if path.stat().st_size > MAX_CACHE_FILE_BYTES:
            log.warning("ignoring oversized cache file %s", path)
            return {}
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:              # ValueError covers JSON and UTF-8 errors
        log.warning("ignoring unreadable cache file %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        log.warning("ignoring cache file %s: not a JSON object", path)
        return {}
    return {v["title"].strip().lower(): v for v in data.values() if valid_record(v)}


_load = load_json   # backwards-compatible name


class AnimalCache:
    def __init__(self, bundled: Path = BUNDLED, user: Path | None = None):
        self.user_path = user or user_cache_path()
        self._lock = threading.Lock()
        self._user = _load(self.user_path)
        self._all = {**_load(bundled), **self._user}   # key: lower-case title

    def titles(self) -> list[str]:
        return sorted(r["title"] for r in self._all.values())

    def search(self, query: str, limit: int = 8) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return []
        starts, contains = [], []
        for key, r in self._all.items():
            if key.startswith(q):
                starts.append(r)
            elif q in key or q in str(r.get("description") or "").lower():
                contains.append(r)
        hits = sorted(starts, key=lambda r: r["title"]) + sorted(contains, key=lambda r: r["title"])
        return [{"title": r["title"], "description": str(r.get("description") or ""), "cached": True}
                for r in hits[:limit]]

    def get(self, title: str) -> dict | None:
        r = self._all.get(str(title).lower())
        return decode(r) if r else None

    def put(self, record: dict) -> None:
        if not valid_record(record):
            return
        enc = encode(record)
        with self._lock:
            key = record["title"].lower()
            self._all[key] = enc
            self._user.pop(key, None)
            self._user[key] = enc                       # re-insert: dict order = recency
            while len(self._user) > MAX_USER_ENTRIES:
                del self._user[next(iter(self._user))]
            try:
                self.user_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.user_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._user), "utf-8")
                tmp.replace(self.user_path)
            except (OSError, TypeError, ValueError) as e:
                log.warning("couldn't save the local cache: %s", e)   # cache is best-effort
