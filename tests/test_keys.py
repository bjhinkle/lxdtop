"""The keys: the help screen (?), the name filter (/), the highlight (Up, Down) and the detail view (Enter).

handle_key() is tested on its own, with a Monitor holding chosen rows; the views
are drawn on a TextCanvas, the way --dump draws. KEYS is the one table both the
help screen and handle_key() use.
"""

import contextlib
import curses
import io
import itertools
import re
import sys
import time
import types
import unittest
from unittest import mock

from tests.support import GiB, MiB, glyphs, lxdtop, monitor, row

Q, ESC, ENTER, UP, DOWN = ord("q"), 27, 10, curses.KEY_UP, curses.KEY_DOWN


def rows(*names, **fields):
    return [row(name, cpu=float(50 - i), **fields) for i, name in enumerate(names)]


def keys(mon, ui, *pressed):
    """Press keys in turn; return what handle_key() said for each."""
    return [lxdtop.handle_key(ord(k) if isinstance(k, str) else k, mon, ui) for k in pressed]


def draw(mon, ui, width=160, height=45, ascii=False):
    canvas = lxdtop.TextCanvas(height, width)
    with glyphs(lxdtop.GLYPHS_ASCII if ascii else lxdtop.GLYPHS_UNICODE), \
            mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
        lxdtop.draw(canvas, mon, ui)
    return canvas


def table_rows(canvas):
    """The names in the table's rows, top to bottom (below its header line)."""
    found, lines = [], canvas.text().splitlines()
    header = next(i for i, line in enumerate(lines) if " NAME " in line)
    for line in lines[header + 1:]:
        parts = line.split()
        if len(parts) > 2 and parts[1] in ("●", "■", "*", "#"):
            found.append(parts[2])
    return found


class KeyTableTest(unittest.TestCase):
    def test_one_table_for_the_keys_and_their_help(self):
        codes = [code for _, keycodes, _, _ in lxdtop.KEYS for code in keycodes]
        self.assertEqual(len(codes), len(set(codes)))                   # no key does two things
        self.assertEqual(lxdtop.KEY_ACTIONS, {code: action for _, keycodes, _, action in lxdtop.KEYS
                                              for code in keycodes})
        self.assertNotIn("this help", draw(monitor(rows("web")), lxdtop.new_ui(2)).text())
        ui = lxdtop.new_ui(2)
        keys(monitor(rows("web")), ui, "?")
        text = draw(monitor(rows("web")), ui).text()
        for label, _, description, _ in lxdtop.KEYS:                    # every key, in full
            self.assertRegex(text, rf"{re.escape(label)}\s+{re.escape(description)}")

    def test_every_action_in_the_table_does_something(self):
        mon = monitor(rows("web", "db"))
        for _, keycodes, _, action in lxdtop.KEYS:
            with self.subTest(action):
                ui = lxdtop.new_ui(2)
                ui.selected = "web"                                     # so Enter has something to open
                self.assertIn(lxdtop.handle_key(keycodes[0], mon, ui), (True, lxdtop.QUIT))

    def test_dash_dash_help_lists_the_same_keys(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["lxdtop", "--help"]), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                lxdtop.main()
        self.assertIn(lxdtop.keys_help(), out.getvalue())
        self.assertEqual(len(lxdtop.keys_help().splitlines()), len(lxdtop.KEYS))


