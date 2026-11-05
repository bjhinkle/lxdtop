"""contrib/lxdtop-remote, run with a stand-in ssh (tests/fake_ssh.py) instead of a real host."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests.support import LXDTOP_PATH, REMOTE_PATH, ROOT

FAKE_SSH = os.path.join(ROOT, "tests", "fake_ssh.py")

STUB_LXDTOP = """#!/bin/sh
# Stands in for lxdtop on the host: write down how it was started.
{ printf 'TZ=%s\\n' "${TZ-unset}"; for a; do printf 'arg=%s\\n' "$a"; done; } > "$HOME/lxdtop-ran"
"""


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class RemoteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lxdtop-remote-")
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.bin = self.mkdir("bin")
        self.home = self.mkdir("home")                    # this machine's $HOME
        self.remote_home = self.mkdir("remote")           # the login on the LXD host
        self.log = os.path.join(self.dir, "ssh.log")
        self.write(os.path.join(self.bin, "ssh"), f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SSH}" "$@"\n', 0o755)
        self.write(os.path.join(self.remote_home, "bin", "lxdtop"), STUB_LXDTOP, 0o755)

    def mkdir(self, name):
        path = os.path.join(self.dir, name)
        os.makedirs(path)
        return path

    def write(self, path, text, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def conf(self, text):
        self.write(os.path.join(self.home, ".config", "lxdtop", "remote.conf"), text)

    def remote(self, *args, stdin="", **env):
        environ = dict(PATH=self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), HOME=self.home,
                       FAKE_SSH_LOG=self.log, FAKE_SSH_REMOTE_HOME=self.remote_home, LANG="C.UTF-8")
        environ.update(env)
        return subprocess.run(["sh", REMOTE_PATH, *args], input=stdin, capture_output=True, text=True,
                              env=environ, timeout=60)

    def ssh_runs(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def connections(self):
        """The ssh runs that would connect somewhere (not ssh -G)."""
        return [run for run in self.ssh_runs() if ["-G", ""] not in run["options"]]

    def lxdtop_ran(self):
        with open(os.path.join(self.remote_home, "lxdtop-ran")) as f:
            return f.read().splitlines()

    def test_runs_lxdtop_on_the_host_with_its_options(self):
        result = self.remote("me@lxdhost", "--ascii", "--interval", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        (run,) = self.connections()
        self.assertEqual(run["destination"], "me@lxdhost")
        self.assertIn(["-t", ""], run["options"])         # lxdtop needs a terminal
        self.assertEqual(self.lxdtop_ran(), ["TZ=unset", "arg=--ascii", "arg=--interval", "arg=5"])

    def test_options_reach_lxdtop_exactly_as_given(self):
        """SECURITY-REVIEW.md R-2: the host's shell used to read the options again.

        ssh joins its arguments with spaces and hands them to the login shell on
        the host, so an option with a space was split in two, and `;`, `$` or `*`
        in one ran or expanded there.
        """
        marker = os.path.join(self.remote_home, "ran")
        options = ["--socket", "/var/snap/lxd/common/lxd dir/unix.socket", "--interval", f"2;touch {marker}",
                   "it's", "$HOME", "*", ""]
        result = self.remote("me@lxdhost", *options)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lxdtop_ran(), ["TZ=unset"] + [f"arg={o}" for o in options])
        self.assertFalse(os.path.exists(marker))

    def test_the_clock_time_zone_setting(self):
        self.conf("REMOTE_TZ=Europe/Berlin\n")
        self.remote("me@lxdhost")
        self.assertEqual(self.lxdtop_ran(), ["TZ=Europe/Berlin"])

    def test_default_host(self):
        self.conf("DEFAULT_HOST=me@lxdhost\n")
        self.remote("--no-api")
        self.assertEqual(self.connections()[0]["destination"], "me@lxdhost")
        self.assertEqual(self.lxdtop_ran(), ["TZ=unset", "arg=--no-api"])

    def test_no_host(self):
        result = self.remote()
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: lxdtop-remote <host>", result.stderr)
        self.assertEqual(self.ssh_runs(), [])

    def test_lxdtop_on_the_hosts_path(self):
        os.remove(os.path.join(self.remote_home, "bin", "lxdtop"))
        shared = self.mkdir("usr-local-bin")
        self.write(os.path.join(shared, "lxdtop"), STUB_LXDTOP, 0o755)
        result = self.remote("me@lxdhost", "--ascii", PATH=shared + os.pathsep + self.bin + os.pathsep
                             + os.environ.get("PATH", "/usr/bin:/bin"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lxdtop_ran(), ["TZ=unset", "arg=--ascii"])

    def test_no_lxdtop_on_the_host(self):
        os.remove(os.path.join(self.remote_home, "bin", "lxdtop"))
        result = self.remote("me@lxdhost")
        self.assertEqual(result.returncode, 127)
        self.assertIn("lxdtop is not in ~/bin or on the PATH of", result.stderr)

    def test_install_copies_lxdtop_and_shows_its_version(self):
        result = self.remote("install", "me@lxdhost")
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = os.path.join(self.remote_home, "bin", "lxdtop")
        with open(installed, "rb") as copy, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(copy.read(), original.read())
        self.assertEqual(stat.S_IMODE(os.stat(installed).st_mode), 0o755)
        self.assertIn("lxdtop 1.5.0", result.stdout)
        self.assertEqual(len(self.connections()), 1)           # one connection, one login

    def test_install_needs_a_host(self):
        result = self.remote("install")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.ssh_runs(), [])

    def test_asks_for_a_username_once_and_remembers_it(self):
        first = self.remote("lxdhost", stdin="alice\n")
        self.assertIn("username on lxdhost: ", first.stderr)
        self.assertEqual(self.connections()[-1]["destination"], "alice@lxdhost")
        with open(os.path.join(self.home, ".config", "lxdtop", "users")) as f:
            self.assertEqual(f.read(), "lxdhost alice\n")
        second = self.remote("lxdhost", stdin="\n")
        self.assertIn("username on lxdhost [alice]: ", second.stderr)
        self.assertEqual(self.connections()[-1]["destination"], "alice@lxdhost")

    def test_hosts_in_the_ssh_config_are_used_as_they_are(self):
        result = self.remote("lxdhost", FAKE_SSH_CONFIGURED="lxdhost")
        self.assertNotIn("username", result.stderr)
        self.assertEqual(self.connections()[-1]["destination"], "lxdhost")

    def test_screen_flips_between_two_consoles(self):
        result = self.remote("screen", "me@lxdhost", FAKE_SSH_DRY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        (run,) = self.connections()
        self.assertEqual(" ".join(run["words"]),
                         'if [ "$(cat /sys/class/tty/tty0/active)" = tty8 ]; then sudo -n chvt 1 && '
                         'echo "console: tty1"; else sudo -n chvt 8 && echo "console: tty8"; fi')

    def test_a_host_or_user_starting_with_a_dash_is_not_an_ssh_option(self):
        """SECURITY-REVIEW.md R-1: ssh used to read such a host as an option.

        With `-oProxyCommand=...` as the host, ssh ran that command on this
        machine. OpenSSH 9.6 and later refuse the connection first, because the
        next argument (the remote command) then becomes the host name and has
        spaces in it; older versions, and this stand-in, run it.
        """
        marker = os.path.join(self.dir, "ran")
        for args, stdin in ((("install", f"-oProxyCommand=touch {marker}"), ""),
                            (("screen", f"-oProxyCommand=touch {marker}"), ""),
                            (("lxdhost",), f"-oProxyCommand=touch {marker}\n")):    # typed as the username
            with self.subTest(args=args, stdin=stdin):
                for path in (marker, marker + "@lxdhost", self.log):
                    if os.path.exists(path):
                        os.remove(path)
                self.remote(*args, stdin=stdin, FAKE_SSH_DRY="1")
                self.assertFalse(os.path.exists(marker) or os.path.exists(marker + "@lxdhost"))
                for run in self.ssh_runs():
                    self.assertNotIn("-o", [name for name, _ in run["options"]], run["argv"])


if __name__ == "__main__":
    unittest.main()
