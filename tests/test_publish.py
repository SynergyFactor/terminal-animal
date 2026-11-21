"""publish.py: error classification, plus real Git runs against local bare repositories."""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import publish  # noqa: E402

HAS_GIT = shutil.which("git") is not None


class ClassifyTests(unittest.TestCase):
    def test_classify(self):
        c = publish.classify
        self.assertEqual(c("! [rejected] main -> main (fetch first)"), "diverged")
        gh007 = ("remote: error: GH007: Your push would publish a private email address.\n"
                 " ! [remote rejected] main -> main (push declined due to email privacy restrictions)")
        self.assertEqual(c(gh007), "private_email")                  # not "diverged" (the old bug)
        self.assertEqual(c(" ! [remote rejected] main -> main (pre-receive hook declined)"), "declined")
        self.assertEqual(c("remote: Repository not found.\nfatal: repository 'x' not found"), "not_found")
        self.assertEqual(c("fatal: Authentication failed for 'https://github.com/a/b.git/'"), "auth")
        self.assertEqual(c("remote: Permission to a/b.git denied to c.\nfatal: ... 403"), "auth")
        self.assertEqual(c("fatal: unable to access 'x': Could not resolve host: github.com"), "network")
        self.assertEqual(c("remote: error: GH001: Large files detected."), "too_large")
        self.assertEqual(c("error: src refspec main does not match any"), "no_commit")
        self.assertEqual(c("something odd"), "other")

    def test_validation(self):
        for good in ("octocat", "a-b", "A1"):
            self.assertTrue(publish.USER_RE.match(good))
        for bad in ("-a", "a-", "a--b", "a b", "x" * 40, "a/b"):
            self.assertFalse(publish.USER_RE.match(bad), bad)
        self.assertTrue(publish.REPO_RE.match("terminal-animal"))
        for good in ("12345678+SynergyFactor@users.noreply.github.com", "octocat@users.noreply.github.com"):
            self.assertTrue(publish.NOREPLY_RE.match(good), good)
        for bad in ("me@gmail.com", "x@users.noreply.github.com.evil.com", "@users.noreply.github.com"):
            self.assertFalse(publish.NOREPLY_RE.match(bad), bad)
        self.assertFalse(publish.REPO_RE.match("bad name"))
        for f in (".env", "config/.env.local", "keys/server.pem", "id_rsa", "credentials.json"):
            self.assertTrue(publish.SENSITIVE.search(f), f)
        for f in ("app/ui.py", "data/animals.json", "README.md"):
            self.assertFalse(publish.SENSITIVE.search(f), f)