class HandleKeyTest(unittest.TestCase):
    def setUp(self):
        self.mon = monitor(rows("web", "db", "dns1", "dns2"))
        self.ui = lxdtop.new_ui(2)

    def test_q_quits_from_the_main_screen_and_goes_back_from_the_others(self):
        self.assertEqual(keys(self.mon, self.ui, "q"), [lxdtop.QUIT])
        keys(self.mon, self.ui, "?")
        self.assertEqual((keys(self.mon, self.ui, "q"), self.ui.view), ([True], "main"))
        keys(self.mon, self.ui, DOWN, ENTER)
        self.assertEqual(self.ui.view, "detail")
        self.assertEqual((keys(self.mon, self.ui, "Q"), self.ui.view), ([True], "main"))

    def test_any_key_closes_the_help_and_it_returns_where_it_was_opened(self):
        keys(self.mon, self.ui, "?", "x")
        self.assertEqual(self.ui.view, "main")
        keys(self.mon, self.ui, DOWN, ENTER, "?")
        self.assertEqual((self.ui.view, self.ui.back), ("help", "detail"))
        keys(self.mon, self.ui, " ")
        self.assertEqual(self.ui.view, "detail")

    def test_nothing_to_do_means_no_redraw_but_a_resize_redraws(self):
        self.assertEqual(keys(self.mon, self.ui, -1, "x", ESC, ENTER), [False] * 4)
        self.assertEqual(keys(self.mon, self.ui, curses.KEY_RESIZE), [True])

    def test_typing_a_filter(self):
        keys(self.mon, self.ui, "/", "D", "n", "x")
        self.assertEqual((self.ui.view, self.ui.filter), ("typing", "Dnx"))
        keys(self.mon, self.ui, curses.KEY_BACKSPACE, 127)
        self.assertEqual(self.ui.filter, "D")
        keys(self.mon, self.ui, "q", "s")                           # letters are text while typing
        self.assertEqual((self.ui.filter, self.ui.sort), ("Dqs", "cpu"))
        keys(self.mon, self.ui, 127, 127, "n", "s", ENTER)          # Enter keeps it
        self.assertEqual((self.ui.view, self.ui.filter), ("main", "Dns"))
        self.assertEqual([r["name"] for r in lxdtop.listed_rows(self.mon, self.ui)], ["dns1", "dns2"])
        keys(self.mon, self.ui, ESC)                                # Esc clears it from the main screen
        self.assertEqual(self.ui.filter, "")

    def test_esc_while_typing_clears_the_filter(self):
        keys(self.mon, self.ui, "/", "w", ESC)
        self.assertEqual((self.ui.view, self.ui.filter), ("main", ""))

    def test_esc_clears_the_filter_then_the_highlight(self):
        keys(self.mon, self.ui, "/", "d", ENTER, DOWN)
        self.assertEqual((self.ui.filter, self.ui.selected), ("d", "db"))
        self.assertEqual(keys(self.mon, self.ui, ESC, ESC, ESC), [True, True, False])
        self.assertEqual((self.ui.filter, self.ui.selected), ("", None))

    def test_up_and_down_move_the_highlight_and_stop_at_the_ends(self):
        keys(self.mon, self.ui, DOWN)
        self.assertEqual(self.ui.selected, "web")                    # starts at the top
        keys(self.mon, self.ui, DOWN, DOWN, DOWN, DOWN)
        self.assertEqual(self.ui.selected, "dns2")                   # and stops at the bottom
        keys(self.mon, self.ui, UP)
        self.assertEqual(self.ui.selected, "dns1")
        fresh = lxdtop.new_ui(2)
        keys(self.mon, fresh, UP)
        self.assertEqual(fresh.selected, "dns2")                     # Up first starts at the bottom

    def test_the_highlight_stays_on_its_instance_when_the_sort_changes(self):
        keys(self.mon, self.ui, DOWN, DOWN)                          # db, second by CPU
        keys(self.mon, self.ui, "s", "s", "s", "s")                  # sorted by name now: db is first
        self.assertEqual((self.ui.sort, self.ui.selected), ("name", "db"))
        keys(self.mon, self.ui, DOWN)
        self.assertEqual(self.ui.selected, "dns1")                   # the next one by name

    def test_enter_opens_the_highlighted_instance_only(self):
        self.assertEqual(keys(self.mon, self.ui, ENTER), [False])
        keys(self.mon, self.ui, DOWN, DOWN, 13)                      # Enter is \r on some terminals
        self.assertEqual((self.ui.view, self.ui.detail), ("detail", "db"))
        keys(self.mon, self.ui, ESC)
        keys(self.mon, self.ui, "/", "w", "e", "b", ENTER)           # db is filtered out now
        self.assertEqual(keys(self.mon, self.ui, curses.KEY_ENTER), [False])

    def test_the_detail_view_ignores_the_tables_keys(self):
        keys(self.mon, self.ui, DOWN, ENTER)
        self.assertEqual(keys(self.mon, self.ui, "s", "/", DOWN), [False, False, False])
        self.assertEqual(keys(self.mon, self.ui, "+"), [True])       # the refresh interval still changes
        self.assertEqual((self.ui.view, self.ui.sort, self.ui.interval), ("detail", "cpu", 3))

    def test_no_key_asks_lxd_anything(self):
        mon = monitor(rows("web", "db"))
        mon.api = mock.Mock(spec=[])                                 # any use of it would raise
        ui = lxdtop.new_ui(2)
        for key in [code for _, codes, _, _ in lxdtop.KEYS for code in codes] + [ord("/"), ord("w"), ENTER, DOWN,
                                                                                  ENTER, ord("?"), ord("x"), ESC]:
            lxdtop.handle_key(key, mon, ui)
            draw(mon, ui)


