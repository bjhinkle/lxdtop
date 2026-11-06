"""The README's install commands, run as written, with a stand-in curl or install."""

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests.support import LXDTOP_PATH, ROOT, lxdtop

README = os.path.join(ROOT, "README.md")

RELEASE = "https://github.com/bjhinkle/lxdtop/releases/latest/download/"

FAKE_CURL = f'''#!{sys.executable}
"""Stands in for curl -o FILE URL, or curl -O URL: writes the file that URL names from the stand-in release
in $FAKE_RELEASE, to FILE or under its own name here, and writes URL down in $CURL_LOG. With $FAKE_CURL_CUT,
it writes only that many bytes of lxdtop, then exits with $FAKE_CURL_EXIT (curl's 18 means "partial file")."""
import os, sys
url = sys.argv[-1]
name = url.rsplit("/", 1)[-1]
with open(os.environ["CURL_LOG"], "a") as log:
    log.write(url + "\\n")
with open(os.path.join(os.environ["FAKE_RELEASE"], name), "rb") as f:
    body = f.read()
cut = os.environ.get("FAKE_CURL_CUT") if name == "lxdtop" else None
with open(sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else name, "wb") as f:
    f.write(body[:int(cut)] if cut else body)
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")) if cut else 0)
'''

MKTEMP = f'''#!{sys.executable}
"""Stands in for mktemp -d: makes a new directory in $TMPDIR and prints its name, as GNU mktemp does.
macOS's mktemp makes it in the user's own temporary directory whatever $TMPDIR says, out of the test's reach."""
import os, sys, tempfile
assert sys.argv[1:] == ["-d"], sys.argv
print(tempfile.mkdtemp(prefix="tmp.", dir=os.environ["TMPDIR"]))
'''

OLD = "#!/bin/sh\necho 'lxdtop 1.4.0 (the old copy)'\n"

SYNC = f'''#!{sys.executable}
"""Stands in for sync FILE: writes down what it was asked to flush, and the SHA-256 of that file and of
~/bin/lxdtop at that moment, then exits with $SYNC_EXIT (default 0)."""
import hashlib, os, sys
def held(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
with open(os.environ["SYNC_LOG"], "a") as log:
    log.write(" ".join(["sync"] + sys.argv[1:]) + "\\n")
    log.write(held(sys.argv[1]) + "\\n" + held(os.path.join(os.environ["HOME"], "bin", "lxdtop")) + "\\n")
sys.exit(int(os.environ.get("SYNC_EXIT", "0")))
'''


def sha256(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def add_tool(directory, name, text):
    """A script in this directory, which comes first on the PATH, standing in for one of the host's tools."""
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, 0o755)


def install_commands():
    """The README's commands under "Install and run", without the last one, which starts lxdtop."""
    with open(README, encoding="utf-8") as f:
        section = f.read().split("\n## Install and run\n", 1)[1]
    lines = section.split("```bash\n", 1)[1].split("\n```", 1)[0].splitlines()
    assert lines[-1] == "~/bin/lxdtop", lines
    return "\n".join(lines[:-1]) + "\n"


def clone_commands():
    """The code the README gives for installing from a clone, before the paragraph on SSH."""
    with open(README, encoding="utf-8") as f:
        text = f.read().split("\nFrom a clone, ", 1)[1].split("\nOver SSH, ", 1)[0]
    block = re.search(r"```bash\n(.*?)\n```", text, re.S)
    return (block.group(1) if block else re.search(r"`([^`]+)`", text).group(1)) + "\n"


