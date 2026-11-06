"""contrib/lxdtop-remote, run with a stand-in ssh (tests/fake_ssh.py) instead of a real host."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from tests.support import LXDTOP_PATH, REMOTE_PATH, ROOT, lxdtop

FAKE_SSH = os.path.join(ROOT, "tests", "fake_ssh.py")
FAKE_SUDO = os.path.join(ROOT, "tests", "fake_sudo.py")
SH = shutil.which("sh")
KEEPALIVE = [("-o", "ServerAliveInterval=15"), ("-o", "ServerAliveCountMax=3")]     # install's: see the script
OWN_OPTIONS = {("-G", ""), ("-t", ""), *KEEPALIVE}       # every option lxdtop-remote gives ssh

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
        # The host's /usr/local/bin, which only root can write to: here, only the stand-in sudo can
        self.root = self.mkdir("root")
        self.system_bin = os.path.join(self.root, "usr", "local", "bin")
        os.makedirs(self.system_bin)
        os.chmod(self.system_bin, 0o555)
        self.addCleanup(os.chmod, self.system_bin, 0o755)

    def mkdir(self, name):
        path = os.path.join(self.dir, name)
        os.makedirs(path)
        return path

    def write(self, path, text, mode=0o644):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, mode)

    def on_host(self, text):
        """Text as the host prints it: tests/fake_ssh.py points the command's /usr/local/bin at the stand-in."""
        return text.replace("/usr/local/bin", self.system_bin)

    def write_as_root(self, path, text, mode=0o755):
        """A file in the stand-in /usr/local/bin, put there as root would."""
        os.chmod(self.system_bin, 0o755)
        try:
            self.write(path, text, mode)
        finally:
            os.chmod(self.system_bin, 0o555)

    def conf(self, text):
        self.write(os.path.join(self.home, ".config", "lxdtop", "remote.conf"), text)

    def environ(self, **env):
        environ = dict(PATH=self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), HOME=self.home,
                       FAKE_SSH_LOG=self.log, FAKE_SSH_REMOTE_HOME=self.remote_home, LANG="C.UTF-8",
                       FAKE_SSH_ROOT=self.root, FAKE_SUDO_ROOT=self.root, FAKE_SUDO_LOG=self.log + ".sudo")
        environ.update(env)
        return environ

    def remote(self, *args, stdin="", **env):
        return subprocess.run([SH, REMOTE_PATH, *args], input=stdin, capture_output=True, text=True,
                              env=self.environ(**env), timeout=60)

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

    def test_install_takes_one_host_with_system_before_it(self):
        """A --system after the host, or a second host, is refused rather than quietly dropped."""
        for args in (("install", "--system"), ("install", "me@lxdhost", "--system"), ("install", "--system", "a", "b"),
                     ("install", "a", "b")):
            with self.subTest(args=args):
                result = self.remote(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage: lxdtop-remote install [--system] <host>", result.stderr)
        self.assertEqual(self.ssh_runs(), [])

    def test_the_view_and_screen_give_up_on_a_quiet_connection_within_a_minute_too(self):
        """#16: after a drop without a word, the view stayed frozen until TCP gave up, and screen could wait as
        long. They get install's two keepalive options: see InstallTest's test of them."""
        self.remote("me@lxdhost")
        self.remote("screen", "me@lxdhost", FAKE_SSH_DRY="1")
        view, screen = ([tuple(given) for given in run["options"]] for run in self.connections())
        self.assertEqual(view, KEEPALIVE + [("-t", "")])
        self.assertEqual(screen, KEEPALIVE)

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

        The only options ssh gets are lxdtop-remote's own, the same whatever the
        host and user: -G to read the SSH config, -t for lxdtop's screen, and
        the two keepalive options every connection gets (#16).
        """
        marker = os.path.join(self.dir, "ran")
        option = f"-oProxyCommand=touch {marker}"
        for args, stdin, destination in ((("install", option), "", option),
                                         (("install", "--system", option), "", option),
                                         (("screen", option), "", option),
                                         (("lxdhost",), option + "\n", f"{option}@lxdhost")):   # typed as the username
            with self.subTest(args=args, stdin=stdin):
                for path in (marker, marker + "@lxdhost", self.log):
                    if os.path.exists(path):
                        os.remove(path)
                self.remote(*args, stdin=stdin, FAKE_SSH_DRY="1")
                self.assertFalse(os.path.exists(marker) or os.path.exists(marker + "@lxdhost"))
                for run in self.ssh_runs():
                    self.assertLessEqual({tuple(given) for given in run["options"]}, OWN_OPTIONS, run["argv"])
                self.assertEqual([run["destination"] for run in self.connections()], [destination])


with open(LXDTOP_PATH, "rb") as _f:
    SHA256 = hashlib.sha256(_f.read()).hexdigest()

SHASUM = f'''#!{sys.executable}
"""Stands in for macOS's shasum: SHA-1 unless -a names another, and "<digest>  -" for its standard input."""
import hashlib, sys
algorithm = sys.argv[2] if sys.argv[1:2] == ["-a"] else "1"
print(hashlib.new("sha" + algorithm, sys.stdin.buffer.read()).hexdigest() + "  -")
'''

NO_WAIT = """#!/bin/sh
# Stands in for sleep on the host: it returns at once, so that a minute of one-second waits passes at once.
"""

PAUSE_AT_SYNC = f'''#!{sys.executable}
"""Stands in for sync on the host: say it got here, then wait for the go-ahead, for 30 seconds at most."""
import os, time
open(os.environ["AT_SYNC"], "w").close()
for _ in range(3000):
    if os.path.exists(os.environ["GO"]):
        break
    time.sleep(0.01)
'''

SYSTEMCTL = """#!/bin/sh
# Stands in for systemctl on the host: write down what it was asked, answer that lxdtop-console
# is running when $CONSOLE_SERVICE says so, and that it runs $CONSOLE_RUNS, in systemd's words.
echo "$*" >> "$HOME/systemctl-log"
case "$*" in
  "is-active --quiet lxdtop-console") [ "$CONSOLE_SERVICE" = running ] ;;
  "show -p ExecStart lxdtop-console")
    echo "ExecStart={ path=$CONSOLE_RUNS ; argv[]=$CONSOLE_RUNS ; ignore_errors=no ;" \\
         "start_time=[Fri 2026-11-06 09:00:00 UTC] ; stop_time=[n/a] ; pid=1234 ; code=(null) ; status=0/0 }" ;;
  *) exit 1 ;;
esac
"""

STILL_RUNNING = ("lxdtop-console is still running the lxdtop it started with: to run this one, restart it on the "
                 "host with sudo systemctl restart lxdtop-console")

RUNS_ANOTHER = ("lxdtop-console runs {}, not /usr/local/bin/lxdtop, so a restart would not run this one: to move it "
                "to this one, change its ExecStart to /usr/local/bin/lxdtop, then on the host run sudo systemctl "
                "daemon-reload and sudo systemctl restart lxdtop-console")

SHARED_COPY = ("this host also has /usr/local/bin/lxdtop, for every account, and this install did not update it. "
               "To update that one instead, use lxdtop-remote install --system, then remove this one: rm ~/bin/lxdtop")

OWN_COPY = ("this account still has its own ~/bin/lxdtop, which lxdtop-remote runs instead of /usr/local/bin/lxdtop, "
            "as does a PATH with ~/bin first, and which nothing updates now. To use /usr/local/bin/lxdtop, remove it: "
            "rm ~/bin/lxdtop")

ROOT_ID = """#!/bin/sh
# Stands in for id on the host, where the login is root: id -u prints 0.
[ "$1" = -u ] && echo 0
"""

SYNC_AS_ROOT = """#!/bin/sh
# Stands in for sync on the host, run by sudo: write down what it was asked to flush, and what that file
# and the installed copy held at that moment.
{ echo "sync $*"; sha256sum < "$1"; sha256sum < "$INSTALLED"; } >> "$SYNC_LOG"
"""

SYNC_RECORDER = """#!/bin/sh
# Stands in for sync on the host: write down what it was asked to flush, and what that file
# and ~/bin/lxdtop held at that moment.
{ echo "sync $*"; sha256sum < "$1"; sha256sum < "$HOME/bin/lxdtop"; } >> "$HOME/sync-log"
"""


class HostCase(RemoteCase):
    """An install on a host with a login shell of its own, and the file it replaces there, self.installed."""

    login_shell = "dash"                    # on the host; the classes at the end run these again with others

    def setUp(self):
        super().setUp()
        self.shell = shutil.which(self.login_shell)
        if not self.shell:
            self.skipTest(f"needs {self.login_shell}")
        self.host_path = os.environ.get("PATH", "/usr/bin:/bin")             # the PATH on the host

    @staticmethod
    def touch(path):
        open(path, "w").close()

    def wait_until(self, done, what):
        for _ in range(3000):
            if done():
                return
            time.sleep(0.01)
        self.fail(f"waited 30 seconds for {what}")

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

    def assert_refused(self, result, old, says, status=1):
        self.assertEqual(self.state(), old)                 # the installed file exactly as it was
        self.assertEqual(self.leftovers(), [])              # and nothing left beside it
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertIn(says, result.stderr)
        self.assertNotIn("installed", result.stdout)


class InstallTest(HostCase):
    """`install` checks the copy as a new file in ~/bin on the host, then renames it over ~/bin/lxdtop.

    SECURITY-REVIEW.md N-7: it used to write straight over ~/bin/lxdtop, so a
    connection cut partway left a broken file there, which the console service
    then restarted every 3 seconds, and a symlink there was written through.
    Now ~/bin/lxdtop is either the old file, exactly as it was, or the whole new
    one, checked.
    """

    def setUp(self):
        super().setUp()
        self.installed = os.path.join(self.remote_home, "bin", "lxdtop")     # the old copy: STUB_LXDTOP

    def install(self, **env):
        env.setdefault("FAKE_SSH_REMOTE_PATH", self.host_path)
        return self.remote("install", "me@lxdhost", FAKE_SSH_LOGIN_SHELL=self.shell, **env)

    def start_install(self, **env):
        """An install that runs while the test goes on."""
        env.setdefault("FAKE_SSH_REMOTE_PATH", self.host_path)
        proc = subprocess.Popen([SH, REMOTE_PATH, "install", "me@lxdhost"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self.environ(FAKE_SSH_LOGIN_SHELL=self.shell, **env))
        self.addCleanup(proc.wait, 60)
        self.addCleanup(proc.stderr.close)
        self.addCleanup(proc.stdout.close)
        return proc

    def leftovers(self):
        """The new files installs make beside ~/bin/lxdtop, if any are left there."""
        return sorted(name for name in os.listdir(os.path.dirname(self.installed)) if name.startswith("lxdtop.new"))

    def copy_there(self):
        """What ~/bin/lxdtop holds: its size and SHA-256."""
        with open(self.installed, "rb") as f:
            data = f.read()
        return len(data), hashlib.sha256(data).hexdigest()

    def assert_new_copy(self):
        with open(self.installed, "rb") as copy, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(copy.read(), original.read())
        self.assertEqual(self.leftovers(), [])

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

    def test_anything_left_at_lxdtop_new_is_removed_not_written_through(self):
        """~/bin/lxdtop.new is the one name earlier versions used for the new file."""
        notes = os.path.join(self.remote_home, "notes.txt")
        self.write(notes, "keep me\n")
        os.symlink(notes, self.installed + ".new")
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
            # sync was asked to flush the new file alone, once, when it held the whole copy and
            # ~/bin/lxdtop was still the old one: before the rename
            synced, *held = f.read().splitlines()
        self.assertRegex(synced, f"^sync {re.escape(self.installed)}\\.new\\.[^/]{{6}}$")
        self.assertEqual(held, [f"{SHA256}  -", f"{old}  -"])

    def test_a_copy_that_cannot_be_flushed_leaves_the_old_one(self):
        failing = "#!/bin/sh\necho \"sync: error syncing '$1': Input/output error\" >&2\nexit 1\n"
        old = self.state()
        result = self.install(FAKE_SSH_REMOTE_PATH=self.host_tool("sync", failing))
        self.assert_refused(result, old, "; ~/bin/lxdtop was not changed")
        self.assertRegex(result.stderr, r"could not flush ~/bin/lxdtop\.new\.[^/]{6} to disk; ")

    def test_a_directory_at_bin_lxdtop_is_left_alone(self):
        os.remove(self.installed)
        os.mkdir(self.installed)
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("can not replace ~/bin/lxdtop: it is a directory, or a link to one", result.stderr)
        self.assertEqual(os.listdir(self.installed), [])            # mv would have moved the copy into it
        self.assertEqual(self.leftovers(), [])

    def test_two_installs_at_once_each_rename_their_own_copy(self):
        """#12: two installs at once shared ~/bin/lxdtop.new, so one could rename the other's unfinished copy.

        The first has its whole copy, checked, and waits at sync. The second starts, and part of
        its copy arrives. Then the first renames its copy into place: that must be its own, not
        the second's unfinished one. The rest of the second's copy then arrives, and it installs
        that: a pause in the copy isn't a dropped connection.
        """
        other = os.path.join(self.dir, "other")                             # another version
        with open(LXDTOP_PATH) as f:
            text = f.read().replace(f'\nVERSION = "{lxdtop.VERSION}"\n', '\nVERSION = "9.9.9 (other)"\n')
        self.write(other, text, 0o755)
        at_sync, go, resume = (os.path.join(self.dir, name) for name in ("at-sync", "go", "resume"))
        first = self.start_install(FAKE_SSH_REMOTE_PATH=self.host_tool("sync", PAUSE_AT_SYNC), AT_SYNC=at_sync, GO=go)
        self.addCleanup(self.touch, go)                 # let it go on, whatever happens, before waiting for it
        self.wait_until(lambda: os.path.exists(at_sync), "the first install to reach sync")
        second = self.start_install(LXDTOP=other, FAKE_SSH_STALL_AFTER="1000", FAKE_SSH_RESUME=resume)
        self.addCleanup(self.touch, resume)
        bin_ = os.path.dirname(self.installed)
        self.wait_until(lambda: 1000 in [os.path.getsize(os.path.join(bin_, name)) for name in self.leftovers()],
                        "part of the second copy to arrive")
        self.touch(go)
        out, err = first.communicate(timeout=60)
        self.assertEqual((first.returncode, out.splitlines()[1:]),
                         (0, [f"installed lxdtop {lxdtop.VERSION}", f"SHA-256 {SHA256}, checked on the host"]), err)
        self.assertEqual(self.copy_there(), (os.path.getsize(LXDTOP_PATH), SHA256))       # the first's own copy
        self.touch(resume)
        out, err = second.communicate(timeout=60)
        self.assertEqual(second.returncode, 0, err)
        self.assertIn("installed lxdtop 9.9.9 (other)\n", out)
        self.assertEqual(self.copy_there(), (len(text.encode()), hashlib.sha256(text.encode()).hexdigest()))
        self.assertEqual(self.leftovers(), [])

    def test_a_connection_that_goes_quiet_ends_the_install_within_a_minute(self):
        """#12: a connection that dropped without a word left lxdtop.new, and cat waiting for more, on the host.

        sshd notices such a drop only when TCP keepalive gives up, about two hours on with Ubuntu's
        defaults. Now the host stops once nothing more has arrived for a minute. Its sleep returns
        at once here, so that minute passes at once.
        """
        old = self.state()
        result = self.install(FAKE_SSH_STALL_AFTER="1000", FAKE_SSH_REMOTE_PATH=self.host_tool("sleep", NO_WAIT))
        with open(self.log + ".host") as f:
            host = json.load(f)
        self.assertTrue(host["ended"], "the install on the host was still waiting after 20 seconds")
        self.assertFalse(host["input_read"], "something on the host still reads the connection")
        self.assertEqual(host["stderr"], "lxdtop-remote: nothing more of the copy arrived for a minute; "
                                         "~/bin/lxdtop was not changed\n")
        self.assertEqual(self.state(), old)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(result.returncode, 255)
        self.assertIn("ssh failed before me@lxdhost confirmed the install", result.stderr)

    def test_ssh_on_this_machine_gives_up_on_a_quiet_connection_within_a_minute_too(self):
        """#16: after a drop without a word, this machine's ssh waited until Ctrl-C or TCP gave up.

        With these options, OpenSSH asks the host's sshd for an answer once 15 seconds pass with
        nothing from it, then every 15 seconds, and gives up when a fourth ask is due with none of
        the three answered (server_alive_check() in its clientloop.c): 60 seconds. This stand-in
        doesn't act them out, so this checks that install gives them, and only them.
        """
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([[tuple(given) for given in run["options"]] for run in self.connections()], [KEEPALIVE])

    def test_what_an_install_killed_outright_left_is_removed(self):
        """kill -9 or a power cut leaves an install's new file. The next removes those two days old, and lxdtop.new.

        No install is still writing a file that old. ~/bin/lxdtop.new is the one name earlier
        versions used.
        """
        bin_ = os.path.dirname(self.installed)
        for name in ("lxdtop.new.Ab12Cd", "lxdtop.new.Ef34Gh", "lxdtop.new", "lxdtop.new.notes"):
            self.write(os.path.join(bin_, name), "part of a copy\n")
        three_days_ago = time.time() - 3 * 24 * 3600
        for name in ("lxdtop.new.Ab12Cd", "lxdtop.new.notes"):
            os.utime(os.path.join(bin_, name), (three_days_ago, three_days_ago))
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        # left: one another install may still be writing, and a file of the user's own
        self.assertEqual(self.leftovers(), ["lxdtop.new.Ef34Gh", "lxdtop.new.notes"])

    def test_says_when_the_console_service_is_still_running_the_old_copy(self):
        """#12: the console service keeps running the copy it started with. install restarts nothing, but says so."""
        path = self.host_tool("systemctl", SYSTEMCTL)
        installed = [f"installed lxdtop {lxdtop.VERSION}", f"SHA-256 {SHA256}, checked on the host"]
        for service, said in (("running", [STILL_RUNNING]), ("stopped", [])):
            with self.subTest(service):
                result = self.install(FAKE_SSH_REMOTE_PATH=path, CONSOLE_SERVICE=service)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[1:], installed + said)
        refused = self.install(FAKE_SSH_REMOTE_PATH=path, CONSOLE_SERVICE="running", FAKE_SSH_FLIP_BYTE="5000")
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn("lxdtop-console", refused.stdout + refused.stderr)
        with open(os.path.join(self.remote_home, "systemctl-log")) as f:
            self.assertEqual(f.read().splitlines(), ["is-active --quiet lxdtop-console"] * 2)   # nothing restarted

    def test_says_when_the_host_also_has_a_copy_for_every_account(self):
        """That copy isn't updated, and this account runs its own ~/bin/lxdtop in front of it."""
        installed = [f"installed lxdtop {lxdtop.VERSION}", f"SHA-256 {SHA256}, checked on the host"]
        shared = os.path.join(self.system_bin, "lxdtop")
        self.write_as_root(shared, STUB_LXDTOP)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1:], installed + [self.on_host(SHARED_COPY)])
        self.assertEqual(self.state(shared)["data"], STUB_LXDTOP.encode())           # left alone
        os.chmod(self.system_bin, 0o755)
        os.remove(shared)
        os.symlink(self.installed, shared)              # a link to this account's copy is that copy, not another
        os.chmod(self.system_bin, 0o555)
        result = self.install()
        self.assertEqual(result.stdout.splitlines()[1:], installed)

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


class SystemInstallTest(HostCase):
    """`install --system` puts one copy for every account at /usr/local/bin/lxdtop, with sudo on the host.

    The copy arrives and is checked as plain install's is, in a new file in the home
    directory. Then sudo, as root, copies it to a new file beside /usr/local/bin/lxdtop,
    checks that one again, and renames it into place. When sudo wants a password, a
    second login, with a terminal, does that part. Here /usr/local/bin is a stand-in
    that only the stand-in sudo, tests/fake_sudo.py, can write to, and
    tests/fake_ssh.py points the command's /usr/local/bin at it, so the host's
    messages name the stand-in.
    """

    def setUp(self):
        super().setUp()
        shutil.rmtree(os.path.join(self.remote_home, "bin"))            # this account has no copy of its own
        self.installed = os.path.join(self.system_bin, "lxdtop")
        self.write_as_root(self.installed, STUB_LXDTOP)                  # the old copy
        self.sudo = self.mkdir("host-sudo")
        self.write(os.path.join(self.sudo, "sudo"), f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SUDO}" "$@"\n', 0o755)
        self.report = [f"installed lxdtop {lxdtop.VERSION}", f"SHA-256 {SHA256}, checked on the host"]

    def install(self, sudo=True, **env):
        path = env.pop("FAKE_SSH_REMOTE_PATH", self.host_path)
        return self.remote("install", "--system", "me@lxdhost", FAKE_SSH_LOGIN_SHELL=self.shell,
                           FAKE_SSH_REMOTE_PATH=self.sudo + os.pathsep + path if sudo else path, **env)

    def sudo_runs(self):
        if not os.path.exists(self.log + ".sudo"):
            return []
        with open(self.log + ".sudo") as f:
            return [json.loads(line) for line in f]

    def leftovers(self):
        """The new files installs make, if any are left: beside /usr/local/bin/lxdtop, and in the home directory."""
        return sorted([name for name in os.listdir(self.system_bin) if name.startswith("lxdtop.new")] +
                      ["~/" + name for name in os.listdir(self.remote_home) if name.startswith(".lxdtop.new")])

    def path_without(self, name):
        """The host's PATH without this tool: a directory of links to every other command on it."""
        path = self.mkdir("host-without-" + name)
        for directory in self.host_path.split(os.pathsep):
            for tool in sorted(os.listdir(directory)) if os.path.isdir(directory) else ():
                there, here = os.path.join(directory, tool), os.path.join(path, tool)
                if tool != name and not os.path.lexists(here) and os.path.isfile(there) and os.access(there, os.X_OK):
                    os.symlink(there, here)
        return path

    def assert_new_copy(self):
        with open(self.installed, "rb") as copy, open(LXDTOP_PATH, "rb") as original:
            self.assertEqual(copy.read(), original.read())
        st = os.lstat(self.installed)
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o755)
        self.assertEqual(self.leftovers(), [])

    def test_install_checks_the_copy_then_root_renames_it_into_place(self):
        old = self.state()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].endswith(" to me@lxdhost:/usr/local/bin/lxdtop"), lines[0])
        self.assertEqual(lines[1:], self.report)
        self.assert_new_copy()
        self.assertNotEqual(os.lstat(self.installed).st_ino, old["inode"])     # renamed into place, not written over
        self.assertEqual(len(self.connections()), 1)           # sudo needs no password: one connection, one login
        check, install = self.sudo_runs()
        self.assertEqual(check["argv"], ["-n", "true"])
        self.assertEqual(install["argv"][:3] + install["argv"][4:5], ["-n", "sh", "-c", "lxdtop-remote"])
        src, want = install["argv"][-2:]                        # the file the user checked, and its SHA-256
        self.assertRegex(src, f"^{re.escape(self.remote_home)}/\\.lxdtop\\.new\\.[^/]{{6}}$")
        self.assertEqual(want, SHA256)

    def test_a_copy_that_arrives_damaged_is_refused_before_sudo(self):
        old = self.state()
        result = self.install(FAKE_SSH_FLIP_BYTE="5000")
        self.assert_refused(result, old, "the copy that arrived is incomplete or damaged: its SHA-256 is ")
        self.assertIn(f", not {SHA256}; {self.installed} was not changed", result.stderr)
        self.assertEqual(self.sudo_runs(), [])

    def test_a_file_that_does_not_run_is_refused_before_sudo(self):
        old = self.state()
        path = os.path.join(self.dir, "not-lxdtop")
        self.write(path, "#!/bin/sh\necho 'top 4.0'\n")
        self.assert_refused(self.install(LXDTOP=path), old, 'the copy is not lxdtop: its --version printed "top 4.0"')
        self.assertEqual(self.sudo_runs(), [])

    def test_root_checks_the_copy_again_before_it_installs_it(self):
        """What sudo installs is what was checked: a change to the checked file before root copies it is refused."""
        old = self.state()
        result = self.install(FAKE_SUDO_CHANGE="1")
        self.assert_refused(result, old, f"the copy changed after it was checked: its SHA-256 is no longer {SHA256}; "
                                         f"{self.installed} was not changed\n")
        self.assertEqual(len(self.sudo_runs()), 2)                      # root's part ran, and refused
        self.assertNotIn("sudo did not run the install", result.stderr)  # and said so once

    def test_a_password_for_sudo_gets_a_second_login_with_a_terminal(self):
        result = self.install(FAKE_SUDO="password")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1:], ["sudo on me@lxdhost wants a password: logging in again, "
                                                          "with a terminal for it to ask on"] + self.report)
        self.assertIn("[sudo] password for me: ", result.stderr)
        self.assert_new_copy()                                  # the checked file, gone from the home directory too
        first, second = self.connections()
        self.assertNotIn(["-t", ""], first["options"])
        self.assertIn(["-t", ""], second["options"])
        check, install = self.sudo_runs()
        self.assertEqual((check["argv"], check["terminal"]), (["-n", "true"], False))
        self.assertEqual((install["argv"][:2], install["terminal"]), (["sh", "-c"], True))
        name = install["argv"][-2].rsplit(".", 1)[1]            # the second login installs the file the first checked
        self.assertIn(f"new=~/.lxdtop.new.{name} ", " ".join(second["words"]))

    def test_without_a_terminal_for_the_password_nothing_changes(self):
        old = self.state()
        result = self.install(FAKE_SUDO="password", FAKE_SSH_NO_TERMINAL="1")
        self.assert_refused(result, old, f"sudo did not run the install; {self.installed} was not changed")
        self.assertIn("sudo: a terminal is required to read the password", result.stderr)

    def test_sudo_that_refuses_leaves_the_old_copy(self):
        old = self.state()
        result = self.install(FAKE_SUDO="refuse")
        self.assert_refused(result, old, "me is not in the sudoers file.\nlxdtop-remote: sudo did not run the install; "
                                         f"{self.installed} was not changed\n")
        self.assertEqual(len(self.connections()), 2)

    def test_a_host_without_sudo_is_refused_before_anything_is_made(self):
        old = self.state()
        result = self.install(sudo=False, FAKE_SSH_REMOTE_PATH=self.path_without("sudo"))
        self.assert_refused(result, old, f"installing in {self.system_bin} needs sudo, and this host has none; "
                                         f"{self.installed} was not changed")
        self.assertEqual(os.listdir(self.remote_home), [])

    def test_a_root_login_needs_no_sudo(self):
        """#16: logged in as root, it was refused on a host without sudo, though root needs none.

        Now a root login runs root's part itself, with the same checks and messages, and never asks sudo, even
        where there is one. Here the stand-in id says the login is root, and the stand-in /usr/local/bin is
        writable, as it is for root.
        """
        self.host_path = self.path_without("sudo")
        as_root = self.host_tool("id", ROOT_ID)
        os.chmod(self.system_bin, 0o755)
        for host, path in (("without sudo", as_root), ("with a sudo that refuses", self.sudo + os.pathsep + as_root)):
            with self.subTest(host):
                result = self.install(sudo=False, FAKE_SSH_REMOTE_PATH=path, FAKE_SUDO="refuse")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[1:], self.report)
                self.assert_new_copy()
        self.assertEqual(self.sudo_runs(), [])
        self.assertEqual(len(self.connections()), 2)            # one login each
        os.remove(self.installed)
        os.mkdir(self.installed)
        result = self.install(sudo=False, FAKE_SSH_REMOTE_PATH=as_root)
        self.assertEqual(result.returncode, 1)                  # root's part refuses, and says why once
        self.assertEqual(result.stderr, f"lxdtop-remote: can not replace {self.installed}: it is a directory, or a "
                                        f"link to one; {self.installed} was not changed\n")
        self.assertEqual(os.listdir(self.installed), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_connection_that_goes_quiet_ends_the_install_within_a_minute(self):
        """As plain install's does: see InstallTest. Its sleep returns at once here, so that minute passes at once."""
        old = self.state()
        result = self.install(FAKE_SSH_STALL_AFTER="1000", FAKE_SSH_REMOTE_PATH=self.host_tool("sleep", NO_WAIT))
        with open(self.log + ".host") as f:
            host = json.load(f)
        self.assertTrue(host["ended"], "the install on the host was still waiting after 20 seconds")
        self.assertFalse(host["input_read"], "something on the host still reads the connection")
        self.assertEqual(host["stderr"], "lxdtop-remote: nothing more of the copy arrived for a minute; "
                                         f"{self.installed} was not changed\n")
        self.assertEqual(self.state(), old)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.sudo_runs(), [])
        self.assertEqual(result.returncode, 255)
        self.assertIn("ssh failed before me@lxdhost confirmed the install; its /usr/local/bin/lxdtop is unchanged",
                      result.stderr)

    def test_warns_when_this_account_has_its_own_copy_in_bin(self):
        """lxdtop-remote and a PATH with ~/bin first run that one in front of this one, and nothing updates it."""
        own = os.path.join(self.remote_home, "bin", "lxdtop")
        self.write(own, STUB_LXDTOP, 0o755)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1:], self.report + [self.on_host(OWN_COPY)])
        with open(own) as f:
            self.assertEqual(f.read(), STUB_LXDTOP)                      # left alone
        os.remove(own)
        os.symlink(self.installed, own)                 # a link to the copy for every account is that copy
        result = self.install()
        self.assertEqual(result.stdout.splitlines()[1:], self.report)

    def test_the_copy_is_flushed_to_disk_before_the_rename(self):
        log = os.path.join(self.dir, "sync-log")
        result = self.install(FAKE_SSH_REMOTE_PATH=self.host_tool("sync", SYNC_AS_ROOT), SYNC_LOG=log,
                              INSTALLED=self.installed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_new_copy()
        with open(log) as f:
            # sync was asked to flush root's new file alone, once, when it held the whole copy and
            # /usr/local/bin/lxdtop was still the old one: before the rename
            synced, *held = f.read().splitlines()
        self.assertRegex(synced, f"^sync {re.escape(self.system_bin)}/lxdtop\\.new\\.[^/]{{6}}$")
        self.assertEqual(held, [f"{SHA256}  -", f"{hashlib.sha256(STUB_LXDTOP.encode()).hexdigest()}  -"])

    def test_a_directory_at_usr_local_bin_lxdtop_is_left_alone(self):
        os.chmod(self.system_bin, 0o755)
        os.remove(self.installed)
        os.mkdir(self.installed)
        os.chmod(self.system_bin, 0o555)
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"can not replace {self.installed}: it is a directory, or a link to one; {self.installed} was "
                      "not changed", result.stderr)
        self.assertEqual(os.listdir(self.installed), [])            # mv would have moved the copy into it
        self.assertEqual(self.leftovers(), [])

    def test_says_when_the_console_service_is_still_running_the_old_copy(self):
        path = self.host_tool("systemctl", SYSTEMCTL)
        for service, said in (("running", [STILL_RUNNING]), ("stopped", [])):
            with self.subTest(service):
                result = self.install(FAKE_SSH_REMOTE_PATH=path, CONSOLE_SERVICE=service, CONSOLE_RUNS=self.installed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[1:], self.report + said)
        with open(os.path.join(self.remote_home, "systemctl-log")) as f:
            asked = f.read().splitlines()
        # which lxdtop the running service runs, asked once, and nothing restarted
        self.assertEqual(asked, ["is-active --quiet lxdtop-console", "show -p ExecStart lxdtop-console",
                                 "is-active --quiet lxdtop-console"])

    def test_says_when_the_console_service_runs_another_copy(self):
        """#16: a console service made from the earlier example runs ~/bin/lxdtop, which a restart only starts again.

        The note said to restart it to run the new copy. Now it says the service runs another, and how to move the
        service to /usr/local/bin/lxdtop, after one login or two.
        """
        own = os.path.join(self.remote_home, "bin", "lxdtop")
        self.write(own, STUB_LXDTOP, 0o755)
        path = self.host_tool("systemctl", SYSTEMCTL)
        second = "sudo on me@lxdhost wants a password: logging in again, with a terminal for it to ask on"
        for sudo, said in (("", []), ("password", [second])):
            with self.subTest(sudo=sudo):
                result = self.install(FAKE_SSH_REMOTE_PATH=path, FAKE_SUDO=sudo, CONSOLE_SERVICE="running",
                                      CONSOLE_RUNS=own)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[1:], said + self.report + [
                    self.on_host(OWN_COPY), self.on_host(RUNS_ANOTHER.format(own))])
        os.remove(own)
        os.symlink(self.installed, own)                 # a link to the copy for every account runs that copy
        result = self.install(FAKE_SSH_REMOTE_PATH=path, CONSOLE_SERVICE="running", CONSOLE_RUNS=own)
        self.assertEqual(result.stdout.splitlines()[1:], self.report + [STILL_RUNNING])

    def test_what_an_install_stopped_outright_left_is_removed(self):
        """New files two days old: the user's in the home directory, such as one left between the two logins, and
        root's beside /usr/local/bin/lxdtop. No install is still using a file that old."""
        home, system = self.remote_home, self.system_bin
        files = [os.path.join(home, ".lxdtop.new.Ab12Cd"), os.path.join(home, ".lxdtop.new.Ef34Gh"),
                 os.path.join(system, "lxdtop.new.Ij56Kl"), os.path.join(system, "lxdtop.new.Mn78Op")]
        for path in files:
            (self.write_as_root if path.startswith(self.system_bin) else self.write)(path, "part of a copy\n", 0o644)
        three_days_ago = time.time() - 3 * 24 * 3600
        for path in files[0], files[2]:
            os.utime(path, (three_days_ago, three_days_ago))
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.leftovers(), ["lxdtop.new.Mn78Op", "~/.lxdtop.new.Ef34Gh"])


class BusyBoxHost:
    """The host's shell and tools all come from BusyBox, as on Alpine: the install classes above, run there.

    Some BusyBox builds run their own applets before they search the PATH, among them Ubuntu's
    busybox-static, which most Ubuntu systems (and CI's runners) have. A stand-in tool on the
    host's PATH never runs there, so the tests that need one are skipped on those builds; the
    rest still run BusyBox's own tools, sync included. BusyBox has no sudo, so the stand-in
    sudo runs everywhere.
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


class BashInstallTest(InstallTest):
    """The same, with bash as the login shell on the host."""

    login_shell = "bash"


class BusyBoxInstallTest(BusyBoxHost, InstallTest):
    """The same, on a host whose shell and tools all come from BusyBox: see BusyBoxHost."""


class BashSystemInstallTest(SystemInstallTest):
    """The same, with bash as the login shell on the host."""

    login_shell = "bash"


class BusyBoxSystemInstallTest(BusyBoxHost, SystemInstallTest):
    """The same, on a host whose shell and tools all come from BusyBox: see BusyBoxHost."""


if __name__ == "__main__":
    unittest.main()
