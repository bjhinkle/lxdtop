"""Ops signals: an instance's limits, stalls and memory kills, ZFS pool health and snapshot ages."""

import datetime
import threading
import time
import unittest

from tests.support import GiB, MiB, FakeHost, lxdtop, monitor, render, row

WEB = "/sys/fs/cgroup/lxc.payload.web"


def pressure(some):
    return f"some avg10={some} avg60=1.00 avg300=0.50 total=12345\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"


class FakeApi:
    def __init__(self, found=None, zfs_roots=()):
        self.found, self.wake = found or {}, threading.Event()
        self.server = dict(version="6.7", member="", zfs_roots=list(zfs_roots))

    def snapshot(self):
        return dict(self.found), "ok", dict(self.server), []


class CgroupLimitTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)
        self.host.add_container("web", 1)

    def limits(self):
        with self.host.installed():
            return lxdtop.read_cgroup_limits(WEB)

    def test_limits_and_the_worst_stall(self):
        h = self.host
        h.write(WEB + "/cpu.max", "150000 100000\n")                  # 1.5 cores
        h.write(WEB + "/memory.max", f"{2048 * MiB}\n")
        h.write(WEB + "/pids.max", "500\n")
        for resource, some in (("cpu", "3.50"), ("memory", "41.20"), ("io", "12.00")):
            h.write(f"{WEB}/{resource}.pressure", pressure(some))
        self.assertEqual(self.limits(), dict(cpu_max=1.5, mem_max=2048 * MiB, tasks_max=500, waiting=("mem", 41.2)))

    def test_no_limits(self):
        for name, text in (("cpu.max", "max 100000\n"), ("memory.max", "max\n"), ("pids.max", "max\n")):
            self.host.write(f"{WEB}/{name}", text)
        self.assertEqual(self.limits(), dict(cpu_max=None, mem_max=None, tasks_max=None, waiting=None))

    def test_files_that_are_missing_or_turned_off_are_left_out(self):
        # add_container writes none of these: a kernel without them, or a controller that's off.
        self.assertEqual(self.limits(), dict(cpu_max=None, mem_max=None, tasks_max=None, waiting=None))
        # With pressure accounting off (psi=0), reading a .pressure file fails: read() gives None.
        self.host.mkdir(WEB + "/cpu.pressure")
        self.host.write(WEB + "/io.pressure", pressure("7.00"))
        self.assertEqual(self.limits()["waiting"], ("io", 7.0))


