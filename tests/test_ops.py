"""Ops signals: an instance's limits, stalls and memory kills, ZFS pool health and snapshot ages."""

import datetime
import threading
import unittest

from tests.support import MiB, FakeHost, lxdtop

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


if __name__ == "__main__":
    unittest.main()