class FilterViewTest(unittest.TestCase):
    def setUp(self):
        self.mon = monitor(rows("web", "db", "dns1", "dns2") + [row("old", state="Stopped", live=False)])
        self.ui = lxdtop.new_ui(2)

    def test_the_title_says_so_and_counts(self):
        self.ui.filter = "DNS"
        canvas = draw(self.mon, self.ui)
        self.assertIn("┐filter DNS  2 of 5┌", canvas.text())
        self.assertEqual(table_rows(canvas), ["dns1", "dns2"])

    def test_the_panels_and_the_warnings_still_cover_the_whole_host(self):
        mon = monitor(rows("web", "db"), kills={"web": (1, 1, time.monotonic() - 60)})
        self.ui.filter = "db"
        text = draw(mon, self.ui).text()
        self.assertIn("web: memory kill 1m ago", text)               # web is filtered out, its warning isn't
        self.assertIn("■ web", text)                                 # and it is still in the graph
        self.assertIn("2 running", text.splitlines()[0])

    def test_no_matches(self):
        self.ui.filter = "mysql"
        text = draw(self.mon, self.ui).text()
        self.assertIn("┐filter mysql  0 of 5┌", text)
        self.assertIn('no instance name contains "mysql"   (Esc clears the filter)', text)

    def test_the_filter_line_while_typing(self):
        keys(self.mon, self.ui, "/", "w", "e")
        bottom = draw(self.mon, self.ui).text().splitlines()[-1]
        self.assertTrue(bottom.startswith("╰─┘/ we_└─┘Enter keep└─┘Esc clear└"), bottom)

    def test_ascii(self):
        keys(self.mon, self.ui, "/", "d")
        for width, height in ((160, 45), (80, 24)):
            self.assertTrue(draw(self.mon, self.ui, width, height, ascii=True).text().isascii())


class HighlightTest(unittest.TestCase):
    def test_no_highlight_no_reverse_video(self):
        canvas = draw(monitor(rows("web", "db")), lxdtop.new_ui(2))
        self.assertNotIn("sel", {style for line in canvas.cells for _, style in line})

    def test_the_highlighted_row_is_in_reverse_video_across_the_table(self):
        mon, ui = monitor(rows("web", "db", "dns1")), lxdtop.new_ui(2)
        keys(mon, ui, DOWN, DOWN)
        canvas = draw(mon, ui)
        lines = canvas.text().splitlines()
        y = next(i for i, line in enumerate(lines) if " db " in line and "●" in line)
        self.assertEqual({style for _, style in canvas.cells[y][1:159]}, {"sel"})
        self.assertNotIn("sel", {style for _, style in canvas.cells[y + 1][1:159]})
        self.assertIn("7;", canvas.text(color=True).splitlines()[y])           # ANSI reverse video

    def test_the_table_scrolls_to_keep_it_in_view(self):
        mon = monitor(rows(*[f"ct{i:02}" for i in range(40)]))
        ui = lxdtop.new_ui(2)
        keys(mon, ui, UP)                                            # the last of 40
        canvas = draw(mon, ui, 160, 30)
        shown = table_rows(canvas)
        self.assertEqual(shown[-1], "ct39")
        self.assertEqual(len(shown), 40 - ui.scroll)
        lines = canvas.text().splitlines()
        first = next(i for i, line in enumerate(lines) if f" {shown[0]} " in line)
        self.assertEqual(lines[first][-1], "▲")                       # rows above, out of view
        keys(mon, ui, *[UP] * 39)                                    # back to the first
        shown = table_rows(draw(mon, ui, 160, 30))
        self.assertEqual((shown[0], ui.scroll), ("ct00", 0))

    def test_stopped_instances_get_a_row_each_while_one_is_highlighted(self):
        mon = monitor(rows(*[f"ct{i:02}" for i in range(30)]) + [row(f"old{i}", state="Stopped", live=False)
                                                                   for i in range(4)])
        ui = lxdtop.new_ui(2)
        self.assertIn("4 stopped: old0, old1, old2, old3", draw(mon, ui, 160, 45).text())   # folded, as today
        keys(mon, ui, UP)
        self.assertEqual(table_rows(draw(mon, ui, 160, 45))[-4:], ["old0", "old1", "old2", "old3"])


