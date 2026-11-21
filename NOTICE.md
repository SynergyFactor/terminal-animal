# Third-party notices

The source code in this repository is MIT-licensed (see `LICENSE`). The data and components
below keep their own licences. This summary is provided in good faith and is not legal advice.

## Data bundled in `data/`

| File | Contents | Source and licence |
|---|---|---|
| `animals.json` | Article excerpts, descriptions, thumbnails, parsed sizes | Text: English Wikipedia, [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Images: Wikipedia / Wikimedia Commons, under each file's own licence. Taxon names and some sizes: [Wikidata](https://www.wikidata.org/), CC0. Per-animal links are in `data/ATTRIBUTION.md`. |
| `traits.json.gz` | Condensed body-mass/length index | Derived from the three databases below |
| `overrides.csv` | Hand-entered values | Each row cites its own source |

Because `animals.json` contains CC BY-SA text, redistributed copies of that file (including inside
the built app) must keep the attribution and stay under CC BY-SA 4.0. Images are only included as
small thumbnails; check `data/ATTRIBUTION.md` and each file page if you reuse them elsewhere.

### Fallback trait databases

- **Amniote Life History Database:** Myhrvold, N.P., Baldridge, E., Chan, B., Sivam, D.,
  Freeman, D.L. & Ernest, S.K.M. (2015). *An amniote life-history database to perform comparative
  analyses with birds, mammals, and reptiles.* Ecology 96:3109. CC0.
  https://doi.org/10.6084/m9.figshare.3563457.v1
- **AmphiBIO:** Oliveira, B.F., São-Pedro, V.A., Santos-Barrera, G., Penone, C. & Costa, G.C.
  (2017). *AmphiBIO, a global database for amphibian ecological traits.* Scientific Data 4:170123.
  **CC BY 4.0, which requires this attribution.** https://doi.org/10.6084/m9.figshare.4644424.v5
- **AnimalTraits:** Herberstein, M.E. et al. (2022). *AnimalTraits — a curated animal trait
  database for body mass, metabolic rate and brain size.* Scientific Data 9:265. CC0.
  https://doi.org/10.5281/zenodo.6468938

Raw dataset files are downloaded into `sources/` at build time and are not committed.

## Software dependencies

| Component | Licence | Notes |
|---|---|---|
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/) | LGPL-3.0 (or GPL / commercial) | If you distribute the built app, LGPL lets users replace the Qt libraries. A `--onedir` build keeps them as separate files, which makes that straightforward. |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 | CA certificate bundle |
| [PyInstaller](https://pyinstaller.org/) | GPL-2.0 with bootloader exception | Build tool only; the exception allows distributing the built app under any licence |
| Python standard library | PSF License | |
