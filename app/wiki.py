"""Wikipedia search, scraping and size-parsing. Standard library only."""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import ssl
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, traits

try:  # bundled CA certs: frozen apps (esp. macOS) often can't see the system store
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

API = "https://en.wikipedia.org/w/api.php"


def _contact() -> str:
    """TV_CONTACT reduced to printable ASCII so it can never break or inject into HTTP headers."""
    raw = os.environ.get("TV_CONTACT", "")
    clean = re.sub(r"[^\x21-\x7e ]", "", raw).strip()[:100]
    return clean.replace("(", "").replace(")", "").replace(";", "") or "unset"


# Wikimedia asks for a descriptive User-Agent with contact info; set TV_CONTACT to your email/URL.
USER_AGENT = f"TerminalVelocityApp/{__version__} (desktop app; contact: {_contact()}) python-urllib"
TIMEOUT = 15
MAX_JSON_BYTES = 8 * 1024 * 1024        # API responses (a long article's text is ~100-300 KB)
MAX_IMAGE_BYTES = 5 * 1024 * 1024       # 330 px thumbnails are ~20-60 KB
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024  # dataset archives (scripts/fallback_data.py)
IMAGE_DOMAIN = "wikimedia.org"         # thumbnails: upload.wikimedia.org, thumb.wikimedia.org, ...
LOCAL_TEST_HOSTS = {"127.0.0.1", "localhost"}   # plain http allowed only for the test suite


class WikiError(RuntimeError):
    """An error reported inside a MediaWiki API response."""

    def __init__(self, code: str, info: str):
        super().__init__(f"{code}: {info}")
        self.code = code
THUMB_WIDTH = 330          # a standard Wikimedia thumbnail width (served from cache, rarely throttled)
PARSER_VERSION = 4         # bump when parsing changes; older cached records are refreshed

TAXOBOXES = [
    "Template:Speciesbox", "Template:Taxobox", "Template:Automatic taxobox",
    "Template:Subspeciesbox", "Template:Infraspeciesbox",
]
NON_ANIMAL = re.compile(
    r"\b(plant|flowering|tree|shrub|grass|fung(us|i)|mushroom|bacteri|virus|alga|moss|fern|"
    r"genus of plants|lichen|archaea)\b", re.I)


# -- HTTP ----------------------------------------------------------------------------------------
class RateLimiter:
    """Thread-safe: spaces requests at least `interval` seconds apart, across all threads."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> float:
        """Block until this caller's slot; returns the slot time (time.perf_counter() clock)."""
        with self._lock:
            now = time.perf_counter()
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            time.sleep(slot - now)
        return slot

    def cooldown(self, seconds: float) -> None:
        """Pause everyone (used after a 429 so other threads back off too)."""
        with self._lock:
            self._next = max(self._next, time.perf_counter() + seconds)


LIMITER = RateLimiter(0.5)     # interactive default: <= 2 requests/s
RETRY_CODES = {429, 500, 502, 503, 504}
BACKOFF_BASE = 2.0           # seconds; doubles each retry


def set_request_interval(seconds: float) -> None:
    LIMITER.interval = seconds


def _retry_after(err: urllib.error.HTTPError) -> float:
    value = err.headers.get("Retry-After") if err.headers else None
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


def _check_url(url: str) -> None:
    """Only HTTPS is allowed (plain HTTP only to the local test server)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in LOCAL_TEST_HOSTS:
        return
    raise ValueError(f"refusing non-HTTPS URL: {url[:120]}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to HTTPS URLs (no downgrade to http://, file://, ftp://)."""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL), _SafeRedirect())


def _read_limited(resp, limit: int) -> bytes:
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > limit:
        raise ValueError(f"response too large ({int(length):,} bytes > {limit:,})")
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"response too large (> {limit:,} bytes)")
    return data