class MonitorSignalsTest(unittest.TestCase):
    """Readings 2 s apart of a host with one container, "web"."""

    def setUp(self):
        self.host = h = FakeHost(self)
        h.set_basics()
        h.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        with h.installed():
            self.mon = lxdtop.Monitor(FakeApi(), None)

    def reading(self, periods=0, throttled=0, kills=None, present=True):
        h = self.host
        if present:
            h.add_container("web", 1_000_000 + 1000 * periods, memory=512 * MiB)
            h.write(WEB + "/cpu.stat", f"usage_usec {1_000_000 + 1000 * periods}\nuser_usec 1\nsystem_usec 1\n"
                                       f"nr_periods {periods}\nnr_throttled {throttled}\nthrottled_usec 0\n")
            if kills is not None:
                h.write(WEB + "/memory.events", f"low 0\nhigh 0\nmax 4\noom 2\noom_kill {kills}\noom_group_kill 0\n")
        else:
            h.remove(WEB)
        with h.installed():
            self.mon.sample()
        h.clock += 2
        return next((r for r in self.mon.rows if r["cg"] == "web"), None)

    def test_throttled_is_the_share_of_the_last_intervals_periods(self):
        self.assertIsNone(self.reading(periods=100, throttled=10)["throttled"])   # one reading: no interval yet
        self.assertEqual(self.reading(periods=120, throttled=25)["throttled"], 75.0)   # 15 of 20 periods
        self.assertEqual(self.reading(periods=120, throttled=25)["throttled"], 0.0)    # no periods: idle
        self.assertEqual(self.reading(periods=5, throttled=1)["throttled"], 0.0)       # it restarted

    def test_no_cpu_controller_means_no_throttle_reading(self):
        self.host.add_container("web", 1)
        with self.host.installed():
            self.mon.sample()
        self.host.clock += 2
        self.host.add_container("web", 2)                  # cpu.stat without nr_periods
        with self.host.installed():
            self.mon.sample()
        self.assertIsNone(self.mon.rows[0]["throttled"])

    def test_a_cpu_controller_turned_on_while_lxdtop_runs(self):
        self.host.add_container("web", 1)                  # cpu.stat without nr_periods: the controller is off
        with self.host.installed():
            self.mon.sample()
        self.host.clock += 2
        # It's on now: nr_periods appears, with no earlier count to compare it with.
        self.assertIsNone(self.reading(periods=100, throttled=10)["throttled"])
        self.assertEqual(self.reading(periods=120, throttled=25)["throttled"], 75.0)

    def test_memory_kills_since_lxdtop_started(self):
        self.reading(kills=3)                              # killed 3 times before lxdtop started: not counted
        self.assertEqual(self.mon.kills["web"][1:], (0, None))
        first_seen = self.host.clock
        self.reading(kills=4)
        self.assertEqual(self.mon.kills["web"][1:], (1, first_seen))
        self.reading(kills=0)                              # restarted: the count starts again, no new kill
        self.assertEqual(self.mon.kills["web"][1:], (1, first_seen))
        self.reading(kills=2)
        self.assertEqual(self.mon.kills["web"][1:], (3, self.host.clock - 2))

    def test_a_kill_is_remembered_after_the_container_stops_until_it_is_old(self):
        self.reading(kills=0)
        self.reading(kills=1)                              # killed, then its init died and it stopped
        self.reading(present=False)
        self.assertIn("web", self.mon.kills)
        self.host.clock += lxdtop.RECENT_KILL
        self.reading(present=False)
        self.assertNotIn("web", self.mon.kills)

    def test_a_container_without_kills_is_forgotten_when_it_goes(self):
        self.reading(kills=0)
        self.reading(present=False)
        self.assertNotIn("web", self.mon.kills)

    def test_limits_and_stalls_reach_the_row(self):
        self.host.add_container("web", 1)
        self.host.write(WEB + "/memory.max", f"{1024 * MiB}\n")
        self.host.write(WEB + "/memory.pressure", pressure("30.00"))
        with self.host.installed():
            self.mon.sample()
        row = self.mon.rows[0]
        self.assertEqual((row["mem_max"], row["waiting"]), (1024 * MiB, ("mem", 30.0)))


class PoolStateTest(unittest.TestCase):
    def test_each_pools_state(self):
        host = FakeHost(self)
        host.set_basics()
        host.write("/proc/spl/kstat/zfs/default/state", "ONLINE\n")
        host.write("/proc/spl/kstat/zfs/tank/state", "DEGRADED\n")
        host.add_objset("tank", "0x36", "tank/lxd/containers/web", 0, 0)     # objset files aren't pools
        with host.installed():
            self.assertEqual(lxdtop.read_zpool_states(), {"default": "ONLINE", "tank": "DEGRADED"})
            mon = lxdtop.Monitor(None, None)
            mon.sample()
        self.assertEqual(mon.host["zpools"], {"default": "ONLINE", "tank": "DEGRADED"})

    def test_no_zfs(self):
        host = FakeHost(self)
        with host.installed():
            self.assertEqual(lxdtop.read_zpool_states(), {})


