"""Text that people inside containers choose: process names, cgroup names, and what LXD passes on.

Any process in any container can name itself (prctl, /proc/self/comm, or the
name of the file it runs), and the kernel shows that name in /proc/<pid>/stat
as raw bytes. lxdtop runs on the host with access equivalent to root and draws
those names on the operator's terminal. See SECURITY-REVIEW.md.
"""

import locale
import os
import tempfile
import time
import types
import unittest

from tests.support import FakeHost, glyphs, lxdtop, monitor, pid_stat, render, row

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


class RecordingScreen:
    """Enough of a curses window for CursesCanvas: keeps every string it is given."""

    def __init__(self, h=45, w=160):
        self.size, self.strings = (h, w), []

    def getmaxyx(self):
        return self.size

    def addstr(self, y, x, text, attr=0):
        self.strings.append(text)

    insstr = addstr


class ControlCharacterTest(unittest.TestCase):
    """SECURITY-REVIEW.md L-2 and L-3: control characters in names reached the terminal.

    --dump printed them as they were, so an escape sequence in a process name
    went straight to the terminal. The live view hands text to curses, which
    shows ESC as ^[ but moves its cursor on \r, \b and \n (a name could
    overwrite the start of its own row) and passes format characters such as
    U+202E through. Canvas.put() now replaces anything that isn't printable.
    """

    NASTY = {
        "escape sequence": "a\x1b]0;pwned\x07b",
        "carriage return": "abc\rXY",
        "backspace": "abc\b\bXY",
        "newline": "abc\nXY",
        "tab": "a\tb",
        "delete": "a\x7fb",
        "C1 control": "a\u009b31mb",
        "right-to-left override": "a\u202eb",
        "zero-width space": "a\u200bb",
        "line separator": "a\u2028b",
    }

    def test_the_canvas_replaces_them_one_for_one(self):
        for label, text in self.NASTY.items():
            with self.subTest(label):
                cv = lxdtop.TextCanvas(1, 30)
                self.assertEqual(cv.put(0, 0, text), len(text))        # the same width as before
                self.assertEqual(cv.text(), "".join(c if c.isprintable() else "?" for c in text))

    def test_curses_never_gets_them(self):
        screen = RecordingScreen()
        cv = lxdtop.CursesCanvas(screen, {})
        for text in self.NASTY.values():
            cv.put(0, 0, text)
        self.assertTrue(screen.strings)
        self.assertTrue(all(s.isprintable() for s in screen.strings), screen.strings)

    def test_glyphs_and_ordinary_text_are_unchanged(self):
        samples = ["web-01", "nginx: worker", "(sd-pam)", "10.10.10.21", "Größe 東京", "\\xff\\xfe"]
        for table in (lxdtop.GLYPHS_UNICODE, lxdtop.GLYPHS_ASCII):
            samples += list(table.values())
        for text in samples:
            cv = lxdtop.TextCanvas(1, 40)
            cv.put(0, 0, text)
            self.assertEqual(cv.text(), text.rstrip())

    def test_a_process_name_in_dump_output(self):
        canvas = render(monitor([row("web", top=("\x1b]0;pwned\x07", 50.0))]))
        for text in (canvas.text(), canvas.text(color=True)):          # --dump and --dump --color
            self.assertNotIn("\x1b]", text)
            self.assertNotIn("\x07", text)
        self.assertIn("?]0;pwned?", canvas.text())

    def test_a_carriage_return_in_a_process_name_stays_in_its_column(self):
        screen = RecordingScreen()
        mon = monitor([row("web", top=("\rvictim", 50.0))])
        with glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.draw(lxdtop.CursesCanvas(screen, {}), mon, types.SimpleNamespace(sort="cpu", interval=2))
        self.assertIn("?victim", screen.strings)
        self.assertFalse(any("\r" in s for s in screen.strings))

    def test_event_text_and_addresses(self):
        mon = monitor([row("win", vm=True, top=None, ip="\x1b[2J10.10.10.30")])
        feed = [dict(action="instance-file-pushed", name="web\x1b[8m", detail="/tmp/\x1b]52;c;cm0K\x07",
                     user="ci\r", t=time.time(), count=1)]
        mon.events = types.SimpleNamespace(snapshot=lambda: (feed, "live"))
        text = render(mon).text()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)
        self.assertIn("?[2J10.10.10.30", text)


if __name__ == "__main__":
    unittest.main()