def _get(url: str, params: dict | None = None, retries: int = 5, timeout: float = TIMEOUT,
         max_bytes: int = MAX_JSON_BYTES) -> bytes:
    """GET with HTTPS enforcement, a size cap, throttling and backoff on 429/5xx/network errors."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "identity"})
    for attempt in range(retries + 1):
        LIMITER.wait()
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                return _read_limited(r, max_bytes)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES or attempt == retries:
                raise
            delay = max(min(_retry_after(e), 300.0), BACKOFF_BASE * 2 ** attempt)  # 2, 4, 8, 16, 32 s
            LIMITER.cooldown(delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError) or attempt == retries:
                raise                                      # certificate problems won't fix themselves
            LIMITER.cooldown(BACKOFF_BASE / 2 * 2 ** attempt)
    raise RuntimeError("unreachable")


def _get_json(url: str, params: dict | None = None, retries: int = 5) -> dict:
    """Fetch a MediaWiki API response; retries the API's own rate-limit errors too."""
    for attempt in range(3):
        try:
            data = json.loads(_get(url, params, retries).decode("utf-8"))
        except UnicodeDecodeError as e:
            raise ValueError("response was not valid UTF-8") from e
        if not isinstance(data, dict):
            raise ValueError("unexpected API response (not a JSON object)")
        err = data.get("error")
        if isinstance(err, dict):
            code = str(err.get("code", "error"))
            if code in ("ratelimited", "maxlag") and attempt < 2:
                LIMITER.cooldown(BACKOFF_BASE * 2 ** (attempt + 2))
                continue
            raise WikiError(code, str(err.get("info", ""))[:200])
        return data
    raise WikiError("ratelimited", "Wikipedia kept rate-limiting the request")


def _str(v, limit: int = 2000) -> str:
    return v[:limit] if isinstance(v, str) else ""


