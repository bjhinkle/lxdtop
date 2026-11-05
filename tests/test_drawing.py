"""The two drawings: classic, which the Linux console can show, and fine, for terminal windows.

pick_drawing() chooses between them. The fine drawing changes how the CPU graph, the CPU history
and the meters are drawn, and nothing else. The classic drawing and --ascii stay exactly as they
were: tests/fixtures/frame-*.txt were drawn by lxdtop 1.7.1, and ClassicUnchangedTest compares.
"""

import collections
import contextlib
import io
import itertools
import os
import re
import sys
import unittest
from unittest import mock

from tests.support import GiB, MiB, fixture, glyphs, lxdtop, row
from tests import support

ESC = re.compile(r"\x1b\[[0-9;]*m")
SHADES, BRAILLE = set("█▒░"), {chr(c) for c in range(0x2801, 0x2900)}


@contextlib.contextmanager
def drawing(fine):
    """Draw with the fine drawing (True) or the classic one (False) for a while."""
    saved = lxdtop.FINE, lxdtop.DRAWING
    lxdtop.FINE = fine
    try:
        yield
    finally:
        lxdtop.FINE, lxdtop.DRAWING = saved


def monitor():
    """A busy host with a few instances, and histories that rise and fall."""
    rows = [row("web", cpu=72.0), row("db", cpu=35.0, mem=3 * GiB), row("cache", cpu=4.0, mem=96 * MiB),
            row("win", cpu=12.0, vm=True, top=None, tasks=None), row("idle", cpu=0.2, top=("sleep", 0.0)),
            row("old", state="Stopped", live=False, cpu=None, mem=None, rd=None, wr=None, net_in=None,
                net_out=None, tasks=None, top=None, up=None, ip="")]
    mon = support.monitor(rows)
    mon.host["threads"] = [12.0, 80.0, 45.0, 3.0, 97.0, 60.0, 25.0, 100.0]
    mon.ncpu = 8
    wave = [(i * 37) % 100 for i in range(120)]                # 0, 37, 74, 11, 48, ...
    for r in rows:
        if r["live"]:
            peak = r["cpu"]
            mon.cpu_history[r["cg"]] = collections.deque([peak * w / 100 for w in wave[:90]],
                                                         maxlen=lxdtop.HISTORY)
    mon.stack.clear()
    for w in wave:
        sample = {r["cg"]: r["cpu"] * w / 100 for r in rows if r["live"]}
        sample[lxdtop.HOST] = 20.0 + w / 10
        mon.stack.append(sample)
    return mon