class DetailViewTest(unittest.TestCase):
    def open(self, mon, name, width=160, height=45):
        ui = lxdtop.new_ui(2)
        ui.selected = name
        keys(mon, ui, ENTER)
        return ui, draw(mon, ui, width, height).text()

    def test_a_short_window_cuts_the_page_to_fit_inside_its_box(self):
        # The page takes 15 rows inside its box, so a window under 18 rows can't show it all.
        mon = monitor(rows("web"))
        for width, height in itertools.product((70, 80), range(12, 21)):
            _, text = self.open(mon, "web", width, height)
            lines = text.splitlines()
            where = f"{width}x{height}"
            self.assertEqual(len(lines), height, where)
            self.assertTrue(lines[-1].startswith("╰─┘Esc back└─┘q back└─┘? keys└"), f"{where}: {lines[-1]}")
            last = lines[-2].strip("│ ")                             # the last row inside the box
            if height < 18:
                self.assertEqual(last, "… the rest needs a window 18 rows tall", where)
            else:
                self.assertTrue(last.startswith("events") or last == "", f"{where}: {last}")
                self.assertIn("events", text)
        with glyphs(lxdtop.GLYPHS_ASCII):
            ui = lxdtop.new_ui(2)
            ui.view, ui.detail = "detail", "web"
            canvas = lxdtop.TextCanvas(12, 70)
            with mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
                lxdtop.draw(canvas, mon, ui)
        lines = canvas.text().splitlines()
        self.assertTrue(lines[-1].startswith("+-[Esc back]-[q back]-[? keys]"), lines[-1])
        self.assertEqual(lines[-2].strip("| "), "~ the rest needs a window 18 rows tall")

    def test_a_container_with_everything_lxdtop_has(self):
        web = row("web", cpu_max=2.0, throttled=45.0, mem_max=4096 * MiB, tasks_max=500, waiting=("mem", 34.0),
                  snapshot=time.time() - 3 * 3600)
        events = types.SimpleNamespace(snapshot=lambda: ([
            dict(t=time.time() - 60, action="instance-exec", name="web", detail="", user="backup", count=3),
            dict(t=time.time() - 90, action="instance-started", name="db", detail="", user="bj", count=1)], "live"))
        mon = monitor([web, row("db")], events=events, kills={"web": (4, 1, time.monotonic() - 720)})
        mon.past["web"] = [dict(disk_src="zfs", disk=(1, 1))]
        _, text = self.open(mon, "web")
        for expected in (
                "┐web┌",
                "state       running   container on lxdhost",
                "address     10.10.10.21",
                "up          2h 0m",
                "cpu         33.3% of one core   limit 2 cores   throttled 45% of its periods",
                "memory      384M   limit 4.0G (9%)",
                "processes   12   limit 500 (2%)",
                "busiest     nginx 25.0%",
                "disk        read 2.0M/s   write 512K/s   from zfs",
                "network     in 10.0 Mb/s   out 1.00 Mb/s",
                "waiting     mem 34% of the last 10 s",
                "mem kills   1 since lxdtop started, the last 12m ago",
                "snapshot    3h ago",
                "events",
                "exec      backup  ×3",
                "Esc back"):
            self.assertIn(expected, text)
        self.assertNotIn("bj", text)                                  # db's event isn't web's

    def test_a_vm_and_a_stopped_instance(self):
        stopped = dict.fromkeys(("mem", "tasks", "top", "up", "cpu", "rd", "wr", "net_in", "net_out"))
        mon = monitor([row("win", vm=True, top=None, tasks=None, mem=3 * GiB, cpu_max=2, mem_max=4 * GiB),
                       row("old", state="Stopped", live=False, **stopped)])
        _, text = self.open(mon, "win")
        self.assertIn("virtual machine on lxdhost", text)
        self.assertIn("busiest     inside the guest", text)
        self.assertRegex(text, r"cpu {9}33\.3% of one core   limit 2 cores +│")       # LXD's, and no throttling
        self.assertIn("memory      3.0G   limit 4.0G (75%)", text)
        self.assertIn("processes   inside the guest", text)
        _, text = self.open(mon, "old")
        self.assertIn("state       stopped   container on lxdhost", text)
        self.assertIn("cpu         -", text)

    def test_if_the_instance_goes_away_the_view_says_so(self):
        mon = monitor(rows("web", "db"))
        ui, _ = self.open(mon, "db")
        mon.rows = [r for r in mon.rows if r["name"] != "db"]        # deleted, say
        text = draw(mon, ui).text()
        self.assertIn("state       gone: no longer on this host, last seen running", text)
        self.assertIn("┐db┌", text)

    def test_80x24_and_ascii(self):
        mon = monitor(rows("web"))
        _, text = self.open(mon, "web", 80, 24)
        self.assertEqual(len(text.splitlines()), 24)
        self.assertTrue(all(len(line) <= 80 for line in text.splitlines()))
        ui = lxdtop.new_ui(2)
        ui.selected = "web"
        keys(mon, ui, ENTER)
        self.assertTrue(draw(mon, ui, 80, 24, ascii=True).text().isascii())