def _image_kind(data: bytes) -> str | None:
    """Identify JPEG/PNG/GIF/WebP by magic bytes; anything else is rejected."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


REST_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def _normalise_image_url(url: str) -> str:
    """Wikimedia sometimes returns protocol-relative links (//upload.wikimedia.org/...)."""
    url = url.strip()
    return "https:" + url if url.startswith("//") else url


def _file_name_from_thumb(url: str) -> str | None:
    """'.../thumb/a/ab/Some_file.jpg/330px-Some_file.jpg' -> 'Some_file.jpg' (for attribution)."""
    parts = urllib.parse.urlsplit(url).path.split("/")
    if "thumb" in parts and len(parts) >= 2:
        return urllib.parse.unquote(parts[-2]) or None
    return urllib.parse.unquote(parts[-1]) or None if parts else None


def _rest_thumbnail(title: str) -> str:
    """Thumbnail URL from Wikipedia's summary service (a second source for the lead image)."""
    try:
        data = _get_json(REST_SUMMARY + urllib.parse.quote(title.replace(" ", "_"), safe=""), retries=2)
    except Exception:  # noqa: BLE001 - optional fallback
        return ""
    return _str((data.get("thumbnail") or {}).get("source"), 1000)


def fetch_image(page: dict, title: str) -> tuple[bytes | None, str | None, str]:
    """Download the article's lead image. Returns (bytes, file name, status).

    status: "ok", "none_on_wikipedia", "blocked_host:<host>", "not_an_image", "download_failed:<why>".
    A missing picture is never silent: the status says why, and only "none_on_wikipedia" is final.
    """
    img_url = _normalise_image_url(_str((page.get("thumbnail") or {}).get("source"), 1000))
    file_name = _str(page.get("pageimage"), 300) or None
    if not img_url:
        img_url = _normalise_image_url(_rest_thumbnail(title))
        file_name = file_name or (_file_name_from_thumb(img_url) if img_url else None)
    if not img_url:
        return None, None, "none_on_wikipedia"
    if not _image_url_ok(img_url):
        host = urllib.parse.urlsplit(img_url).hostname or "?"
        return None, None, f"blocked_host:{host}"
    try:
        blob = _get(img_url, max_bytes=MAX_IMAGE_BYTES)
    except (OSError, ValueError, http.client.HTTPException) as e:
        return None, None, f"download_failed:{str(e)[:120]}"
    if not _image_kind(blob):
        return None, None, "not_an_image"
    return blob, file_name or _file_name_from_thumb(img_url), "ok"


def _image_url_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "http" and parts.hostname in LOCAL_TEST_HOSTS:
        return True
    host = (parts.hostname or "").lower()
    # Any Wikimedia-run host (Wikimedia moves thumbnails between subdomains), HTTPS only.
    return parts.scheme == "https" and (host == IMAGE_DOMAIN or host.endswith("." + IMAGE_DOMAIN))


# -- Search --------------------------------------------------------------------------------------
def search(query: str, limit: int = 8) -> list[dict]:
    """Prefix-search Wikipedia, keeping only pages with a taxobox (i.e. organisms) that look animal."""
    query = " ".join(str(query).split())[:100]
    if not query:
        return []
    data = _get_json(API, {
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "generator": "prefixsearch", "gpssearch": query, "gpslimit": 25,
        "prop": "templates|description", "tltemplates": "|".join(TAXOBOXES), "tllimit": "max",
    }, retries=2)
    pages = [p for p in (data.get("query") or {}).get("pages", []) if isinstance(p, dict)]
    pages.sort(key=lambda p: p.get("index", 999) if isinstance(p.get("index"), int) else 999)
    out = []
    for p in pages:
        title, desc = _str(p.get("title"), 300), _str(p.get("description"), 300)
        if not title or not p.get("templates") or NON_ANIMAL.search(desc):
            continue
        out.append({"title": title, "description": desc})
        if len(out) >= limit:
            break
    return out


# -- Fetch ---------------------------------------------------------------------------------------
def fetch_animal(title: str, with_image: bool = True) -> dict:
    """Scrape description, image and average mass/length for one article (3 requests)."""
    title = " ".join(str(title).split())[:300]
    if not title:
        raise ValueError("empty title")
    data = _get_json(API, {
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "prop": "extracts|description|pageimages|info|pageprops", "explaintext": 1,
        "ppprop": "wikibase_item",
        "piprop": "thumbnail|name", "pithumbsize": THUMB_WIDTH, "inprop": "url", "titles": title,
    })
    pages = (data.get("query") or {}).get("pages") or []
    if not pages or not isinstance(pages[0], dict) or pages[0].get("missing") or pages[0].get("invalid"):
        raise LookupError(f"No Wikipedia article named “{title}”")
    page = pages[0]
    text = _str(page.get("extract"), 2_000_000)
    lead = text.split("\n", 1)[0]

    mass, mass_src = parse_mass(text)
    length, length_src = parse_length(text)

    qid = _str((page.get("pageprops") or {}).get("wikibase_item"), 20)
    qid = qid if re.fullmatch(r"Q\d{1,12}", qid) else None
    facts = {}
    if qid:
        try:
            facts = wikidata_facts(qid)
        except Exception:  # noqa: BLE001 - fallback data is best-effort
            facts = {}

    image, image_file, image_status = None, None, "skipped"
    if with_image:
        image, image_file, image_status = fetch_image(page, _str(page.get("title"), 300) or title)
    image_failed = image_status not in ("ok", "none_on_wikipedia", "skipped")
    url = _str(page.get("fullurl"), 1000)

    rec = {
        "title": _str(page.get("title"), 300) or title,
        "description": _str(page.get("description"), 300),
        "summary": short_summary(lead),
        "url": url if is_wikipedia_url(url) else "",
        "image_file": image_file if image else None,
        "image_status": image_status,
        "wiki_mass_kg": mass, "wiki_mass_source": mass_src,
        "wiki_length_m": length, "wiki_length_source": length_src,
        "qid": qid, "sci_name": facts.get("sci_name"), "rank": facts.get("rank"),
        "wd_mass_kg": facts.get("mass_kg"), "wd_length_m": facts.get("length_m"),
        "image": image, "image_failed": image_failed,
        "parser_version": PARSER_VERSION, "fetched": time.strftime("%Y-%m-%d"),
    }
    return traits.enrich(rec)


WIKIDATA = "https://www.wikidata.org/w/api.php"
RANKS = {"Q7432": "species", "Q68947": "subspecies", "Q34740": "genus", "Q35409": "family",
         "Q36602": "order", "Q37517": "class", "Q164280": "subfamily", "Q2136103": "superfamily"}
MASS_UNITS_WD = {"Q11570": 1.0, "Q41803": 1e-3, "Q3241121": 1e-6, "Q191118": 1000.0,
                 "Q100995": 0.45359, "Q48013": 0.02835}
LENGTH_UNITS_WD = {"Q11573": 1.0, "Q174728": 0.01, "Q174789": 0.001, "Q3710": 0.3048,
                   "Q218593": 0.0254, "Q828224": 1000.0}


def _wd_quantities(claims: dict, prop: str, units: dict) -> float | None:
    vals = []
    for c in claims.get(prop) or []:
        if not isinstance(c, dict):
            continue
        dv = (c.get("mainsnak") or {}).get("datavalue") or {}
        v = dv.get("value") if isinstance(dv, dict) else None
        if not isinstance(v, dict):
            continue
        unit = str(v.get("unit", "")).rsplit("/", 1)[-1]
        if unit in units:
            try:
                vals.append(float(v["amount"]) * units[unit])
            except (KeyError, TypeError, ValueError):
                pass
    vals = [x for x in vals if math.isfinite(x) and 0 < x < 1e7]
    return statistics.median(vals) if vals else None


def wikidata_facts(qid: str) -> dict:
    """Scientific name, rank, and any mass/length statements from the Wikidata item."""
    if not re.fullmatch(r"Q\d{1,12}", str(qid)):
        raise ValueError(f"invalid Wikidata id: {qid!r}")
    data = _get_json(WIKIDATA, {"action": "wbgetentities", "ids": qid, "props": "claims",
                                "format": "json"}, retries=3)
    entity = (data.get("entities") or {}).get(qid)
    claims = entity.get("claims") if isinstance(entity, dict) else None
    if not isinstance(claims, dict):
        claims = {}

    def first_value(prop):
        for c in claims.get(prop) or []:
            if isinstance(c, dict):
                v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if v:
                    return v
        return None

    name = first_value("P225")
    rank = first_value("P105")
    return {
        "sci_name": name[:200] if isinstance(name, str) else None,
        "rank": RANKS.get(rank.get("id")) if isinstance(rank, dict) else None,
        "mass_kg": _wd_quantities(claims, "P2067", MASS_UNITS_WD),
        "length_m": _wd_quantities(claims, "P2043", LENGTH_UNITS_WD),
    }


def is_wikipedia_url(url) -> bool:
    """True only for https://en.wikipedia.org/... (safe to open in a browser)."""
    if not isinstance(url, str):
        return False
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "en.wikipedia.org"


def image_page_url(file_name) -> str:
    """Wikipedia file page (author + licence) for an image name such as 'Cat_poster_1.jpg'."""
    if not isinstance(file_name, str) or not file_name:
        return ""
    return "https://en.wikipedia.org/wiki/File:" + urllib.parse.quote(file_name.replace(" ", "_"))


def is_stale(rec: dict) -> bool:
    """True if a cached record is from an older parser, its image failed, or it lacks attribution."""
    if os.environ.get("TV_REFRESH_ALL") and rec.get("fetched") != time.strftime("%Y-%m-%d"):
        return True
    version = rec.get("parser_version")
    if not isinstance(version, int) or version < PARSER_VERSION:
        return True
    if rec.get("image_failed"):
        return True
    if not rec.get("image"):
        # No picture: refetch unless Wikipedia genuinely has none for this article.
        return rec.get("image_status") != "none_on_wikipedia"
    return "image_file" not in rec                                  # pre-attribution record


def short_summary(extract: str, max_chars: int = 420) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", extract.strip())
    out = ""
    for s in sentences:
        if out and len(out) + len(s) > max_chars:
            break
        out = (out + " " + s).strip()
    return out


def estimate_mass_from_length(length_m: float) -> float:
    # Typical animal: mass ~ 25 kg/m^3 * L^3 (between slender and stocky bodies)
    return 25.0 * length_m ** 3


# -- Parsing -------------------------------------------------------------------------------------
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_RANGE = _NUM + r"(?:\s*(?:–|—|-|to|and)\s*" + _NUM + r")?\s*"

MASS_UNITS = {
    "kg": 1.0, "kilogram": 1.0, "kilograms": 1.0,
    "g": 1e-3, "gram": 1e-3, "grams": 1e-3,
    "mg": 1e-6, "milligram": 1e-6, "milligrams": 1e-6,
    "t": 1000.0, "tonne": 1000.0, "tonnes": 1000.0, "metric tons": 1000.0,
    "lb": 0.4536, "lbs": 0.4536, "pound": 0.4536, "pounds": 0.4536,
    "oz": 0.02835, "ounce": 0.02835, "ounces": 0.02835,
    "short tons": 907.2, "long tons": 1016.0, "tons": 907.2,
}
LENGTH_UNITS = {
    "m": 1.0, "metre": 1.0, "metres": 1.0, "meter": 1.0, "meters": 1.0,
    "cm": 0.01, "centimetre": 0.01, "centimetres": 0.01, "centimeter": 0.01, "centimeters": 0.01,
    "mm": 0.001, "millimetre": 0.001, "millimetres": 0.001, "millimeter": 0.001, "millimeters": 0.001,
    "ft": 0.3048, "feet": 0.3048, "foot": 0.3048,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
}
METRIC = {"kg", "kilogram", "kilograms", "g", "gram", "grams", "mg", "milligram", "milligrams",
          "t", "tonne", "tonnes", "metric tons", "m", "metre", "metres", "meter", "meters", "cm",
          "centimetre", "centimetres", "centimeter", "centimeters", "mm", "millimetre",
          "millimetres", "millimeter", "millimeters"}


def _unit_regex(units: dict, exclude_bare: set[str]) -> re.Pattern:
    names = sorted(units, key=len, reverse=True)
    alts = []
    for n in names:
        if n in exclude_bare:  # ambiguous words ("in", "t"): only accept before ")" or "/"
            alts.append(re.escape(n) + r"(?=\s*[()/;,])")
        else:
            alts.append(re.escape(n) + r"\b")
    return re.compile(_RANGE + "(" + "|".join(alts) + ")", re.I)


_MASS_RE = _unit_regex(MASS_UNITS, {"t"})
_LENGTH_RE = _unit_regex(LENGTH_UNITS, {"in"})

_MASS_KEYS = re.compile(r"\b(weigh\w*|weight|mass|heav\w*)\b", re.I)
_LENGTH_KEYS = re.compile(r"\b(length|long|measur\w*|size|head[- ]and[- ]body|body)\b", re.I)
_SKIP_SENTENCE = re.compile(
    r"\b(birth|born|newborn|neonat\w*|cubs?|calf|calves|chicks?|eggs?|hatchlings?|hatch\w*|pups?|"
    r"kittens?|foals?|larva\w*|juveniles?|record\w*|fossil\w*|extinct|ancestor\w*|brain|heart|"
    r"liver|kidney|testes|food|prey|eats?|consum\w*|daily|per day|litter|meat|captivity)\b", re.I)
_SKIP_BEFORE = re.compile(
    r"\b(tail|wing\s*span|wingspan|wings?|ears?|horns?|tusks?|antlers?|beak|bill|legs?|trunk|"
    r"teeth|tooth|claws?|feathers?|shoulders?|hind\s*foot|skull|neck|fins?|canines?|tongue)\b"
    r"[^.;]{0,25}$|\b(up to|as much as|maximum|max\.?|exceed\w*|over|more than|less than)\s*$", re.I)
_SKIP_AFTER = re.compile(
    r"^\s*(\([^)]*\))?\s*(in\s+height|tall|high|at\s+the\s+shoulder|long\s+tails?|tails?\b|"
    r"wing\s*span|wingspan|at\s+the\s+withers|hands?\b|or\s+more|or\s+larger|or\s+heavier|\+)", re.I)


_LENGTH_SKIP = re.compile(r"\b(withers|hands high|\d+(\.\d+)? hands)\b", re.I)  # horse heights
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])|(?<=[.!?][\"”’'])\s+(?=[A-Z\"“])")


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\{\\displaystyle[^}]*\}", " ", text)
    out = []
    for line in text.splitlines():           # paragraphs and "== Heading ==" lines never merge
        line = line.strip()
        if not line or line.startswith("="):
            continue
        out.extend(_SPLIT.split(line))
    return [s.strip() for s in out if s.strip()]


