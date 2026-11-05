"""Drawing: the canvas, panel boxes, and how the layout drops panels and columns as the window shrinks."""

import curses
import re
import time
import types
import unittest

from tests.support import HOST_READING, glyphs, lxdtop, monitor, render, row

ALL_HEADERS = ("NAME", "UP", "CPU", "CPU HISTORY", "BUSIEST PROCESS", "MEM", "DISK R", "DISK W",
               "IN Mb/s", "OUT Mb/s", "TASKS", "IPV4")


def rows(n):
    return [row(f"c{i:02}", cpu=float(n - i)) for i in range(n)]


class CanvasTest(unittest.TestCase):
    def test_put_returns_the_column_after_the_text(self):
        cv = lxdtop.TextCanvas(1, 20)
        self.assertEqual(cv.put(0, 3, "hello"), 8)
        self.assertEqual(cv.put(0, 18, "clipped"), 25)       # even when clipped

    def test_put_clips_at_the_edges(self):
        cv = lxdtop.TextCanvas(2, 5)
        cv.put(0, 3, "hello")
        cv.put(1, -2, "hello")
        cv.put(2, 0, "off the bottom")
        cv.put(-1, 0, "off the top")
        self.assertEqual(cv.text().split("\n"), ["   he", "llo"])

    def test_put_stops_at_right(self):
        cv = lxdtop.TextCanvas(1, 10)
        cv.put(0, 0, "hello", right=3)
        cv.put(0, 5, "world", right=5)                        # nothing fits before column 5
        self.assertEqual(cv.text(), "hel")

    def test_put_cells_joins_runs_of_one_style(self):
        calls = []
        cv = types.SimpleNamespace(put=lambda *a: calls.append(a))
        lxdtop.put_cells(cv, 0, 4, [("a", "lo"), ("b", "lo"), ("c", "hi"), ("d", "lo")], right=9)
        self.assertEqual(calls, [(0, 4, "ab", "lo", 9), (0, 6, "c", "hi", 9), (0, 7, "d", "lo", 9)])

    def test_text_with_color(self):
        cv = lxdtop.TextCanvas(1, 5)
        cv.put(0, 0, "ab", "lo")                              # ("green", bold)
        self.assertEqual(cv.text(color=True), "\x1b[0;1;32mab\x1b[0;37m   \x1b[0m")
        self.assertEqual(cv.text(), "ab")

    def test_curses_canvas_writes_the_bottom_right_cell_with_insstr(self):
        screen = FakeScreen(3, 10)
        cv = lxdtop.CursesCanvas(screen, {"text": 7})
        cv.put(0, 0, "hi")
        cv.put(2, 5, "hello")                                 # ends in the bottom-right cell
        self.assertEqual(screen.calls, [("addstr", 0, 0, "hi", 7), ("addstr", 2, 5, "hell", 7),
                                        ("insstr", 2, 9, "o", 7)])

    def test_curses_errors_are_ignored(self):
        screen = FakeScreen(3, 10, fail=True)
        lxdtop.CursesCanvas(screen, {}).put(0, 0, "x")        # no exception


class FakeScreen:
    def __init__(self, h, w, fail=False):
        self.size, self.fail, self.calls = (h, w), fail, []

    def getmaxyx(self):
        return self.size

    def addstr(self, y, x, text, attr=0):
        if self.fail:
            raise curses.error("addwstr() returned ERR")
        self.calls.append(("addstr", y, x, text, attr))

    def insstr(self, y, x, text, attr=0):
        self.calls.append(("insstr", y, x, text, attr))