class HelpViewTest(unittest.TestCase):
    def test_fits_80x24_and_ascii(self):
        ui = lxdtop.new_ui(2)
        ui.view = "help"
        mon = monitor(rows("web"))
        for ascii in (False, True):
            text = draw(mon, ui, 80, 24, ascii=ascii).text()
            for _, _, description, _ in lxdtop.KEYS:
                self.assertIn(description, text)                     # nothing cut short
            self.assertEqual(text.isascii(), ascii)


class HelpTabTest(unittest.TestCase):
    """The "? keys" tab in the table's bottom edge: after the other keys, where it fits."""

    KEYS_80 = "╰─┘q quit└─┘s sort└─┘- + every 2s└"

    def bottom(self, width=80, height=24, ascii=False, status="ok", zfs=True, ui=None):
        mon = monitor(rows("web", "db"), api_status=status, zfs={"web": "kstat"} if zfs else {})
        return draw(mon, ui or lxdtop.new_ui(2), width, height, ascii).text().splitlines()[-1]

    def test_after_the_other_keys_at_80x24(self):
        self.assertEqual(self.bottom(), self.KEYS_80 + "─┘? keys└" + "─" * 12 + "┘lxd api ok└─┘disk zfs└─╯")
        self.assertEqual(self.bottom(ascii=True), "+-[q quit]-[s sort]-[- + every 2s]-[? keys]"
                                                  "------------[lxd api ok]-[disk zfs]-+")
        self.assertIn("┘- + every 2s└─┘? keys└", self.bottom(160, 45))

    def test_it_gives_way_to_the_lxd_api_status(self):
        # At 80 columns a longer status leaves no room for it: the status stays where it always was.
        self.assertEqual(self.bottom(status="no access", zfs=False),
                         self.KEYS_80 + "─" * 10 + "┘lxd api no access└─┘disk io.stat└─╯")
        self.assertIn("┘? keys└", self.bottom(120, 30, status="no access", zfs=False))

    def test_not_while_typing_a_filter(self):
        ui = lxdtop.new_ui(2)
        keys(monitor(rows("web")), ui, "/", "w")
        self.assertNotIn("keys", self.bottom(ui=ui))                 # a ? typed now goes into the filter

    def test_nothing_else_on_the_screen_moves(self):
        real_box = lxdtop.box

        def box_without_the_tab(*args, footer_if_room=(), **kwargs):
            return real_box(*args, **kwargs)

        clock = mock.patch.object(lxdtop.time, "strftime", return_value="Thu Nov 5  09:30:00 UTC")
        for (width, height), ascii in itertools.product(((80, 24), (160, 45), (200, 60)), (False, True)):
            mon, ui = monitor(rows("web", "db", "cache")), lxdtop.new_ui(2)
            with clock:
                with_tab = draw(mon, ui, width, height, ascii)
                with mock.patch.object(lxdtop, "box", box_without_the_tab):
                    without = draw(mon, ui, width, height, ascii)
            self.assertEqual(with_tab.text(color=True).splitlines()[:-1], without.text(color=True).splitlines()[:-1])
            last, before = with_tab.text().splitlines()[-1], without.text().splitlines()[-1]
            changed = [x for x, (a, b) in enumerate(zip(last, before)) if a != b]
            self.assertEqual(len(last), len(before))
            self.assertEqual(changed, list(range(35, 43)))           # just the tab's 8 cells, where the edge was
            self.assertEqual(before[35:43], ("-" if ascii else "─") * 8)


