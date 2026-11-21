# Changelog

## 1.3.1
- Fix: Wikimedia moved thumbnails to `thumb.wikimedia.org`, which the image host check rejected;
  any `*.wikimedia.org` host is now accepted (HTTPS only).
- Fix: pictures could be skipped silently (149 of 150 bundled animals had none). Every missing
  picture now records why (`image_status`). Protocol-relative image links are accepted, and
  Wikipedia's summary service is used as a second source for the picture. Entries without a
  picture are refreshed by `--mode stale`.
- New: `python scripts/build_cache.py --diagnose "Cat"` shows what Wikipedia returns for a picture.
- `publish.py` now checks for outdated entries and missing pictures before uploading.
- Fix: `publish.py` didn't recognise the project folder when its path was spelled differently
  (symlinks, Windows short names, OneDrive), which made the macOS and Windows CI builds fail.
- Fix: GitHub's private-email block (GH007) was misreported as "repository has other work";
  publish now switches to your noreply address and retries.
- CI: actions updated to Node.js 24 versions; tests also run on macOS.
- Windows `.bat` files use Windows line endings.

## 1.3.0
- `publish.py` (publish.bat / publish.sh): guided, checked upload to GitHub.
- Security hardening:
  - HTTPS-only requests and redirects, response size limits, image type and host checks.
  - Downloaded text is rendered as plain text, and only Wikipedia links can be opened.
  - Spreadsheet-formula escaping in `overrides.csv`.
  - Arrow images now go in a private folder instead of the shared temp directory.
- Robustness:
  - Damaged cache, index and overrides files are tolerated, and input numbers are validated.
  - The local cache is capped at 500 entries.
  - Wikipedia API errors are retried; errors are logged with a crash dialog.
  - Number input is locale-proof.
- Attribution: image file pages are recorded; `data/ATTRIBUTION.md` and `NOTICE.md` added.
- GitHub: MIT licence, CI tests (Windows + Linux, Python 3.10/3.12), hardened build and release
  workflow, Dependabot.
- Launchers prefer the project's `.venv`.

## 1.2.0
- Fallback body-size data (Amniote DB, AmphiBIO, AnimalTraits, Wikidata, overrides) and the
  Manager GUI.

## 1.1.0
- Dark theme, request throttling with backoff, cache management options, 150 bundled animals.

## 1.0.0
- First release.