class BoxTest(unittest.TestCase):
    def test_box_with_labels_in_tabs(self):
        cv = lxdtop.TextCanvas(5, 30)
        with glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.box(cv, 0, 0, 5, 30, "b_cpu", title=[[("cpu", "title")]], right=[[("54°C", "lo")]],
                       footer=[[("q", "key"), (" quit", "text")]])
        lines = cv.text().split("\n")
        self.assertEqual(lines[0], "╭─┐cpu┌" + "─" * 15 + "┐54°C┌─╮")
        self.assertEqual(lines[1], "│" + " " * 28 + "│")
        self.assertEqual(lines[4], "╰─┘q quit└" + "─" * 19 + "╯")

    def test_right_label_is_dropped_when_there_is_no_room(self):
        cv = lxdtop.TextCanvas(3, 12)
        with glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.box(cv, 0, 0, 3, 12, "b_mem", title=[[("memory", "title")]], right=[[("64G total", "text")]])
        self.assertEqual(cv.text().split("\n")[0], "╭─┐memory┌─╮")

    def test_a_footer_label_if_room_never_pushes_a_right_hand_one_out(self):
        def bottom(width):
            cv = lxdtop.TextCanvas(3, width)
            with glyphs(lxdtop.GLYPHS_UNICODE):
                lxdtop.box(cv, 0, 0, 3, width, "b_table", footer=[[("q", "key")]], footer_right=[[("ok", "lo")]],
                           footer_if_room=[[("? keys", "text")]])
            return cv.text().split("\n")[2]
        self.assertEqual(bottom(24), "╰─┘q└─┘? keys└────┘ok└─╯")
        self.assertEqual(bottom(22), "╰─┘q└─┘? keys└──┘ok└─╯")      # as far from "ok" as "ok" may be from "q"
        self.assertEqual(bottom(21), "╰─┘q└──────────┘ok└─╯")       # no room: left out, and "ok" stays put

    def test_tabs_width_says_when_box_has_room_for_every_right_hand_label(self):
        left = [[("q", "key"), (" quit", "text")], [("s", "key"), (" sort", "text")]]
        for right in ([[("lxd api ok", "lo")]], [[("lxd api ", "dim"), ("error (ConnectionRefusedError)", "mid")],
                                                   [("disk ", "dim"), ("zfs", "dim")]]):
            for width in range(20, 90):
                cv = lxdtop.TextCanvas(3, width)
                with glyphs(lxdtop.GLYPHS_UNICODE):
                    lxdtop.box(cv, 0, 0, 3, width, "b_table", footer=left, footer_right=right)
                drawn = all("".join(text for text, _ in label) in cv.text() for label in right)
                fits = lxdtop.tabs_width(left) + lxdtop.tabs_width(right) <= width - 4
                self.assertEqual(drawn, fits, f"width {width}")

    def test_ascii_box(self):
        cv = lxdtop.TextCanvas(3, 12)
        with glyphs(lxdtop.GLYPHS_ASCII):
            lxdtop.box(cv, 0, 0, 3, 12, "b_mem", title=[[("mem", "title")]])
        self.assertEqual(cv.text().split("\n"), ["+-[mem]----+", "|          |", "+----------+"])


class TableLayoutTest(unittest.TestCase):
    """table_layout(width) drops ip, tasks, net, up, top, then disk, in that order."""

    def columns(self, width):
        return set(lxdtop.table_layout(width))

    def test_widths_where_each_column_goes(self):
        everything = {"dot", "name", "up", "cpu", "hist", "top", "mem", "rd", "wr", "net_in", "net_out",
                      "tasks", "ip"}
        steps = [(136, set()), (135, {"ip"}), (119, {"ip"}), (118, {"ip", "tasks"}),
                 (112, {"ip", "tasks"}), (111, {"ip", "tasks", "net_in", "net_out"}),
                 (91, {"ip", "tasks", "net_in", "net_out"}), (90, {"ip", "tasks", "net_in", "net_out", "up"}),
                 (86, {"ip", "tasks", "net_in", "net_out", "up"}),
                 (85, {"ip", "tasks", "net_in", "net_out", "up", "top"}),
                 (66, {"ip", "tasks", "net_in", "net_out", "up", "top"}),
                 (65, {"ip", "tasks", "net_in", "net_out", "up", "top", "rd", "wr"})]
        for width, dropped in steps:
            self.assertEqual(self.columns(width), everything - dropped, f"width {width}")

    def test_cpu_history_gets_the_spare_width(self):
        self.assertEqual(lxdtop.table_layout(136)["hist"][1], 16)   # the least it gets before a drop
        self.assertEqual(lxdtop.table_layout(156)["hist"][1], 36)   # the 160-column screen
        self.assertEqual(lxdtop.table_layout(400)["hist"][1], 120)  # capped
        self.assertEqual(lxdtop.table_layout(20)["hist"][1], 0)     # nothing left

    def test_columns_are_in_order_and_do_not_overlap(self):
        for width in range(66, 300):
            layout = sorted(lxdtop.table_layout(width).values())
            for (x1, w1), (x2, _) in zip(layout, layout[1:]):
                self.assertLessEqual(x1 + w1, x2, f"width {width}")
            last_x, last_w = layout[-1]
            self.assertLessEqual(last_x + last_w, width, f"width {width}")


