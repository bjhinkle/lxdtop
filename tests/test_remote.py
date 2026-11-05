"""contrib/lxdtop-remote, run with a stand-in ssh (tests/fake_ssh.py) instead of a real host."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests.support import LXDTOP_PATH, REMOTE_PATH, ROOT, lxdtop

FAKE_SSH = os.path.join(ROOT, "tests", "fake_ssh.py")
SH = shutil.which("sh")

STUB_LXDTOP = """#!/bin/sh
# Stands in for lxdtop on the host: write down how it was started.
{ printf 'TZ=%s\\n' "${TZ-unset}"; for a; do printf 'arg=%s\\n' "$a"; done; } > "$HOME/lxdtop-ran"
"""


@unittest.skipUnless(SH, "needs a POSIX shell")
class RemoteCase(unittest.TestCase):
    """This machine (a $HOME and a PATH with the fake ssh on it) and the LXD host's home directory."""

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
        return subprocess.run([SH, REMOTE_PATH, *args], input=stdin, capture_output=True, text=True,
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


class RemoteTest(RemoteCase):
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


with open(LXDTOP_PATH, "rb") as _f:
    SHA256 = hashlib.sha256(_f.read()).hexdigest()

SHASUM = f'''#!{sys.executable}
"""Stands in for macOS's shasum: SHA-1 unless -a names another, and "<digest>  -" for its standard input."""
import hashlib, sys
algorithm = sys.argv[2] if sys.argv[1:2] == ["-a"] else "1"
print(hashlib.new("sha" + algorithm, sys.stdin.buffer.read()).hexdigest() + "  -")
'''

SYNC_RECORDER = """#!/bin/sh
# Stands in for sync on the host: write down what it was asked to flush, and what that file
# and ~/bin/lxdtop held at that moment.
{ echo "sync $*"; sha256sum < "$1"; sha256sum < "$HOME/bin/lxdtop"; } >> "$HOME/sync-log"
"""


class InstallTest(RemoteCase):
    """`install` checks the copy as ~/bin/lxdtop.new on the host, then renames it over ~/bin/lxdtop.

    SECURITY-REVIEW.md N-7: it used to write straight over ~/bin/lxdtop, so a
    connection cut partway left a broken file there, which the console service
    then restarted every 3 seconds, and a symlink there was written through.
    Now ~/bin/lxdtop is either the old file, exactly as it was, or the whole new
    one, checked.
    """

    login_shell = "dash"                    # on the host; the classes below run these again with others

    def setUp(self):
        super().setUp()
        self.shell = shutil.which(self.login_shell)
        if not self.shell:
            self.skipTest(f"needs {self.login_shell}")
        self.host_path = os.environ.get("PATH", "/usr/bin:/bin")             # the PATH on the host
        self.installed = os.path.join(self.remote_home, "bin", "lxdtop")     # the old copy: STUB_LXDTOP
        self.new = self.installed + ".new"

    def install(self, **env):
        env.setdefault("FAKE_SSH_REMOTE_PATH", self.host_path)
        return self.remote("install", "me@lxdhost", FAKE_SSH_LOGIN_SHELL=self.shell, **env)

    def host_tool(self, name, text):
        """A PATH for the host with this script in front, standing in for one of its tools."""
        path = self.mkdir("host-" + name)
        self.write(os.path.join(path, name), text, 0o755)
        return path + os.pathsep + self.host_path

    def state(self, path=None):
        """What a failed install must leave exactly as it was: the file or symlink at path, and what it holds."""
        path = path or self.installed
        st = os.lstat(path)
        with open(path, "rb") as f:
            data = f.read()
        return dict(inode=st.st_ino, mode=st.st_mode, mtime=st.st_mtime_ns, ctime=st.st_ctime_ns, data=data,
                    link=os.readlink(path) if stat.S_ISLNK(st.st_mode) else None)

    def assert_new_copy(self):
        with open(self.installed, "rb") as copy, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(copy.read(), original.read())
        self.assertFalse(os.path.lexists(self.new))

    def assert_refused(self, result, old, says, status=1):
        self.assertEqual(self.state(), old)                 # ~/bin/lxdtop exactly as it was
        self.assertFalse(os.path.lexists(self.new))         # and nothing left beside it
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertIn(says, result.stderr)
        self.assertNotIn("installed", result.stdout)

    def test_install_checks_the_copy_then_renames_it_into_place(self):
        old = self.state()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1:], [f"installed lxdtop {lxdtop.VERSION}",
                                                          f"SHA-256 {SHA256}, checked on the host"])
        self.assert_new_copy()
        new = os.lstat(self.installed)
        self.assertTrue(stat.S_ISREG(new.st_mode))
        self.assertEqual(stat.S_IMODE(new.st_mode), 0o755)
        self.assertNotEqual(new.st_ino, old["inode"])        # renamed into place, not written over the old one
        self.assertEqual(len(self.connections()), 1)         # one connection, one login

    def test_a_copy_cut_off_partway_leaves_the_old_one(self):
        old = self.state()
        size = os.path.getsize(LXDTOP_PATH)
        for after in (0, 1000, size // 2, size - 1):
            with self.subTest(after=after):
                result = self.install(FAKE_SSH_DROP_AFTER=str(after))
                self.assert_refused(result, old, "ssh failed before me@lxdhost confirmed the install; its "
                                    "~/bin/lxdtop is unchanged unless the whole copy arrived", status=255)
                self.assertIn("Broken pipe", result.stderr)              # what ssh says

    def test_a_drop_after_the_whole_copy_arrived_installs_it_whole(self):
        """The host can't tell this drop from the copy's normal end: it checks the copy and installs it."""
        result = self.install(FAKE_SSH_DROP_AFTER=str(os.path.getsize(LXDTOP_PATH)))
        self.assertEqual(result.returncode, 255)
        self.assertIn("unless the whole copy arrived and passed its checks", result.stderr)
        self.assert_new_copy()

    def test_a_copy_that_arrives_damaged_leaves_the_old_one(self):
        old = self.state()
        result = self.install(FAKE_SSH_FLIP_BYTE="5000")
        self.assert_refused(result, old, "the copy that arrived is incomplete or damaged: its SHA-256 is ")
        self.assertIn(f", not {SHA256}; ~/bin/lxdtop was not changed", result.stderr)

    def test_a_file_that_does_not_run_leaves_the_old_one(self):
        with open(LXDTOP_PATH) as f:
            conflict = f.read().replace("\ndef main():", "\n<<<<<<< HEAD\ndef main():")   # a merge left half done
        cases = [
            ("conflict", conflict, "the copy does not run: its --version exited with status 1"),
            ("no python", "#!/usr/bin/env python3.0\n", "the copy does not run: its --version exited with status 127"),
            ("not lxdtop", "#!/bin/sh\necho 'top 4.0'\n", 'the copy is not lxdtop: its --version printed "top 4.0"'),
        ]
        old = self.state()
        for name, text, says in cases:
            with self.subTest(name):
                path = os.path.join(self.dir, name)
                self.write(path, text)
                self.assert_refused(self.install(LXDTOP=path), old, says)

    def test_a_symlink_at_bin_lxdtop_is_replaced_not_written_through(self):
        target = os.path.join(self.remote_home, "src", "lxdtop", "lxdtop")        # say, a clone on the host
        self.write(target, STUB_LXDTOP, 0o755)
        os.remove(self.installed)
        os.symlink(target, self.installed)
        link, pointed_to = self.state(), self.state(target)
        self.assert_refused(self.install(FAKE_SSH_FLIP_BYTE="5000"), link, "incomplete or damaged")
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.islink(self.installed))
        self.assert_new_copy()
        self.assertEqual(self.state(target), pointed_to)

    def test_anything_left_at_lxdtop_new_is_replaced_not_written_through(self):
        notes = os.path.join(self.remote_home, "notes.txt")
        self.write(notes, "keep me\n")
        os.symlink(notes, self.new)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_new_copy()
        with open(notes) as f:
            self.assertEqual(f.read(), "keep me\n")

    def test_the_copy_is_flushed_to_disk_before_the_rename(self):
        with open(self.installed, "rb") as f:
            old = hashlib.sha256(f.read()).hexdigest()
        result = self.install(FAKE_SSH_REMOTE_PATH=self.host_tool("sync", SYNC_RECORDER))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_new_copy()
        with open(os.path.join(self.remote_home, "sync-log")) as f:
            # sync was asked to flush lxdtop.new alone, once, when it held the whole copy and
            # ~/bin/lxdtop was still the old one: before the rename
            self.assertEqual(f.read().splitlines(), [f"sync {self.new}", f"{SHA256}  -", f"{old}  -"])

    def test_a_copy_that_cannot_be_flushed_leaves_the_old_one(self):
        failing = "#!/bin/sh\necho \"sync: error syncing '$1': Input/output error\" >&2\nexit 1\n"
        old = self.state()
        result = self.install(FAKE_SSH_REMOTE_PATH=self.host_tool("sync", failing))
        self.assert_refused(result, old, "could not flush ~/bin/lxdtop.new to disk; ~/bin/lxdtop was not changed")

    def test_a_directory_at_bin_lxdtop_is_left_alone(self):
        os.remove(self.installed)
        os.mkdir(self.installed)
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("can not replace ~/bin/lxdtop: it is a directory, or a link to one", result.stderr)
        self.assertEqual(os.listdir(self.installed), [])            # mv would have moved the copy into it
        self.assertFalse(os.path.lexists(self.new))

    def test_the_first_install_makes_bin(self):
        shutil.rmtree(os.path.join(self.remote_home, "bin"))
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_new_copy()

    def only(self, name, *tools):
        """A PATH with just the fake ssh, dirname and these tools on it, as a computer without the rest."""
        path = self.mkdir(name)
        os.symlink(os.path.join(self.bin, "ssh"), os.path.join(path, "ssh"))
        os.symlink(shutil.which("dirname"), os.path.join(path, "dirname"))
        for tool, text in tools:
            self.write(os.path.join(path, tool), text, 0o755)
        return path

    def test_on_a_mac_shasum_works_out_the_sha256(self):
        mac = self.only("mac", ("shasum", SHASUM))                      # macOS has no sha256sum
        result = self.install(PATH=mac)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"SHA-256 {SHA256}, checked on the host", result.stdout)
        self.assert_new_copy()

    def test_without_sha256sum_or_shasum_nothing_is_sent(self):
        old = self.state()
        result = self.install(PATH=self.only("bare"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("can't work out the SHA-256 of", result.stderr)
        self.assertEqual(self.ssh_runs(), [])
        self.assertEqual(self.state(), old)

    def test_install_needs_a_host(self):
        result = self.remote("install")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.ssh_runs(), [])


class BashInstallTest(InstallTest):
    """The same, with bash as the login shell on the host."""

    login_shell = "bash"


class BusyBoxInstallTest(InstallTest):
    """The same, on a host whose shell and tools all come from BusyBox, as on Alpine.

    Some BusyBox builds run their own applets before they search the PATH, among them Ubuntu's
    busybox-static, which most Ubuntu systems (and CI's runners) have. A stand-in tool on the
    host's PATH never runs there, so the tests that need one are skipped on those builds; the
    rest still run BusyBox's own tools, sync included.
    """

    login_shell = "busybox"

    def host_tool(self, name, text):
        path = super().host_tool(name, text)
        found = subprocess.run([self.shell, "-c", f"command -v {name}"], env=dict(PATH=path),
                               capture_output=True, text=True, timeout=60).stdout.strip()
        if found != os.path.join(path.split(os.pathsep)[0], name):
            self.skipTest(f"this BusyBox's sh runs its own {name} before it searches the PATH ({found!r}), "
                          f"so a stand-in {name} there never runs")
        return path

    def setUp(self):
        super().setUp()
        tools = self.mkdir("busybox")                   # BusyBox acts as the tool it is run as
        names = subprocess.run([self.shell, "--list"], capture_output=True, text=True, check=True).stdout.split()
        for name in names:
            os.symlink(self.shell, os.path.join(tools, name))
        os.symlink(sys.executable, os.path.join(tools, "python3"))       # for lxdtop itself
        self.shell = os.path.join(tools, "sh")
        self.host_path = tools


if __name__ == "__main__":
    unittest.main()
