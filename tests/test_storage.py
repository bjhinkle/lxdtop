"""Storage: ZFS datasets without LXD's API, the state of a pool kept inside another zpool, what counts as
stopped, and disk numbers for VMs whose disks aren't on ZFS."""

import unittest
from unittest import mock

from tests.support import FakeHost, FakeLxd, glyphs, lxd_routes, lxdtop, monitor, render, row

KSTAT = "/proc/spl/kstat/zfs/tank/objset-"


class NoApiDatasetTest(unittest.TestCase):
    """Without LXD's API, lxdtop works out which ZFS dataset is each instance's disk from the names."""

    def setUp(self):
        self.host = FakeHost(self)
        for cg in ("web", "lxd"):
            self.host.add_container(cg, 1)

    def datasets(self, own, *names):
        for i, dataset in enumerate(names, 1):
            self.host.add_objset("tank", f"0x{i}", dataset, 1, 1)
        with self.host.installed():
            return lxdtop.find_zfs_datasets(own=own)

    def test_a_pool_kept_under_containers_lxd_with_a_container_called_lxd(self):
        """#5: a pool with zfs.pool_name=tank/containers/lxd, and a running container named lxd.

        Every dataset of that pool has containers/lxd/ in its path, so the rule against
        datasets made inside a running container took them all for lxd's, and its instances
        lost their ZFS counters. Each container's own dataset, from what it has mounted as "/",
        says where it is.
        """
        mapping = self.datasets({"web": "tank/containers/lxd/containers/web",
                                 "lxd": "tank/containers/lxd/containers/lxd"},
                                "tank/containers/lxd/containers/web", "tank/containers/lxd/containers/lxd",
                                "tank/containers/lxd/virtual-machines/vm1.block")
        self.assertEqual(mapping, {"web": KSTAT + "0x1", "lxd": KSTAT + "0x2", "vm1": KSTAT + "0x3"})

    def test_what_a_running_container_made_inside_its_own_dataset_is_still_nobodys(self):
        """SECURITY-REVIEW.md L-8: with zfs.delegate, a container can make datasets inside its own."""
        mapping = self.datasets({"web": "tank/containers/web", "lxd": "tank/containers/lxd"},
                                "tank/containers/web", "tank/containers/lxd",
                                "tank/containers/lxd/containers/web",            # made inside lxd's
                                "tank/containers/lxd/x/virtual-machines/vm1.block")
        self.assertEqual(mapping, {"web": KSTAT + "0x1", "lxd": KSTAT + "0x2"})

    def test_for_a_container_whose_root_is_unknown_its_name_still_rules_them_out(self):
        """When lxdtop can't read which dataset lxd has as "/", a dataset under containers/lxd/ may be in it."""
        mapping = self.datasets({"web": "tank/containers/lxd/containers/web"},          # nothing for lxd
                                "tank/containers/lxd/containers/web")
        self.assertEqual(mapping, {})


class PoolStateTest(unittest.TestCase):
    """#9: an LXD pool kept inside another zpool, with zfs.pool_name=tank/lxd."""

    def test_lxds_api_says_which_zpool_each_pool_is_in(self):
        routes = lxd_routes()
        routes["/1.0/storage-pools?recursion=1"][1]["metadata"][0]["config"]["zfs.pool_name"] = "tank/lxd"
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        self.assertEqual(api.server["zpools"], {"default": "tank"})
        self.assertEqual(api.server["zfs_roots"], ["tank/lxd"])

    def sick(self):
        host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        host.write("/proc/spl/kstat/zfs/tank/state", "DEGRADED\n")
        host.write("/proc/spl/kstat/zfs/backup/state", "FAULTED\n")         # a zpool LXD doesn't use
        mon = monitor([row("web")])
        mon.server["zpools"] = {"default": "tank"}                            # from LXD's API
        with host.installed():
            mon.sample_host(123456.0)
        return mon

    def test_its_state_shows_under_the_lxd_pools_name(self):
        text = render(self.sick()).text()
        self.assertIn("│ default 13% DEGRADED   backup FAULTED   root 40% │", text)             # sizes give way at 160
        self.assertIn("backup FAULTED · default DEGRADED", text)                       # the warning line
        self.assertNotIn("tank", text)

    def test_and_in_the_json(self):
        doc = lxdtop.reading_json(self.sick(), 2)
        self.assertEqual([(p["name"], p["health"]) for p in doc["pools"]], [("default", "DEGRADED"),
                                                                           ("backup", "FAULTED")])
        self.assertEqual([(w["pool"], w["text"]) for w in doc["warnings"]], [("backup", "backup FAULTED"),
                                                                            ("default", "default DEGRADED")])


