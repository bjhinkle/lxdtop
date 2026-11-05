"""A short screen: the cpu, memory and network panels stay, and other things give way first.

fit_layout() shares the rows. When everything fits, the layout is the one lxdtop always had. When
the screen is short, the CPU-by-instance graph shrinks to its smallest, then the stopped instances
fold into one line, then only the top running instances keep a row, with "+N more" for the rest;
the graph goes next, and the host panels last. The example here is a 160x45 console screen with 29
instances (25 running, 4 stopped): tests/fixtures/lxd-instances-29.json is LXD's answer for them.
"""

import contextlib
import io
import itertools
import re
import sys
import time
import unittest
from unittest import mock

from tests.support import MiB, FakeHost, FakeLxd, fixture_json, lxd_routes, lxdtop, monitor, render, row

INSTANCES = "/1.0/instances?recursion=2&all-projects=true"
ANSWER_29 = fixture_json("lxd-instances-29.json")
RUNNING = [inst["name"] for inst in ANSWER_29["metadata"] if inst["status"] == "Running"]   # busiest first
STOPPED = ["auth-two-old", "billing", "web-31", "web-32"]
FOLDED = "4 stopped: auth-two-old, billing, web-31, web-32"


def todays_layout(width, height, rows):
    """draw()'s panel heights before fit_layout(), for a screen where everything fitted."""
    spare = height - 1 - (rows + 3)
    top = 7 if width >= 120 and spare >= 15 else 0
    mid = min(14, spare - top) if spare - top >= 7 else 0
    fits = spare >= 15 if width >= 120 else spare >= 7          # every panel there's width for, every row
    return (top, mid) if fits else None


class FitLayoutTest(unittest.TestCase):
    def test_when_everything_fits_it_is_the_layout_lxdtop_always_had(self):
        checked = 0
        for width in (70, 80, 100, 119, 120, 139, 140, 160, 200):
            for height in range(12, 61):
                for running in range(0, 45):
                    for stopped in (0, 1, 4, 9):
                        today = todays_layout(width, height, running + stopped)
                        if today:
                            checked += 1
                            self.assertEqual(lxdtop.fit_layout(width, height, running, stopped),
                                             (*today, running, False), (width, height, running, stopped))
        self.assertGreater(checked, 10_000)

    def test_29_instances_and_the_order_things_give_way(self):
        fit = lxdtop.fit_layout
        self.assertEqual(fit(160, 45, 25, 4), (7, 8, 25, True))      # graph shrinks, the stopped fold: all fit
        self.assertEqual(fit(160, 45, 27, 0), (7, 7, 27, False))     # the graph shrinking is enough
        self.assertEqual(fit(160, 45, 30, 4), (7, 7, 25, True))      # then the top running ones, "+5 more"
        self.assertEqual(fit(160, 45, 60, 0), (7, 7, 26, False))
        self.assertEqual(fit(120, 40, 25, 4), (7, 7, 20, True))
        self.assertEqual(fit(80, 24, 25, 4), (0, 7, 11, True))       # no host panels under 120 columns, as before

    def test_the_graph_goes_before_the_host_panels(self):
        fit = lxdtop.fit_layout
        self.assertEqual(fit(160, 25, 25, 4), (7, 7, 5, True))       # MIN_RUNNING rows beside both panels
        self.assertEqual(fit(160, 24, 25, 4), (7, 0, 11, True))      # one row short: the graph goes
        self.assertEqual(fit(160, 18, 25, 4), (7, 0, 5, True))
        self.assertEqual(fit(160, 17, 25, 4), (0, 0, 11, True))      # then the host panels
        self.assertEqual(fit(70, 12, 25, 4), (0, 0, 6, True))        # the smallest screen lxdtop draws on

    def test_running_rows_are_never_given_up_for_stopped_ones(self):
        for height in range(12, 61):
            host, graph, shown, fold = lxdtop.fit_layout(160, height, 25, 4)
            if shown < 25:
                self.assertTrue(fold, height)


def render_short(rows, width=160, height=24, **attrs):
    mon = monitor(rows, **attrs)
    return render(mon, width, height)


