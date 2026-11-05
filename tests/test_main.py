"""Running it: the keyboard loop with a fake screen, and main() with --dump, --version and a fake LXD."""

import contextlib
import curses
import io
import locale
import sys
import types
import unittest
from unittest import mock

from tests.support import MiB, FakeHost, FakeLxd, lxd_routes, lxdtop


class FakeScreen:
    def __init__(self, keys, size=(45, 160)):
        self.keys, self.size, self.refreshes = list(keys), size, 0

    def getmaxyx(self):
        return self.size

    def timeout(self, ms):
        self.timeout_ms = ms

    def erase(self):
        pass

    def refresh(self):
        self.refreshes += 1

    def addstr(self, *args):
        pass

    def insstr(self, *args):
        pass

    def getch(self):
        return self.keys.pop(0) if self.keys else ord("q")


class RunLoopTest(unittest.TestCase):
    def run_keys(self, keys, interval=2, **kwargs):
        frames = []
        mon = types.SimpleNamespace(samples=0)
        mon.sample = lambda: setattr(mon, "samples", mon.samples + 1)
        ui = lxdtop.new_ui(interval)
        screen = FakeScreen(keys)
        with mock.patch.object(lxdtop, "curses_attrs", return_value={}), \
                mock.patch.object(lxdtop, "draw", lambda cv, m, u: frames.append((u.sort, u.interval))):
            lxdtop.run(screen, mon, ui, **kwargs)
        return frames, mon, screen

    def test_keys_change_the_sort_and_the_interval(self):
        frames, _, _ = self.run_keys([ord("s"), ord("s"), ord("+"), ord("+"), ord("-")])
        self.assertEqual(frames, [("cpu", 2), ("mem", 2), ("disk", 2), ("disk", 3), ("disk", 5), ("disk", 3)])

    def test_sort_cycles_back_to_cpu(self):
        frames, _, _ = self.run_keys([ord("s"), ord("S"), ord("s"), ord("s"), ord("s")])
        self.assertEqual([sort for sort, _ in frames], ["cpu", "mem", "disk", "net", "name", "cpu"])

    def test_interval_stays_between_1_and_10_seconds(self):
        frames, _, _ = self.run_keys([ord("+"), ord("="), ord("+")], interval=5)
        self.assertEqual([i for _, i in frames], [5, 10, 10, 10])
        frames, _, _ = self.run_keys([ord("-"), ord("_"), ord("-")], interval=2)
        self.assertEqual([i for _, i in frames], [2, 1, 1, 1])
        frames, _, _ = self.run_keys([ord("+")], interval=2.5)       # --interval 2.5
        self.assertEqual([i for _, i in frames], [2.5, 3])

    def test_no_key_no_redraw_and_resize_redraws(self):
        frames, _, _ = self.run_keys([-1, -1, -1])
        self.assertEqual(len(frames), 1)
        frames, _, _ = self.run_keys([curses.KEY_RESIZE, -1])
        self.assertEqual(len(frames), 2)

    def test_q_quits_and_other_keys_are_ignored(self):
        frames, mon, screen = self.run_keys([ord("x"), ord("Q"), ord("s")])
        self.assertEqual(len(frames), 1)
        self.assertEqual(mon.samples, 1)
        self.assertEqual(screen.timeout_ms, 250)

    def test_max_frames(self):
        ticks = iter(range(0, 1000, 1))                  # the clock moves a second per look
        clock = types.SimpleNamespace(monotonic=lambda: float(next(ticks)))
        with mock.patch.object(lxdtop, "time", clock):
            frames, mon, _ = self.run_keys([-1] * 100, max_frames=3)
        self.assertEqual(len(frames), 3)
        self.assertGreater(mon.samples, 1)


class _Locale:
    """lxdtop's view of the locale module: nothing changes this test process's locale."""

    def __init__(self, encoding="UTF-8", error=None):
        self.encoding, self.error, self.calls = encoding, error, []

    def setlocale(self, category, value=None):
        self.calls.append((category, value))
        if self.error:
            raise self.error

    def getpreferredencoding(self, do_setlocale=True):
        return self.encoding

    def __getattr__(self, name):
        import locale
        return getattr(locale, name)