class StoppedTest(unittest.TestCase):
    """#11: a running VM whose QEMU process lxdtop can't read counted as stopped."""

    def rows(self, running=1):
        unread = row("win", vm=True, live=False, **dict.fromkeys(("mem", "tasks", "top", "up", "cpu", "rd", "wr",
                                                                  "net_in", "net_out")))
        stopped = row("old", state="Stopped", live=False)
        return [row(f"web{i}", cpu=10.0 + i) for i in range(running)] + [unread, stopped]

    def test_the_status_line_counts_it_as_running(self):
        text = render(monitor(self.rows())).text()
        self.assertIn("2 running · 1 stopped", text.splitlines()[0])

    def test_it_stays_among_the_running_on_a_short_screen(self):
        text = render(monitor(self.rows(running=30)), 160, 24).text()
        self.assertIn("1 stopped: old", text)
        self.assertNotIn("stopped: old, win", text)

    def test_and_in_the_json(self):
        doc = lxdtop.reading_json(monitor(self.rows()), 2)
        self.assertEqual(doc["instance_counts"], dict(running=2, stopped=1))
        self.assertEqual([i["name"] for i in doc["instances"]], ["web0", "win", "old"])


class UnreadVmTest(unittest.TestCase):
    """#16: a running VM whose QEMU process lxdtop can't read showed a grey "running" row with no numbers,
    and nothing said why. lxdtop reads a VM's CPU, memory and uptime from that process (read_vm_process())."""

    def setUp(self):
        self.host = FakeHost(self)
        self.host.set_basics()
        self.host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        for cg in ("web", "db", "dev_api"):
            self.host.add_container(cg, 1_000_000)
        self.routes = lxd_routes()

    def readings(self, **state):
        """Two readings, 2 s apart, with LXD's word on win's state changed as given."""
        for inst in self.routes["/1.0/instances?recursion=2&all-projects=true"][1]["metadata"]:
            if inst["name"] == "win":
                inst["state"].update(state)
        with FakeLxd(self.routes) as lxd, self.host.installed():
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            mon = lxdtop.Monitor(api, None)
            mon.sample()
            self.host.clock += 2
            mon.sample()
        return mon

    def draw(self, mon, width=160, height=45, detail=None):
        ui = lxdtop.new_ui(2)
        if detail:
            ui.view, ui.detail = "detail", detail
        canvas = lxdtop.TextCanvas(height, width)
        with glyphs(lxdtop.GLYPHS_UNICODE), mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
            lxdtop.draw(canvas, mon, ui)
        return canvas.text()

    def says(self, mon, why):
        """win's row and its detail view say why it has no readings, at 80x24, 160x45 and 240x60."""
        for width, height in ((80, 24), (160, 45), (240, 60)):
            with self.subTest(size=f"{width}x{height}"):
                line = next(line for line in self.draw(mon, width, height).splitlines() if " win " in line)
                self.assertRegex(line, r"win +running   no readings: " + why)
                self.assertRegex(self.draw(mon, width, height, detail="win"), r"\n│ readings    none: " + why)

    def test_its_process_is_not_there(self):
        self.says(self.readings(), "QEMU process 5151 not found")              # this fake host has no /proc/5151

    def test_its_process_cannot_be_read(self):
        self.host.mkdir("/proc/5151")                                          # there, but not its stat
        mon = self.readings()
        self.says(mon, "no access to QEMU process 5151")
        self.assertIn("readings    none: no access to QEMU process 5151                             │\n"
                      "│             /proc may hide other users' processes (hidepid)", self.draw(mon, 80, 24, "win"))

    def test_its_process_is_something_else(self):
        self.host.add_process(5151, "bash")
        self.says(self.readings(), "process 5151 is not QEMU")

    def test_lxd_gave_no_process_id(self):
        self.says(self.readings(pid=0), "LXD gave no QEMU process ID")

    def test_a_vm_it_reads_or_one_that_is_stopped_says_nothing_more(self):
        self.host.copy_fixture("pid-stat-qemu.txt", "/proc/5151/stat")
        self.host.copy_fixture("pid-status-qemu.txt", "/proc/5151/status")
        text = self.draw(self.readings())
        self.assertNotIn("no readings", text)
        self.routes["/1.0/instances?recursion=2&all-projects=true"][1]["metadata"][3]["status"] = "Stopped"
        text = self.draw(self.readings())
        self.assertRegex(text, r"win +stopped\b")
        self.assertNotIn("no readings", text)