class StoppedLineTest(unittest.TestCase):
    def setUp(self):
        saved = lxdtop.G
        self.addCleanup(setattr, lxdtop, "G", saved)

    def stopped_line(self, canvas):
        return next((i, line) for i, line in enumerate(canvas.text().splitlines()) if " stopped: " in line)

    def test_the_stopped_instances_share_one_line_cut_to_fit(self):
        rows = [row(f"run{i:02}") for i in range(30)] + [row(f"old-instance-{i:02}", state="Stopped", live=False)
                                                          for i in range(12)]
        canvas = render_short(rows, 80, 24)
        _, line = self.stopped_line(canvas)
        self.assertTrue(line.startswith("│ 12 stopped: old-instance-00, old-instance-01, "), line)
        self.assertTrue(line.endswith("… │"), line)                    # cut, and it says so
        self.assertEqual(len(line), 80)
        self.assertIn("+", canvas.text())                            # and "+N more" for the running ones

    def test_ascii(self):
        lxdtop.G = lxdtop.GLYPHS_ASCII
        rows = [row(f"run{i:02}") for i in range(30)] + [row(f"old-instance-{i:02}", state="Stopped", live=False)
                                                          for i in range(12)]
        text = render_short(rows, 80, 24).text()
        self.assertTrue(text.isascii())
        self.assertRegex(text, r"\| 12 stopped: old-instance-00, .*~ *\|")

    def test_a_stopped_instance_with_a_warning_stays_red(self):
        rows = [row(f"run{i:02}") for i in range(30)] + [row("billing", state="Stopped", live=False),
                                                          row("web-31", state="Stopped", live=False)]
        canvas = render_short(rows, 160, 24, kills={"billing": (1, 1, time.monotonic() - 60)})
        y, line = self.stopped_line(canvas)
        self.assertEqual(canvas.cells[y][line.index("billing")][1], "hi")
        self.assertEqual(canvas.cells[y][line.index("web-31")][1], "dim")

    def test_more_counts_the_running_ones_left_out(self):
        rows = [row(f"run{i:02}", cpu=float(100 - i)) for i in range(40)]
        text = render_short(rows, 160, 30).text()
        names = re.findall(r"│ ● (run\d\d) ", text)
        self.assertEqual(names, [f"run{i:02}" for i in range(len(names))])          # the busiest, in order
        self.assertIn(f"│ +{40 - len(names)} more ", text)
        self.assertNotIn(" stopped: ", text)


class EndToEndTest(unittest.TestCase):
    """End to end: LXD's answer for 29 instances, and a host running 25 of them."""

    def frame(self, size, ascii=False):
        host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 8)
        host.add_nic("eno1", 0, 0, physical=True)
        for i, name in enumerate(RUNNING):
            host.add_container(name, 1_000_000, memory=(256 + 8 * i) * MiB)
            host.add_nic(f"veth{i:04x}abc", 0, 0)

        def sleep(secs):                         # between --dump's readings: each one uses less CPU
            host.clock += secs
            for i, name in enumerate(RUNNING):
                host.add_container(name, 1_000_000 + (25 - i) * 20_000 * int(secs), memory=(256 + 8 * i) * MiB)

        out = io.StringIO()
        args = ["lxdtop", "--dump", "--size", size] + (["--ascii"] if ascii else [])
        saved = lxdtop.G
        self.addCleanup(setattr, lxdtop, "G", saved)
        with FakeLxd(lxd_routes(**{INSTANCES: (200, ANSWER_29)})) as lxd, host.installed(), \
                mock.patch.object(lxdtop.LxdApi, "start"), mock.patch.object(lxdtop.LxdEvents, "start"), \
                mock.patch.object(sys, "argv", args + ["--socket", lxd.path]), \
                mock.patch.object(lxdtop.time, "sleep", sleep), \
                mock.patch.object(lxdtop.socket, "gethostname", return_value="server"), \
                contextlib.redirect_stdout(out):
            lxdtop.main()
        return out.getvalue().splitlines()

    def panel_rows(self, lines, title):
        """How tall the panel with this title is, in rows; 0 if it isn't there."""
        start = next((i for i, line in enumerate(lines) if f"┐{title}┌" in line), None)
        if start is None:
            return 0
        column = lines[start].index("╭", lines[start].rfind("╭", 0, lines[start].index(f"┐{title}┌") + 1))
        end = next(i for i in range(start + 1, len(lines)) if lines[i][column:column + 1] == "╰")
        return end - start + 1

    def check_frame(self, lines, width, height):
        self.assertEqual(len(lines), height)
        self.assertTrue(all(len(line) <= width for line in lines))
        self.assertTrue(lines[-1].startswith("╰") and lines[-1].endswith("╯"))
        self.assertIn("25 running · 4 stopped", lines[0])

    def shown(self, lines):
        return re.findall(r"│ ● (\S+) ", "\n".join(lines))

    def test_160x45_keeps_the_host_panels(self):
        lines = self.frame("160x45")
        self.check_frame(lines, 160, 45)
        for title in ("cpu", "memory", "network"):
            self.assertEqual(self.panel_rows(lines, title), 7, title)
        self.assertEqual(self.panel_rows(lines, "cpu by container"), 8)      # down from 14
        self.assertEqual(self.shown(lines), RUNNING)                         # every running one, busiest first
        self.assertIn(f"│ {FOLDED} ", "\n".join(lines))
        self.assertNotIn("more", "\n".join(lines[22:]))

    def test_120x40(self):
        lines = self.frame("120x40")
        self.check_frame(lines, 120, 40)
        self.assertEqual(self.panel_rows(lines, "cpu"), 7)
        self.assertEqual(self.panel_rows(lines, "cpu by container"), 7)
        self.assertEqual(self.shown(lines), RUNNING[:20])
        text = "\n".join(lines)
        self.assertIn("│ +5 more ", text)
        self.assertIn(f"│ {FOLDED} ", text)

    def test_80x24(self):
        lines = self.frame("80x24")
        self.check_frame(lines, 80, 24)
        self.assertEqual(self.panel_rows(lines, "cpu"), 0)                   # under 120 columns, as before
        self.assertEqual(self.panel_rows(lines, "cpu by container"), 7)
        self.assertEqual(self.shown(lines), RUNNING[:11])
        text = "\n".join(lines)
        self.assertIn("│ +14 more ", text)
        self.assertIn(f"│ {FOLDED} ", text)

    def test_a_larger_screen_has_room_for_everything(self):
        lines = self.frame("200x60")
        self.check_frame(lines, 200, 60)
        self.assertEqual(self.panel_rows(lines, "cpu"), 7)
        self.assertEqual(self.panel_rows(lines, "cpu by container"), 14)
        self.assertEqual(self.shown(lines), RUNNING + STOPPED)              # a row each, as always
        self.assertNotIn(" stopped: ", "\n".join(lines))

    def test_ascii(self):
        lines = self.frame("160x45", ascii=True)
        text = "\n".join(lines)
        self.assertTrue(text.isascii())
        self.assertIn(f"| {FOLDED} ", text)


