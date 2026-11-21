"""Publish this project to GitHub, step by step, with checks and safe automatic fixes.

    python publish.py            guided upload (or publish.bat / ./publish.sh)
    python publish.py --check    run every check and stage the files, but don't commit or push

What it does, in order:
  1. Checks Git is installed (offers to install it on Windows) and that your name/email are set.
  2. Asks for your GitHub username and repository name, and checks the repository exists.
  3. Pre-flight: runs the tests, checks the bundled data and image attribution, fills in
     README placeholders.
  4. Stages the files, and keeps out anything that shouldn't be uploaded (virtual env, build
     output, raw datasets, secrets, files over GitHub's 100 MB limit).
  5. Commits, connects the GitHub remote, and pushes. Common push problems are explained,
     and fixed where safe.
  6. Optionally tags a release so GitHub Actions builds downloadable apps.

Nothing is uploaded until you confirm, and nothing is ever force-pushed without you typing the
repository name.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    from app import __version__ as VERSION
except Exception:  # noqa: BLE001 - publishing shouldn't depend on the app importing cleanly
    VERSION = "0.0.0"

USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NOREPLY_RE = re.compile(r"^(\d+\+)?[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})@users\.noreply\.github\.com$", re.I)
NEVER_UPLOAD = (".venv/", "venv/", "build/", "dist/", "sources/", "__pycache__/", ".idea/", ".vscode/")
SENSITIVE = re.compile(
    r"(^|/)(\.env(\..*)?|id_rsa.*|id_ed25519.*|.*\.pem|.*\.key|.*\.pfx|.*\.p12|"
    r"credentials.*\.json|secrets?\..*|.*\.kdbx)$", re.I)
GITHUB_STARTER_FILES = {"README.md", "LICENSE", ".gitignore"}
WARN_MB, LIMIT_MB = 50, 95          # GitHub warns above 50 MB and rejects files above 100 MB
DEFAULT_GITIGNORE = """__pycache__/
*.py[cod]
.venv/
venv/
build/
dist/
*.spec
sources/
data/*.tmp
data/*.bak
*.part
.env
.env.*
.vscode/
.idea/
.DS_Store
Thumbs.db
"""


class Abort(Exception):
    """Stop publishing with a message (nothing half-done is left behind)."""


class GitError(Exception):
    def __init__(self, args, result):
        self.args_ = args
        self.stdout = result.stdout or ""
        self.stderr = result.stderr or ""
        self.code = result.returncode
        super().__init__(f"git {' '.join(args)} failed ({self.code}): {self.stderr.strip()[:500]}")


# -- console helpers -----------------------------------------------------------------------------
def step(text: str) -> None:
    print(f"\n=== {text} ===")


def ok(text: str) -> None:
    print(f"  [ok] {text}")


def warn(text: str) -> None:
    print(f"  [!]  {text}")


def info(text: str) -> None:
    print(f"       {text}")


def ask(prompt: str, default: str | None = None, validate=None, hint: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"  > {prompt}{suffix}: ").strip()
        except EOFError:
            raise Abort("no input available (run this in a terminal)") from None
        value = value or (default or "")
        if not value:
            print("    Please enter a value.")
            continue
        if validate and not validate(value):
            print(f"    That doesn't look right. {hint}".rstrip())
            continue
        return value


def confirm(prompt: str, default: bool = False) -> bool:
    options = "Y/n" if default else "y/N"
    try:
        answer = input(f"  > {prompt} [{options}]: ").strip().lower()
    except EOFError:
        raise Abort("no input available (run this in a terminal)") from None
    return default if not answer else answer in ("y", "yes")


# -- git -----------------------------------------------------------------------------------------
_safe_dir_fixed = False


def git(*args: str, check: bool = True, capture: bool = True, quiet_stdout: bool = False,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Run git in the project folder (argument list, never a shell)."""
    global _safe_dir_fixed
    kwargs = dict(cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                  env={**os.environ, **(env or {})})
    if capture:
        kwargs["capture_output"] = True
    else:
        kwargs["stderr"] = subprocess.PIPE              # show progress, keep errors to classify
        if quiet_stdout:
            kwargs["stdout"] = subprocess.DEVNULL
    result = subprocess.run(["git", *args], **kwargs)
    if result.returncode != 0 and "dubious ownership" in (result.stderr or "") and not _safe_dir_fixed:
        # Git refuses folders owned by another account (common after copying/extracting on Windows).
        warn("Git doesn't trust this folder because it's owned by a different user account.")
        if confirm(f"Mark {ROOT} as safe for Git?", default=True):
            subprocess.run(["git", "config", "--global", "--add", "safe.directory", ROOT.as_posix()],
                           check=True)
            _safe_dir_fixed = True
            ok("Folder marked as safe.")
            return git(*args, check=check, capture=capture, quiet_stdout=quiet_stdout, env=env)
    if check and result.returncode != 0:
        raise GitError(list(args), result)
    return result


def git_out(*args: str) -> str:
    return git(*args).stdout.strip()


def is_repo() -> bool:
    r = git("rev-parse", "--show-toplevel", check=False)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == ROOT


def has_commits() -> bool:
    return git("rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0


# -- steps ---------------------------------------------------------------------------------------
def check_git() -> None:
    step("1. Git")
    if not shutil.which("git"):
        warn("Git isn't installed (or isn't on PATH).")
        if platform.system() == "Windows" and shutil.which("winget"):
            if confirm("Install Git now with winget?", default=True):
                subprocess.run(["winget", "install", "--id", "Git.Git", "-e", "--source", "winget",
                                "--accept-package-agreements", "--accept-source-agreements"])
                raise Abort("Git was installed. Close this window, open a new terminal, and run "
                            "publish again so the new PATH is picked up.")
        raise Abort("Install Git from https://git-scm.com/downloads, open a new terminal, "
                    "and run publish again.")
    version = git_out("--version")
    ok(version)
    m = re.search(r"(\d+)\.(\d+)", version)
    if m and (int(m.group(1)), int(m.group(2))) < (2, 28):
        warn("Your Git is quite old; consider updating from https://git-scm.com/downloads.")


def check_identity() -> None:
    step("2. Your Git identity (recorded on each commit)")
    name = git("config", "user.name", check=False).stdout.strip()
    email = git("config", "user.email", check=False).stdout.strip()
    if name and email:
        ok(f"{name} <{email}>")
        if not NOREPLY_RE.match(email):
            info("Note: if GitHub blocks this address for privacy, you'll be asked for your")
            info("      private 'noreply' address and the commit will be fixed automatically.")
        return
    info("Git needs a name and email for commits. They are public on GitHub.")
    info("Tip: use your private GitHub address from GitHub -> Settings -> Emails")
    info("     (looks like 12345678+username@users.noreply.github.com).")
    if not name:
        name = ask("Your name", validate=lambda v: len(v) <= 100)
        git("config", "--global", "user.name", name)
    if not email:
        email = ask("Your email", validate=lambda v: bool(EMAIL_RE.match(v)),
                    hint="Expected something like you@example.com.")
        git("config", "--global", "user.email", email)
    ok(f"Saved: {name} <{email}>")


def existing_origin() -> str:
    if not is_repo():
        return ""
    return git("remote", "get-url", "origin", check=False).stdout.strip()


def repo_details(remote_override: str | None) -> tuple[str, str, str]:
    step("3. GitHub repository")
    if remote_override:                                   # used by the test suite
        ok(f"Using remote {remote_override}")
        return "local", "test", remote_override
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", existing_origin())
    user = ask("GitHub username", default=m.group(1) if m else None,
               validate=lambda v: bool(USER_RE.match(v)),
               hint="Usernames use letters, numbers and single hyphens (max 39).")
    repo = ask("Repository name", default=m.group(2) if m else "terminal-animal",
               validate=lambda v: bool(REPO_RE.match(v)) and v not in (".", ".."),
               hint="Use letters, numbers, '.', '_' or '-'.")
    return user, repo, f"https://github.com/{user}/{repo}.git"


def check_remote(url: str) -> str:
    """'empty', 'has_commits' or 'unknown'. Signing in may be requested for private repos."""
    info("Checking the repository on GitHub (a sign-in window may open)...")
    r = git("ls-remote", url, check=False)
    if r.returncode == 0:
        if r.stdout.strip():
            warn("The GitHub repository already has commits; they'll be checked before pushing.")
            return "has_commits"
        ok("Repository found, and it's empty. Perfect.")
        return "empty"
    err = r.stderr.lower()
    if "not found" in err:
        warn("GitHub says this repository wasn't found.")
        info("Check the username and name, and that you created it at https://github.com/new.")
        raise Abort("repository not found")
    if "could not resolve host" in err or "unable to access" in err:
        raise Abort("couldn't reach GitHub. Check your internet connection and try again.")
    warn(f"Couldn't check the repository yet ({r.stderr.strip()[:200]}).")
    info("Continuing. If it's private, you'll sign in when pushing.")
    return "unknown"


def run_tests() -> None:
    info("Running the tests...")
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    summary = [ln for ln in r.stderr.splitlines() if ln.startswith(("Ran ", "OK", "FAILED"))]
    if r.returncode == 0:
        ok(" ".join(summary) or "tests passed")
        return
    print(r.stderr[-3000:])
    warn("Some tests failed (details above).")
    if not confirm("Upload anyway?", default=False):
        raise Abort("fix the failing tests, then run publish again")


def check_data() -> None:
    import json
    cache = ROOT / "data" / "animals.json"
    try:
        records = json.loads(cache.read_text("utf-8"))
        if not isinstance(records, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as e:
        warn(f"data/animals.json is missing or unreadable ({e}).")
        info("The app will still work, but it won't have any animals bundled for offline use.")
        if not confirm("Continue without bundled animals?", default=False):
            raise Abort("build the cache first: python scripts/build_cache.py --mode missing")
        return
    recs = [r for r in records.values() if isinstance(r, dict)]
    ok(f"data/animals.json has {len(recs)} animals")
    if not (ROOT / "data" / "traits.json.gz").exists():
        warn("data/traits.json.gz (fallback size data) is missing; gaps will rely on Wikidata.")
        info("To add it: python scripts/fallback_data.py")

    unattributed = [r for r in recs if r.get("image") and "image_file" not in r]
    attribution = ROOT / "data" / "ATTRIBUTION.md"
    if unattributed:
        warn(f"{len(unattributed)} cached images don't record their source file page yet, so "
             "data/ATTRIBUTION.md would be incomplete.")
        info("Wikipedia text and images must be credited when shared. Fixing this re-downloads "
             f"those entries (about {max(1, len(unattributed) * 3 // 60)} min).")
        if confirm("Fix it now?", default=True):
            if not os.environ.get("TV_CONTACT"):
                contact = ask("Contact email for Wikimedia (sent with requests)", default="unset")
                os.environ["TV_CONTACT"] = contact
            r = subprocess.run([sys.executable, "scripts/build_cache.py", "--mode", "stale", "--yes"],
                               cwd=ROOT)
            if r.returncode not in (0, 1):
                raise Abort("updating the cache failed; see the messages above")
            ok("Attribution updated.")
        elif not confirm("Upload without complete attribution?", default=False):
            raise Abort("run: python scripts/build_cache.py --mode stale")
    elif recs and not attribution.exists():
        info("Creating data/ATTRIBUTION.md from the cache (offline, a few seconds)...")
        r = subprocess.run([sys.executable, "scripts/build_cache.py", "--mode", "apply"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0 and attribution.exists():
            ok("Created data/ATTRIBUTION.md")
        else:
            warn("Couldn't create it automatically; run: python scripts/build_cache.py --mode apply")
    elif recs:
        ok("Image attribution is complete (data/ATTRIBUTION.md)")


def fix_readme(user: str, repo: str) -> None:
    readme = ROOT / "README.md"
    try:
        text = readme.read_text("utf-8")
    except OSError:
        warn("README.md not found.")
        return
    new = text.replace("OWNER/REPO", f"{user}/{repo}").replace("cd REPO", f"cd {repo}")
    if new != text:
        readme.write_text(new, "utf-8")
        ok(f"README links now point to github.com/{user}/{repo}")
    else:
        ok("README links already set")


def preflight(user: str, repo: str, skip_tests: bool) -> None:
    step("4. Pre-flight checks")
    for name in ("LICENSE", "README.md", ".gitattributes"):
        if not (ROOT / name).exists():
            warn(f"{name} is missing (it's included in the project download).")
    gi = ROOT / ".gitignore"
    if not gi.exists():
        gi.write_text(DEFAULT_GITIGNORE, "utf-8")
        ok("Created a missing .gitignore (keeps .venv, build output and datasets out).")
    if not skip_tests:
        run_tests()
    check_data()
    if user != "local":
        fix_readme(user, repo)


def add_ignore(pattern: str) -> None:
    gi = ROOT / ".gitignore"
    lines = gi.read_text("utf-8").splitlines() if gi.exists() else []
    if pattern not in lines:
        with open(gi, "a", encoding="utf-8") as f:
            f.write(("" if not lines or lines[-1] == "" else "\n") + pattern + "\n")


def unstage(*paths: str) -> None:
    for i in range(0, len(paths), 100):                  # keep command lines short on Windows
        git("rm", "-r", "--cached", "--quiet", "--ignore-unmatch", "--", *paths[i:i + 100])


def stage() -> list[str]:
    step("5. Choosing files to upload")
    if not is_repo():
        git("init", "--quiet")
        ok("Created a local Git repository")
    git("add", "--all")
    files = git_out("diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMR").splitlines()
    tracked = git_out("ls-files").splitlines()

    # Folders that must never be uploaded (e.g. if .gitignore was missing or edited).
    for prefix in NEVER_UPLOAD:
        hits = [f for f in tracked if f.startswith(prefix) or f"/{prefix}" in f]
        if hits:
            warn(f"{prefix} was about to be uploaded ({len(hits)} files). Excluding it.")
            unstage(*hits)
            add_ignore(prefix)
            git("add", ".gitignore")

    # Anything that looks like a secret.
    tracked = git_out("ls-files").splitlines()
    for f in [f for f in tracked if SENSITIVE.search(f)]:
        warn(f"'{f}' looks like it could contain a password or key.")
        if confirm("Keep it out of the upload (recommended)?", default=True):
            unstage(f)
            add_ignore(f)
            git("add", ".gitignore")
            ok(f"Excluded {f}")

    # GitHub's file size limit.
    for f in git_out("ls-files").splitlines():
        p = ROOT / f
        size_mb = p.stat().st_size / 1e6 if p.is_file() else 0
        if size_mb > LIMIT_MB:
            warn(f"{f} is {size_mb:.0f} MB; GitHub rejects files over 100 MB. Excluding it.")
            unstage(f)
            add_ignore(f)
            git("add", ".gitignore")
        elif size_mb > WARN_MB:
            warn(f"{f} is {size_mb:.0f} MB (GitHub allows it but recommends < 50 MB).")

    final = git_out("ls-files").splitlines()
    tops: dict[str, int] = {}
    for f in final:
        top = f.split("/", 1)[0] + ("/" if "/" in f else "")
        tops[top] = tops.get(top, 0) + 1
    ok(f"{len(final)} files ready: " + ", ".join(f"{k}({v})" if v > 1 else k
                                                 for k, v in sorted(tops.items())))
    return files


def commit() -> bool:
    step("6. Commit")
    staged = git("diff", "--cached", "--quiet", check=False).returncode != 0
    if has_commits() and not staged:
        ok("No new changes since the last commit.")
        git("branch", "-M", "main")
        return False
    default = f"Terminal Velocity {VERSION}" if not has_commits() else "Update"
    message = ask("Commit message", default=default, validate=lambda v: len(v) <= 200)
    git("commit", "--quiet", "-m", message)
    git("branch", "-M", "main")
    ok(f"Committed: {message}")
    return True


def set_remote(url: str) -> None:
    current = existing_origin()
    if not current:
        git("remote", "add", "origin", url)
        ok(f"Connected to {url}")
    elif current != url:
        warn(f"This project was linked to {current}.")
        if confirm(f"Switch it to {url}?", default=True):
            git("remote", "set-url", "origin", url)
            ok("Remote updated")
        else:
            raise Abort("keeping the old remote; nothing was uploaded")
    else:
        ok(f"Remote: {url}")


def classify(stderr: str) -> str:
    """Turn git push's error text into one of a few known problems.

    Order matters: GitHub's specific rejections are checked before the generic ones.
    """
    s = stderr.lower()
    if "gh007" in s or "would publish a private email" in s or "email privacy" in s:
        return "private_email"
    if ("fetch first" in s or "non-fast-forward" in s
            or "updates were rejected because the remote contains work" in s):
        return "diverged"
    if "remote rejected" in s or "declined" in s or "pre-receive hook" in s:
        return "declined"                                # GitHub refused for another stated reason
    if "repository not found" in s or "not found" in s and "repository" in s:
        return "not_found"
    if ("authentication failed" in s or "could not read username" in s or "403" in s
            or "permission to" in s and "denied" in s):
        return "auth"
    if "could not resolve host" in s or "unable to access" in s or "timed out" in s:
        return "network"
    if "large files detected" in s or "exceeds github's file size limit" in s:
        return "too_large"
    if "src refspec" in s:
        return "no_commit"
    return "other"


def fix_private_email() -> None:
    """GH007: commits use an email GitHub keeps private. Switch to the noreply address and re-sign
    the commits that haven't been uploaded yet (already-uploaded history is never rewritten)."""
    warn("GitHub blocked the upload because the commit shows your personal email, which your "
         "GitHub settings keep private.")
    info("Fix: sign commits with your private GitHub 'noreply' address instead.")
    info("Find it at https://github.com/settings/emails under 'Keep my email addresses private'.")
    info("It looks like 12345678+YourName@users.noreply.github.com")
    email = ask("Your GitHub noreply email", validate=lambda v: bool(NOREPLY_RE.match(v)),
                hint="It must end in @users.noreply.github.com.")
    git("config", "--global", "user.email", email)
    if git("config", "--local", "user.email", check=False).stdout.strip():
        git("config", "--local", "user.email", email)    # a repo-level setting would win otherwise
    ok(f"Commits will now use {email}")

    # Re-sign only commits not yet on GitHub.
    upstream = git("rev-parse", "--verify", "--quiet", "origin/main", check=False).returncode == 0
    base = ["origin/main"] if upstream else ["--root"]
    r = git("rebase", "--quiet", "--rebase-merges", *base, "--exec",
            "git commit --amend --no-edit --reset-author --quiet", check=False)
    if r.returncode != 0:
        git("rebase", "--abort", check=False)
        raise Abort("couldn't update the commit email automatically. Run: "
                    "git commit --amend --reset-author --no-edit   then publish again")
    ok("Updated the commit(s) to use the new address. Uploading again...")


def remote_is_starter_only(repo_name: str) -> bool:
    """True if the remote only has GitHub's auto-created README/LICENSE/.gitignore."""
    git("fetch", "--quiet", "origin", "main", check=False)
    if git("rev-parse", "--verify", "--quiet", "origin/main", check=False).returncode != 0:
        return False
    count = int(git_out("rev-list", "--count", "origin/main") or "0")
    files = set(git_out("ls-tree", "-r", "--name-only", "origin/main").splitlines())
    return count <= 3 and files <= GITHUB_STARTER_FILES


def push(url: str, repo_name: str) -> None:
    step("7. Upload to GitHub")
    info("Uploading (a GitHub sign-in window may open the first time)...")
    for attempt in range(3):
        r = git("push", "-u", "origin", "main", check=False, capture=False, quiet_stdout=True)
        if r.returncode == 0:
            ok("Uploaded.")
            return
        stderr = r.stderr or ""
        print(stderr.strip()[-1500:])
        kind = classify(stderr)
        if kind == "private_email" and attempt < 2:
            fix_private_email()
            continue
        if kind == "diverged":
            if remote_is_starter_only(repo_name):
                warn("The GitHub repository only contains starter files GitHub created "
                     "(README/LICENSE/.gitignore). This project has its own versions of these.")
                info("Replacing them overwrites only those starter files on GitHub.")
                typed = ask(f"Type the repository name ({repo_name}) to replace them, or 'no'")
                if typed == repo_name:
                    lease = git_out("rev-parse", "origin/main")
                    r2 = git("push", "-u", "origin", "main", f"--force-with-lease=main:{lease}",
                             check=False, capture=False, quiet_stdout=True)
                    if r2.returncode == 0:
                        ok("Uploaded (starter files replaced).")
                        return
                    print((r2.stderr or "").strip()[-1500:])
                raise Abort("upload cancelled; nothing on GitHub was changed")
            raise Abort("the GitHub repository already has other work in it. To be safe this script "
                        "won't overwrite it. Use a new empty repository, or merge manually "
                        "(git pull origin main --allow-unrelated-histories).")
        if kind == "auth":
            warn("GitHub didn't accept the sign-in.")
            info("Make sure you're signed in as the repository owner. On Windows, Git opens a "
                 "browser sign-in; if it doesn't, update Git for Windows.")
            if attempt < 2 and confirm("Try again?", default=True):
                continue
            raise Abort("sign-in failed")
        if kind == "not_found":
            raise Abort("GitHub can't find the repository, or your account can't write to it. "
                        "Check the name and that you're signed in as its owner.")
        if kind == "network":
            if attempt < 2 and confirm("Couldn't reach GitHub. Retry?", default=True):
                continue
            raise Abort("network problem; check your connection and run publish again")
        if kind == "too_large":
            raise Abort("GitHub rejected a large file. Remove it from the project and run publish "
                        "again (files over 100 MB can't be uploaded).")
        if kind == "no_commit":
            raise Abort("there's nothing committed to upload yet")
        if kind == "declined":
            raise Abort("GitHub declined the upload; its reason is in the 'remote:' lines above. "
                        "Nothing was uploaded.")
        if kind == "private_email":
            raise Abort("GitHub still blocks the commit email. Check the address at "
                        "https://github.com/settings/emails and run publish again.")
        raise Abort("the upload failed (see the message above)")


def offer_release(url_base: str) -> None:
    step("8. Release (optional)")
    tag = f"v{VERSION}"
    info(f"Pushing a tag such as {tag} makes GitHub Actions build Windows, macOS and Linux apps")
    info("and attach them to a release (about 15-20 minutes).")
    if not confirm(f"Create release {tag} now?", default=False):
        return
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).returncode == 0:
        warn(f"Tag {tag} already exists locally. Bump __version__ in app/__init__.py for a new release.")
    else:
        git("tag", "-a", tag, "-m", f"Terminal Velocity {tag}")
    r = git("push", "origin", tag, check=False, capture=False, quiet_stdout=True)
    if r.returncode == 0:
        ok(f"Release started: {url_base}/actions")
    else:
        print((r.stderr or "").strip()[-800:])
        warn("Couldn't push the tag; you can retry later with: git push origin " + tag)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="run checks and stage files; don't commit or push")
    ap.add_argument("--skip-tests", action="store_true", help="don't run the test suite first")
    ap.add_argument("--remote-url", help=argparse.SUPPRESS)          # tests use a local bare repo
    args = ap.parse_args(argv)

    print(f"Terminal Velocity {VERSION}: publish to GitHub"
          + ("  (check only: nothing will be committed or uploaded)" if args.check else ""))
    try:
        check_git()
        check_identity()
        user, repo, url = repo_details(args.remote_url)
        check_remote(url)
        preflight(user, repo, args.skip_tests)
        stage()
        if args.check:
            step("Done (check only)")
            ok("Everything is ready. Run publish again without --check to upload.")
            return 0
        if not confirm("Commit and upload these files now?", default=True):
            raise Abort("cancelled; files are staged but nothing was committed or uploaded")
        commit()
        set_remote(url)
        push(url, repo)
        web = url[:-4] if url.endswith(".git") else url
        if user != "local":
            offer_release(web)
        step("All done")
        ok(f"Your project: {web}")
        if user != "local":
            info(f"Tests run automatically: {web}/actions")
            info("Recommended on GitHub: Settings -> Security -> enable 'Private vulnerability reporting'.")
            info("Later changes: run publish again (it commits and uploads only what changed).")
        return 0
    except Abort as e:
        print(f"\n  [x] Stopped: {e}")
        return 1
    except GitError as e:
        print(f"\n  [x] A Git command failed:\n      {e}")
        print("      Nothing was uploaded. Fix the problem above and run publish again.")
        return 1
    except KeyboardInterrupt:
        print("\n  [x] Cancelled. Nothing was uploaded.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