METRICS = """\
# HELP lxd_disk_read_bytes_total The total number of bytes read.
# TYPE lxd_disk_read_bytes_total counter
lxd_disk_read_bytes_total{{device="sda",name="win",project="default",type="virtual-machine"}} {sda_read}
lxd_disk_read_bytes_total{{device="sda1",name="win",project="default",type="virtual-machine"}} {sda_read}
lxd_disk_read_bytes_total{{device="dm-0",name="win",project="default",type="virtual-machine"}} 7e+08
lxd_disk_read_bytes_total{{device="loop3",name="win",project="default",type="virtual-machine"}} 5e+06
lxd_disk_read_bytes_total{{device="sdb",name="win",project="default",type="virtual-machine"}} 2048
lxd_disk_read_bytes_total{{device="nvme0n1",name="db",project="dev",type="virtual-machine"}} 4096
lxd_disk_read_bytes_total{{device="nvme0n1p1",name="db",project="dev",type="virtual-machine"}} 4096
lxd_disk_read_bytes_total{{device="dev-lxd_root",name="bare",project="default",type="virtual-machine"}} 100
lxd_disk_read_bytes_total{{device="8:0",name="web",project="default",type="container"}} 123
# HELP lxd_disk_written_bytes_total The total number of bytes written.
# TYPE lxd_disk_written_bytes_total counter
lxd_disk_written_bytes_total{{device="sda",name="win",project="default",type="virtual-machine"}} {sda_written}
lxd_disk_written_bytes_total{{device="sdb",name="win",project="default",type="virtual-machine"}} 0
lxd_disk_written_bytes_total{{device="nvme0n1",name="db",project="dev",type="virtual-machine"}} 512
lxd_disk_written_bytes_total{{device="dev-lxd_root",name="bare",project="default",type="virtual-machine"}} 200
lxd_cpu_seconds_total{{cpu="0",mode="user",name="win",project="default",type="virtual-machine"}} 12.5
"""


def metrics(read=1.073741824e+09, written=2.147483648e+09):
    return METRICS.format(sda_read=read, sda_written=written).encode()


