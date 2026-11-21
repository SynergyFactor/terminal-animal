# Terminal Velocity — Animal Fall Calculator

[![tests](https://github.com/SynergyFactor/terminal-animal/actions/workflows/tests.yml/badge.svg)](https://github.com/SynergyFactor/terminal-animal/actions/workflows/tests.yml)
[![build](https://github.com/SynergyFactor/terminal-animal/actions/workflows/build.yml/badge.svg)](https://github.com/SynergyFactor/terminal-animal/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A small dark-themed desktop app (Python + PySide6) that estimates how fast an animal falls, using size data
scraped from Wikipedia.

- **Viewport:** the animal's Wikipedia image beside a chart you can switch between
  *speed vs distance*, *speed vs time* and *injury risk vs fall height*.
- **Search bar** (labelled *Search*): type a name and matching animals appear in a compact list underneath. Bundled
  animals show up instantly, and live Wikipedia results are added as you type. Use ↑/↓ and Enter, or click.
- **Stats panel:** a short description plus average mass and length, terminal velocity, distance and
  time to reach it, impact energy, severe-injury likelihood, the fall heights for 10 % and 50 % risk,
  drag coefficient and Reynolds number. You can override the mass or drag coefficient. The Wikipedia
  sentences each value came from, plus model notes, sit in a collapsible *Data sources & model notes*
  panel that's hidden by default.

**Requirements:** Python 3.10+ on Windows, macOS or Linux. The only dependencies are
PySide6-Essentials and certifi, plus PyInstaller for building.

## Quick start

```bash
git clone https://github.com/SynergyFactor/terminal-animal.git
cd terminal-animal
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py                     # run from source
python build.py                   # build dist/TerminalVelocity.exe (use --os macos / linux there)
```

The animal cache (`data/animals.json`) and fallback index (`data/traits.json.gz`) are committed,
so the app works offline right away and builds don't need to scrape Wikipedia.

## Manager (GUI for all terminal operations)

Run `manager.bat` (Windows) or `./manager.sh`. It has four tabs:

- **Cache:** add missing, update outdated, update all, re-apply fallback data, rebuild, and clear the
  app's local cache. Also sets the request delay and your Wikimedia contact.
- **Fallback data:** download the trait databases and build the index, with per-source status.
- **Missing data:** lists animals with missing, estimated or genus/family-average values. Type cited
  values in to save them as overrides.
- **Build & run:** builds the executable (target OS, one-folder, cache and fallback steps), runs the
  app from source and runs the tests.

Every button runs the same script listed below, so the log shows exactly what a terminal would.
A progress bar and Cancel button cover long jobs.

## Run from source

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_cache.py     # optional: scrape the offline cache (needs internet)
python run.py
```

## Build a standalone executable

| Target | Command (run on that OS) | Output |
|---|---|---|
| **Windows (default)** | `build.bat` or `python build.py` | `dist/TerminalVelocity.exe` |
| macOS | `./build.sh` or `python build.py --os macos` | `dist/TerminalVelocity.app` |
| Linux | `./build.sh` or `python build.py --os linux` | `dist/TerminalVelocity` |

The build installs the requirements, asks what to do with the offline cache (see below), then runs
PyInstaller. Flags: `--cache {ask,missing,stale,update,rebuild,skip}`, `--fallback {auto,refresh,skip}`, `--delay SECONDS`,
`--no-install`, `--onedir`. In CI or other non-interactive runs it defaults to `--cache missing`.

PyInstaller can't cross-compile, so each OS has to be built on that OS. To build all three from one
place, push the repo to GitHub and run the **build** workflow (`.github/workflows/build.yml`).
It defaults to Windows, and you can pick `windows,macos,linux`.

## Managing the cache

Run `cache.bat` (Windows) or `./cache.sh` with no arguments for a menu, or pass a mode:

| Command | What it does |
|---|---|
| `cache.bat --mode missing` | Add animals from `scripts/animals.txt` that aren't cached yet |
| `cache.bat --mode stale` | Re-scrape only entries made by an older parser, or whose image failed |
| `cache.bat --mode update` | Re-scrape everything in place; an entry keeps its old data if its fetch fails |
| `cache.bat --mode rebuild` | Delete the bundled cache (kept as `animals.json.bak`) and scrape from scratch |
| `cache.bat --clear-user` | Delete the app's local cache of animals looked up live |
| `cache.bat --mode apply` | Re-apply overrides and fallback databases to cached animals (offline) |
| `cache.bat --mode fallback` | Download the fallback databases and rebuild the trait index |
| `cache.bat --status` | Show what's cached, what's missing, where each mass came from, and why any pictures are missing |
| `cache.bat --diagnose "Cat"` | Show step by step what Wikipedia returns for one animal's picture |

Progress is saved every 5 animals, so you can stop with Ctrl+C and resume with `--mode missing` or
`--mode stale`. The app has its own flags too: `TerminalVelocity.exe --clear-cache` and
`--refresh-all`, which re-fetches each animal the next time you open it.

You normally won't need any of this after an update. Every record stores the parser version it was
made with, and the app shows an outdated record straight away while it re-fetches it in the background.

### Request rate

All Wikipedia requests go through one throttle shared by every thread. The app allows at most
2 requests per second and runs only one live search at a time, waiting 450 ms after you stop typing.
The cache scraper sends one request per second (change it with `--delay`). On HTTP 429 or 5xx, it
honours `Retry-After` and backs off exponentially (2, 4, 8, 16, 32 s), pausing every thread. Each
animal needs 3 requests: the article (text, description, thumbnail URL and Wikidata ID), its
Wikidata item (scientific name, rank, mass and length), and the image at a standard 330 px width. Anything still incomplete after a 30 s cool-off is retried at
half speed. Scraping all 150 animals takes about 8 minutes.

Wikimedia asks clients to identify themselves. Set `TV_CONTACT` to your email or project URL
(e.g. `set TV_CONTACT=you@example.com`) and it's added to the User-Agent. Clients with contact
details are less likely to be throttled.

## When Wikipedia doesn't have the number

Every animal's scientific name is resolved through Wikidata. Each value (mass, length) then comes
from the first source that has it:

1. **Your override** in `data/overrides.csv`, entered on the Manager's Missing data tab.
2. **Wikipedia text**, unless it disagrees more than 4× with a species-level database value, which
   catches parsing slips.
3. **Trait databases, species level.** Peer-reviewed compilations:

   | Database | Covers | Licence |
   |---|---|---|
   | Amniote Life History Database (Myhrvold et al. 2015) | ~21,300 mammals, birds, reptiles; median of literature values | CC0 |
   | AmphiBIO (Oliveira et al. 2017) | ~6,800 amphibians; *maximum* adult size | CC BY 4.0 |
   | AnimalTraits (Herberstein et al. 2022) | ~2,000 species incl. insects, spiders, molluscs, worms | CC0 |

4. **Wikidata** mass (P2067) or length (P2043) statements.
5. **Trait databases, higher taxon:** the median of the genus, then family, then order. This handles
   family articles like "Squirrel" (Sciuridae).
6. **Mass estimated from length**, as a last resort.

Run `python scripts/fallback_data.py` (or the Manager's Fallback data tab) to download the databases
into `sources/` and build `data/traits.json.gz`, a few hundred KB that ships inside the app. That lets
live lookups of any animal use it offline too. `build.py` does this automatically if the index is
missing. The app's sources panel names the source behind every value. Fish aren't covered by the
databases (FishBase is CC BY-NC and needs extra tooling), so they rely on Wikipedia, Wikidata or
overrides.

Attribution: AmphiBIO is CC BY 4.0. Cite Oliveira, B.F. et al. (2017) *AmphiBIO, a global database
for amphibian ecological traits*, Sci. Data 4:170123, if you distribute the app. The other two are CC0,
though citing them is good practice: Myhrvold et al. (2015) Ecology 96:3109; Herberstein et al. (2022)
Sci. Data 9:265.

## Data and caching

- **Search** uses Wikipedia prefix search and keeps only articles with a taxobox, which drops
  non-organisms. Results described as plants or fungi are filtered out too.
- **Scraping** pulls the plain-text article, short description and thumbnail from one Action API
  call. Mass and length are parsed from sentences about weight and length.
  The parser skips values for newborns, eggs and records, as well as maxima ("up to…") and tail,
  wingspan and height measurements. It takes the median of what's left, preferring metric values.
  The sentence each value came from is shown in the app.
- **Cache:** the bundled `data/animals.json` holds the 150 animals from the list. Every animal looked up
  live is also saved to a user cache (`%LOCALAPPDATA%\TerminalVelocity`, `~/Library/Caches/...`
  or `~/.cache/...`), so it's available offline afterwards.

## The model

- Quadratic drag, belly-down at sea level: `v_t = sqrt(2mg / (ρ_air · C_d · A))`.
- Body is modelled as a flattened box of water-density tissue. Its frontal area comes from the mass
  and, when known, the length. `C_d = 1 + 24/Re` (bluff body) unless you override it.
- Distance to 99 % of v_t: `d = −v_t²/(2g) · ln(1 − 0.99²)`.
- **Injury likelihood is a heuristic.** It's a log-logistic curve on impact speed, with its 50 % point
  at about 16 m/s for a 70 kg human, scaled by mass^−¼. That reproduces the familiar pattern where
  mice walk away, cats often survive and large animals don't. It ignores righting reflexes, landing
  surface and posture, so treat it as illustrative.

## Layout

```
run.py                     app entry point
app/physics.py             fall model (validated inputs)
app/wiki.py                Wikipedia/Wikidata access: HTTPS-only, size-capped, throttled
app/cache.py               bundled + local cache (validated on load)
app/traits.py              fallback chain: overrides, trait index, Wikidata
app/ui.py                  main window
app/plot.py                lightweight QPainter chart (no matplotlib)
app/theme.py               shared spin box / combo box styling
app/logsetup.py            rotating log file + crash reporting
manager.py                 GUI for every terminal operation (manager.bat / manager.sh)
scripts/build_cache.py     cache manager (menu + flags); scripts/animals.txt is its list
scripts/fallback_data.py   downloads trait databases, builds data/traits.json.gz
data/                      bundled data: animals.json, traits.json.gz, overrides.csv, ATTRIBUTION.md
build.py / build.bat / build.sh / cache.* / manager.*   build and launcher scripts
publish.py / publish.bat / publish.sh                    guided upload to GitHub
tests/                     python -m unittest discover -s tests  (no Qt or internet needed)
.github/workflows/         tests on every push/PR; builds (+ releases on v* tags)
```

## Troubleshooting

The app writes a log to `%LOCALAPPDATA%\TerminalVelocity\TerminalVelocity.log`
(`~/Library/Caches/TerminalVelocity/` on macOS, `~/.cache/TerminalVelocity/` on Linux). If something
goes wrong, it shows a message and keeps running. The details are in that log.

## Licence and credits

Code: [MIT](LICENSE). Bundled data keeps its own licences: Wikipedia text is CC BY-SA 4.0, images
carry their own licences, and the trait databases are CC0 or CC BY 4.0. See [NOTICE.md](NOTICE.md)
and [data/ATTRIBUTION.md](data/ATTRIBUTION.md). Security reports: [SECURITY.md](SECURITY.md).