def table(canvas):
    """The table's header line and row lines: the lines inside its box, below the title."""
    lines = canvas.text().split("\n")
    top = next(i for i, line in enumerate(lines) if re.search(r"[┐\[](containers|instances)[┌\]]", line))
    return lines[top + 1], lines[top + 2:-1]


def header_line(canvas):
    return table(canvas)[0]


def panels(canvas):
    text = canvas.text()
    names = ("cpu", "memory", "network", "cpu by container", "cpu by instance", "lxd events", "containers",
             "instances")
    return {name for name in names if f"┐{name}┌" in text}


class FrameLayoutTest(unittest.TestCase):
    """draw() fits the panels and the table to the screen, the way fit_layout() says."""

    def setUp(self):
        saved = lxdtop.G
        lxdtop.G = lxdtop.GLYPHS_UNICODE
        self.addCleanup(setattr, lxdtop, "G", saved)

    def test_full_screen_shows_everything(self):
        canvas = render(monitor(rows(8)), 160, 45)
        self.assertEqual(panels(canvas), {"cpu", "memory", "network", "cpu by container", "lxd events",
                                          "containers"})
        header = header_line(canvas)
        for label in ALL_HEADERS:
            self.assertIn(label, header)

    def test_events_panel_needs_140_columns(self):
        self.assertIn("lxd events", panels(render(monitor(rows(8)), 140, 45)))
        self.assertEqual(panels(render(monitor(rows(8)), 139, 45)),
                         {"cpu", "memory", "network", "cpu by container", "containers"})

    def test_host_panels_need_120_columns(self):
        self.assertEqual(panels(render(monitor(rows(8)), 119, 45)), {"cpu by container", "containers"})
        self.assertIn("cpu", panels(render(monitor(rows(8)), 120, 45)))

    def test_the_host_panels_stay_and_rows_give_way(self):
        # 45 rows high: the status line, the panels, then the table (rows + 3). Past 26 rows the
        # graph shrinks, then only the top running instances keep a row: the panels all stay.
        everything = {"cpu", "memory", "network", "cpu by container", "lxd events", "containers"}
        for n in (26, 27, 34, 35, 60):
            self.assertEqual(panels(render(monitor(rows(n)), 160, 45)), everything, n)
        # On a much shorter screen, once fewer than MIN_RUNNING rows would be left, the graph goes,
        # then the host panels last.
        self.assertEqual(panels(render(monitor(rows(30)), 160, 24)), everything)       # 5 rows, "+25 more"
        self.assertEqual(panels(render(monitor(rows(30)), 160, 23)), {"cpu", "memory", "network", "containers"})
        self.assertEqual(panels(render(monitor(rows(30)), 160, 16)), {"containers"})

    def test_every_row_is_shown_when_it_fits(self):
        for n in (1, 8, 26, 27):                            # 27: beside the host panels and a 7-row graph
            text = render(monitor(rows(n)), 160, 45).text()
            for i in range(n):
                self.assertIn(f" c{i:02} ", text, f"{n} rows")
            self.assertNotIn(" more", text)

    def test_rows_that_do_not_fit_are_counted(self):
        text = render(monitor(rows(60)), 160, 45).text()
        self.assertIn(" c25 ", text)                         # the top 26, in the sort order
        self.assertNotIn(" c26 ", text)
        self.assertIn("│ +34 more ", text)

    def test_columns_drop_as_the_window_narrows(self):
        def headers(width):
            line = header_line(render(monitor(rows(3)), width, 45))
            return {label for label in ALL_HEADERS if label in line}
        everything = set(ALL_HEADERS)
        self.assertEqual(headers(140), everything)
        self.assertEqual(headers(139), everything - {"IPV4"})
        self.assertEqual(headers(122), everything - {"IPV4", "TASKS"})
        self.assertEqual(headers(115), everything - {"IPV4", "TASKS", "IN Mb/s", "OUT Mb/s"})
        self.assertEqual(headers(94), everything - {"IPV4", "TASKS", "IN Mb/s", "OUT Mb/s", "UP"})
        self.assertEqual(headers(89), {"NAME", "CPU", "CPU HISTORY", "MEM", "DISK R", "DISK W"})
        self.assertEqual(headers(70), {"NAME", "CPU", "CPU HISTORY", "MEM", "DISK R", "DISK W"})

    def test_80_by_24(self):
        canvas = render(monitor(rows(3)), 80, 24)
        self.assertEqual(panels(canvas), {"cpu by container", "containers"})
        self.assertTrue(all(len(line) <= 80 for line in canvas.text().split("\n")))

    def test_too_small(self):
        for width, height in ((69, 24), (70, 11)):
            text = render(monitor(rows(3)), width, height).text()
            self.assertEqual(text.split("\n")[0], f"lxdtop needs at least 70x12 (this is {width}x{height})")
        self.assertIn("containers", panels(render(monitor(rows(3)), 70, 12)))

    def test_vms_turn_containers_into_instances(self):
        canvas = render(monitor(rows(2) + [row("win", vm=True, top=None)]), 160, 45)
        self.assertLessEqual({"instances", "cpu by instance"}, panels(canvas))

    def test_lines_never_pass_the_right_edge(self):
        for width in (70, 95, 120, 139, 140, 160, 200):
            for height in (12, 24, 45):
                canvas = render(monitor(rows(12)), width, height)
                lines = canvas.text().split("\n")
                self.assertEqual(len(lines), height)
                self.assertTrue(all(len(line) <= width for line in lines), f"{width}x{height}")

    def test_ascii_frame_is_plain_ascii(self):
        with glyphs(lxdtop.GLYPHS_ASCII):
            text = render(monitor(rows(5)), 160, 45).text()
        self.assertTrue(text.isascii())
        self.assertIn("[containers]", text)