@unittest.skipUnless(HAS_GIT, "git not installed")
class PublishFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Git marks objects read-only on Windows
        base = Path(self.tmp.name)
        # Isolated copy of the project, with things that must NOT be uploaded.
        self.proj = base / "proj"
        shutil.copytree(ROOT, self.proj, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".venv", "build", "dist", "sources"))
        # Isolate from the developer's real data: an empty cache means publish never offers to
        # re-download anything, so these tests never touch the network and don't depend on it.
        (self.proj / "data" / "animals.json").write_text("{}")
        (self.proj / "data" / "ATTRIBUTION.md").unlink(missing_ok=True)
        (self.proj / ".venv").mkdir()
        (self.proj / ".venv" / "python.exe").write_text("x")
        (self.proj / "keys").mkdir()
        (self.proj / "keys" / "server.pem").write_text("-----BEGIN PRIVATE KEY-----")
        (self.proj / "app" / "__pycache__").mkdir(exist_ok=True)
        (self.proj / "app" / "__pycache__" / "x.pyc").write_text("x")
        # Isolated git config (identity set, no global settings leak in).
        cfg = base / "gitconfig"
        cfg.write_text("[user]\n\tname = Test\n\temail = test@example.com\n"
                       "[init]\n\tdefaultBranch = main\n")
        self.env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(cfg),
                                                "GIT_CONFIG_NOSYSTEM": "1"})
        self.env.start()
        self.remote = base / "remote.git"
        subprocess.run(["git", "init", "--bare", "--quiet", str(self.remote)], check=True)
        publish.ROOT = self.proj

    def tearDown(self):
        self.env.stop()
        publish.ROOT = ROOT
        self.tmp.cleanup()

    def run_publish(self, answers):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers), redirect_stdout(out):
            code = publish.main(["--skip-tests", "--remote-url", str(self.remote)])
        return code, out.getvalue()

    def remote_files(self):
        r = subprocess.run(["git", "--git-dir", str(self.remote), "ls-tree", "-r", "--name-only", "main"],
                           capture_output=True, text=True, check=True)
        return set(r.stdout.split())

    # The test copy's cache is empty, so no attribution questions are asked.
    # answers: exclude server.pem? y, upload? y, commit message: default
    FIRST_RUN = ["y", "y", ""]

    def test_fresh_upload(self):
        code, out = self.run_publish(self.FIRST_RUN)
        self.assertEqual(code, 0, out)
        files = self.remote_files()
        self.assertIn("app/ui.py", files)
        self.assertIn("data/animals.json", files)
        self.assertFalse(any(f.startswith(".venv/") or "__pycache__" in f for f in files))
        self.assertNotIn("keys/server.pem", files)
        self.assertIn("keys/server.pem", (self.proj / ".gitignore").read_text())

        # Second run with no changes: nothing new to commit, push is a no-op.
        code, out = self.run_publish(["y"])                       # upload? y
        self.assertEqual(code, 0, out)
        self.assertIn("No new changes", out)

    def _seed_remote(self, files):
        seed = Path(self.tmp.name) / "seed"
        subprocess.run(["git", "clone", "--quiet", str(self.remote), str(seed)], check=True,
                       capture_output=True)
        for name, text in files.items():
            (seed / name).write_text(text)
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(["git", "-C", str(seed), "commit", "--quiet", "-m", "seed"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "--quiet", "origin", "HEAD:main"], check=True,
                       capture_output=True)

    def test_replaces_github_starter_files_after_confirmation(self):
        self._seed_remote({"README.md": "# starter", "LICENSE": "x"})
        code, out = self.run_publish(self.FIRST_RUN + ["test"])     # type repo name to confirm
        self.assertEqual(code, 0, out)
        self.assertIn("starter files replaced", out)
        self.assertIn("app/ui.py", self.remote_files())

    def test_wrong_confirmation_changes_nothing(self):
        self._seed_remote({"README.md": "# starter"})
        code, out = self.run_publish(self.FIRST_RUN + ["no"])
        self.assertEqual(code, 1)
        self.assertEqual(self.remote_files(), {"README.md"})

    def test_refuses_to_overwrite_real_work(self):
        self._seed_remote({"README.md": "# other", "main.c": "int main(){}"})
        code, out = self.run_publish(self.FIRST_RUN)
        self.assertEqual(code, 1)
        self.assertIn("won't overwrite", out)
        self.assertEqual(self.remote_files(), {"README.md", "main.c"})

    def test_attribution_created_offline_when_missing(self):
        import json
        (self.proj / "data" / "animals.json").write_text(json.dumps(
            {"cat": {"title": "Cat", "url": "https://en.wikipedia.org/wiki/Cat", "parser_version": 4,
                     "image": "AAAA", "image_file": "Cat.jpg", "image_status": "ok"}}))
        (self.proj / "data" / "ATTRIBUTION.md").unlink(missing_ok=True)
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=["y"]), redirect_stdout(out):
            code = publish.main(["--check", "--skip-tests", "--remote-url", str(self.remote)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("File:Cat.jpg", (self.proj / "data" / "ATTRIBUTION.md").read_text())

    def test_private_email_block_is_fixed(self):
        # Simulate GitHub's GH007 check with a server-side hook on the local "GitHub" repo.
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "while read old new ref; do\n"
            "  for c in $(git rev-list \"$new\" --not --all); do\n"
            "    case \"$(git log -1 --format=%ae \"$c\")\" in\n"
            "      *@users.noreply.github.com) ;;\n"
            "      *) echo 'error: GH007: Your push would publish a private email address.' >&2; exit 1;;\n"
            "    esac\n"
            "  done\n"
            "done\n", newline="\n")
        hook.chmod(0o755)
        noreply = "123456+tester@users.noreply.github.com"
        code, out = self.run_publish(self.FIRST_RUN + ["me@gmail.com", noreply])  # bad, then good
        self.assertEqual(code, 0, out)
        self.assertIn("GitHub blocked the upload", out)
        self.assertNotIn("other work", out)
        author = subprocess.run(["git", "--git-dir", str(self.remote), "log", "-1", "--format=%ae", "main"],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(author, noreply)
        cfg = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True)
        self.assertEqual(cfg.stdout.strip(), noreply)

        # Later upload made with a private email again: only the new commit is re-signed,
        # history already on GitHub is untouched.
        first = subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "main"],
                               capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "config", "--global", "user.email", "me@gmail.com"], check=True)
        (self.proj / "CHANGELOG.md").write_text("changed\n")
        code, out = self.run_publish(["y", "Second change", noreply])
        self.assertEqual(code, 0, out)
        log = subprocess.run(["git", "--git-dir", str(self.remote), "log", "--format=%H %ae", "main"],
                             capture_output=True, text=True, check=True).stdout.split("\n")
        self.assertEqual(log[1].split()[0], first)                    # first commit unchanged
        self.assertTrue(log[0].endswith(noreply))

    def test_check_mode_commits_nothing(self):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=["y"]), redirect_stdout(out):
            code = publish.main(["--check", "--skip-tests", "--remote-url", str(self.remote)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(subprocess.run(["git", "--git-dir", str(self.remote), "rev-parse", "--verify",
                                         "--quiet", "main"], capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