class SnapshotTimeTest(unittest.TestCase):
    def test_lxd_times(self):
        utc = datetime.timezone.utc
        for text, when in (("2026-11-04T09:00:05.123456789Z", datetime.datetime(2026, 11, 4, 9, 0, 5, tzinfo=utc)),
                           ("2026-11-04T09:00:05Z", datetime.datetime(2026, 11, 4, 9, 0, 5, tzinfo=utc)),
                           ("2021-03-23T20:00:00-04:00", datetime.datetime(2021, 3, 24, 0, 0, tzinfo=utc)),
                           ("2026-11-04T09:00:05.5+02:00", datetime.datetime(2026, 11, 4, 7, 0, 5, tzinfo=utc)),
                           ("2026-11-04T09:00:05", datetime.datetime(2026, 11, 4, 9, 0, 5, tzinfo=utc))):
            with self.subTest(text):
                self.assertEqual(lxdtop.parse_time(text), when.timestamp())

    def test_not_a_time(self):
        for text in (None, "", "yesterday", "2026-13-45T99:00:00Z", 1730000000, ["2026"]):
            with self.subTest(text=text):
                self.assertIsNone(lxdtop.parse_time(text))


TODAY_AT_160 = {"dot": (0, 1), "name": (2, 14), "up": (17, 4), "cpu": (22, 6), "hist": (30, 36), "top": (68, 18),
                "mem": (88, 6), "rd": (96, 7), "wr": (104, 7), "net_in": (113, 9), "net_out": (123, 9),
                "tasks": (134, 5), "ip": (141, 15)}         # table_layout(156) before the ops columns
HEADER_AT_160 = ("│   NAME             UP   CPU↓  CPU HISTORY (last 72s)                BUSIEST PROCESS"
                 "        MEM   DISK R  DISK W    IN Mb/s  OUT Mb/s  TASKS  IPV4            │")


def struggling(**attrs):
    """A Monitor with something wrong in each way the warning line knows."""
    now = time.monotonic()
    rows = [row("web1", cpu=180.0, throttled=45.0, cpu_max=2.0, mem_max=1 * GiB, tasks_max=500,
                waiting=("cpu", 31.0), snapshot=time.time() - 3 * 3600),
            row("db", mem=900 * MiB, mem_max=1 * GiB, tasks=480, tasks_max=500, waiting=("mem", 42.0)),
            row("cache", waiting=("io", 0.4), cpu_max=4.0, throttled=0.0, snapshot=time.time() - 20 * 86400),
            row("old", state="Stopped", live=False, snapshot=time.time() - 2 * 86400)]
    mon = monitor(rows, **attrs)
    mon.kills = {"web1": (7, 1, now - 12 * 60), "cache": (0, 0, None), "gone": (3, 2, now - 180)}
    mon.host["zpools"] = {"default": "ONLINE", "tank": "DEGRADED", "fast": "SUSPENDED"}
    return mon


def table_top(canvas):
    return next(line for line in canvas.text().splitlines() if "sorted by" in line)