class TableRowTest(unittest.TestCase):
    def setUp(self):
        saved = lxdtop.G
        lxdtop.G = lxdtop.GLYPHS_UNICODE
        self.addCleanup(setattr, lxdtop, "G", saved)

    def line_for(self, canvas, name):
        return next(line for line in table(canvas)[1] if name in line)

    def test_a_running_container(self):
        canvas = render(monitor([row("web", cpu=33.3, top=("nginx", 25.0), tasks=31)]), 160, 45)
        line = self.line_for(canvas, " web ")
        for part in ("● web", "2h", "33.3%", "nginx", "25.0%", "384M", "2.0M", "512K", "10.0", "1.00", "31",
                     "10.10.10.21"):
            self.assertIn(part, line)

    def test_long_names_are_cut_with_an_ellipsis(self):
        canvas = render(monitor([row("a-very-long-container-name")]), 160, 45)
        (line,) = [line for line in table(canvas)[1] if line.strip("│ ")]
        self.assertIn("● a-very-long-c… ", line)              # NAME is 14 wide
        self.assertNotIn("a-very-long-co", line)

    def test_stopped_instances_show_their_state(self):
        canvas = render(monitor([row("web"), row("old", live=False, state="Stopped", cpu=None)]), 160, 45)
        self.assertRegex(self.line_for(canvas, " old "), r"● old +stopped")

    def test_a_vm_has_no_busiest_process(self):
        canvas = render(monitor([row("win", vm=True, top=None)]), 160, 45)
        line = self.line_for(canvas, " win ")
        self.assertIn("■ win", line)
        self.assertIn("virtual machine", line)

    def test_an_idle_busiest_process_has_no_percentage(self):
        canvas = render(monitor([row("web", top=("sleep", 0.01))]), 160, 45)
        line = self.line_for(canvas, " web ")
        self.assertIn("sleep", line)
        self.assertNotIn("0.0%", line)

    def test_header_marks_the_sort_column(self):
        mon = monitor([row("web")])
        self.assertIn("MEM↓", header_line(render(mon, 160, 45, sort="mem")))
        self.assertIn("DISK R↓", header_line(render(mon, 160, 45, sort="disk")))
        self.assertIn("NAME↓", header_line(render(mon, 160, 45, sort="name")))