class MainTest(unittest.TestCase):
    def setUp(self):
        saved = lxdtop.G
        self.addCleanup(setattr, lxdtop, "G", saved)
        self.host = FakeHost(self)
        self.host.set_basics()
        self.host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        self.host.add_container("web", 10_000_000, memory=512 * MiB)

    def main(self, *args, locale=None):
        out = io.StringIO()
        with self.host.installed(), mock.patch.object(sys, "argv", ["lxdtop", *args]), \
                mock.patch.object(lxdtop, "locale", locale or _Locale()), \
                mock.patch.object(lxdtop.time, "sleep", lambda s: setattr(self.host, "clock", self.host.clock + s)), \
                mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"), \
                contextlib.redirect_stdout(out):
            lxdtop.main()
        return out.getvalue()

    def test_version(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["lxdtop", "--version"]), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as stop:
                lxdtop.main()
        self.assertEqual(stop.exception.code, 0)
        self.assertEqual(out.getvalue(), f"lxdtop {lxdtop.VERSION}\n")

    def test_dump_prints_one_frame(self):
        lines = self.main("--dump", "--no-api").rstrip("\n").split("\n")
        self.assertEqual(len(lines), 45)
        self.assertIn("┐containers┌", "\n".join(lines))
        self.assertTrue(any(" web " in line for line in lines))
        self.assertTrue(all(len(line) <= 160 for line in lines))

    def test_dump_size_and_ascii(self):
        text = self.main("--dump", "--no-api", "--size", "100x30", "--ascii")
        lines = text.rstrip("\n").split("\n")
        self.assertEqual(len(lines), 30)
        self.assertTrue(text.isascii())
        self.assertTrue(all(len(line) <= 100 for line in lines))

    def test_dump_with_color(self):
        self.assertIn("\x1b[0;1;37m", self.main("--dump", "--no-api", "--color"))

    def test_a_non_utf8_terminal_gets_ascii(self):
        text = self.main("--dump", "--no-api", locale=_Locale(encoding="ANSI_X3.4-1968"))
        self.assertTrue(text.isascii())

    def test_a_locale_the_host_does_not_have(self):
        # ssh passes LANG along (SendEnv/AcceptEnv LANG LC_* are the defaults on macOS and Ubuntu), so
        # LANG=de_DE.UTF-8 from a Mac reaches a host that may not have that locale installed.
        text = self.main("--dump", "--no-api", locale=_Locale(error=locale.Error("unsupported locale setting")))
        self.assertIn("┐containers┌", text)                   # still UTF-8, so still the Unicode glyphs

    def test_samples_take_readings_first(self):
        start = self.host.clock
        self.main("--dump", "--no-api", "--samples", "4", "--interval", "3")
        self.assertEqual(self.host.clock - start, 9)          # three waits between four readings

    def test_dump_with_lxd(self):
        self.host.add_container("db", 1, memory=64 * MiB)
        with FakeLxd(lxd_routes()) as lxd, \
                mock.patch.object(lxdtop.LxdApi, "start"), mock.patch.object(lxdtop.LxdEvents, "start"):
            text = self.main("--dump", "--socket", lxd.path)
        self.assertIn("LXD 6.9", text)
        self.assertIn("10.10.10.21", text)                    # web's address, from the API
        self.assertIn("┐instances┌", text)                    # the fixture host has a VM
        self.assertRegex(text, r"● old +stopped")
        self.assertEqual({m for m, _, _ in lxd.requests}, {"GET"})

    def test_dump_on_a_cluster_member(self):
        # SECURITY-REVIEW.md N-10: instances on other cluster members used to show here as stopped.
        routes = lxd_routes()
        routes["/1.0"][1]["metadata"]["environment"].update(server_clustered=True, server_name="m1")
        for inst in routes["/1.0/instances?recursion=2&all-projects=true"][1]["metadata"]:
            inst["location"] = "m1" if inst["name"] == "web" else "m2"
        with FakeLxd(routes) as lxd, \
                mock.patch.object(lxdtop.LxdApi, "start"), mock.patch.object(lxdtop.LxdEvents, "start"):
            text = self.main("--dump", "--socket", lxd.path)
        self.assertIn("1 running   up", text)                 # and no "stopped" count
        self.assertIn("10.10.10.21", text)                    # web is still there, with its address
        self.assertNotIn("stopped", text)
        self.assertIn("┐containers┌", text)                   # the VM is on m2

    def test_dump_when_lxd_answers_in_an_unexpected_shape(self):
        # SECURITY-REVIEW.md L-9: main() asks LXD once before drawing, and an
        # AttributeError from that first answer used to end lxdtop there.
        routes = lxd_routes(**{"/1.0/instances?recursion=2&all-projects=true": (200, {"metadata": "x"})})
        with FakeLxd(routes) as lxd, \
                mock.patch.object(lxdtop.LxdApi, "start"), mock.patch.object(lxdtop.LxdEvents, "start"):
            text = self.main("--dump", "--socket", lxd.path)
        self.assertIn("lxd api error (AttributeError)", text)

    def test_no_api_means_no_lxd_socket_at_all(self):
        with FakeLxd(lxd_routes()) as lxd:
            self.main("--dump", "--no-api", "--socket", lxd.path)
        self.assertEqual(lxd.requests, [])


if __name__ == "__main__":
    unittest.main()
