"""Fallback data: dataset parsing, index building, lookup levels and source priority."""
import gzip
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fallback_data  # noqa: E402
from app import traits  # noqa: E402

AMNIOTE = """class,order,family,genus,species,subspecies,common_name,adult_body_mass_g,adult_svl_cm,female_body_mass_g
Mammalia,Rodentia,Sciuridae,Sciurus,carolinensis,-999,Eastern gray squirrel,520,25.5,-999
Mammalia,Rodentia,Sciuridae,Sciurus,vulgaris,-999,Red squirrel,300,21,-999
Mammalia,Rodentia,Sciuridae,Marmota,monax,-999,Groundhog,4000,-999,-999
Mammalia,Carnivora,Felidae,Felis,catus,-999,Cat,4500,46,-999
Aves,Passeriformes,Passeridae,Passer,domesticus,-999,House sparrow,30,-999,-999
"""
AMPHIBIO = """id,Order,Family,Genus,Species,Body_mass_g,Body_size_mm
1,Anura,Ranidae,Rana,Rana temporaria,22.5,110
2,Anura,Bufonidae,Rhinella,Rhinella marina,1500,240
"""
ANIMALTRAITS = '''"phylum","class","order","family","genus","species","specificEpithet","sex","body mass","body mass - units"
"Arthropoda","Insecta","Hymenoptera","Apidae","Apis","Apis mellifera","mellifera","female","0.0001","kg"
"Arthropoda","Insecta","Hymenoptera","Apidae","Apis","Apis mellifera","mellifera","male","0.0002","kg"
"Chordata","Mammalia","Carnivora","Felidae","Felis","Felis catus","catus","","9.0","kg"
'''


class FallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        fallback_data.RAW = d / "sources"
        fallback_data.RAW.mkdir()
        fallback_data.OUT = d / "traits.json.gz"
        (fallback_data.RAW / "Amniote_Database_Aug_2015.csv").write_text(AMNIOTE)
        (fallback_data.RAW / "AmphiBIO_v1.csv").write_text(AMPHIBIO, encoding="cp1252")
        (fallback_data.RAW / "observations.csv").write_text(ANIMALTRAITS)
        with redirect_stdout(io.StringIO()):
            fallback_data.build()
        traits._index = None
        traits.index(fallback_data.OUT)
        cls.ov_path = d / "overrides.csv"
        traits.write_overrides([{"title": "Goldfish", "mass_kg": "0.3", "length_m": "0.2",
                                 "source": "test"}], cls.ov_path)
        traits._overrides = traits.load_overrides(cls.ov_path)
        traits.index(fallback_data.OUT)

    @classmethod
    def tearDownClass(cls):
        traits.reload()
        cls.tmp.cleanup()

    def setUp(self):
        traits._index = None
        traits.index(fallback_data.OUT)
        traits._overrides = traits.load_overrides(self.ov_path)

    def test_index_contents(self):
        with gzip.open(fallback_data.OUT, "rt") as f:
            idx = json.load(f)
        self.assertEqual(idx["species"]["felis catus"][:4], [4.5, "amniote", 0.46, "amniote"])
        self.assertEqual(idx["species"]["rana temporaria"][1], "amphibio")      # binomial column
        self.assertAlmostEqual(idx["species"]["apis mellifera"][0], 0.00015)    # median of rows
        self.assertEqual(idx["counts"]["amniote"], 5)

    def test_species_genus_family_levels(self):
        self.assertEqual(traits.lookup("Sciurus carolinensis")["mass"][3], "species")
        hit = traits.lookup("Sciurus niger")["mass"]                 # unknown species, known genus
        self.assertEqual((hit[0], hit[3]), (0.41, "genus"))
        fam = traits.lookup("Sciuridae")["mass"]                     # family-level article
        self.assertEqual((fam[0], fam[3]), (0.52, "family"))
        self.assertIsNone(traits.lookup("Nonexistus fakeus")["mass"])

    def test_priority_wikipedia_then_db(self):
        rec = traits.enrich({"title": "Cat", "sci_name": "Felis catus", "wiki_mass_kg": 4.0,
                             "wiki_mass_source": "weighs 4 kg", "wiki_length_m": None})
        self.assertEqual((rec["mass_kg"], rec["mass_origin"]), (4.0, "Wikipedia"))
        self.assertEqual(rec["length_m"], 0.46)
        self.assertIn("Amniote", rec["length_origin"])

    def test_inconsistent_wikipedia_replaced(self):
        rec = traits.enrich({"title": "Cat", "sci_name": "Felis catus", "wiki_mass_kg": 45.0})
        self.assertEqual(rec["mass_kg"], 4.5)
        self.assertTrue(rec["data_notes"])

    def test_wikidata_before_higher_taxa(self):
        rec = traits.enrich({"title": "Fox squirrel", "sci_name": "Sciurus niger",
                             "wiki_mass_kg": None, "wd_mass_kg": 0.8})
        self.assertEqual((rec["mass_kg"], rec["mass_origin"]), (0.8, "Wikidata"))

    def test_override_wins(self):
        rec = traits.enrich({"title": "Goldfish", "sci_name": "Carassius auratus", "wiki_mass_kg": 0.1})
        self.assertEqual((rec["mass_kg"], rec["mass_origin"], rec["mass_source"]), (0.3, "Override", "test"))

    def test_invertebrate_from_animaltraits(self):
        rec = traits.enrich({"title": "Western honey bee", "sci_name": "Apis mellifera"})
        self.assertAlmostEqual(rec["mass_kg"], 0.00015)
        self.assertIn("AnimalTraits", rec["mass_origin"])

    def test_estimate_last_resort(self):
        rec = traits.enrich({"title": "Mystery", "sci_name": None, "wiki_length_m": 1.0})
        self.assertTrue(rec["mass_estimated"])
        rec = traits.enrich({"title": "Mystery", "sci_name": None})
        self.assertIsNone(rec["mass_kg"])

    def test_old_record_compat(self):
        rec = traits.enrich({"title": "Horse", "mass_kg": 500, "mass_source": "s", "length_m": None})
        self.assertEqual((rec["mass_kg"], rec["mass_origin"]), (500, "Wikipedia"))

    def test_zip_extraction(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Full Archive/Amniote_Database_Aug_2015.csv", "a,b\n1,2\n")
        self.assertEqual(fallback_data._extract(buf.getvalue(), "amniote_database_aug_2015.csv"),
                         b"a,b\n1,2\n")
        self.assertIsNone(fallback_data._extract(b"<!DOCTYPE html><html>", "x.csv"))


if __name__ == "__main__":
    unittest.main()
