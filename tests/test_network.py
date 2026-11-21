"""Throttling/backoff and cache-manager tests against a local fake Wikipedia (no internet)."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import wiki  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"fakejpeg"
ARTICLE = ("The test cat is a small mammal.\nAdult test cats typically weigh 4–5 kg (8.8–11.0 lb). "
           "It averages about 46 cm (18 in) in head-to-body length.")


class FakeWiki(BaseHTTPRequestHandler):
    fail_first = 0          # respond 429 to this many requests first
    thumb = None            # override the thumbnail URL the fake API reports (False = no thumbnail)
    rest_thumb = None       # thumbnail the fake summary service reports
    times: list = []
    count = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with FakeWiki.lock:
            FakeWiki.count += 1
            n = FakeWiki.count
            FakeWiki.times.append(time.monotonic())
        if n <= FakeWiki.fail_first:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if q.get("action") == ["wbgetentities"]:
            qid = q["ids"][0]
            claims = {"P225": [{"mainsnak": {"datavalue": {"value": "Testus felis"}}}],
                      "P105": [{"mainsnak": {"datavalue": {"value": {"id": "Q7432"}}}}],
                      "P2043": [{"mainsnak": {"datavalue": {"value": {
                          "amount": "+50", "unit": "http://www.wikidata.org/entity/Q174728"}}}}]}
            body = json.dumps({"entities": {qid: {"claims": claims}}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path.startswith("/rest/"):
            self._json({"thumbnail": {"source": FakeWiki.rest_thumb}} if FakeWiki.rest_thumb else {})
            return
        if url.path == "/redirect-to-http":
            self.send_response(302)
            self.send_header("Location", "http://example.com/evil.json")
            self.end_headers()
            return
        if url.path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 2_000_000)
            return
        if url.path == "/api-error":
            self._json({"error": {"code": "badtitle", "info": "Bad title"}})
            return
        if url.path == "/not-image.jpg":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>error page</html>")
            return
        if url.path.endswith(".zip"):
            buf = io.BytesIO()
            import zipfile
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("Archive/readme.txt", "x")
                z.writestr("Archive/Amniote_Database_Aug_2015.csv", "genus,species\nFelis,catus\n")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(buf.getvalue())
            return
        if url.path.endswith(".jpg"):
            body = JPEG
        else:
            title = parse_qs(url.query).get("titles", ["X"])[0]
            body = json.dumps({"query": {"pages": [{
                "title": title, "description": "test animal", "extract": ARTICLE,
                "fullurl": f"https://en.wikipedia.org/wiki/{title}",
                "pageprops": {"wikibase_item": "Q42"},
                **({} if FakeWiki.thumb is False else {
                    "thumbnail": {"source": FakeWiki.thumb or
                                  f"http://127.0.0.1:{self.server.server_port}/img.jpg"},
                    "pageimage": "Test_cat.jpg"}),
            }]}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


class NetBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeWiki)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.old_api, cls.old_wd, cls.old_rest = wiki.API, wiki.WIKIDATA, wiki.REST_SUMMARY
        wiki.REST_SUMMARY = f"http://127.0.0.1:{cls.srv.server_port}/rest/"
        wiki.API = f"http://127.0.0.1:{cls.srv.server_port}/w/api.php"
        wiki.WIKIDATA = f"http://127.0.0.1:{cls.srv.server_port}/wd/api.php"
        wiki.BACKOFF_BASE = 0.2

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        wiki.API, wiki.WIKIDATA, wiki.REST_SUMMARY = cls.old_api, cls.old_wd, cls.old_rest

    def setUp(self):
        FakeWiki.count, FakeWiki.times, FakeWiki.fail_first, FakeWiki.thumb = 0, [], 0, None
        FakeWiki.rest_thumb = None
        wiki.LIMITER._next = 0.0


class ThrottleTests(NetBase):
    def test_requests_are_spaced(self):
        interval = 0.15
        wiki.set_request_interval(interval)
        slots, real_wait = [], wiki.LIMITER.wait

        def recording_wait():                                  # when the throttle releases each request
            real_wait()
            slots.append(time.perf_counter())

        with mock.patch.object(wiki.LIMITER, "wait", recording_wait):
            threads = [threading.Thread(target=wiki.fetch_animal, args=("Cat",)) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(FakeWiki.times), 9)               # page + Wikidata + image, x3
        slots.sort()
        gaps = [b - a for a, b in zip(slots, slots[1:])]
        # The throttle itself: every release at least one interval apart, across threads.
        self.assertTrue(all(g >= interval - 0.005 for g in gaps), gaps)
        # On the wire, individual arrivals jitter with thread scheduling (notably on Windows),
        # but the overall rate must still hold.
        span = FakeWiki.times[-1] - FakeWiki.times[0]
        self.assertGreaterEqual(span, (len(FakeWiki.times) - 1) * interval * 0.9)

    def test_429_is_retried(self):
        wiki.set_request_interval(0.05)
        FakeWiki.fail_first = 3
        rec = wiki.fetch_animal("Cat")
        self.assertEqual(rec["mass_kg"], 4.5)
        self.assertEqual(rec["image"], JPEG)
        self.assertEqual(rec["image_file"], "Test_cat.jpg")
        self.assertFalse(rec["image_failed"])
        self.assertEqual(rec["parser_version"], wiki.PARSER_VERSION)
        self.assertEqual((rec["sci_name"], rec["rank"], rec["qid"]), ("Testus felis", "species", "Q42"))
        self.assertAlmostEqual(rec["wd_length_m"], 0.5)
        self.assertEqual(rec["length_m"], 0.46)                # Wikipedia text still wins
        self.assertEqual(rec["mass_origin"], "Wikipedia")

    def test_gives_up_eventually(self):
        wiki.set_request_interval(0.01)
        FakeWiki.fail_first = 100
        with self.assertRaises(Exception):
            wiki.search("ca")                                   # search only retries twice


class HardeningTests(NetBase):
    def base(self):
        return f"http://127.0.0.1:{self.srv.server_port}"

    def test_non_https_refused(self):
        for url in ("http://example.com/x", "file:///etc/passwd", "ftp://example.com/x", "https://"):
            with self.assertRaises(ValueError):
                wiki._get(url, retries=0)

    def test_redirect_downgrade_blocked(self):
        with self.assertRaises(ValueError):
            wiki._get(self.base() + "/redirect-to-http", retries=0)

    def test_size_cap(self):
        with self.assertRaises(ValueError):
            wiki._get(self.base() + "/big", retries=0, max_bytes=1_000_000)

    def test_api_error_raised(self):
        with self.assertRaises(wiki.WikiError):
            wiki._get_json(self.base() + "/api-error", retries=0)

    def test_non_image_rejected(self):
        wiki.set_request_interval(0.01)
        FakeWiki.thumb = self.base() + "/not-image.jpg"
        rec = wiki.fetch_animal("Cat")
        self.assertIsNone(rec["image"])
        self.assertTrue(rec["image_failed"])

    def test_foreign_image_host_not_fetched(self):
        wiki.set_request_interval(0.01)
        FakeWiki.thumb = "https://evil.example.com/x.jpg"
        rec = wiki.fetch_animal("Cat")
        self.assertIsNone(rec["image"])
        self.assertEqual(FakeWiki.count, 2)                 # article + Wikidata only
        self.assertEqual(rec["image_status"], "blocked_host:evil.example.com")

    def test_link_and_contact_sanitising(self):
        self.assertTrue(wiki.is_wikipedia_url("https://en.wikipedia.org/wiki/Cat"))
        for bad in ("http://en.wikipedia.org/wiki/Cat", "https://en.wikipedia.org.evil.com/",
                    "javascript:alert(1)", "file:///c:/x", None, 42):
            self.assertFalse(wiki.is_wikipedia_url(bad), bad)
        with mock.patch.dict(os.environ, {"TV_CONTACT": "me@x.com\r\nX-Evil: 1 (é);"}):
            c = wiki._contact()
        self.assertNotIn("\n", c)
        self.assertTrue(c.isascii() and "(" not in c and ";" not in c)


class ImageTests(NetBase):
    def setUp(self):
        super().setUp()
        wiki.set_request_interval(0.01)

    def test_summary_service_fallback(self):
        FakeWiki.thumb = False
        FakeWiki.rest_thumb = (f"http://127.0.0.1:{self.srv.server_port}"
                               "/wikipedia/commons/thumb/a/ab/Felis_catus.jpg/330px-Felis_catus.jpg")
        rec = wiki.fetch_animal("Cat")
        self.assertEqual((rec["image"], rec["image_status"]), (JPEG, "ok"))
        self.assertEqual(rec["image_file"], "Felis_catus.jpg")
        self.assertFalse(wiki.is_stale(rec))

    def test_no_picture_anywhere_is_final(self):
        FakeWiki.thumb = False
        rec = wiki.fetch_animal("Cat")
        self.assertEqual(rec["image_status"], "none_on_wikipedia")
        self.assertFalse(rec["image_failed"])
        self.assertFalse(wiki.is_stale(rec))                    # not retried forever

    def test_blocked_host_is_reported_and_retried(self):
        FakeWiki.thumb = "https://evil.example.com/x.jpg"
        rec = wiki.fetch_animal("Cat")
        self.assertEqual(rec["image_status"], "blocked_host:evil.example.com")
        self.assertTrue(rec["image_failed"])
        self.assertTrue(wiki.is_stale(rec))

    def test_url_forms(self):
        self.assertEqual(wiki._normalise_image_url("//upload.wikimedia.org/a.jpg"),
                         "https://upload.wikimedia.org/a.jpg")
        self.assertTrue(wiki._image_url_ok(wiki._normalise_image_url("//upload.wikimedia.org/a.jpg")))
        # The address Wikimedia returns as of Nov 2026 (from --diagnose "Cat"):
        thumb = ("https://thumb.wikimedia.org/wikipedia/commons/thumb/2/25/Siam_lilacpoint.jpg/"
                 "330px-Siam_lilacpoint.jpg?utm_source=en.wikipedia.org&utm_campaign=api")
        self.assertTrue(wiki._image_url_ok(thumb))
        self.assertEqual(wiki._file_name_from_thumb(thumb), "Siam_lilacpoint.jpg")
        for bad in ("http://thumb.wikimedia.org/a.jpg", "https://wikimedia.org.evil.com/a.jpg",
                    "https://evilwikimedia.org/a.jpg", "https://example.com/wikimedia.org/a.jpg"):
            self.assertFalse(wiki._image_url_ok(bad), bad)
        self.assertEqual(wiki._file_name_from_thumb(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Cat03.jpg/330px-Cat03.jpg"), "Cat03.jpg")
        self.assertEqual(wiki._file_name_from_thumb(
            "https://upload.wikimedia.org/wikipedia/commons/3/3a/Cat%2003.jpg"), "Cat 03.jpg")

    def test_old_records_without_picture_are_refetched(self):
        base = {"title": "Cat", "parser_version": wiki.PARSER_VERSION, "image": None}
        self.assertTrue(wiki.is_stale({**base, "image_file": None}))          # 1.3.0 silent skip
        self.assertFalse(wiki.is_stale({**base, "image_status": "none_on_wikipedia"}))


class DownloadTests(NetBase):
    def test_download_extracts_from_zip_after_429(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import fallback_data
        wiki.set_request_interval(0.05)
        FakeWiki.fail_first = 1
        with tempfile.TemporaryDirectory() as d:
            old_raw, old_src = fallback_data.RAW, fallback_data.SOURCES
            fallback_data.RAW = Path(d)
            fallback_data.SOURCES = {"amniote": {**old_src["amniote"], "figshare": None,
                                                 "direct": [f"http://127.0.0.1:{self.srv.server_port}/a.zip"]}}
            fallback_data.SOURCES["amniote"].pop("figshare")
            try:
                with redirect_stdout(io.StringIO()) as out:
                    ok = fallback_data.download("amniote")
                self.assertTrue(ok, out.getvalue())
                self.assertEqual((Path(d) / "Amniote_Database_Aug_2015.csv").read_text(),
                                 "genus,species\nFelis,catus\n")
                self.assertIn("MD5", out.getvalue())          # differs from published -> noted, not fatal
            finally:
                fallback_data.RAW, fallback_data.SOURCES = old_raw, old_src


class CacheManagerTests(NetBase):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import build_cache
        self.bc = build_cache
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.bc.OUT = d / "animals.json"
        self.bc.LIST = d / "list.txt"
        self.bc.LIST.write_text("# comment\nCat\nDog\n", "utf-8")
        self.bc.SAVE_EVERY = 1

    def tearDown(self):
        self.tmp.cleanup()

    def run_mode(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.bc.main(["--delay", "0.2", "--yes", *args])
        return code, buf.getvalue(), json.loads(self.bc.OUT.read_text("utf-8"))

    def test_modes(self):
        code, out, cache = self.run_mode("--mode", "missing")
        self.assertEqual((code, sorted(cache)), (0, ["cat", "dog"]))

        code, out, _ = self.run_mode("--mode", "missing")
        self.assertIn("0 of 2 animals", out)                   # nothing to do

        cache["cat"]["parser_version"] = 1                     # simulate an old entry
        self.bc.OUT.write_text(json.dumps(cache), "utf-8")
        code, out, _ = self.run_mode("--mode", "stale")
        self.assertIn("1 of 2 animals", out)

        code, out, _ = self.run_mode("--mode", "update")
        self.assertIn("2 of 2 animals", out)

        code, out, cache = self.run_mode("--mode", "rebuild")
        self.assertTrue(self.bc.OUT.with_suffix(".json.bak").exists())
        self.assertEqual(sorted(cache), ["cat", "dog"])

    def test_update_keeps_old_data_on_failure(self):
        self.run_mode("--mode", "missing")
        before = self.bc.OUT.read_text("utf-8")
        FakeWiki.count, FakeWiki.fail_first = 0, 10_000
        with mock.patch.object(self.bc.time, "sleep", lambda s: None):   # skip the 30 s cool-off
            code, out, _ = self.run_mode("--mode", "update")
        self.assertEqual(code, 1)
        self.assertEqual(self.bc.OUT.read_text("utf-8"), before)


if __name__ == "__main__":
    unittest.main()