class NoReadingTest(unittest.TestCase):
    """A file lxdtop couldn't read leaves its reading out (None), and the panels draw without it."""

    def test_no_meminfo_loadavg_or_memory_current(self):
        host = dict(HOST_READING, mem_total=None, mem_used=None, mem_free=None, swap_total=None, swap_used=None,
                    load=[])
        mon = monitor([row("web", mem=None), row("db")], host=host)
        for width, height in ((80, 24), (160, 45), (200, 60)):
            lines = render(mon, width, height).text().splitlines()
            self.assertEqual(len(lines), height)
            if width >= 120:                                     # the host panels
                side = width * 52 // 160
                memory = [line[side:2 * side] for line in lines[1:8]]
                self.assertRegex(memory[0], r"^╭─┐memory┌─+╮$")    # no total
                for label in ("used", "free"):
                    (line,) = [line for line in memory if line.startswith(f"│ {label} ")]
                    self.assertRegex(line, rf"^│ {label} +■+ +│$")     # an empty meter: no number, no %
                self.assertFalse(any(line.startswith("│ swap ") for line in memory))
                self.assertRegex(lines[6][:side], r"^│ load +\d+ threads │$")
            web = next(line for line in lines if "● web " in line)
            db = next(line for line in lines if "● db " in line)
            self.assertNotIn("384M", web)                        # web's MEM is blank, db's is as it was
            self.assertIn("384M", db)


class SortTest(unittest.TestCase):
    def test_each_sort(self):
        mon = monitor([row("b", cpu=5.0, mem=300, rd=1, wr=1, net_in=50, net_out=0),
                       row("a", cpu=50.0, mem=100, rd=900, wr=0, net_in=1, net_out=0),
                       row("c", cpu=20.0, mem=200, rd=0, wr=0, net_in=0, net_out=900),
                       row("z", live=False, state="Stopped"), row("d", live=False, state="Stopped")])
        order = lambda mode: [r["name"] for r in lxdtop.sort_rows(mon, mode)]
        self.assertEqual(order("cpu"), ["a", "c", "b", "d", "z"])      # stopped last, by name
        self.assertEqual(order("mem"), ["b", "c", "a", "d", "z"])
        self.assertEqual(order("disk"), ["a", "b", "c", "d", "z"])
        self.assertEqual(order("net"), ["c", "b", "a", "d", "z"])
        self.assertEqual(order("name"), ["a", "b", "c", "d", "z"])


class GraphLegendTest(unittest.TestCase):
    """The CPU graph's legend: whole words only. Where it doesn't fit, "other instances" and "host itself"
    shorten, then the least busy names give way; the table shows every name in its color anyway."""

    def legend(self, names, width=80, height=24, ascii=False):
        mon = monitor([row(name, cpu=float(60 - 5 * i)) for i, name in enumerate(names)])
        with glyphs(lxdtop.GLYPHS_ASCII if ascii else lxdtop.GLYPHS_UNICODE):
            lines = render(mon, width, height).text().splitlines()
        top = next(i for i, line in enumerate(lines) if "cpu by" in line)
        bottom = next(i for i in range(top + 1, len(lines)) if lines[i][0] in "╰+")
        return lines[bottom - 1]                                         # the row above the panel's bottom edge

    def test_as_before_where_it_fits(self):
        self.assertEqual(self.legend(["web", "db"]),
                         "│ ■ web  ■ db  ■ other containers  ■ host itself" + " " * 31 + "│")

    def test_at_80_columns_long_names_give_way_and_no_word_is_cut(self):
        names = ["auth-lex-old", "avimark-db", "isc-31-web", "nextcloud", "unifi-ctrl"]
        self.assertEqual(self.legend(names),
                         "│ ■ auth-lex-old  ■ avimark-db  ■ isc-31-web  ■ nextcloud  …  ■ other  ■ host  │")
        self.assertEqual(self.legend(names, ascii=True),
                         "| # auth-lex-old  # avimark-db  # isc-31-web  # nextcloud  ~  # other  # host  |")

    def test_shorter_words_before_any_name_goes(self):
        line = self.legend(["auth-lex-old", "avimark-db", "isc-31-web", "nextcloud"])
        self.assertTrue(line.endswith("■ nextcloud  ■ other  ■ host" + " " * 5 + "│"), line)

    def test_every_size_shows_other_and_host_and_whole_names(self):
        names = ["a-very-long-instance-name-%d" % i for i in range(5)]
        for width, height in ((70, 24), (80, 24), (120, 30), (160, 45), (200, 60)):
            line = self.legend(names, width, height).rstrip("│ ")
            self.assertRegex(line, r"■ other( containers)?  ■ host( itself)?$", f"{width}x{height}")
            for word in re.findall(r"■ (\S+)", line):
                self.assertTrue(word in names or word in ("other", "host"), f"{width}x{height}: {line}")


