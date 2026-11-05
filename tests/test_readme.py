"""The README's install commands, run as written, with a stand-in curl or install."""

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

FAKE_CURL = f'''#!{sys.executable}
"""Stands in for curl -o FILE URL: writes $FAKE_CURL_BODY to FILE, or only its first $FAKE_CURL_CUT
bytes, then exits with $FAKE_CURL_EXIT (curl's 18 means "partial file")."""
import os, sys
with open(os.environ["FAKE_CURL_BODY"], "rb") as f:
    body = f.read()
cut = os.environ.get("FAKE_CURL_CUT")
with open(sys.argv[sys.argv.index("-o") + 1], "wb") as f:
    f.write(body[:int(cut)] if cut else body)
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")))
'''

OLD = "#!/bin/sh\necho 'lxdtop 1.4.0 (the old copy)'\n"


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
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lxdtop-readme-")
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "home")
        tools = os.path.join(tmp.name, "tools")
        os.makedirs(self.home)
        os.makedirs(tools)
        with open(os.path.join(tools, "curl"), "w") as f:
            f.write(FAKE_CURL)
        os.chmod(os.path.join(tools, "curl"), 0o755)
        self.path = tools + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
        self.installed = os.path.join(self.home, "bin", "lxdtop")

    def old_copy(self):
        os.makedirs(os.path.dirname(self.installed))
        with open(self.installed, "w") as f:
            f.write(OLD)
        os.chmod(self.installed, 0o755)

    def run_readme(self, body=LXDTOP_PATH, **env):
        environ = dict(PATH=self.path, HOME=self.home, LANG="C.UTF-8", FAKE_CURL_BODY=body, **env)
        return subprocess.run([shutil.which("sh"), "-c", install_commands()], capture_output=True, text=True,
                              env=environ, timeout=60)

    def assert_installed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"lxdtop {lxdtop.VERSION}\n")         # the version, shown by grep
        with open(self.installed, "rb") as copy, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(copy.read(), original.read())
        self.assertEqual(stat.S_IMODE(os.stat(self.installed).st_mode), 0o755)
        self.assertFalse(os.path.exists(self.installed + ".new"))

    def assert_old_copy(self):
        with open(self.installed) as f:
            self.assertEqual(f.read(), OLD)

    def test_a_download_replaces_the_old_copy(self):
        self.old_copy()
        self.assert_installed(self.run_readme())

    def test_the_first_install(self):
        self.assert_installed(self.run_readme())

    def test_a_download_cut_partway_leaves_the_old_copy(self):
        self.old_copy()
        result = self.run_readme(FAKE_CURL_CUT="40000", FAKE_CURL_EXIT="18")
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_copy()

    def test_a_download_cut_where_it_still_runs_leaves_the_old_copy(self):
        """Cut just before its last lines, lxdtop is still a whole Python program that never calls main().

        `--version` then exits 0 without printing anything, so checking its exit
        status alone would install it. The README checks that it prints lxdtop's
        version.
        """
        with open(LXDTOP_PATH, "rb") as f:
            cut = f.read().rindex(b'\nif __name__ == "__main__":')
        self.old_copy()
        result = self.run_readme(FAKE_CURL_CUT=str(cut))                       # and curl says all went well
        self.assertNotEqual(result.returncode, 0)
        self.assert_old_copy()
        cut_off = subprocess.run([self.installed + ".new", "--version"], capture_output=True, text=True,
                                 env=dict(PATH=self.path), timeout=60)
        self.assertEqual((cut_off.returncode, cut_off.stdout), (0, ""))        # why the check needs grep


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
        with open(os.path.join(self.tools, "install"), "w") as f:
            f.write(GNU_INSTALL)
        os.chmod(os.path.join(self.tools, "install"), 0o755)
        self.installed = os.path.join(self.home, "bin", "lxdtop")
        os.makedirs(os.path.dirname(self.installed))
        with open(self.installed, "w") as f:
            f.write(OLD)
        os.chmod(self.installed, 0o755)

    def run_readme(self, text=None, stand_in=True):
        with open(LXDTOP_PATH) as f:
            text = text or f.read()
        with open(os.path.join(self.clone, "lxdtop"), "w") as f:
            f.write(text)
        os.chmod(os.path.join(self.clone, "lxdtop"), 0o755)
        path = (self.tools + os.pathsep if stand_in else "") + os.environ.get("PATH", "/usr/bin:/bin")
        return subprocess.run([shutil.which("sh"), "-c", clone_commands()], capture_output=True, text=True,
                              cwd=self.clone, env=dict(PATH=path, HOME=self.home, LANG="C.UTF-8", MIDWAY=self.midway),
                              timeout=60)

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


if __name__ == "__main__":
    unittest.main()