def frame(width, height, ascii=False, fine=False, view=None, color=True, mon=None):
    """One frame as --dump --color draws it, with the clock and the host name fixed."""
    mon = mon or monitor()
    ui = lxdtop.new_ui(2)
    if view == "help":
        ui.view, ui.back = "help", "main"
    elif view == "detail":
        ui.view, ui.detail = "detail", "web"
    canvas = lxdtop.TextCanvas(height, width)
    clock = lambda fmt, *_: ("Thu Nov 5  09:30:00 UTC" if "%a" in fmt else "09:30:00" if "%S" in fmt
                             else "09:30 UTC")
    with contextlib.ExitStack() as stack:
        stack.enter_context(glyphs(lxdtop.GLYPHS_ASCII if ascii else lxdtop.GLYPHS_UNICODE))
        if fine is not None:                         # (None: an lxdtop from before the fine drawing)
            stack.enter_context(drawing(fine))
        stack.enter_context(mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"))
        stack.enter_context(mock.patch.object(lxdtop.time, "strftime", clock))
        stack.enter_context(mock.patch.object(lxdtop.time, "time", return_value=1_800_000_000.0))
        stack.enter_context(mock.patch.object(lxdtop.time, "monotonic", return_value=5000.0))
        lxdtop.draw(canvas, mon, ui)
        return canvas.text(color=color)              # (the colors depend on the drawing too)


def plain(text):
    return ESC.sub("", text)


def net_graph(lines, width):
    """The network panel's graph in a frame's lines: its four rows (in, then out), past the labels."""
    x = 2 * (width * 52 // 160)
    return [line[x + 13:width - 2] for line in lines[2:6]]


class PickDrawingTest(unittest.TestCase):
    def pick(self, asked=None, colors=256, term="xterm-256color", ascii=False, encoding="UTF-8"):
        with drawing(False), glyphs(lxdtop.GLYPHS_ASCII if ascii else lxdtop.GLYPHS_UNICODE), \
                mock.patch.dict(os.environ, TERM=term), \
                mock.patch.object(lxdtop.locale, "getpreferredencoding", return_value=encoding):
            lxdtop.pick_drawing(asked, colors)
            return lxdtop.FINE, lxdtop.DRAWING

    def test_a_terminal_window_with_256_colors_gets_the_fine_drawing(self):
        self.assertEqual(self.pick(), (True, "fine (256 colors)"))
        self.assertEqual(self.pick(colors=16777216, term="xterm-direct"), (True, "fine (16777216 colors)"))

    def test_the_linux_console_keeps_the_classic_drawing(self):
        for term in ("linux", "linux-16color"):
            self.assertEqual(self.pick(term=term), (False, f"classic (TERM={term})"))   # even with 256 colors

    def test_fewer_than_256_colors_keep_the_classic_drawing(self):
        self.assertEqual(self.pick(colors=8, term="xterm"), (False, "classic (8 colors)"))
        self.assertEqual(self.pick(colors=0, term="vt100"), (False, "classic (0 colors)"))

    def test_dump_draws_classic_unless_asked(self):
        self.assertEqual(self.pick(colors=None), (False, "classic (--dump)"))
        self.assertEqual(self.pick("fine", colors=None), (True, "fine (--drawing fine)"))

    def test_asked_for(self):
        self.assertEqual(self.pick("classic"), (False, "classic (--drawing classic)"))
        self.assertEqual(self.pick("fine", colors=8, term="linux"), (True, "fine (--drawing fine)"))

    def test_ascii_wins(self):
        self.assertEqual(self.pick("fine", ascii=True), (False, "ascii (--ascii)"))
        self.assertEqual(self.pick(ascii=True, encoding="ANSI_X3.4-1968"), (False, "ascii (not a UTF-8 locale)"))


class FineGlyphsTest(unittest.TestCase):
    def test_a_meter_fills_in_eighths_then_shows_its_track(self):
        with drawing(True):
            cells = lxdtop.meter_cells(10, 37, 100, lxdtop.heat)          # 3.7 cells: 3 full, then 6 eighths
        self.assertEqual("".join(ch for ch, _ in cells), "███▊░░░░░░")
        self.assertEqual([style for _, style in cells], ["lv0", "lv1", "lv2", "lv3"] + ["faint"] * 6)
        with drawing(True):
            self.assertEqual(lxdtop.meter_cells(4, 0, 100, lxdtop.heat), [("░", "faint")] * 4)
            self.assertEqual(lxdtop.meter_cells(4, 5, 0, lxdtop.heat), [("░", "faint")] * 4)       # no scale
            self.assertEqual(lxdtop.meter_cells(4, 250, 100, lxdtop.heat),
                             [("█", "lv0"), ("█", "lv2"), ("█", "lv5"), ("█", "lv7")])           # no more than full

    def test_heat_has_ten_shades_changing_where_the_three_do(self):
        with drawing(True):
            shades = [lxdtop.heat(p / 100) for p in (0, 49, 50, 79, 80, 99, 100)]
        self.assertEqual(shades, ["lv0", "lv4", "lv5", "lv7", "lv8", "lv9", "lv9"])
        for p in range(100):
            with drawing(True):
                fine = lxdtop.heat(p / 100)
            self.assertEqual(lxdtop.STYLES[fine], lxdtop.STYLES[lxdtop.heat(p / 100)])   # same 16-color stand-in
        self.assertEqual(lxdtop.STYLES_256["lv0"][0], 41)                 # green
        self.assertEqual(lxdtop.STYLES_256["lv5"][0], 221)                # yellow
        self.assertEqual(lxdtop.STYLES_256["lv9"][0], 196)                # red

    def test_an_upright_bar_tops_out_in_eighths(self):
        self.assertEqual([lxdtop.bar_glyph(f) for f in (0, 0.05, 0.07, 0.5, 0.94, 1.0, 3.0)],
                         [None, None, "▁", "▄", "█", "█", "█"])

    def test_two_history_samples_share_a_braille_cell(self):
        spark = lxdtop.braille_spark
        self.assertEqual(spark(None, None), (" ", "text"))
        self.assertEqual(spark(None, 0.1), ("⢀", "faint"))           # idle: a faint dot at the bottom
        self.assertEqual(spark(0.0, 0.3), ("⣀", "faint"))
        self.assertEqual(spark(1.0, None), ("⡀", "lo"))              # 0.5-2%: one dot
        self.assertEqual(spark(5.0, 20.0), ("⣴", "lo"))              # 2-10%: two; 10-50%: three
        self.assertEqual(spark(0.0, 60.0), ("⢸", "mid"))             # idle beside busy: only the busy dots
        self.assertEqual(spark(95.0, 12.0), ("⣷", "hi"))             # colored as the busier one

    def test_history_cells_newest_on_the_right(self):
        with drawing(False), glyphs(lxdtop.GLYPHS_UNICODE):
            self.assertEqual(lxdtop.history_cells([0.0, 60.0], 4),            # as 1.7.1 drew it
                             [(" ", "text"), (" ", "text"), ("·", "faint"), ("█", "mid")])
        with drawing(True):
            cells = lxdtop.history_cells([1.0, 5.0, 20.0, 60.0, 95.0], 4)    # odd: the oldest sits alone
            last = lxdtop.history_cells(range(1000), 3)
        self.assertEqual(cells, [(" ", "text"), ("⢀", "lo"), ("⣴", "lo"), ("⣿", "hi")])
        self.assertEqual(last, [lxdtop.braille_spark(994, 995), lxdtop.braille_spark(996, 997),
                                lxdtop.braille_spark(998, 999)])            # the newest six samples


class FineGraphTest(unittest.TestCase):
    def graph(self, samples, height=2, width=10):
        mon = support.monitor()
        mon.ncpu = 8                                                 # so the scale can reach 800%
        mon.stack.clear()
        mon.stack.extend(samples)
        canvas = lxdtop.TextCanvas(height, width)
        with drawing(True):
            series = lxdtop.draw_graph(canvas, 0, 0, width, height, mon)
        return canvas, series, mon

    def test_two_samples_a_cell_and_four_dots_to_its_height(self):
        # A 2-row graph has 8 dots of height. With 800% (eight cores) at the top, 100% is one dot.
        samples = [{"web": 100.0}, {"web": 300.0}, {"web": 450.0}, {"web": 800.0}]
        canvas, series, mon = self.graph([{"web": 760.0}] + samples)      # scale: 800% (the 760 needs 800)
        lines = canvas.text().split("\n")
        self.assertEqual(series, ["web"])
        # Cells, oldest first: [nothing | 760], [100 | 300], [450 | 800], so the newest is at the right.
        self.assertEqual(lines[0], " 800%  ⢸ ⣸")     # the top row: 760's last 4 dots; 450 -> 5 dots, its 5th here
        self.assertEqual(lines[1], "   0%  ⢸⣰⣿")     # the bottom row: 4; 1 and 3; 4 and 4
        styles = {style for row_cells in canvas.cells for ch, style in row_cells if ord(ch) >= 0x2800}
        self.assertEqual(styles, {mon.series_style["web"]})

    def test_a_cell_takes_its_taller_columns_layer(self):
        # One row of 4 dots, scale 800%. Left: db's 600% (3 dots) under web's 100% (half a dot): 4 dots.
        # Right: web's 200%, one dot. The left column is taller, and the layer at its middle is db's.
        canvas, series, mon = self.graph([{"web": 100.0, "db": 600.0}, {"web": 200.0}], height=1)
        self.assertEqual(series, ["db", "web"])                      # the busiest at the bottom
        (ch, style), = [(ch, st) for ch, st in canvas.cells[0] if ord(ch) >= 0x2800]
        self.assertEqual(ch, chr(0x2800 + 0x47 + 0x80))              # 4 dots on the left, 1 on the right
        self.assertEqual(style, mon.series_style["db"])
        canvas, _, mon = self.graph([{"web": 200.0}, {"web": 100.0, "db": 600.0}], height=1)
        (ch, style), = [(ch, st) for ch, st in canvas.cells[0] if ord(ch) >= 0x2800]
        self.assertEqual((ch, style), (chr(0x2800 + 0x40 + 0xB8), mon.series_style["db"]))   # and mirrored

    def test_the_fine_graph_and_history_cover_twice_the_time(self):
        classic, fine = plain(frame(160, 45, fine=False)), plain(frame(160, 45, fine=True))
        self.assertIn("last 3 min", classic)                         # 96 cells x 2 s
        self.assertIn("last 6 min", fine)                            # 192 samples x 2 s
        self.assertIn("CPU HISTORY (last 72s)", classic)
        self.assertIn("CPU HISTORY (last 2 min)", fine)


class FineNetGraphTest(unittest.TestCase):
    """The network graph in the fine drawing: braille, two samples a cell, and four dots to a row, with
    traffic in filling up from the middle line and traffic out filling down from it."""

    def graph(self, ins, outs, width=56, fine=True):
        """Draw the network panel (7 rows, as on screen) and return its graph's rows and the panel's top."""
        mon = support.monitor()
        mon.net_in.clear()
        mon.net_out.clear()
        mon.net_in.extend(ins)
        mon.net_out.extend(outs)
        canvas = lxdtop.TextCanvas(7, width)
        with drawing(fine), glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.draw_net_panel(canvas, 0, 0, 7, width, mon)
        rows = [canvas.cells[y][13:width - 2] for y in range(1, 5)]
        return ["".join(ch for ch, _ in row) for row in rows], rows, "".join(ch for ch, _ in canvas.cells[0])

    def test_in_fills_up_and_out_fills_down(self):
        # 8 Mb/s at the peak: a scale of 1,000,000 bytes a second to the dot, eight dots each way.
        text, rows, _ = self.graph([1e6, 8e6], [4e6, 0.0])
        self.assertEqual([line[-1] for line in text], [
            chr(0x2800 + 0xB8),               # in, upper row: the right column's last four dots
            chr(0x2800 + 0x40 + 0xB8),        # in, by the middle line: one dot at the bottom left, four right
            chr(0x2800 + 0x47),               # out, by the middle line: four dots down the left
            " "])                             # out, lower row: nothing
        self.assertEqual([row[-1][1] for row in rows[:3]], ["net_in", "net_in", "net_out"])
        self.assertEqual(set("".join(line[:-1] for line in text)), {" "})     # one cell: two samples

    def test_two_samples_a_cell_the_newest_at_the_right(self):
        # Odd: the oldest sample (8 dots) sits alone, in the right column of its cell. Then 1 dot and 4.
        text, _, _ = self.graph([8e6, 1e6, 4e6], [0.0, 0.0, 0.0])
        self.assertEqual(text[0][-2:], chr(0x2800 + 0xB8) + " ")
        self.assertEqual(text[1][-2:], chr(0x2800 + 0xB8) + chr(0x2800 + 0x40 + 0xB8))

    def test_too_little_for_a_dot_is_a_faint_dot_on_the_middle_line(self):
        text, rows, _ = self.graph([0.0, 100.0, 0.0], [0.0, 0.0, 3e6])
        self.assertEqual(text[1][-2:], chr(0x2800 + 0x80) + chr(0x2800 + 0x40 + 0x80))   # in: bottom dots
        self.assertEqual(text[2][-2:], chr(0x2800 + 0x08) + chr(0x2800 + 0xB8))           # out: top dots, and 4
        self.assertEqual([rows[1][-2][1], rows[1][-1][1], rows[2][-2][1], rows[2][-1][1]],
                         ["faint", "faint", "faint", "net_out"])        # a cell has one color: the busy one's
        self.assertEqual(text[0].strip(), "")                           # idle dots only by the middle line

    def test_the_peak_and_the_graph_cover_twice_the_time(self):
        # 41 cells: the classic graph shows the newest 41 samples, the fine one the newest 82.
        ins = [1e6] * 100
        ins[-60] = 50e6                                            # 400 Mb/s, 60 samples ago
        _, _, classic = self.graph(ins, [0.0] * 100, fine=False)
        _, _, fine = self.graph(ins, [0.0] * 100)
        self.assertIn("┐peak 8.00 Mb/s┌", classic)
        self.assertIn("┐peak 400 Mb/s┌", fine)

    def test_the_classic_drawing_is_as_it_was(self):
        text, rows, _ = self.graph([1e6, 8e6, 0.0], [4e6, 0.0, 0.0], fine=False)
        self.assertEqual([line[-3:] for line in text], [" █ ", "░█·", "█··", "   "])   # a sample a column
        self.assertEqual({row[-1][1] for row in rows[1:3]}, {"faint"})

    def test_whole_frames(self):
        for width, height in ((120, 30), (160, 45), (200, 60)):
            mon = monitor()
            mon.net_in.extend((i * 37) % 100 * 1e5 for i in range(300))
            mon.net_out.extend((i * 53) % 100 * 4e4 for i in range(300))
            fine = net_graph(plain(frame(width, height, fine=True, mon=mon)).split("\n"), width)
            classic = net_graph(plain(frame(width, height, fine=False, mon=mon)).split("\n"), width)
            self.assertFalse(SHADES & set("".join(fine)), f"{width}x{height}")
            self.assertTrue(set("".join(fine)) <= BRAILLE | {" "}, f"{width}x{height}")
            self.assertTrue(set("".join(classic)) <= SHADES | {" ", "·"}, f"{width}x{height}")
            self.assertEqual([len(line) for line in fine], [len(line) for line in classic])


class FineFrameTest(unittest.TestCase):
    """The fine drawing on the whole screen: only the graphs, the history and the meters change."""

    def test_only_the_graph_the_history_and_the_meters_change(self):
        # Cell by cell, the two drawings differ only where both have a drawing character, apart from
        # the time spans in the graph's title and the history's heading, which double.
        classic_glyphs, fine_glyphs = set("█▒░■· "), set(lxdtop.EIGHTHS_LEFT + lxdtop.EIGHTHS_UP) | BRAILLE | {"░", "·"}
        for width, height in ((80, 24), (160, 45), (200, 60)):
            classic = plain(frame(width, height, fine=False)).split("\n")
            fine = plain(frame(width, height, fine=True)).split("\n")
            self.assertEqual(len(classic), len(fine))
            for y, (a, b) in enumerate(zip(classic, fine)):
                spans = {x for line in (a, b) for m in re.finditer(r"last [0-9]+ ?(s|min)\)?", line)
                         for x in range(m.start(), m.end())}
                for x, (ca, cb) in enumerate(itertools.zip_longest(a, b, fillvalue=" ")):
                    if ca != cb and x not in spans:
                        where = f"{width}x{height} line {y} column {x}: {ca!r} -> {cb!r}"
                        self.assertIn(ca, classic_glyphs, where)
                        self.assertIn(cb, fine_glyphs, where)

    def test_no_shade_blocks_in_the_fine_graph_or_history(self):
        lines = plain(frame(160, 45, fine=True)).split("\n")
        top = next(i for i, line in enumerate(lines) if "┐cpu by" in line)
        legend = next(i for i, line in enumerate(lines) if "host itself" in line)
        graph = [line[8:160 - 54 - 2] for line in lines[top + 1:legend]]    # the plot, past the scale's labels
        heading = next(i for i, line in enumerate(lines) if "CPU HISTORY" in line)
        x, w = lxdtop.table_layout(160 - 4)["hist"]
        history = [line[2 + x:2 + x + w] for line in lines[heading + 1:heading + 6]]   # the running instances
        network = net_graph(lines, 160)
        for text in graph + history + network:
            self.assertFalse(SHADES & set(text), text)
        self.assertTrue(BRAILLE & set("".join(graph)))
        self.assertTrue(all(BRAILLE & set(text) for text in history), history)
        self.assertTrue(BRAILLE & set("".join(network)), network)

    def test_meter_colors_by_level_and_memory_keeps_its_cache_and_free_colors(self):
        canvas = lxdtop.TextCanvas(7, 52)
        mon = monitor()
        with drawing(True), glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.draw_cpu_panel(canvas, 0, 0, 7, 52, mon)
        total = {style for ch, style in canvas.cells[1][8:45] if ch == "█"}
        self.assertTrue(total and total <= {f"lv{i}" for i in range(10)}, total)
        bars = {style for line in canvas.cells[2:5] for ch, style in line if ch in lxdtop.EIGHTHS_UP[1:]}
        self.assertEqual(bars, {"lv3", "lv6", "lv9"})                # one shade a row, up the three rows
        canvas = lxdtop.TextCanvas(7, 52)
        with drawing(True), glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.draw_mem_panel(canvas, 0, 0, 7, 52, mon)
        meters = {line[2:6]: {style for ch, style in canvas.cells[i][8:39] if ch != "░"}
                  for i, line in enumerate(canvas.text().split("\n")) if line[2:6] in ("used", "zfs ", "free", "swap")}
        self.assertTrue(meters["used"] <= {f"lv{i}" for i in range(10)}, meters)
        self.assertTrue(meters["swap"] <= {f"lv{i}" for i in range(10)}, meters)
        self.assertEqual(meters["zfs "], {"arc"})
        self.assertEqual(meters["free"], {"free"})

    def test_the_detail_views_history_is_braille_too(self):
        text = plain(frame(160, 45, fine=True, view="detail"))
        line = next(line for line in text.split("\n") if "cpu history" in line)
        self.assertTrue(BRAILLE & set(line))
        self.assertFalse(SHADES & set(line))

    def test_fits_80x24_and_nothing_reaches_past_the_edge(self):
        for width, height in ((80, 24), (160, 45), (200, 60)):
            lines = plain(frame(width, height, fine=True)).split("\n")
            self.assertEqual(len(lines), height)
            self.assertTrue(all(len(line) <= width for line in lines))

    def test_256_color_codes_in_a_fine_dump(self):
        text = frame(160, 45, fine=True)
        self.assertIn("\x1b[0;38;5;41m", text)                       # lv0
        self.assertIn("\x1b[0;38;5;240m", text)                      # faint, as a 256-color terminal has it
        self.assertNotIn("\x1b[0;38;5;", frame(160, 45, fine=False))


class ClassicUnchangedTest(unittest.TestCase):
    """The classic drawing and --ascii, against frames that lxdtop 1.7.1 drew."""

    def test_the_same_frames_as_1_7_1(self):
        for name, ascii in (("classic", False), ("ascii", True)):
            for width, height in ((160, 45), (80, 24)):
                expected = fixture(f"frame-{name}-{width}x{height}.txt").rstrip("\n")
                got = frame(width, height, ascii=ascii, fine=False)
                if got != expected:                                  # say where, in plain text
                    for y, (a, b) in enumerate(zip(plain(expected).split("\n"), plain(got).split("\n"))):
                        self.assertEqual(b, a, f"{name} {width}x{height} line {y}")
                self.assertEqual(got, expected, f"{name} {width}x{height}: the colors changed")

    def test_ascii_ignores_the_fine_drawing(self):
        # pick_drawing() never turns it on with ASCII glyphs; even if it were, nothing would be braille.
        self.assertTrue(plain(frame(160, 45, ascii=True, fine=False)).isascii())

    def test_sgr_is_as_it_was_for_every_style(self):
        for style in list(lxdtop.STYLES) + [None]:
            color, bold = lxdtop.STYLES.get(style, ("white", False))
            old = (f"\x1b[0;{'7;' if style in lxdtop.REVERSE else ''}{'1;' if bold else ''}"
                   f"{30 + lxdtop.COLORS.index(color)}m")
            with drawing(False):
                self.assertEqual(lxdtop.sgr(style), old)


class CursesColorsTest(unittest.TestCase):
    """curses_attrs() on a terminal with 8 colors (the Linux console) and with 256."""

    def attrs(self, colors):
        pairs = []
        fake = mock.MagicMock(COLORS=colors, A_BOLD=1 << 21, A_NORMAL=0, A_REVERSE=1 << 18, COLOR_BLACK=0,
                              error=lxdtop.curses.error)
        fake.has_colors.return_value = True
        fake.init_pair.side_effect = lambda n, fg, bg: pairs.append((n, fg, bg))
        fake.color_pair.side_effect = lambda n: n << 8
        with mock.patch.object(lxdtop, "curses", fake):
            return lxdtop.curses_attrs(), pairs

    def test_the_console_gets_no_new_color_pairs(self):
        attrs, pairs = self.attrs(8)
        self.assertEqual(len(pairs), len({lxdtop.STYLES[name][0] for name in lxdtop.STYLES}))   # one per color
        for i in range(10):                                          # the shades fall back to heat()'s three
            self.assertEqual(attrs[f"lv{i}"], attrs["lo" if i < 5 else "mid" if i < 8 else "hi"])

    def test_a_256_color_terminal_gets_the_shades(self):
        attrs, pairs = self.attrs(256)
        numbers = {fg for _, fg, _ in pairs}
        self.assertTrue({41, 77, 113, 149, 185, 221, 215, 209, 203, 196} <= numbers)
        self.assertNotEqual(attrs["lv0"], attrs["lv9"])


class DrawingOptionTest(unittest.TestCase):
    def run_main(self, *args, term="xterm-256color"):
        out = io.StringIO()
        saved = lxdtop.G, lxdtop.FINE, lxdtop.DRAWING
        self.addCleanup(setattr, lxdtop, "G", saved[0])
        self.addCleanup(setattr, lxdtop, "FINE", saved[1])
        self.addCleanup(setattr, lxdtop, "DRAWING", saved[2])
        host = support.FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        host.add_container("web", 1_000_000)
        clock = lambda fmt, *_: "Thu Nov 5  09:30:00 UTC" if "%a" in fmt else "09:30 UTC"
        with host.installed(), mock.patch.object(sys, "argv", ["lxdtop", "--no-api", *args]), \
                mock.patch.dict(os.environ, TERM=term), contextlib.redirect_stdout(out), \
                mock.patch.object(lxdtop.locale, "getpreferredencoding", return_value="UTF-8"), \
                mock.patch.object(lxdtop.time, "sleep", lambda secs: setattr(host, "clock", host.clock + secs)), \
                mock.patch.object(lxdtop.time, "strftime", clock), \
                mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
            lxdtop.main()
        return out.getvalue()

    def test_dump_draws_either_way(self):
        classic = self.run_main("--dump")
        self.assertFalse(lxdtop.FINE)
        fine = self.run_main("--dump", "--drawing", "fine")
        self.assertTrue(lxdtop.FINE)
        self.assertFalse(BRAILLE & set(classic))
        self.assertTrue(BRAILLE & set(fine))                         # the history column, at least
        self.assertIn("░", fine.split("\n")[2])                      # the cpu meter's track
        self.assertEqual(self.run_main("--dump", "--drawing", "classic"), classic)

    def test_dump_is_classic_whatever_the_terminal(self):
        self.assertEqual(self.run_main("--dump", term="xterm-256color"), self.run_main("--dump", term="linux"))

    def test_a_drawing_that_does_not_exist(self):
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()) as err:
            self.run_main("--dump", "--drawing", "braille")
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())

    def test_the_help_screen_says_which_drawing(self):
        for fine, label in ((True, "fine (256 colors)"), (False, "classic (TERM=linux)")):
            with drawing(fine):
                lxdtop.DRAWING = label
                text = plain(frame(80, 24, fine=fine, view="help"))
            self.assertIn(f"drawing {label}", text)
        with drawing(False):
            lxdtop.DRAWING = "ascii (not a UTF-8 locale)"
            self.assertIn("[drawing ascii (not a UTF-8 locale)]", plain(frame(70, 12, ascii=True, view="help")))

    def test_run_picks_from_the_terminals_colors(self):
        screen = mock.MagicMock()
        screen.getmaxyx.return_value = (45, 160)
        screen.getch.return_value = ord("q")
        mon = mock.MagicMock()
        for term, colors, fine in (("xterm-256color", 256, True), ("linux", 8, False), ("xterm", 8, False)):
            with drawing(False), mock.patch.dict(os.environ, TERM=term), \
                    mock.patch.object(lxdtop.curses, "COLORS", colors, create=True), \
                    mock.patch.object(lxdtop, "curses_attrs", return_value={}), \
                    mock.patch.object(lxdtop, "draw"), mock.patch.object(lxdtop.curses, "curs_set"), \
                    mock.patch.object(lxdtop.curses, "set_escdelay"):
                lxdtop.run(screen, mon, lxdtop.new_ui(2))
                self.assertEqual(lxdtop.FINE, fine, term)


if __name__ == "__main__":
    unittest.main()