class VmDiskMetricsTest(unittest.TestCase):
    """#14: a VM's disk numbers came only from ZFS, so VMs on other storage drivers showed none.

    QEMU runs as LXD's unprivileged user, so its /proc/<pid>/io can't be read by lxdtop's user,
    and a VM has no cgroup of its own. LXD's metrics (GET /1.0/metrics), which the lxd group
    can read, have each VM's disk counters, from its agent or from QEMU.
    """

    def test_whole_disks(self):
        for device in ("sda", "sdab", "vdb", "xvdc", "hda", "nvme0n1", "nvme12n3", "dev-lxd_root",
                       "/machine/peripheral/dev-lxd_data/virtio-backend"):
            self.assertTrue(lxdtop.whole_disk(device), device)
        for device in ("sda1", "vdb15", "nvme0n1p1", "nvme0", "loop0", "dm-0", "md127", "sr0", "zram0", "", "nvmen1"):
            self.assertFalse(lxdtop.whole_disk(device), device)

    def test_the_counters_of_each_vms_whole_disks(self):
        self.assertEqual(lxdtop.vm_disk_totals(metrics().decode()), {
            "win": {"sda": (1073741824.0, 2147483648.0), "sdb": (2048.0, 0.0)},
            "dev_db": {"nvme0n1": (4096.0, 512.0)},                  # its cgroup-style name, as the table keys it
            "bare": {"dev-lxd_root": (100.0, 200.0)}})               # from QEMU: no agent in this one

    def test_odd_lines_are_skipped(self):
        text = ('lxd_disk_read_bytes_total{device="sda",name="win",type="virtual-machine"} many\n'
                'lxd_disk_read_bytes_total{device="sda"\n'
                'lxd_disk_read_bytes_total\n')
        self.assertEqual(lxdtop.vm_disk_totals(text), {})

    def setUp(self):
        self.host = FakeHost(self)
        self.host.set_basics()
        self.host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        self.host.copy_fixture("pid-stat-qemu.txt", "/proc/5151/stat")
        self.host.copy_fixture("pid-status-qemu.txt", "/proc/5151/status")

    def run_lxdtop(self, zfs=False):
        """The API's readings 15 s apart, and the screen's, for the fixture's VM, win: its disk on a dir pool,
        or on ZFS. Between the readings, win reads 15 MiB and writes 30 MiB."""
        if zfs:
            self.host.add_objset("default", "0x9", "default/virtual-machines/win.block", 1, 1)
        routes = lxd_routes(**{"/1.0/metrics": (200, metrics())})
        with FakeLxd(routes) as lxd, self.host.installed():
            api = lxdtop.LxdApi(lxd.path)
            mon = lxdtop.Monitor(api, None)
            for i in range(3):
                api.fetch()
                mon.sample()
                self.host.clock += 15
                lxd.routes["/1.0/metrics"] = (200, metrics(1.073741824e+09 + (i + 1) * 15 * 2 ** 20,
                                                           2.147483648e+09 + (i + 1) * 30 * 2 ** 20))
        return mon, [target for _, target, _ in lxd.requests]

    def test_a_vm_not_on_zfs_gets_its_disk_numbers_from_lxds_metrics(self):
        mon, requests = self.run_lxdtop()
        win = next(r for r in mon.rows if r["name"] == "win")
        self.assertEqual((win["rd"], win["wr"]), (2 ** 20, 2 * 2 ** 20))       # bytes a second, over 15 s
        self.assertEqual(requests.count("/1.0/metrics"), 2)    # once the screen asked: the API's 2nd and 3rd
        doc = lxdtop.reading_json(mon, 2)
        win = next(i for i in doc["instances"] if i["name"] == "win")
        self.assertEqual((win["disk_read_bytes_per_s"], win["disk_write_bytes_per_s"], win["disk_source"]),
                         (1048576.0, 2097152.0, "lxd"))
        ui = lxdtop.new_ui(2)
        ui.view, ui.detail = "detail", "win"
        canvas = lxdtop.TextCanvas(45, 160)
        with glyphs(lxdtop.GLYPHS_UNICODE), mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
            lxdtop.draw(canvas, mon, ui)
        self.assertIn("disk        read 1.0M/s   write 2.0M/s   from lxd", canvas.text())

    def test_a_vm_on_zfs_keeps_its_zfs_numbers_and_lxd_is_not_asked(self):
        mon, requests = self.run_lxdtop(zfs=True)
        self.assertNotIn("/1.0/metrics", requests)
        self.assertEqual(lxdtop.disk_source(next(r for r in mon.rows if r["name"] == "win"), mon), "zfs")

    def test_without_lxds_metrics_it_shows_none_as_before(self):
        with FakeLxd(lxd_routes()) as lxd, self.host.installed():         # /1.0/metrics: 404
            api = lxdtop.LxdApi(lxd.path)
            mon = lxdtop.Monitor(api, None)
            for _ in range(3):
                api.fetch()
                mon.sample()
                self.host.clock += 15
        win = next(r for r in mon.rows if r["name"] == "win")
        self.assertEqual((win["rd"], win["wr"], api.status), (None, None, "ok"))


if __name__ == "__main__":
    unittest.main()