class EveryInstanceTest(unittest.TestCase):
    """At any size, every instance listed has a row of its own, is counted in "+N more", or is named in
    the stopped line: fit_layout() never gives the table fewer rows than that takes. (So the "… N more"
    line draw_table_panel() had for a table that ran out of rows was never drawn.)"""

    def draw(self, mon, width, height, text_filter=""):
        ui = lxdtop.new_ui(2)
        ui.filter = text_filter
        canvas = lxdtop.TextCanvas(height, width)
        with mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
            lxdtop.draw(canvas, mon, ui)
        lines = canvas.text().splitlines()
        return lines[next(i for i, line in enumerate(lines) if " NAME " in line) + 1:]     # under the header

    def test_every_size_and_count(self):
        for running, stopped in ((0, 0), (1, 0), (5, 3), (12, 0), (25, 4), (40, 10), (0, 12)):
            names = [f"c{i:02}" for i in range(running)] + [f"old{i:02}" for i in range(stopped)]
            mon = monitor([row(name, cpu=float(running - i)) for i, name in enumerate(names[:running])]
                          + [row(name, live=False, state="Stopped") for name in names[running:]])
            for (width, height), text_filter in itertools.product(
                    ((70, 12), (80, 24), (100, 18), (120, 30), (160, 45), (160, 20), (200, 60)), ("", "c1")):
                where = f"{running} running, {stopped} stopped, {width}x{height}, filter {text_filter!r}"
                text = "\n".join(self.draw(mon, width, height, text_filter))
                listed = [name for name in names if text_filter in name]
                rows = re.findall(r"^[│|] [●■*#] (\S+) ", text, re.M)
                more = sum(int(n) for n in re.findall(r"^│ \+(\d+) more ", text, re.M))
                folded = re.search(r"^│ (\d+) stopped: ", text, re.M)
                self.assertEqual(len(set(rows)), len(rows), where)
                self.assertEqual(len(rows) + more + (int(folded.group(1)) if folded else 0), len(listed), where)
                self.assertTrue(set(rows) <= set(listed), where)
                self.assertNotRegex(text, r"^│ … \d+ more", where)


if __name__ == "__main__":
    unittest.main()
