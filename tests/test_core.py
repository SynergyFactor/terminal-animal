import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import physics                                   # noqa: E402
from app.cache import AnimalCache                         # noqa: E402
from app.wiki import parse_length, parse_mass, short_summary  # noqa: E402

CAT = ("The domestic cat is a small carnivorous mammal. It averages about 46 cm (18 in) in "
       "head-to-body length and 23–25 cm (9.1–9.8 in) in height, with about 30 cm (12 in) long "
       "tails. Adult domestic cats typically weigh 4–5 kg (8.8–11.0 lb). Kittens weigh about 100 g "
       "at birth. The heaviest cat on record weighed 21.3 kg.")
ELEPHANT = ("The African bush elephant is the largest living terrestrial animal, with bulls "
            "reaching a shoulder height of up to 3.96 m (13.0 ft) and a body mass of up to 10.4 t "
            "(11.5 short tons). Males weigh 6,000 kg (13,000 lb) on average. The trunk is up to "
            "2 m long. Its body length is 6–7.5 m (20–25 ft).")
SPARROW = ("The house sparrow is a bird of the sparrow family. It is typically about 16 cm (6.3 in) "
           "long and weighs 24–39.5 g (0.85–1.39 oz). Its wingspan is 19–25 cm. Eggs weigh 2.9 g.")
IMPERIAL = "Adults weigh between 150 and 250 pounds and measure 5 to 6 feet in length."
NONE = "This animal is poorly studied and little is known about it."


class ParseTests(unittest.TestCase):
    def test_cat(self):
        self.assertAlmostEqual(parse_mass(CAT)[0], 4.5)
        self.assertAlmostEqual(parse_length(CAT)[0], 0.46)

    def test_elephant(self):
        self.assertAlmostEqual(parse_mass(ELEPHANT)[0], 6000)
        self.assertAlmostEqual(parse_length(ELEPHANT)[0], 6.75)

    def test_sparrow(self):
        self.assertAlmostEqual(parse_mass(SPARROW)[0], 0.03175, places=4)
        self.assertAlmostEqual(parse_length(SPARROW)[0], 0.16)

    def test_imperial_only(self):
        self.assertAlmostEqual(parse_mass(IMPERIAL)[0], 200 * 0.4536, places=2)
        self.assertAlmostEqual(parse_length(IMPERIAL)[0], 5.5 * 0.3048, places=3)

    def test_nothing(self):
        self.assertEqual(parse_mass(NONE), (None, ""))
        self.assertEqual(parse_length(NONE), (None, ""))

    def test_summary(self):
        self.assertLessEqual(len(short_summary(CAT * 5)), 420)


SQUIRREL = ("A group of squirrels is called a \"dray\" or a \"scurry\".\n\n\n== Characteristics ==\n\n\n"
            "Squirrels are generally small animals, ranging in size from the African pygmy squirrel "
            "at 10–14 cm (3.9–5.5 in) in total length and just 12–26 g (0.42–0.92 oz) in weight, to "
            "the Bhutan giant flying squirrel at up to 1.27 m (4 ft 2 in) in total length, and "
            "several marmot species, which can weigh 8 kg (18 lb) or more.")
HORSE = ("A pony is any horse measuring less than 148 centimetres (58.27 in) at the withers, which "
         "is just over 14.2 hands (58 inches, 147 cm). A horse weighs on average 500 kilograms (1,100 lb).")


class HeadingAndHeightTests(unittest.TestCase):
    def test_headings_not_merged(self):
        _, src = parse_mass(SQUIRREL)
        self.assertNotIn("==", src)
        self.assertNotIn("dray", src)

    def test_or_more_and_up_to_skipped(self):
        self.assertAlmostEqual(parse_mass(SQUIRREL)[0], 0.019, places=3)
        self.assertAlmostEqual(parse_length(SQUIRREL)[0], 0.12, places=2)

    def test_withers_height_ignored(self):
        self.assertEqual(parse_length(HORSE)[0], None)
        self.assertEqual(parse_mass(HORSE)[0], 500)

    def test_inconsistent_length_ignored(self):
        r = physics.compute(4.0, 0.12)   # 4 kg can't be 12 cm long
        self.assertGreater(r.length_m, 0.2)
        self.assertLess(r.v_t, 45)


