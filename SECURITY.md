# Security policy

## Reporting a vulnerability

Please report security issues privately via **GitHub → Security → Report a vulnerability**
(private vulnerability reporting) on this repository, rather than opening a public issue.
Include steps to reproduce and the version (`app/__init__.py`). You should get a reply within
a week.

## What the app does with the network and your files

- It connects only over HTTPS: to `en.wikipedia.org` and `www.wikidata.org` (API), and to
  `upload.wikimedia.org` (thumbnails). Build tools also contact `api.figshare.com`,
  `zenodo.org`, `esapubs.org` and their file hosts to download datasets.
  Redirects to non-HTTPS URLs are refused, and every response has a size limit.
- Downloaded text is shown as plain text, never as HTML. The only link it opens is the
  `https://en.wikipedia.org/…` article link.
- Images must be JPEG, PNG, GIF or WebP from `upload.wikimedia.org`, and under 5 MB.
- It writes only to its own cache folder (`%LOCALAPPDATA%\TerminalVelocity`,
  `~/Library/Caches/TerminalVelocity` or `~/.cache/TerminalVelocity`). That folder holds the
  cache, arrow images and a rotating log of at most about 1.5 MB.
- No telemetry, accounts or credentials. `TV_CONTACT`, if set, is sent only in the User-Agent
  header to Wikimedia, as Wikimedia asks.
