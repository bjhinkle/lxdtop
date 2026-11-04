"""Rates from counters: the rate math, the process table, and a Monitor sampling a fake host."""

import threading
import types
import unittest

from tests.support import GiB, MiB, FakeHost, lxdtop

TICKS = lxdtop.CLOCK_TICKS


class RateTest(unittest.TestCase):
    def test_change_per_second(self):
        self.assertEqual(lxdtop.rate(200, 100, 2), 50.0)
        self.assertEqual(lxdtop.rate(100, 100, 2), 0.0)

    def test_a_counter_that_went_down_is_a_restart(self):
        self.assertEqual(lxdtop.rate(100, 5000, 2), 0.0)

    def test_no_time_between_readings(self):
        self.assertEqual(lxdtop.rate(200, 100, 0), 0.0)
        self.assertEqual(lxdtop.rate(200, 100, -1), 0.0)

    def test_busy_pct(self):
        self.assertEqual(lxdtop.busy_pct((150, 1000), (100, 900)), 50.0)
        self.assertEqual(lxdtop.busy_pct((100, 900), (100, 900)), 0.0)      # no time passed
        self.assertEqual(lxdtop.busy_pct((500, 1000), (100, 900)), 100.0)   # clamped
        self.assertEqual(lxdtop.busy_pct((90, 1000), (100, 900)), 0.0)


class ProcessTableTest(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)
        self.table = lxdtop.ProcessTable()

    def sample(self, now):
        with self.host.installed():
            self.table.sample(now)

    def test_busiest_process_per_container(self):
        h = self.host
        h.add_process(100, "nginx", "/lxc.payload.web/system.slice/nginx.service", utime=100, stime=50, start=5000)
        h.add_process(101, "tmux: server", "/lxc.payload.web/user.slice", utime=10, stime=0, start=7000)
        h.add_process(300, "(sd-pam)", "/lxc.payload.dev_api/user.slice", utime=1, stime=1, start=9000)
        h.add_process(200, "sshd", "/system.slice/ssh.service", utime=5000, stime=5000)   # on the host
        self.sample(10.0)
        self.assertEqual(self.table.top["web"][1], 0.0)          # nothing to compare with yet
        self.assertEqual(self.table.started, {"web": 5000, "dev_api": 9000})
        h.add_process(100, "nginx", "/lxc.payload.web/system.slice/nginx.service", utime=300, stime=100, start=5000)
        h.add_process(101, "tmux: server", "/lxc.payload.web/user.slice", utime=12, stime=0, start=7000)
        h.add_process(300, "(sd-pam)", "/lxc.payload.dev_api/user.slice", utime=1, stime=1, start=9000)
        self.sample(12.0)
        # nginx: 250 ticks in 2 s. The first rate isn't smoothed with anything.
        self.assertEqual(self.table.top["web"], ("nginx", 250 / TICKS / 2 * 100))
        self.assertEqual(self.table.top["dev_api"], ("(sd-pam)", 0.0))   # name in parentheses kept whole
        self.assertNotIn(200, self.table.ticks)                  # host processes aren't read again

    def test_rates_are_smoothed(self):
        self.host.add_process(100, "nginx", "/lxc.payload.web", utime=0)
        self.sample(10.0)
        self.host.add_process(100, "nginx", "/lxc.payload.web", utime=2 * TICKS)    # 100% for 2 s
        self.sample(12.0)
        self.host.add_process(100, "nginx", "/lxc.payload.web", utime=2 * TICKS)    # then idle
        self.sample(14.0)
        self.assertEqual(self.table.top["web"], ("nginx", 50.0))

    def test_gone_processes_are_forgotten(self):
        self.host.add_process(100, "nginx", "/lxc.payload.web", utime=1)
        self.sample(10.0)
        self.host.remove("/proc/100")
        self.sample(12.0)
        self.assertEqual(self.table.owner, {})
        self.assertEqual(self.table.ticks, {})
        self.assertEqual(self.table.top, {})

    def test_a_process_exiting_between_listing_and_reading(self):
        self.host.write("/proc/100/cgroup", "0::/lxc.payload.web\n")      # cgroup readable, stat gone
        self.sample(10.0)
        self.assertEqual(self.table.top, {})

    def test_exec_sessions_are_found_within_30_rounds(self):
        # `lxc exec` starts its process on the host side, then moves it into the container.
        self.host.add_process(500, "bash", "/system.slice/snap.lxd.daemon.service", utime=1)
        for i in range(10):
            self.sample(10.0 + i)
        self.host.add_process(500, "bash", "/lxc.payload.web/init.scope", utime=1)
        rounds = 10
        while "web" not in self.table.top:
            self.sample(10.0 + rounds)
            rounds += 1
            self.assertLessEqual(rounds, 30, "not picked up within 30 rounds")
        self.assertEqual(self.table.top["web"][0], "bash")