class ApiStatusFooterTest(unittest.TestCase):
    """LXD's API status in a bottom edge: an error shows even at 80 columns, where the disk label gives
    way to it, and on a narrower screen its name is shortened, then left out."""

    KEYS = "╰─┘q quit└─┘s sort└─┘- + every 2s└"

    def bottom(self, status, width=80, height=24, zfs=True, ui=None):
        mon = monitor(rows("web", "db"), api_status=status, zfs={"web": "kstat"} if zfs else {})
        return draw(mon, ui or lxdtop.new_ui(2), width, height).text().splitlines()[-1]

    def test_an_error_shows_at_80_columns(self):
        for zfs in (True, False):                                    # whichever the disk label was
            self.assertEqual(self.bottom("error (FileNotFoundError)", zfs=zfs),        # no socket
                             self.KEYS + "─" * 9 + "┘lxd api error (FileNotFoundError)└─╯")
            self.assertEqual(self.bottom("error (ConnectionRefusedError)", zfs=zfs),   # LXD not answering
                             self.KEYS + "─" * 4 + "┘lxd api error (ConnectionRefusedError)└─╯")

    def test_a_status_that_fits_beside_the_disk_label_keeps_it(self):
        self.assertEqual(self.bottom("off"), self.KEYS + "─┘? keys└" + "─" * 11 + "┘lxd api off└─┘disk zfs└─╯")
        self.assertTrue(self.bottom("error (OSError)", zfs=False)
                        .endswith("┘lxd api error (OSError)└─┘disk io.stat└─╯"))
        self.assertTrue(self.bottom("ok", 200, 60).endswith("┘lxd api ok└─┘disk zfs└─╯"))

    def test_on_a_narrower_screen_the_name_is_shortened_then_left_out(self):
        self.assertEqual(self.bottom("error (FileNotFoundError)", 70, 12),            # as the events panel
                         self.KEYS + "─" * 4 + "┘lxd api error (FileNotFound)└─╯")    # shortens it
        self.assertEqual(self.bottom("error (ConnectionRefusedError)", 70, 12),
                         self.KEYS + "─┘? keys└" + "─" * 10 + "┘lxd api error└─╯")

    def test_while_typing_a_long_filter_the_error_still_shows(self):
        ui = lxdtop.new_ui(2)
        keys(monitor(rows("web")), ui, "/", *"web-frontend-01")
        line = self.bottom("error (ConnectionRefusedError)", ui=ui)
        self.assertTrue(line.endswith("┘lxd api error└─╯"), line)
        self.assertTrue(line.startswith("╰─┘/ web-frontend-01_└"), line)

    def test_the_detail_view_already_has_room_at_80_columns(self):
        ui = lxdtop.new_ui(2)
        ui.view, ui.detail = "detail", "web"
        self.assertTrue(self.bottom("error (ConnectionRefusedError)", ui=ui)
                        .endswith("┘lxd api error (ConnectionRefusedError)└─╯"))


class RunLoopKeysTest(unittest.TestCase):
    def test_esc_acts_at_once(self):
        screen = types.SimpleNamespace(getmaxyx=lambda: (45, 160), timeout=lambda ms: None, erase=lambda: None,
                                       refresh=lambda: None, addstr=lambda *a: None, insstr=lambda *a: None,
                                       getch=lambda: ord("q"))
        mon = types.SimpleNamespace(sample=lambda: None)
        with mock.patch.object(lxdtop.curses, "set_escdelay") as escdelay, \
                mock.patch.object(lxdtop, "curses_attrs", return_value={}), mock.patch.object(lxdtop, "draw"):
            lxdtop.run(screen, mon, lxdtop.new_ui(2))
        escdelay.assert_called_once_with(25)                          # curses waits 1000 ms by default


if __name__ == "__main__":
    unittest.main()
