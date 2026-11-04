"""Text that people inside containers choose: process names, cgroup names, and what LXD passes on.

Any process in any container can name itself (prctl, /proc/self/comm, or the
name of the file it runs), and the kernel shows that name in /proc/<pid>/stat
as raw bytes. lxdtop runs on the host with access equivalent to root and draws
those names on the operator's terminal. See SECURITY-REVIEW.md.
"""

import locale
import os
import tempfile
import unittest

from tests.support import FakeHost, lxdtop, pid_stat

BAD_NAME = b"\xff\xfeevil"          # not valid UTF-8; 6 bytes, so it fits the kernel's 15


def as_lxdtop_reads_it(raw):
    """The text lxdtop gets for these bytes with this machine's default encoding."""
    return raw.decode(locale.getpreferredencoding(False), "backslashreplace")


class NotUtf8Test(unittest.TestCase):
    """SECURITY-REVIEW.md L-1: a name that isn't UTF-8 used to end lxdtop with UnicodeDecodeError."""

    def test_read_returns_text_for_bytes_that_are_not_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stat")
            with open(path, "wb") as f:
                f.write(pid_stat(4242, "X", utime=5).encode().replace(b"(X)", b"(" + BAD_NAME + b")"))
            text = lxdtop.read(path)
        self.assertIn(as_lxdtop_reads_it(BAD_NAME), text)

    def test_the_busiest_process_can_have_such_a_name(self):
        host = FakeHost(self)
        line = pid_stat(100, "X", utime=50, start=10).encode().replace(b"(X)", b"(" + BAD_NAME + b")")
        host.write("/proc/100/stat", line)
        host.write("/proc/100/cgroup", "0::/lxc.payload.web/init.scope\n")
        table = lxdtop.ProcessTable()
        with host.installed():
            table.sample(10.0)
        self.assertEqual(table.top["web"][0], as_lxdtop_reads_it(BAD_NAME))

    def test_a_cgroup_named_by_the_container(self):
        # A container's root can create sub-cgroups with any name.
        host = FakeHost(self)
        host.write("/proc/100/cgroup", b"0::/lxc.payload.web/\xff\xfe/x\n")
        with host.installed():
            self.assertEqual(lxdtop.container_of(100), "web")


if __name__ == "__main__":
    unittest.main()