def instances():
    """What LxdApi.fetch_instances() returns for the fixture host (see test_lxd_api)."""
    return {
        "web": dict(name="web", status="Running", veths=["veth1a2b3c4d"], ipv4="10.10.10.21",
                    type="container", pid=4242),
        "dev_api": dict(name="api", status="Running", veths=[], ipv4="", type="container", pid=4400),
        "win": dict(name="win", status="Running", veths=["tap0f1e2d3c"], ipv4="10.10.10.30",
                    type="virtual-machine", pid=5151),
        "old": dict(name="old", status="Stopped", veths=[], ipv4="", type="container", pid=0),
    }


class FakeApi:
    def __init__(self, found, server=None):
        self.found = found
        self.server = server or dict(version="6.9")
        self.wake = threading.Event()

    def snapshot(self):
        return dict(self.found), "ok", dict(self.server), [("default", 1, 2)]


class MonitorTest(unittest.TestCase):
    """Two readings, two seconds apart, of a host with two containers, a VM and a stopped container."""

    def setUp(self):
        self.host = h = FakeHost(self)
        h.set_basics(uptime=123456.78)
        h.copy_fixture("zfs-arcstats.txt", lxdtop.ZFS_ARCSTATS)
        h.write("/sys/class/hwmon/hwmon1/name", "k10temp\n")
        h.write("/sys/class/hwmon/hwmon1/temp1_input", "54250\n")
        h.write("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "3400000\n")
        h.write("/sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "2200000\n")
        self.api = FakeApi(instances())
        self.reading(0)
        with h.installed():
            self.mon = lxdtop.Monitor(self.api, None)
            self.mon.sample()
        h.clock += 2
        self.reading(1)
        with h.installed():
            self.mon.sample()
        self.rows = {r["cg"]: r for r in self.mon.rows}

    def reading(self, n):
        """The host's counters at the first (n=0) and second (n=1) reading."""
        h = self.host
        # Host CPU: 800 of the 1000 ticks busy between the readings (80%).
        h.set_cpu_times((1000 + 800 * n, 0, 9000 + 200 * n), [(100 + 400 * n, 0, 900 + 100 * n)] * 4)
        h.add_container("web", 10_000_000 + 3_000_000 * n, memory=512 * MiB, inactive_file=128 * MiB, pids=31)
        h.add_container("dev_api", 5_000_000 + 200_000 * n, pids=7, io=(1000 + 4000 * n, 0 + 2000 * n))
        h.add_objset("default", "0x36", "default/containers/web", 1_000_000 + 2_000_000 * n, 500 + 1000 * n)
        h.add_objset("default", "0x52", "default/virtual-machines/win.block", 0, 0)
        # The web container sent 250,000 bytes and received 500,000 bytes.
        h.add_nic("veth1a2b3c4d", rx=1000 + 250_000 * n, tx=2000 + 500_000 * n)
        h.add_nic("tap0f1e2d3c", rx=0, tx=0)
        h.add_nic("eno1", rx=10_000 + 2_500_000 * n, tx=20_000 + 500_000 * n, physical=True)
        # Processes: nginx in web (250 ticks over the 2 s), and the VM's QEMU (2 s of CPU).
        h.add_process(100, "nginx", "/lxc.payload.web/system.slice/nginx.service",
                      utime=100 + 200 * n, stime=50 + 50 * n, start=5000)
        h.add_process(300, "python3", "/lxc.payload.dev_api/init.scope", utime=10, start=9000)
        h.write("/proc/5151/stat", lxdtop_stat_qemu(utime=1000 + int(1.5 * TICKS) * n,
                                                     stime=500 + int(0.5 * TICKS) * n))
        h.copy_fixture("pid-status-qemu.txt", "/proc/5151/status")

    def test_container_rates(self):
        web = self.rows["web"]
        self.assertEqual(web["name"], "web")
        self.assertTrue(web["live"])
        self.assertFalse(web["vm"])
        self.assertAlmostEqual(web["cpu"], 150.0)             # 3 s of CPU in 2 s: one and a half cores
        self.assertEqual(web["mem"], 384 * MiB)               # memory.current minus inactive file cache
        self.assertEqual(web["tasks"], 31)
        self.assertEqual(web["rd"], 1_000_000.0)              # from ZFS: 2,000,000 bytes over 2 s
        self.assertEqual(web["wr"], 500.0)
        self.assertEqual(web["net_in"], 250_000.0)            # bytes/s the container received
        self.assertEqual(web["net_out"], 125_000.0)           # bytes/s it sent
        self.assertEqual(web["ip"], "10.10.10.21")
        self.assertEqual(web["top"], ("nginx", 250 / TICKS / 2 * 100))
        self.assertAlmostEqual(web["up"], 123456.78 - 5000 / TICKS)

    def test_project_container_uses_cgroup_io_and_its_lxd_name(self):
        api = self.rows["dev_api"]
        self.assertEqual(api["name"], "api")                  # cgroup "dev_api" is "api" in project "dev"
        self.assertAlmostEqual(api["cpu"], 10.0)
        self.assertEqual((api["rd"], api["wr"]), (2000.0, 1000.0))   # io.stat: no ZFS dataset
        self.assertIsNone(api["net_in"])                      # no network interfaces

    def test_vm_reads_its_qemu_process(self):
        win = self.rows["win"]
        self.assertTrue(win["vm"])
        self.assertAlmostEqual(win["cpu"], 100.0)
        self.assertEqual(win["mem"], 2 * GiB)                 # VmRSS
        self.assertEqual(win["rd"], 0.0)

    def test_stopped_instance(self):
        old = self.rows["old"]
        self.assertFalse(old["live"])
        self.assertEqual(old["state"], "Stopped")
        self.assertIsNone(old["cpu"])

    def test_host_panel_reading(self):
        host = self.mon.host
        self.assertAlmostEqual(host["cpu"], 80.0)
        self.assertEqual(host["threads"], [80.0] * 4)
        self.assertEqual(host["temp"], 54.25)
        self.assertEqual(host["mhz"], 2800.0)
        self.assertEqual(host["arc"], 8 * GiB)
        self.assertEqual(host["mem_used"], (65536000 - 32768000) * 1024 - 8 * GiB)
        self.assertEqual(host["swap_used"], (8388604 - 8000000) * 1024)
        self.assertEqual((host["root_total"], host["root_used"]), (100 * GiB, 40 * GiB))
        self.assertEqual(host["load"], ["0.52", "0.48", "0.40"])

    def test_physical_port_rates(self):
        self.assertEqual(self.mon.nic_rates, {"eno1": (1_250_000.0, 250_000.0)})
        self.assertEqual(list(self.mon.net_in), [1_250_000.0])
        self.assertEqual(list(self.mon.net_out), [250_000.0])

    def test_cpu_graph_sample(self):
        sample = self.mon.stack[-1]
        self.assertAlmostEqual(sample["web"], 150.0)
        self.assertAlmostEqual(sample["dev_api"], 10.0)
        self.assertAlmostEqual(sample["win"], 100.0)
        # 80% of 4 cores is 320% of one core; 260% of it is the instances.
        self.assertAlmostEqual(sample[lxdtop.HOST], 60.0)

    def test_first_reading_has_no_rates(self):
        host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((1, 0, 1), [])
        host.add_container("web", 1000)
        with host.installed():
            mon = lxdtop.Monitor(None, None)
            mon.sample()
        (web,) = mon.rows
        self.assertIsNone(web["cpu"])
        self.assertIsNone(web["rd"])
        self.assertIsNone(mon.host["cpu"])
        self.assertEqual(len(mon.stack), 0)

    def test_a_restarted_container_shows_zero_not_a_negative_rate(self):
        self.host.add_container("web", 1000)                  # counters reset
        self.host.clock += 2
        with self.host.installed():
            self.mon.sample()
        web = next(r for r in self.mon.rows if r["cg"] == "web")
        self.assertEqual(web["cpu"], 0.0)

    def test_disk_rates_restart_when_the_source_changes(self):
        self.host.remove("/proc/spl/kstat/zfs/default/objset-0x36")   # dataset gone: io.stat instead
        self.host.clock += 2
        with self.host.installed():
            self.mon.zfs_checked = 0
            self.mon.sample()
        web = next(r for r in self.mon.rows if r["cg"] == "web")
        self.assertIsNone(web["rd"])

    def test_an_unknown_container_wakes_the_api_once(self):
        self.assertFalse(self.api.wake.is_set())
        self.host.add_container("new", 1)                     # appeared after LXD's last answer
        self.host.clock += 2
        with self.host.installed():
            self.mon.sample()
        self.assertTrue(self.api.wake.is_set())
        self.api.wake.clear()
        self.host.clock += 2
        with self.host.installed():
            self.mon.sample()
        self.assertFalse(self.api.wake.is_set())              # asked once, not every reading
        new = next(r for r in self.mon.rows if r["cg"] == "new")
        self.assertEqual(new["name"], "new")                  # the cgroup name until LXD answers

    def test_without_the_api_containers_still_show(self):
        with self.host.installed():
            mon = lxdtop.Monitor(None, None)
            mon.sample()
        self.assertEqual(sorted(r["cg"] for r in mon.rows), ["dev_api", "web"])   # no VM, no stopped
        self.assertEqual(mon.api_status, "off")


def lxdtop_stat_qemu(utime, stime):
    from tests.support import pid_stat
    return pid_stat(5151, "qemu-system-x86", utime=utime, stime=stime, start=1234500)


class ZfsPoolTest(unittest.TestCase):
    """SECURITY-REVIEW.md L-8: the Monitor passes LXD's ZFS pool datasets to find_zfs_datasets()."""

    def test_a_nested_dataset_cannot_feed_another_instances_disk_columns(self):
        h = FakeHost(self)
        h.set_basics()
        found = {cg: dict(name=cg, status="Running", veths=[], ipv4="", type="container", pid=0)
                 for cg in ("web", "db")}
        api = FakeApi(found, server=dict(version="6.9", zfs_roots=["default"]))
        with h.installed():
            mon = lxdtop.Monitor(api, None)
        for n in range(2):
            h.set_cpu_times((100 + n, 0, 900), [])
            h.add_container("web", 1000 + n)
            h.add_container("db", 1000 + n, io=(1000 + 4000 * n, 0))          # db's disk isn't on ZFS
            h.add_objset("default", "0x10", "default/containers/web", 0, 0)
            h.add_objset("default", "0x20", "default/containers/web/containers/db", 5_000_000 * n, 0)  # made in web
            with h.installed():
                mon.sample()
            h.clock += 2
        db = next(r for r in mon.rows if r["cg"] == "db")
        self.assertEqual(db["rd"], 2000.0)                    # from io.stat, not from web's nested dataset


class SeriesTest(unittest.TestCase):
    def setUp(self):
        self.mon = types.SimpleNamespace(series_style={})
        self.pick = lambda samples: lxdtop.Monitor.pick_series(self.mon, samples)

    def test_busiest_five_get_steady_colors(self):
        samples = [{f"c{i}": float(i) for i in range(8)} | {lxdtop.HOST: 500.0}]
        self.assertEqual(self.pick(samples), ["c7", "c6", "c5", "c4", "c3"])
        first = dict(self.mon.series_style)
        self.assertEqual(sorted(first.values()), sorted(lxdtop.SERIES))
        samples = [{"c3": 100.0, "c4": 1.0, "c5": 1.0, "c6": 1.0, "c7": 1.0}]
        self.assertEqual(self.pick(samples)[0], "c3")
        self.assertEqual(self.mon.series_style, first)          # same colors after the order changed

    def test_idle_containers_get_no_color(self):
        self.assertEqual(self.pick([{"a": 0.0, "b": 2.0}]), ["b"])
        self.assertEqual(self.pick([]), [])
        self.assertEqual(self.mon.series_style, {})


if __name__ == "__main__":
    unittest.main()