class PhysicsTests(unittest.TestCase):
    def test_human_in_known_range(self):
        r = physics.compute(70, 1.75)
        self.assertTrue(45 < r.v_t < 60)        # skydiver belly-down ~53 m/s
        self.assertGreater(r.injury_prob, 0.95)

    def test_scaling(self):
        mouse, cat, horse = physics.compute(0.02), physics.compute(4.5), physics.compute(500)
        self.assertLess(mouse.v_t, cat.v_t)
        self.assertLess(cat.v_t, horse.v_t)
        self.assertLess(mouse.injury_prob, 0.01)
        self.assertIsNone(mouse.h50)

    def test_curves_consistent(self):
        r = physics.compute(70)
        self.assertAlmostEqual(r.speed_at_distance(r.d_99) / r.v_t, 0.99, places=6)
        self.assertAlmostEqual(r.speed_at_time(r.t_99) / r.v_t, 0.99, places=6)
        self.assertAlmostEqual(r.injury_at_height(r.h50), 0.5, places=6)

    def test_cd_override(self):
        self.assertAlmostEqual(physics.compute(10, cd=2.0).cd, 2.0)


class CacheTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            c = AnimalCache(bundled=Path(d) / "none.json", user=Path(d) / "u.json")
            c.put({"title": "Cat", "description": "small mammal", "image": b"\x89PNG"})
            c2 = AnimalCache(bundled=Path(d) / "none.json", user=Path(d) / "u.json")
            self.assertEqual(c2.get("cat")["image"], b"\x89PNG")
            self.assertEqual(c2.search("ca")[0]["title"], "Cat")
            self.assertEqual(c2.search("mammal")[0]["title"], "Cat")


if __name__ == "__main__":
    unittest.main()


class RobustnessTests(unittest.TestCase):
    def test_physics_rejects_bad_input(self):
        for bad in (0, -1, float("nan"), float("inf"), "x", None, 2e6):
            with self.assertRaises(ValueError):
                physics.compute(bad)
        with self.assertRaises(ValueError):
            physics.compute(70, cd=0)
        self.assertGreater(physics.compute(70, float("nan")).v_t, 0)   # bad length is ignored

    def test_damaged_cache_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            cases = {"garbage.json": b"{not json", "list.json": b"[1, 2]", "binary.json": b"\xff\xfe\x00",
                     "mixed.json": json.dumps({"ok": {"title": "Cat", "image": "!!notbase64!!"},
                                               "bad1": [], "bad2": {"title": 5},
                                               "bad3": {"title": "  "}}).encode()}
            for name, blob in cases.items():
                (d / name).write_bytes(blob)
                if name == "mixed.json":                 # readable file, bad entries dropped quietly
                    c = AnimalCache(bundled=d / name, user=d / "user.json")
                else:                                     # damaged file: warned about, then ignored
                    with self.assertLogs("app.cache", level="WARNING") as logs:
                        c = AnimalCache(bundled=d / name, user=d / "user.json")
                    self.assertIn(name, logs.output[0])
                if name == "mixed.json":
                    self.assertEqual(c.titles(), ["Cat"])
                    self.assertIsNone(c.get("cat")["image"])        # corrupt image -> None
                else:
                    self.assertEqual(c.titles(), [])

    def test_user_cache_is_capped(self):
        from app import cache as cache_mod
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cache_mod, "MAX_USER_ENTRIES", 3):
            c = AnimalCache(bundled=Path(d) / "none.json", user=Path(d) / "u.json")
            for i in range(5):
                c.put({"title": f"Animal {i}"})
            saved = json.loads((Path(d) / "u.json").read_text())
            self.assertEqual(sorted(saved), ["animal 2", "animal 3", "animal 4"])
            c.put({"title": 7})                                   # invalid record is ignored

    def test_damaged_trait_index(self):
        from app import traits
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json.gz"
            p.write_bytes(gzip.compress(b'{"species": {"a b": [1, "x"')[:-8])   # truncated
            traits._index = None
            try:
                with self.assertLogs("app.traits", level="WARNING") as logs:
                    idx = traits.index(p)
                self.assertIn("damaged trait index", logs.output[0])
                self.assertEqual(idx["species"], {})
                self.assertIsNone(traits.lookup("a b")["mass"])
            finally:
                traits.reload()

    def test_overrides_formula_escaping(self):
        from app import traits
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "o.csv"
            traits.write_overrides([{"title": "Cat", "mass_kg": "4", "source": "=HYPERLINK(\"x\")"},
                                    {"title": "Dog", "mass_kg": "-5", "source": "@cmd"}], p)
            text = p.read_text()
            self.assertIn("'=HYPERLINK", text)
            self.assertIn("'@cmd", text)
            rows = traits.load_overrides(p)
            self.assertEqual(rows["cat"]["source"], '=HYPERLINK("x")')       # round-trips
            self.assertEqual(rows["dog"]["mass_kg"], "-5")                   # numbers untouched
            self.assertIsNone(traits._num(rows["dog"]["mass_kg"]))          # but rejected as mass
            traits.reload()