class WarningTest(unittest.TestCase):
    def test_most_serious_first(self):
        self.assertEqual([text for _, _, text, _ in lxdtop.warnings(struggling())], [
            "fast SUSPENDED",                        # a pool that is down
            "gone: 2 memory kills, last 3m ago",     # memory kills, the most recent first; an instance
            "web1: memory kill 12m ago",             # no longer listed goes by its cgroup name
            "tank DEGRADED",
            "db: waiting on mem 42%",                # high pressure, the worst first
            "web1: waiting on cpu 31%",
            "web1: throttled 45% @2c"])

    def test_the_thresholds(self):
        below = [row("a", throttled=lxdtop.THROTTLED_PCT - 0.1, cpu_max=1.0, waiting=("io", lxdtop.PRESSURE_PCT - 0.1)),
                 row("b", throttled=99.0, cpu_max=None)]       # throttling needs a CPU limit
        mon = monitor(below)
        mon.kills = {"a": (1, 1, time.monotonic() - lxdtop.RECENT_KILL)}
        mon.host["zpools"] = {"tank": "ONLINE"}
        self.assertEqual(lxdtop.warnings(mon), [])
        at = monitor([row("a", throttled=float(lxdtop.THROTTLED_PCT), cpu_max=1.0,
                          waiting=("io", float(lxdtop.PRESSURE_PCT)))])
        self.assertEqual(len(lxdtop.warnings(at)), 2)

    def test_the_line_sits_between_the_title_and_the_sort(self):
        top = table_top(render(struggling(), 160, 45))
        self.assertIn("┐containers┌─┐fast SUSPENDED · gone: 2 memory kills, last 3m ago"
                      " · web1: memory kill 12m ago · tank DEGRADED · +3 more┌", top)   # all that fit
        self.assertTrue(top.endswith("┐sorted by cpu┌─╮"))

    def test_narrow_screens_show_fewer_and_keep_the_sort(self):
        for width in (70, 80, 100):
            with self.subTest(width=width):
                top = table_top(render(struggling(), width, 24))
                self.assertIn("┐fast SUSPENDED", top)
                self.assertRegex(top, r"\+\d+ more┌")
                self.assertTrue(top.endswith("┐sorted by cpu┌─╮"))
                self.assertEqual(len(top), width)

    def test_the_most_serious_is_cut_short_when_nothing_else_fits(self):
        parts = lxdtop.warning_parts([(4, 0, "web1: memory kill 12m ago", "web1"), (1, 0, "x", "x")], 12)
        self.assertEqual(parts, [("web1: memor…", "hi")])

    def test_nothing_wrong_draws_the_edge_as_before(self):
        top = table_top(render(monitor([row("web")]), 160, 45))
        self.assertRegex(top, r"^╭─┐containers┌─+┐sorted by cpu┌─╮$")

    def test_rows_with_a_warning_are_marked_red(self):
        canvas = render(struggling(), 160, 45)
        marks = {}
        for y, line in enumerate(canvas.text().splitlines()):
            for name in ("web1", "db", "cache", "old"):
                if line.startswith(("│ ● " + name + " ", "│ ■ " + name + " ")):
                    marks[name] = canvas.cells[y][2][1]
        self.assertEqual(marks, {"web1": "hi", "db": "hi", "cache": "lo", "old": "dim"})

    def test_a_stopped_instance_killed_for_memory_is_marked(self):
        mon = monitor([row("web1", state="Stopped", live=False)])
        mon.kills = {"web1": (1, 1, time.monotonic() - 60)}
        canvas = render(mon, 160, 45)
        y = next(i for i, line in enumerate(canvas.text().splitlines()) if "● web1" in line)
        self.assertEqual(canvas.cells[y][2][1], "hi")

    def test_ascii_draws_it_with_ascii_only(self):
        saved = lxdtop.G
        self.addCleanup(setattr, lxdtop, "G", saved)
        lxdtop.G = lxdtop.GLYPHS_ASCII
        for width, height in ((80, 24), (160, 45), (240, 60)):
            text = render(struggling(), width, height).text()
            self.assertTrue(text.isascii(), [ch for ch in text if not ch.isascii()][:5])
            self.assertIn("-[fast SUSPENDED . ", text)


class PoolPanelTest(unittest.TestCase):
    def pool_row(self, zpools):
        mon = monitor([row("web")])
        mon.host["zpools"] = zpools
        canvas = render(mon, 160, 45)
        y = next(i for i, line in enumerate(canvas.text().splitlines()) if "root 40G" in line)
        return canvas, y

    def test_all_online_looks_as_before(self):
        before, y = self.pool_row({})
        after, _ = self.pool_row({"default": "ONLINE", "tank": "ONLINE"})
        self.assertEqual(after.cells[y], before.cells[y])

    def test_an_lxd_pool_that_is_degraded(self):
        canvas, y = self.pool_row({"default": "DEGRADED"})
        line = canvas.text().splitlines()[y]
        self.assertIn("default 120G/900G 13% DEGRADED   root", line)
        start = line.index("default")
        self.assertEqual({canvas.cells[y][start][1], canvas.cells[y][line.index("DEGRADED")][1]}, {"hi"})

    def test_a_pool_lxd_doesnt_list(self):
        canvas, y = self.pool_row({"rpool": "FAULTED"})
        line = canvas.text().splitlines()[y]
        self.assertIn("default 120G/900G 13%   rpool FAULTED   root", line)
        self.assertEqual(canvas.cells[y][line.index("rpool")][1], "hi")