def _to_float(s: str) -> float:
    v = float(s.replace(",", ""))
    return v if math.isfinite(v) else 0.0


def _parse(text: str, regex: re.Pattern, units: dict, keys: re.Pattern,
           skip: re.Pattern | None = None) -> tuple[float | None, str]:
    metric, imperial = [], []
    for sent in _sentences(text):
        if not keys.search(sent) or _SKIP_SENTENCE.search(sent) or (skip and skip.search(sent)):
            continue
        for m in regex.finditer(sent):
            if _SKIP_BEFORE.search(sent[max(0, m.start() - 40):m.start()]) or \
                    _SKIP_AFTER.search(sent[m.end():m.end() + 40]):
                continue
            lo = _to_float(m.group(1))
            hi = _to_float(m.group(2)) if m.group(2) else lo
            if hi < lo:  # e.g. "1.5 to 2" fine; garbage otherwise
                lo, hi = hi, lo
            unit = m.group(3).lower()
            unit = re.sub(r"\s+", " ", unit)
            # plain midpoint for normal ranges; geometric mean for very wide ones (10 g – 3 kg)
            mid = math.sqrt(lo * hi) if lo > 0 and hi / lo > 3 else (lo + hi) / 2
            val = mid * units[unit]
            if not (math.isfinite(val) and 0 < val < 1e7):
                continue
            (metric if unit in METRIC else imperial).append((val, sent.strip()[:300]))
    found = metric or imperial
    if not found:
        return None, ""
    found.sort(key=lambda x: x[0])
    value = statistics.median(v for v, _ in found)
    source = min(found, key=lambda x: abs(x[0] - value))[1]
    return value, source


def parse_mass(text: str) -> tuple[float | None, str]:
    """Return (typical adult mass in kg, sentence it came from)."""
    return _parse(text, _MASS_RE, MASS_UNITS, _MASS_KEYS)


def parse_length(text: str) -> tuple[float | None, str]:
    """Return (typical body length in m, sentence it came from)."""
    return _parse(text, _LENGTH_RE, LENGTH_UNITS, _LENGTH_KEYS, _LENGTH_SKIP)