class DiskRowTest(unittest.TestCase):
    """The memory panel's bottom row: each storage pool, then root. Where it doesn't fit, the sizes go,
    then healthy pools give way to "…"; root and a pool that isn't ONLINE always show, whole."""

    def disks(self, width, height, pools=None, zpools=None, ascii=False):
        mon = monitor([row("web")])
        if pools is not None:
            mon.pools = pools
        if zpools:
            mon.host["zpools"] = zpools
        with glyphs(lxdtop.GLYPHS_ASCII if ascii else lxdtop.GLYPHS_UNICODE):
            lines = render(mon, width, height).text().splitlines()
        side = width * 52 // 160                                         # the memory panel's columns
        return next(line[side + 2:2 * side - 2].rstrip() for line in lines if "root" in line[side:2 * side])

    def test_as_before_where_it_fits(self):
        self.assertEqual(self.disks(160, 45), "default 120G/900G 13%   root 40G/100G 40%")
        self.assertEqual(self.disks(200, 60), "default 120G/900G 13%   root 40G/100G 40%")

    def test_at_120_columns_the_sizes_go_and_root_shows_whole(self):
        self.assertEqual(self.disks(120, 30), "default 13%   root 40%")

    def test_a_pool_that_is_not_online_no_longer_pushes_root_out(self):
        self.assertEqual(self.disks(160, 45, zpools={"default": "DEGRADED"}), "default 13% DEGRADED   root 40%")

    def test_many_pools_give_way_but_not_root_or_one_that_is_not_online(self):
        pools = [(name, used * lxdtop.MB * 1024, 900 * lxdtop.MB * 1024)
                 for name, used in (("default", 120), ("fast", 450), ("tank", 630), ("backup", 90))]
        self.assertEqual(self.disks(120, 30, pools, {"tank": "DEGRADED"}), "tank 70% DEGRADED   …   root 40%")
        self.assertEqual(self.disks(120, 30, pools, {"tank": "DEGRADED"}, ascii=True),
                         "tank 70% DEGRADED   ~   root 40%")
        self.assertEqual(self.disks(120, 30, pools), "default 13%   …   root 40%")      # the last pools go first

    def test_without_roots_size_the_pools_still_fit(self):
        mon = monitor([row("web")])
        mon.pools = [(name, used * lxdtop.MB * 1024, 900 * lxdtop.MB * 1024)
                     for name, used in (("default", 120), ("fast", 450), ("tank", 630), ("backup", 90))]
        mon.host["root_total"] = None                                    # statvfs("/") failed
        lines = render(mon, 120, 30).text().splitlines()
        side = 120 * 52 // 160
        self.assertEqual(lines[6][side + 2:2 * side - 2].rstrip(), "default 13%   fast 50%   …")


class FakeEvents:
    def __init__(self, feed, status="live"):
        self.feed, self.status = feed, status

    def snapshot(self):
        return list(self.feed), self.status


class EventsPanelTest(unittest.TestCase):
    def draw(self, feed, status="live", width=54):
        cv = lxdtop.TextCanvas(14, width)
        mon = types.SimpleNamespace(events=FakeEvents(feed, status))
        with glyphs(lxdtop.GLYPHS_UNICODE):
            lxdtop.draw_events_panel(cv, 0, 0, 14, width, mon)
        return cv.text().split("\n")

    def test_waiting_for_events(self):
        lines = self.draw([], "connecting")
        self.assertIn("┐lxd events┌", lines[0])
        self.assertIn("connecting", lines[0])
        self.assertIn("waiting for LXD activity", lines[1])

    def test_event_lines(self):
        now = time.time()
        feed = [dict(action="instance-exec", name="db", detail="", user="backup", t=now - 5, count=3),
                dict(action="instance-snapshot-created", name="web", detail="before-upgrade", user="bj",
                     t=now - 60, count=1),
                dict(action="instance-frobnicated", name="web", detail="", user="", t=now - 90, count=1)]
        lines = self.draw(feed)
        clock = time.strftime("%H:%M:%S", time.localtime(now - 5))
        self.assertRegex(lines[1], rf"│ {clock}  exec +db +backup +×3 │")
        self.assertRegex(lines[2], r"snapshot +web +before-upgrade +│")   # the user is cut at the edge
        self.assertIn("frobnicat", lines[3])                   # unknown actions keep their own name

    def test_no_events_object(self):
        cv = lxdtop.TextCanvas(14, 54)
        lxdtop.draw_events_panel(cv, 0, 0, 14, 54, types.SimpleNamespace(events=None))
        self.assertIn("off", cv.text().split("\n")[0])


if __name__ == "__main__":
    unittest.main()