class OpsColumnsTest(unittest.TestCase):
    def test_up_to_160_columns_the_table_is_as_it_was(self):
        self.assertEqual(lxdtop.table_layout(156), TODAY_AT_160)
        for width in range(40, 157):
            self.assertFalse(set(lxdtop.table_layout(width)) & set(dict(lxdtop.OPS_COLUMNS)), width)
        self.assertIn(HEADER_AT_160, render(struggling(), 160, 45).text().splitlines())

    def test_wider_screens_add_them_in_order(self):
        order = [name for name, _ in lxdtop.OPS_COLUMNS]
        seen = []
        for width in range(156, 400):
            layout = lxdtop.table_layout(width)
            added = [name for name in order if name in layout]
            self.assertEqual(added, order[:len(added)], width)           # always the first few, in order
            self.assertGreaterEqual(len(added), len(seen))
            self.assertEqual({k: w for k, (_, w) in layout.items() if k in TODAY_AT_160 and k != "hist"},
                             {k: w for k, (_, w) in TODAY_AT_160.items() if k != "hist"})   # today's, as wide
            seen = added
        self.assertEqual(seen, order)
        self.assertEqual([n for n in order if n in lxdtop.table_layout(196)], ["thr", "mem_pct", "wait"])

    def test_headers_a_network_engineer_reads_at_a_glance(self):
        header = next(line for line in render(struggling(), 240, 60).text().splitlines() if "NAME" in line)
        for label in ("THROTTLED", "MEM/LIMIT", "WAITING", "MEM KILLS", "PROCS/LIMIT", "LAST SNAP"):
            self.assertIn(label, header)
        self.assertLess(header.index("IPV4"), header.index("THROTTLED"))       # after today's columns

    def test_cells(self):
        mon = struggling()
        cells = {r["name"]: lxdtop.ops_cells(r, mon) for r in mon.rows}
        self.assertEqual(cells["web1"], {"thr": ("45% @2c", "mid"), "mem_pct": ("38% of 1.0G", "text"),
                                         "tasks_pct": ("2% of 500", "text"), "wait": ("cpu 31%", "hi"),
                                         "kills": ("1 12m ago", "hi"), "snap": ("3h", "text")})
        self.assertEqual(cells["db"], {"thr": ("-", "dim"), "mem_pct": ("88% of 1.0G", "text"),
                                       "tasks_pct": ("96% of 500", "mid"), "wait": ("mem 42%", "hi"),
                                       "snap": ("never", "dim")})
        self.assertEqual(cells["cache"], {"thr": ("0% @4c", "dim"), "mem_pct": ("-", "dim"), "tasks_pct": ("-", "dim"),
                                          "wait": ("-", "dim"), "kills": ("-", "dim"), "snap": ("20d", "text")})
        self.assertEqual(cells["old"], {"snap": ("2d", "text")})                 # stopped: no cgroup readings

    def test_a_vm_shows_only_its_snapshot(self):
        vm = row("win", vm=True, cpu_max=None, snapshot=None)
        self.assertEqual(lxdtop.ops_cells(vm, monitor([vm])), {"snap": ("never", "dim")})

    def test_counts(self):
        self.assertEqual([lxdtop.fmt_count(n) for n in (0, 500, 9999, 10_000, 42_000, 4_194_304)],
                         ["0", "500", "9999", "10k", "42k", "4.2M"])


class LayoutSizeTest(unittest.TestCase):
    def test_every_size_keeps_its_frame(self):
        for width, height in ((80, 24), (160, 45), (240, 60)):
            with self.subTest(size=f"{width}x{height}"):
                lines = render(struggling(), width, height).text().splitlines()
                self.assertEqual(len(lines), height)
                self.assertTrue(all(len(line) <= width for line in lines))
                table = [i for i, line in enumerate(lines) if "sorted by" in line][0]
                self.assertTrue(lines[table].startswith("╭") and lines[table].endswith("╮"))
                self.assertTrue(lines[-1].startswith("╰") and lines[-1].endswith("╯"))
                for line in lines[table + 1:-1]:
                    self.assertTrue(line.startswith("│") and line.endswith("│"), line)


if __name__ == "__main__":
    unittest.main()