GNU_INSTALL = f'''#!{sys.executable}
"""Stands in for `install -m MODE SOURCE DEST`, as GNU install does it: remove DEST, then copy SOURCE there.

In between, it writes down what ~/bin/lxdtop holds, as lxdtop starting at that moment would find it.
"""
import os, shutil, sys
mode, source, dest = int(sys.argv[2], 8), sys.argv[3], sys.argv[4]
if os.path.lexists(dest):
    os.unlink(dest)
installed = os.path.join(os.environ["HOME"], "bin", "lxdtop")
with open(os.environ["MIDWAY"], "w") as f:
    f.write(open(installed).read() if os.path.exists(installed) else "(nothing)")
shutil.copyfile(source, dest)
os.chmod(dest, mode)
'''


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class ReadmeInstallTest(unittest.TestCase):
    """The README's install of the latest release, run as written, with a stand-in curl serving the release."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lxdtop-readme-")
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.home = os.path.join(tmp.name, "home")
        self.tools = os.path.join(tmp.name, "tools")
        self.release = os.path.join(tmp.name, "release")
        self.tmpdir = os.path.join(tmp.name, "tmp")             # where the README's mktemp makes its directory
        self.curl_log = os.path.join(tmp.name, "curl-log")
        self.sync_log = os.path.join(tmp.name, "sync-log")
        for path in (self.home, self.tools, self.release, self.tmpdir):
            os.makedirs(path)
        add_tool(self.tools, "curl", FAKE_CURL)
        add_tool(self.tools, "mktemp", MKTEMP)
        self.path = self.tools + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
        self.installed = os.path.join(self.home, "bin", "lxdtop")
        with open(LXDTOP_PATH, "rb") as f:
            self.published = f.read()
        self.publish(self.published)

    def publish(self, body):
        """The release: lxdtop, and SHA256SUMS with its SHA-256, as `sha256sum lxdtop` writes it."""
        self.serve(body)
        with open(os.path.join(self.release, "SHA256SUMS"), "w") as f:
            f.write(f"{sha256(body)}  lxdtop\n")

    def serve(self, body):
        """What a download of the release's lxdtop gets."""
        with open(os.path.join(self.release, "lxdtop"), "wb") as f:
            f.write(body)

    def old_copy(self):
        os.makedirs(os.path.dirname(self.installed))
        with open(self.installed, "w") as f:
            f.write(OLD)
        os.chmod(self.installed, 0o755)

    def run_readme(self, **env):
        environ = dict(PATH=self.path, HOME=self.home, LANG="C.UTF-8", TMPDIR=self.tmpdir,
                       FAKE_RELEASE=self.release, CURL_LOG=self.curl_log, **env)
        return subprocess.run([shutil.which("sh"), "-c", install_commands()], capture_output=True, text=True,
                              cwd=self.tmp, env=environ, timeout=60)

    def assert_installed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        # sha256sum's answer, then the version, shown by grep
        self.assertEqual(result.stdout, f"lxdtop: OK\nlxdtop {lxdtop.VERSION}\n")
        with open(self.installed, "rb") as copy:
            self.assertEqual(copy.read(), self.published)
        self.assertEqual(stat.S_IMODE(os.stat(self.installed).st_mode), 0o755)
        self.assertFalse(os.path.exists(self.installed + ".new"))

    def assert_old_copy(self):
        with open(self.installed) as f:
            self.assertEqual(f.read(), OLD)

    def assert_untouched(self):
        """~/bin as it was: the old copy, and nothing beside it."""
        self.assertEqual(os.listdir(os.path.dirname(self.installed)), ["lxdtop"])
        self.assert_old_copy()

    def test_a_download_replaces_the_old_copy(self):
        self.old_copy()
        self.assert_installed(self.run_readme())

    def test_the_first_install(self):
        self.assert_installed(self.run_readme())

    def test_lxdtop_and_its_checksum_come_from_the_latest_release(self):
        """#16: it downloaded lxdtop from main, with no published checksum to check it against."""
        self.assert_installed(self.run_readme())
        with open(self.curl_log) as f:
            self.assertEqual(f.read().splitlines(), [RELEASE + "lxdtop", RELEASE + "SHA256SUMS"])

    def test_a_download_that_is_not_the_published_file_leaves_the_old_copy(self):
        """This one runs and says it's lxdtop, so only its SHA-256 can tell it from the release's file."""
        self.old_copy()
        self.serve(self.published + b"# not the published file\n")
        result = self.run_readme()
        self.assert_untouched()
        self.assertEqual(result.stdout, "lxdtop: FAILED\n")
        self.assertNotEqual(result.returncode, 0)

    def test_a_download_cut_partway_leaves_the_old_copy(self):
        self.old_copy()
        result = self.run_readme(FAKE_CURL_CUT="40000", FAKE_CURL_EXIT="18")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_a_download_cut_where_it_still_runs_leaves_the_old_copy(self):
        """Cut just before its last lines, lxdtop is still a whole Python program that never calls main().

        Its SHA-256 isn't the release's, so it never reaches ~/bin.
        """
        cut = self.published.rindex(b'\nif __name__ == "__main__":')
        self.old_copy()
        result = self.run_readme(FAKE_CURL_CUT=str(cut))                       # and curl says all went well
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_a_published_file_that_does_not_say_it_is_lxdtop_leaves_the_old_copy(self):
        """The checksum shows only that this is the published file. The README still checks that it runs.

        Say the release's lxdtop were cut just before its last lines. It's still a
        whole Python program that never calls main(), so `--version` exits 0 without
        printing anything, and checking its exit status alone would install it. The
        README checks that it prints lxdtop's version.
        """
        self.publish(self.published[:self.published.rindex(b'\nif __name__ == "__main__":')])
        self.old_copy()
        result = self.run_readme()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "lxdtop: OK\n")
        self.assert_old_copy()
        cut_off = subprocess.run([self.installed + ".new", "--version"], capture_output=True, text=True,
                                 env=dict(PATH=self.path), timeout=60)
        self.assertEqual((cut_off.returncode, cut_off.stdout), (0, ""))        # why the check needs grep

    def test_the_download_is_flushed_to_disk_before_the_rename(self):
        """#16: the README renamed the download into place unflushed, where `lxdtop-remote install` flushes it.

        A power cut just after the rename could then leave an empty ~/bin/lxdtop.
        """
        self.old_copy()
        add_tool(self.tools, "sync", SYNC)
        self.assert_installed(self.run_readme(SYNC_LOG=self.sync_log))
        with open(self.sync_log) as f, open(LXDTOP_PATH, "rb") as original:
            # once, for the download alone, when it was whole and ~/bin/lxdtop was still the old copy
            self.assertEqual(f.read().splitlines(),
                             [f"sync {self.installed}.new", sha256(original.read()), sha256(OLD)])

    def test_a_download_that_cannot_be_flushed_leaves_the_old_copy(self):
        self.old_copy()
        add_tool(self.tools, "sync", SYNC)
        result = self.run_readme(SYNC_LOG=self.sync_log, SYNC_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_copy()


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class ReadmeCloneInstallTest(unittest.TestCase):
    """The README's install from a clone, run as written in the clone's directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lxdtop-readme-")
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "home")
        self.clone = os.path.join(tmp.name, "clone")
        self.tools = os.path.join(tmp.name, "tools")
        self.midway = os.path.join(tmp.name, "midway")
        for path in (self.home, self.clone, self.tools):
            os.makedirs(path)
        add_tool(self.tools, "install", GNU_INSTALL)
        self.sync_log = os.path.join(tmp.name, "sync-log")
        self.installed = os.path.join(self.home, "bin", "lxdtop")
        os.makedirs(os.path.dirname(self.installed))
        with open(self.installed, "w") as f:
            f.write(OLD)
        os.chmod(self.installed, 0o755)

    def run_readme(self, text=None, stand_in=True, **env):
        with open(LXDTOP_PATH) as f:
            text = text or f.read()
        with open(os.path.join(self.clone, "lxdtop"), "w") as f:
            f.write(text)
        os.chmod(os.path.join(self.clone, "lxdtop"), 0o755)
        path = (self.tools + os.pathsep if stand_in else "") + os.environ.get("PATH", "/usr/bin:/bin")
        return subprocess.run([shutil.which("sh"), "-c", clone_commands()], capture_output=True, text=True,
                              cwd=self.clone, timeout=60,
                              env=dict(PATH=path, HOME=self.home, LANG="C.UTF-8", MIDWAY=self.midway, **env))

    def assert_installed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(self.installed, "rb") as copy, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(copy.read(), original.read())
        self.assertEqual(stat.S_IMODE(os.stat(self.installed).st_mode), 0o755)
        self.assertFalse(os.path.exists(self.installed + ".new"))

    def test_the_old_copy_stays_whole_until_the_new_one_replaces_it(self):
        """#13: `install -m 755 lxdtop ~/bin/lxdtop` removed the old file before copying the new one.

        For that moment there was no lxdtop to start, or only part of one, as the console service
        might find when it restarts.
        """
        self.assert_installed(self.run_readme())
        with open(self.midway) as f:
            self.assertEqual(f.read(), OLD)             # while the new copy was written, the old one, whole

    def test_a_clone_that_does_not_run_leaves_the_old_copy(self):
        """Say a merge was left half done. The README checks the copy says it's lxdtop, as lxdtop-remote does."""
        with open(LXDTOP_PATH) as f:
            conflict = f.read().replace("\ndef main():", "\n<<<<<<< HEAD\ndef main():")
        result = self.run_readme(conflict)
        self.assertNotEqual(result.returncode, 0)
        with open(self.installed) as f:
            self.assertEqual(f.read(), OLD)

    def test_with_this_computers_own_install(self):
        self.assert_installed(self.run_readme(stand_in=False))

    def test_the_copy_is_flushed_to_disk_before_the_rename(self):
        """#16: as the download is, and as `lxdtop-remote install` does."""
        add_tool(self.tools, "sync", SYNC)
        self.assert_installed(self.run_readme(SYNC_LOG=self.sync_log))
        with open(self.sync_log) as f, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(f.read().splitlines(),
                             [f"sync {self.installed}.new", sha256(original.read()), sha256(OLD)])

    def test_a_copy_that_cannot_be_flushed_leaves_the_old_copy(self):
        add_tool(self.tools, "sync", SYNC)
        result = self.run_readme(SYNC_LOG=self.sync_log, SYNC_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        with open(self.installed) as f:
            self.assertEqual(f.read(), OLD)


if __name__ == "__main__":
    unittest.main()
